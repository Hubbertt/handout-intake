#!/usr/bin/env python3
"""Build an isolated semantic Word handout from a reviewed content blueprint.

This is a content-assembly step, not a release installer.  Every visible block
must carry a semantic type and source provenance.  Typography comes only from
the current ChengziClass parameter registry.  The generated candidate must
still be opened, repaginated, saved, and accepted by Microsoft Word before it
can become a formal master or produce a PDF.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from xml.sax.saxutils import escape
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor, Twips


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from semantic_title_visual_text_plugin_contract import (  # noqa: E402
    TitleVisualTextEvidenceError,
    build_cache_key,
    build_input_fingerprint,
    validate_evidence_records,
)

# 默认参数表由环境变量给出(执行器从册级绑定注入);工序表总是显式传 --params,
# 这里只是没传时的兜底——兜底也不该指向生产线的固定位置。
import os as _os
DEFAULT_PARAMS = Path(_os.environ.get("HANDOUT_INTAKE_PARAMS_PATH")
                      or (SCRIPT_DIR.parent.parent
                          / "templates/summer-class-layout/summer_class_module_parameters.current.json"))

BLOCK_STYLE_IDS = {
    "chapter": "CZ_ChapterTitle",
    "heading1": "CZ_Heading1",
    "heading2": "CZ_Heading2",
    "heading3": "CZ_Heading3",
    # The textbook's own tree is six deep — 主题 > 专题 > 课题 > 课时 > 栏目 >
    # 知识点 — and the compiler could only express four, so a column banner and
    # a knowledge point had to share a level with the lesson they sit inside.
    "heading4": "CZ_Heading4",
    "heading5": "CZ_Heading5",
    "body": "CZ_Body",
    "long_text": "CZ_LongText",
    "objective": "CZ_CalloutBody",
    "callout_title": "CZ_CalloutTitle",
    "callout_body": "CZ_CalloutBody",
    "callout_subpoint": "CZ_CalloutSubpoint",
    "exercise": "CZ_ExerciseStem",
    # The audits already accept these two as the right styles for an exercise
    # group heading and for a wrapped exercise line; the compiler had no way to
    # emit either, so every group heading came out as a generic heading3 and
    # every continuation as body text.
    "exercise_group_title": "CZ_ExerciseGroupTitle",
    "exercise_continuation": "CZ_ExerciseContinuation",
    # A chemical equation between ① and ② belongs to ①, not to the page edge.
    "list_continuation": "CZ_ListContinuation",
    "choice": "CZ_ChoiceOption",
    "answer_line": "CZ_AnswerLine",
    "caption": "CZ_Caption",
    "image": "CZ_ImageBlock",
    "layout_spacer": "CZ_LayoutSpacer",
}

# How many options the source put on one row decides the column geometry. The
# grouping is the source's; the column positions are the registry's. A row of
# three takes the four-column stops and leaves the last one empty, which is
# what the source's own three-option rows look like.
CHOICE_COLUMN_STYLE_IDS = {
    1: "CZ_ChoiceOption",
    2: "CZ_ChoiceOptionPair",
    3: "CZ_ChoiceOptionQuad",
    4: "CZ_ChoiceOptionQuad",
}

# The registry defines 65 character styles; a run type is the door through
# which a blueprint can reach one. Six doors meant the other 59 styles were
# unreachable no matter what the registry said — the label colour a source
# actually uses (CZ_TopicLabel is bold 1F4E79, exactly what these handouts
# print) had to be flattened to bare bold. Doors are added as real content
# needs them, not speculatively.
RUN_STYLE_IDS = {
    "plain": None,
    "emphasis": "CZ_Emphasis",
    "emphasis_underline": "CZ_EmphasisUnderline",
    # Yellow is this volume's only 「look here」 mark, and it carries weight as
    # well as ground: the fill says where, the weight lifts it off the page.
    # Expressed by pointing at the bold member of the highlight family rather
    # than by setting bold on the plain one — the character styles are a closed
    # lattice in which every bold style owes a non-bold sibling to fall back to
    # inside an already-bold paragraph, and mutating CZ_Highlight broke it.
    "highlight": "CZ_EmphasisHighlight",
    "fill_blank": "CZ_FillBlank",
    "chemical_subscript": "CZ_ChemicalSubscript",
    "chemical_superscript": "CZ_ChemicalSuperscript",
    "topic_label": "CZ_TopicLabel",
    # 样式本体「橙子斜体标记」(italic:true)一直在 wordStyleRegistry 里,只是没有
    # 语义名指向它——于是蓝图无从表达斜体,run_type_for 也就没必要产出它,两边互相
    # 印证着「不需要」。第一本非沪科版的册子(八年级物理)撞破了这个循环:物理量符号
    # f/u/v/AO 按排版惯例是斜体,源里 254 个,成品里 0 个,没有任何门报警。
    "italic": "CZ_Italic",
    "section_banner": "CZ_SectionBanner",
    "emphasis_mark": "CZ_EmphasisMarkDot",
    "source_tag": "CZ_SourceTag",
}

FORBIDDEN_STUDENT_MARKERS = (
    "source_id",
    "OCR复核",
    "内部复核",
    "教师提示",
    "参考答案",
    "答案解析",
)

SOURCE_POLICY_ID = "chengziclass.student-or-original-word-only.v1"
CONTENT_FIDELITY_POLICY_ID = (
    "verbatim-source-visible-text-with-explicit-exclusions.v1"
)
TITLE_DECORATION_CLASSIFICATION = "non_instructional_title_decoration"
SUPPORTED_SOURCE_EXCLUSION_CLASSIFICATIONS = {
    TITLE_DECORATION_CLASSIFICATION,
    "source_platform_metadata",
    "student_answer_or_teacher_content",
    "branding_watermark",
    "non_instructional_shape_carrier",
}
SOURCE_TITLE_HANDLING = "alias-only"
CANONICAL_TITLE_BLOCK_TYPES = {"chapter", "heading1", "heading2", "heading3"}
CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
CONTENT_SOURCE_STATUSES = {"student_word", "original_word"}
LAYOUT_SOURCE_STATUS = "layout"
SOURCE_LOCATOR_KINDS = {
    "paragraph",
    "table",
    "image",
    "shape",
    "bookmark",
    "range",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MIN_COMPATIBILITY_MODE = 15
COMPATIBILITY_MODE_URI = "http://schemas.microsoft.com/office/word"


class BlueprintError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlueprintError(f"JSON root must be an object: {path}")
    return value


def set_val(parent: Any, tag: str, value: Any) -> None:
    child = parent.find(qn(tag))
    if child is None:
        child = OxmlElement(tag)
        parent.append(child)
    child.set(qn("w:val"), str(value))


def set_on_off(parent: Any, tag: str, enabled: bool) -> None:
    child = parent.find(qn(tag))
    if enabled:
        if child is None:
            child = OxmlElement(tag)
            parent.append(child)
        child.set(qn("w:val"), "1")
    elif child is not None:
        parent.remove(child)


def enforce_current_docx_compatibility(document: Document,
                                       params: dict[str, Any] | None = None) -> None:
    """Keep newly assembled DOCX files out of Word compatibility mode.

    One legacy flag is not legacy here. Word does not underline trailing
    spaces unless w:ulTrailSpace says to, and an answer blank at the end of a
    line is exactly a run of trailing spaces — 33 of the 1139 blanks in this
    set printed with no line at all. The source carries the flag; a
    fill-in-the-blank handout needs it, so it is declared in the registry and
    written here rather than inherited by luck.
    """
    settings = document.settings.element
    compat = settings.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings.append(compat)
    matches = [
        item
        for item in compat.findall(qn("w:compatSetting"))
        if item.get(qn("w:name")) == "compatibilityMode"
    ]
    if matches:
        mode = matches[0]
        for duplicate in matches[1:]:
            compat.remove(duplicate)
    else:
        mode = OxmlElement("w:compatSetting")
        compat.append(mode)
    mode.set(qn("w:name"), "compatibilityMode")
    mode.set(qn("w:uri"), COMPATIBILITY_MODE_URI)
    declared = ((params or {}).get("wordStyleRegistry") or {}).get("compatFlags") or []
    for flag in declared:
        if compat.find(qn(f"w:{flag}")) is None:
            compat.insert(0, OxmlElement(f"w:{flag}"))
    mode.set(qn("w:val"), str(MIN_COMPATIBILITY_MODE))


# Enough of the CT_RPr / CT_PPrBase child order to place the typography
# switches. Word rejects a properties element whose children are out of
# schema order, so a switch cannot simply be appended.
RPR_DEFAULT_ORDER = ("w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs",
                     "w:caps", "w:smallCaps", "w:strike", "w:dstrike",
                     "w:outline", "w:shadow", "w:emboss", "w:imprint",
                     "w:noProof", "w:snapToGrid", "w:vanish", "w:webHidden",
                     "w:color", "w:spacing", "w:w", "w:kern", "w:position",
                     "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect",
                     "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl",
                     "w:cs", "w:em", "w:lang")
PPR_DEFAULT_ORDER = ("w:pStyle", "w:keepNext", "w:keepLines",
                     "w:pageBreakBefore", "w:framePr", "w:widowControl",
                     "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd",
                     "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
                     "w:wordWrap", "w:overflowPunct", "w:topLinePunct",
                     "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
                     "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind")


def insert_in_schema_order(parent: Any, tag: str, order: tuple[str, ...]) -> Any:
    """Get or create a child, keeping the element in schema order."""
    existing = parent.find(qn(tag))
    if existing is not None:
        return existing
    child = OxmlElement(tag)
    rank = order.index(tag)
    for sibling in parent:
        name = sibling.tag.split("}")[-1]
        candidate = f"w:{name}"
        if candidate not in order or order.index(candidate) > rank:
            sibling.addprevious(child)
            return child
    parent.append(child)
    return child


def enforce_document_typography_defaults(document: Document,
                                         params: dict[str, Any]) -> None:
    """Declare the CJK typography switches instead of inheriting them.

    autoSpaceDE/DN (the automatic gap between Chinese and Latin text, and
    between Chinese and digits) default to on when the element is absent, and
    kerning defaults to off. Relying on the first is inheriting a default that
    any template is free to turn off, and the second simply loses the pair
    kerning that a Chinese Word document normally has. Both are declared in
    the registry and written here.
    """
    # 真源只有 docDefaults1。曾经这两项另有一份 wordStyleRegistry.documentDefaults,
    # 是 2026-08-15 加 docDefaults1 时我造出来的重复——同一事实两处,必漂。
    # 合并前先逐值比对过:kern 2==2、autoSpaceDE/DN true==true,确认未漂才删,
    # 否则删旧块就是静默改行为(值一样时删是清理,值不一样时删是改产品)。
    root = (params or {}).get("docDefaults1") or {}
    if not root:
        return
    styles = document.styles.element
    doc_defaults = styles.find(qn("w:docDefaults"))
    if doc_defaults is None:
        doc_defaults = OxmlElement("w:docDefaults")
        styles.insert(0, doc_defaults)
    kern = (root.get("rPrDefault") or {}).get("kern")
    if kern is not None:
        holder = doc_defaults.find(qn("w:rPrDefault"))
        if holder is None:
            holder = OxmlElement("w:rPrDefault")
            doc_defaults.insert(0, holder)
        rpr = holder.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            holder.append(rpr)
        insert_in_schema_order(rpr, "w:kern", RPR_DEFAULT_ORDER).set(
            qn("w:val"), str(int(kern)))
    # 从 docDefaults1 接管那些**与 Word 应用默认不同**的项——只有这些留得住。
    #
    # 现状:rFonts(主题引用)/sz/szCs/lang/pPr spacing 全部来自 python-docx 自带的
    # default.docx。库一升级,这些值就可能变,而我们毫不知情。写出来即夺回所有权。
    #
    # 值等于 Word 应用默认的那些(caps=0、autoSpaceDE=1 等)这里不发射:实测 Word
    # 存盘必清,写了也留不住。它们的自足由参数表承载,由生效值门校验。
    root = (params or {}).get("docDefaults1") or {}
    rpr_root = root.get("rPrDefault") or {}
    if rpr_root:
        holder = doc_defaults.find(qn("w:rPrDefault"))
        if holder is None:
            holder = OxmlElement("w:rPrDefault")
            doc_defaults.insert(0, holder)
        rpr = holder.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            holder.append(rpr)
        fonts = rpr_root.get("rFonts")
        if isinstance(fonts, dict):
            el = insert_in_schema_order(rpr, "w:rFonts", RPR_DEFAULT_ORDER)
            for key, value in fonts.items():
                if not key.startswith("_"):
                    el.set(qn(f"w:{key}"), str(value))
        for key, tag in (("sz", "w:sz"), ("szCs", "w:szCs")):
            if rpr_root.get(key) is not None:
                insert_in_schema_order(rpr, tag, RPR_DEFAULT_ORDER).set(
                    qn("w:val"), str(int(rpr_root[key])))
        lang = rpr_root.get("lang")
        if isinstance(lang, dict):
            el = insert_in_schema_order(rpr, "w:lang", RPR_DEFAULT_ORDER)
            for key, value in lang.items():
                if not key.startswith("_"):
                    el.set(qn(f"w:{key}"), str(value))
    spacing = (root.get("pPrDefault") or {}).get("spacing")
    if isinstance(spacing, dict):
        holder = doc_defaults.find(qn("w:pPrDefault"))
        if holder is None:
            holder = OxmlElement("w:pPrDefault")
            doc_defaults.append(holder)
        ppr = holder.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            holder.append(ppr)
        el = insert_in_schema_order(ppr, "w:spacing", PPR_DEFAULT_ORDER)
        for key, value in spacing.items():
            if not key.startswith("_"):
                el.set(qn(f"w:{key}"), str(value))

    # autoSpaceDE/DN 现从 docDefaults1.pPrDefault 取。
    #
    # ★白名单是显式的两项,不是「所有布尔键」。pPrDefault 有 14 键,其中
    # keepNext/keepLines/widowControl/contextualSpacing/pageBreakBefore 同样是布尔、
    # 同样在 PPR_DEFAULT_ORDER 里——按类型筛会顺手多发五项。旧块只承载这两项,
    # 合并就该逐值等价;多发是改产品,不是合并。valuePolicy 写的正是这件事:
    # 夺回所有权与改进取值分开做,混在一起,落地后出现差异就分不清是哪一个引起的。
    # 要发那五项,是另一次有意的、单独可测的改动。
    THIS_PATH_OWNS = ("autoSpaceDE", "autoSpaceDN")
    paragraph_defaults = root.get("pPrDefault") or {}
    switches = [(f"w:{name}", bool(paragraph_defaults[name]))
                for name in THIS_PATH_OWNS
                if isinstance(paragraph_defaults.get(name), bool)]
    if switches:
        holder = doc_defaults.find(qn("w:pPrDefault"))
        if holder is None:
            holder = OxmlElement("w:pPrDefault")
            doc_defaults.append(holder)
        ppr = holder.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            holder.append(ppr)
        for tag, enabled in switches:
            insert_in_schema_order(ppr, tag, PPR_DEFAULT_ORDER).set(
                qn("w:val"), "1" if enabled else "0")


def strip_inherited_unused_styles(document, params: dict) -> list[str]:
    """删掉库模板带来的、我们没声明也没用到的样式。

    python-docx 的 default.docx 自带一批 Word 内置样式(macro、宏文本 字符 等)。
    它们跟着每一份成品走,其中有的引用了我们从未声明的字体——实测 Courier 4 处。
    不影响当前渲染(使用 0 处),但一旦有人用了那个样式,就会渲成没声明过的字体,
    而那正是 GATE_FONT_OWNERSHIP 要拦的东西。

    **不把 Courier 补进声明**:那是把继承来的包袱登记成我们的规范。
    删掉未用的包袱,才是让门诚实地变绿。

    保守起见只删同时满足三条的:不在注册表、正文/页眉页脚未引用、
    且没有被保留下来的样式以 basedOn/link/next 引用。
    """
    registry = (params or {}).get("wordStyleRegistry") or {}
    declared = set(registry.get("paragraphStyles") or {}) | set(
        registry.get("characterStyles") or {})
    declared |= {spec.get("name") for group in ("paragraphStyles", "characterStyles")
                 for spec in (registry.get(group) or {}).values()
                 if isinstance(spec, dict) and spec.get("name")}

    # **必须扫页眉页脚,不能只扫正文。** 首版只取 document.element.body,
    # 页眉里用到的样式会被当成「没人用」而删掉。这次没出事,是因为 Word 存盘时
    # 按需重建了内置样式——**靠环境兜底,不是判据正确**,换个 Word 就未必。
    # 只遍历**已存在**的页眉页脚部件,不碰 section.header 这类属性——
    # python-docx 的那些属性是惰性创建的,**访问即创建**。上一版用它们做「只读扫描」,
    # 凭空造出空的 header3.xml 与无 PAGE 域的 footer3.xml,合规审计当场报出两条 fail。
    # 读操作有副作用,是这一类里最容易忽略的。
    roots = [document.element.body]
    for rel in document.part.rels.values():
        if rel.is_external:
            continue
        if "header" in rel.reltype or "footer" in rel.reltype:
            try:
                roots.append(rel.target_part.element)
            except Exception:
                continue
    used = {el.get(qn("w:val")) for root in roots for el in root.iter()
            if el.tag in (qn("w:pStyle"), qn("w:rStyle")) and el.get(qn("w:val"))}

    styles_el = document.styles.element
    removed: list[str] = []
    changed = True
    while changed:
        changed = False
        kept = [el for el in styles_el.findall(qn("w:style"))]
        referenced = set()
        for el in kept:
            sid = el.get(qn("w:styleId"))
            name_el = el.find(qn("w:name"))
            name = name_el.get(qn("w:val")) if name_el is not None else None
            if sid in used or sid in declared or name in declared:
                for tag in ("w:basedOn", "w:link", "w:next"):
                    ref = el.find(qn(tag))
                    if ref is not None and ref.get(qn("w:val")):
                        referenced.add(ref.get(qn("w:val")))
        for el in kept:
            sid = el.get(qn("w:styleId"))
            name_el = el.find(qn("w:name"))
            name = name_el.get(qn("w:val")) if name_el is not None else None
            if el.get(qn("w:default")) == "1":
                continue                       # 默认样式不动
            if sid in used or sid in declared or name in declared:
                continue
            if sid in referenced:
                continue
            styles_el.remove(el)
            removed.append(f"{name or '?'}({sid})")
            changed = True
    return removed


def style_type_for(style_id: str) -> WD_STYLE_TYPE:
    return WD_STYLE_TYPE.CHARACTER if style_id in {
        value for value in RUN_STYLE_IDS.values() if value
    } or style_id.startswith("CZ_Chemical") or style_id in {
        "CZ_Emphasis",
        "CZ_Highlight",
        "CZ_FillBlank",
    } else WD_STYLE_TYPE.PARAGRAPH


# Registry keys ensure_style() actually materialises into the .docx style.
IMPLEMENTED_SPEC_KEYS = frozenset({
    "name", "fontAscii", "fontCn", "fontCs", "sizePt", "bold", "italic", "color",
    "underline", "verticalAlign", "highlight", "emphasisMark", "shading",
    "paragraphShading", "paragraphBorders",
    # S1 第一刀:样式元数据,零渲染影响。hidden 未纳入——它与 semiHidden 的
    # 映射关系有歧义,待单独裁决,现仍如实记为未落地。
    "uiPriority", "qFormat", "semiHidden", "unhideWhenUsed",
    # S1 第二刀:字符级 A 类。langEastAsia 未纳入——docDefaults 是 en-US 而注册表
    # 声明 zh-CN,发射会改中文禁则断行,属 B 类须单独裁决。
    "allCaps", "smallCaps", "strike", "characterSpacingTwips",
    "positionHalfPt", "scalePercent", "langVal", "contextualSpacing",
    "outlineLevel", "beforeDxa", "afterDxa", "lineDxa", "lineRule", "alignment",
    "keepNext", "keepLines", "pageBreakBefore",
    "leftIndentDxa", "rightIndentDxa", "hangingDxa", "firstLineDxa",
    "firstLineChars", "tabStopDxa", "tabStopsDxa",
})

# Advisory/documentation keys that intentionally carry no Word formatting.
ADVISORY_SPEC_KEYS = frozenset({
    "rule", "required", "scope", "tocLevel", "studentVersionAllowed",
    "inheritsParagraphFont", "inheritsSize", "inheritsWeight",
    "boldAllowed", "italicAllowed", "visualPassThrough", "wordMasterUsage",
    "styleInheritance", "imageOnlyCellParagraph", "usedInThisEdition",
    # 溯源字段是文档,不是排版属性。此前它们被算进「声明了没落地」,
    # 在报告里堆了 77 条噪音(provenanceByKey 37 / provenance 24 /
    # provenanceUnresolvedKeys 16)。噪音会把真缺口淹掉——本轮 GATE_GLYPH_COVERAGE
    # 就被 100 个制表符的假报盖住了旁边那条真发现。测量要先洗干净再据以行动。
    "provenance", "provenanceByKey", "provenanceUnresolvedKeys",
})

# style_id -> sorted list of declared-but-not-materialised keys. Reported in
# the build report so the gap is measurable instead of silent; this is how the
# dropped underline/verticalAlign/highlight keys stayed invisible for so long.
UNIMPLEMENTED_SPEC_KEYS: dict[str, list[str]] = {}


def record_unimplemented_spec_keys(style_id: str, spec: dict[str, Any]) -> None:
    dropped = sorted(
        key for key in spec
        if key not in IMPLEMENTED_SPEC_KEYS and key not in ADVISORY_SPEC_KEYS
    )
    if dropped:
        UNIMPLEMENTED_SPEC_KEYS[style_id] = dropped


def ensure_style(
    document: Document,
    style_id: str,
    spec: dict[str, Any],
    style_type: WD_STYLE_TYPE,
) -> Any:
    style = next(
        (item for item in document.styles if item.style_id == style_id),
        None,
    )
    if style is None:
        name = str(spec.get("name") or style_id)
        style = document.styles.add_style(name, style_type)
        style._element.set(qn("w:styleId"), style_id)
        style._element.set(qn("w:customStyle"), "1")
    # The declared properties are written whether or not the style was already
    # in the template. Returning early left the template as the authority: the
    # registry could say a banner caption is not italic and the document would
    # still print it italic — and carry that italic into the generated table of
    # contents.
    font = style.font
    if spec.get("fontAscii"):
        font.name = str(spec["fontAscii"])
    if spec.get("sizePt") is not None:
        font.size = Pt(float(spec["sizePt"]))
    if spec.get("bold") is not None:
        font.bold = bool(spec["bold"])
    if spec.get("italic") is not None:
        font.italic = bool(spec["italic"])
    if spec.get("color"):
        font.color.rgb = RGBColor.from_string(str(spec["color"]).lstrip("#"))
    rpr = style._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr, field in (
        ("w:eastAsia", "fontCn"),
        ("w:ascii", "fontAscii"),
        ("w:hAnsi", "fontAscii"),
        ("w:cs", "fontCs"),
    ):
        if spec.get(field):
            rfonts.set(qn(attr), str(spec[field]))
    # Character decoration declared by the registry. Without these the styles
    # whose ONLY distinguishing property is one of them (CZ_FillBlank,
    # CZ_ChemicalSubscript, CZ_Highlight, CZ_EmphasisMarkDot, the semantic
    # bands) materialise as name-only shells and render as plain text.
    if spec.get("underline"):
        set_val(rpr, "w:u", spec["underline"])
    if spec.get("verticalAlign"):
        set_val(rpr, "w:vertAlign", spec["verticalAlign"])
    if spec.get("highlight"):
        set_val(rpr, "w:highlight", spec["highlight"])
    if spec.get("emphasisMark"):
        set_val(rpr, "w:em", spec["emphasisMark"])
    # 字符级 A 类:值与 docDefaults 现值相同,故显式化是惰性的——不是我判断的,
    # 是读 docx 的 docDefaults 比出来的:
    #   allCaps/smallCaps/strike        docDefaults 未设 → 默认 false,我们声明 false
    #   characterSpacingTwips/position  未设 → 默认 0,   我们声明 0
    #   scalePercent                    未设 → 默认 100, 我们声明 100
    #   langVal                         docDefaults en-US,我们声明 en-US
    # 显式发射后 docx 自足,不再依赖目标机器的 Normal.dotm。
    #
    # **langEastAsia 不在此列。** docDefaults 是 w:eastAsia="en-US",而注册表声明
    # zh-CN——一份中文讲义的东亚语言默认竟是 en-US。改它会影响中文禁则断行,
    # 可能让 106 页重新分页,属 B 类须单独裁决,故这里不发射,仍如实记为未落地。
    for field, tag in (("allCaps", "w:caps"), ("smallCaps", "w:smallCaps"),
                       ("strike", "w:strike")):
        if spec.get(field) is not None:
            set_val(rpr, tag, "1" if spec[field] else "0")
    for field, tag in (("characterSpacingTwips", "w:spacing"),
                       ("positionHalfPt", "w:position"),
                       ("scalePercent", "w:w")):
        if spec.get(field) is not None:
            set_val(rpr, tag, str(int(spec[field])))
    if spec.get("langVal"):
        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            rpr.append(lang)
        lang.set(qn("w:val"), str(spec["langVal"]))

    shading = spec.get("shading")
    if isinstance(shading, dict):
        shd = rpr.find(qn("w:shd"))
        if shd is None:
            shd = OxmlElement("w:shd")
            rpr.append(shd)
        shd.set(qn("w:val"), str(shading.get("val") or "clear"))
        shd.set(qn("w:color"), str(shading.get("color") or "auto"))
        shd.set(qn("w:fill"), str(shading.get("fill") or "auto"))
    # 样式元数据:控制 Word 样式库里的呈现,不影响渲染。
    #
    # S1 第一刀取这四个,因为语义明确、零渲染影响,能干净地验完——
    # 字符级那批(allCaps/smallCaps/strike/spacing/position/scale/lang)会改渲染,
    # 要配逐页比对单独一轮。
    #
    # 直接发射 OOXML 元素而不用 python-docx 的属性:注册表里 hidden 与 semiHidden
    # 是两个键,而 python-docx 的 .hidden 映射到哪个元素探测不出确切结论。
    # 猜一个映射写进共享编译器,正是本轮反复在防的形状。
    # **hidden 本刀不做**,待单独裁决它到底指 w:semiHidden 还是 run 的 w:vanish。
    style_el = style._element
    if spec.get("uiPriority") is not None:
        set_val(style_el, "w:uiPriority", str(int(spec["uiPriority"])))
    for key, tag in (("qFormat", "w:qFormat"),
                     ("semiHidden", "w:semiHidden"),
                     ("unhideWhenUsed", "w:unhideWhenUsed")):
        if key not in spec:
            continue
        existing = style_el.find(qn(tag))
        if spec[key]:
            if existing is None:
                style_el.append(OxmlElement(tag))   # 空元素,出现即为真
        elif existing is not None:
            style_el.remove(existing)               # 显式声明 false 即移除

    record_unimplemented_spec_keys(style_id, spec)
    if style.type == WD_STYLE_TYPE.PARAGRAPH:
        ppr = style._element.get_or_add_pPr()
        # 段落级底纹,铺满版心宽;与上面那个 shading 是两回事。
        #
        # 上面的 shading 写进 rPr,是**字符级**——只铺在文字后面。栏目标题要的是
        # 整行色块(取代源文那张红色横幅),必须写进 pPr。两者行为不同,故用不同
        # 字段名:共用一个名字,配错的人不会得到报错,只会得到另一个版式,
        # 而「看起来配好了、渲染是另一个东西」正是最难发现的那类错。
        # contextualSpacing:docDefaults 未设 → 默认 false,注册表声明 false,故惰性。
        # 三个 *SourceOverlay 样式按格式契约不带视觉字段,已在参数表侧排除,
        # 此处不会拿到该键。
        if spec.get("contextualSpacing") is not None:
            set_val(ppr, "w:contextualSpacing", "1" if spec["contextualSpacing"] else "0")
        # 段落边框:三级标题的左侧竖线。二级用整行底纹、三级用左竖线,同色不同重量,
        # 层级差别由「份量」而非「新配色」承担——与本轮不引入新字体新配色的裁定一致。
        # **必须用 insert_in_schema_order**:w:pBdr 在 CT_PPrBase 里排在 w:shd 之前,
        # 而下面 shading 用的是裸 append。裸 append 之所以一直没出事,只因代码顺序
        # 碰巧与 schema 顺序一致;pBdr 若照抄就会落到 shd 后面,Word 直接拒收文件。
        para_borders = spec.get("paragraphBorders")
        if isinstance(para_borders, dict):
            pbdr = insert_in_schema_order(ppr, "w:pBdr", PPR_DEFAULT_ORDER)
            for edge in ("top", "left", "bottom", "right", "between", "bar"):
                edge_spec = para_borders.get(edge)
                if not isinstance(edge_spec, dict):
                    continue
                el = pbdr.find(qn(f"w:{edge}"))
                if el is None:
                    el = OxmlElement(f"w:{edge}")
                    pbdr.append(el)
                # val 一律显式写出(含 "none"):缺省与 none 在 Word 里是两回事,
                # 前者继承、后者夺权。这正是「是 0 的都要标出 0」在边框上的形状。
                el.set(qn("w:val"), str(edge_spec.get("val") or "none"))
                el.set(qn("w:sz"), str(int(edge_spec.get("sz", 0))))
                el.set(qn("w:space"), str(int(edge_spec.get("space", 0))))
                el.set(qn("w:color"), str(edge_spec.get("color") or "auto"))
        para_shading = spec.get("paragraphShading")
        if isinstance(para_shading, dict):
            shd = ppr.find(qn("w:shd"))
            if shd is None:
                shd = OxmlElement("w:shd")
                ppr.append(shd)
            shd.set(qn("w:val"), str(para_shading.get("val") or "clear"))
            shd.set(qn("w:color"), str(para_shading.get("color") or "auto"))
            shd.set(qn("w:fill"), str(para_shading.get("fill") or "auto"))
        if spec.get("outlineLevel") is not None:
            outline_level = spec["outlineLevel"]
            set_val(
                ppr,
                "w:outlineLvl",
                9 if outline_level == "body" else int(outline_level),
            )
        spacing = ppr.find(qn("w:spacing"))
        if spacing is None:
            spacing = OxmlElement("w:spacing")
            ppr.append(spacing)
        for attr, field in (
            ("w:before", "beforeDxa"),
            ("w:after", "afterDxa"),
            ("w:line", "lineDxa"),
        ):
            if spec.get(field) is not None:
                spacing.set(qn(attr), str(int(spec[field])))
        if spec.get("lineRule"):
            spacing.set(qn("w:lineRule"), str(spec["lineRule"]))
        alignment = str(spec.get("alignment") or "").lower()
        if alignment:
            set_val(
                ppr,
                "w:jc",
                {
                    "left": "left",
                    "center": "center",
                    "right": "right",
                    "justify": "both",
                }.get(alignment, alignment),
            )
        set_on_off(ppr, "w:keepNext", bool(spec.get("keepNext")))
        set_on_off(ppr, "w:keepLines", bool(spec.get("keepLines")))
        set_on_off(ppr, "w:pageBreakBefore", bool(spec.get("pageBreakBefore")))
        # Indent geometry. The registry declares where a numbered item's label
        # sits and where its text starts; without these the styles that exist
        # only to hold that geometry — CZ_ChoiceOption, CZ_ExerciseContinuation
        # — come out with the same indent as body text, and the label/text
        # alignment the spec is explicit about never happens.
        indent_fields = (
            ("w:left", "leftIndentDxa"),
            ("w:right", "rightIndentDxa"),
            ("w:hanging", "hangingDxa"),
            ("w:firstLine", "firstLineDxa"),
            ("w:firstLineChars", "firstLineChars"),
        )
        if any(spec.get(field) is not None for _, field in indent_fields):
            ind = ppr.find(qn("w:ind"))
            if ind is None:
                ind = OxmlElement("w:ind")
                ppr.append(ind)
            for attr, field in indent_fields:
                if spec.get(field) is not None:
                    ind.set(qn(attr), str(int(spec[field])))
        # One stop or several. A row carrying four options needs a stop at each
        # column, and a column that a paragraph style does not declare would
        # have to be direct formatting, which the spec forbids.
        positions = spec.get("tabStopsDxa")
        if not isinstance(positions, list):
            positions = [spec["tabStopDxa"]] if spec.get("tabStopDxa") is not None else []
        if positions:
            tabs = ppr.find(qn("w:tabs"))
            if tabs is None:
                tabs = OxmlElement("w:tabs")
                ppr.append(tabs)
            for position in positions:
                tab = OxmlElement("w:tab")
                tab.set(qn("w:val"), "left")
                tab.set(qn("w:pos"), str(int(position)))
                tabs.append(tab)
    return style


def install_registered_styles(document: Document, params: dict[str, Any]) -> set[str]:
    registry = params.get("wordStyleRegistry") or {}
    specs: dict[str, dict[str, Any]] = {}
    paragraph_specs = registry.get("paragraphStyles") or {}
    character_specs = registry.get("characterStyles") or {}
    if isinstance(paragraph_specs, dict):
        specs.update({str(k): v for k, v in paragraph_specs.items() if isinstance(v, dict)})
    if isinstance(character_specs, dict):
        specs.update({str(k): v for k, v in character_specs.items() if isinstance(v, dict)})
    required = set(BLOCK_STYLE_IDS.values()) | {
        value for value in RUN_STYLE_IDS.values() if value
    } | {"CZ_TableHeader", "CZ_TableText", "CZ_TocTitle"}
    missing = sorted(required - set(specs))
    if missing:
        raise BlueprintError(f"Current parameter registry is missing required styles: {missing}")
    for style_id, spec in paragraph_specs.items():
        if style_id.startswith("CZ_") and isinstance(spec, dict):
            ensure_style(document, style_id, spec, WD_STYLE_TYPE.PARAGRAPH)
    for style_id, spec in character_specs.items():
        if style_id.startswith("CZ_") and isinstance(spec, dict):
            ensure_style(document, style_id, spec, WD_STYLE_TYPE.CHARACTER)
    return set(specs)


def apply_page_setup(document: Document, params: dict[str, Any]) -> None:
    page = params.get("page") or {}
    margins = page.get("bodyMarginsMm") or {}
    # The registry declares the reading grid the whole book is spaced against
    # (420 dxa = 1.75 x 12pt). Left alone, every section carried the 360 the
    # empty python-docx template ships with, so the document told Word one
    # grid while the styles were built on another.
    line_pitch = int(page.get("docGridLinePitchDxa") or 0)
    for section in document.sections:
        section.page_width = Mm(float(page.get("widthDxa", 11906)) / 56.6929134)
        section.page_height = Mm(float(page.get("heightDxa", 16838)) / 56.6929134)
        section.top_margin = Mm(float(margins.get("top", 27.0)))
        section.bottom_margin = Mm(float(margins.get("bottom", 26.0)))
        section.left_margin = Mm(float(margins.get("left", 22.0)))
        section.right_margin = Mm(float(margins.get("right", 22.0)))
        section.header_distance = Mm(float(margins.get("header", 7.0)))
        section.footer_distance = Mm(float(margins.get("footer", 8.5)))
        if line_pitch:
            grid = section._sectPr.find(qn("w:docGrid"))
            if grid is None:
                grid = OxmlElement("w:docGrid")
                section._sectPr.append(grid)
            grid.set(qn("w:linePitch"), str(line_pitch))


def set_paragraph_style(paragraph: Any, style_id: str) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    set_val(ppr, "w:pStyle", style_id)


def set_run_style(run: Any, style_id: str | None) -> None:
    if not style_id:
        return
    rpr = run._r.get_or_add_rPr()
    set_val(rpr, "w:rStyle", style_id)


def set_table_width_dxa(table: Any, widths: list[int]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table_element = table._tbl
    table_properties = table_element.tblPr
    table_width = table_properties.find(qn("w:tblW"))
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        table_properties.append(table_width)
    table_width.set(qn("w:w"), str(sum(widths)))
    table_width.set(qn("w:type"), "dxa")
    table_indent = table_properties.find(qn("w:tblInd"))
    if table_indent is None:
        table_indent = OxmlElement("w:tblInd")
        table_properties.append(table_indent)
    table_indent.set(qn("w:w"), "0")
    table_indent.set(qn("w:type"), "dxa")
    borders = table_properties.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table_properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = borders.find(qn(f"w:{edge}"))
        if border is None:
            border = OxmlElement(f"w:{edge}")
            borders.append(border)
        border.set(qn("w:val"), "nil")
    grid = table_element.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(width))
        grid.append(column)
    for cell, width in zip(table.rows[0].cells, widths):
        cell.width = Inches(width / 1440.0)
        cell_properties = cell._tc.get_or_add_tcPr()
        cell_width = cell_properties.find(qn("w:tcW"))
        if cell_width is None:
            cell_width = OxmlElement("w:tcW")
            cell_properties.append(cell_width)
        cell_width.set(qn("w:w"), str(width))
        cell_width.set(qn("w:type"), "dxa")


def clear_header_or_footer(story: Any) -> None:
    for child in list(story._element):
        story._element.remove(child)


def add_styleref_field(paragraph: Any, style_id: str, cached: str) -> None:
    """页眉右区写 STYLEREF 域:每页自动取该页所属的那一级标题。

    规范要求页眉右区是「所在目录中的讲级标题」——它随页变化,不是一个常量,
    所以不能写成字面量。分节做也能实现,但要按标题边界切出几十个节;STYLEREF
    是 Word 为此设的原生机制,一个域搞定,且与 PAGE 域同属「构造保证」那一档。

    cached 是域的缓存结果,给未更新域的阅读器看;Word 打开或本流程第 5 步
    (word-native-toc-update-repaginate-save)会把它刷成真值。
    """
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instruction.text = f' STYLEREF "{style_id}" \\* MERGEFORMAT '
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = cached
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    for element in (begin, instruction, separate, result, end):
        run._r.append(element)


def add_header_table(
    story: Any,
    *,
    left_text: str,
    right_text: str,
    widths: list[int],
    right_style_ref: str | None = None,
) -> None:
    clear_header_or_footer(story)
    table = story.add_table(
        rows=1,
        cols=2,
        width=Inches(sum(widths) / 1440.0),
    )
    set_table_width_dxa(table, widths)
    left = table.cell(0, 0).paragraphs[0]
    right = table.cell(0, 1).paragraphs[0]
    set_paragraph_style(left, "CZ_HeaderLeft")
    set_paragraph_style(right, "CZ_HeaderRight")
    left.add_run(left_text)
    if right_style_ref:
        add_styleref_field(right, right_style_ref, right_text)
    else:
        right.add_run(right_text)


def add_page_field(paragraph: Any) -> None:
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction, separate, result, end):
        run = OxmlElement("w:r")
        run.append(element)
        paragraph._p.append(run)


def add_page_footer(story: Any) -> None:
    clear_header_or_footer(story)
    paragraph = story.add_paragraph()
    set_paragraph_style(paragraph, "CZ_FooterPageNumber")
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_field(paragraph)


def set_page_number_start(section: Any, value: int | None) -> None:
    section_properties = section._sectPr
    page_number = section_properties.find(qn("w:pgNumType"))
    if value is None:
        if page_number is not None:
            section_properties.remove(page_number)
        return
    if page_number is None:
        page_number = OxmlElement("w:pgNumType")
        section_properties.append(page_number)
    page_number.set(qn("w:start"), str(value))


def add_semantic_bookmark(paragraph: Any, block_id: str, semantic_type: str) -> None:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", f"CZSEM_{semantic_type}_{block_id}")[:38]
    bookmark_id = str(
        int(hashlib.sha1(f"{block_id}|{semantic_type}".encode("utf-8")).hexdigest()[:7], 16)
    )
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bookmark_id)
    start.set(qn("w:name"), safe)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bookmark_id)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


def reconcile_float(place: dict[str, Any], width_emu: int,
                    params: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """Re-read a source float's intent against our own text block.

    The source states an offset measured from its own column. Ours is not that
    column, so replaying the number replays a position that no longer means
    what it meant: two figures ended up past the right edge of the text block,
    and two left a channel three characters wide that Word duly filled — 「气体」
    then a hand's width of nothing then 「体积」, with 「减少」 on the line below.

    So the offset is clamped into the text block, and the side text may flow
    down is decided by how much room is actually left rather than by what the
    source happened to say.
    """
    registry = (params or {}).get("wordStyleRegistry") or {}
    standard = registry.get("floatingFigureStandard") or {}
    if not standard:
        return None, None
    horizontal = place.get("horizontal") or {}
    if horizontal.get("align") or horizontal.get("relativeFrom") not in (None, "column"):
        return None, None
    body_emu = float(((registry.get("derivation") or {}).get("base") or {})
                     .get("bodyWidthDxa") or 9411) / 1440 * 914400
    left = float(horizontal.get("offsetEmu") or 0)
    left = max(0.0, min(left, body_emu - width_emu))
    body_pt = float((registry.get("paragraphStyles") or {})
                    .get("CZ_Body", {}).get("sizePt", 12))
    channel = float(standard.get("minChannelChars") or 0) * body_pt * 12700
    # Clamping is about where the figure is; choosing a wrap side is about how
    # text behaves beside it. They were gated together on the wrap type, so a
    # wrapNone figure 1.1mm outside the left margin stayed there — the exemption
    # was written for the second decision and silently took the first with it.
    if str(place.get("wrap") or "") not in set(standard.get("appliesTo") or ()):
        changed = abs(left - float(horizontal.get("offsetEmu") or 0)) > 1
        return (int(left) if changed else None), None
    room_left, room_right = left, body_emu - (left + width_emu)
    if room_left >= channel and room_right >= channel:
        side = None                       # both sides usable: leave the source's wrap
    elif room_left >= channel:
        side = "left"
    elif room_right >= channel:
        side = "right"
    else:
        side = "bothSides"                # neither: nothing will flow beside it anyway
    changed = abs(left - float(horizontal.get("offsetEmu") or 0)) > 1
    return (int(left) if changed else None), side


def float_picture(picture: Any, place: dict[str, Any],
                  width_emu: int = 0,
                  params: dict[str, Any] | None = None) -> None:
    """Re-anchor an inline picture where the source floated it.

    python-docx can only insert an inline picture. A source figure that floats
    is placed — its offsets say where and its wrap says how the text behaves
    around it — and dropping it inline reflows the page it belonged to. The
    inline element is rebuilt as an anchor carrying the same graphic.
    """
    inline = picture._inline
    anchor = OxmlElement("wp:anchor")
    for name, value in (("distT", place.get("distT", "0")),
                        ("distB", place.get("distB", "0")),
                        ("distL", place.get("distL", "114300")),
                        ("distR", place.get("distR", "114300")),
                        ("simplePos", "0"),
                        ("relativeHeight", place.get("relativeHeight", "251658240")),
                        ("behindDoc", place.get("behindDoc", "0")),
                        ("locked", place.get("locked", "0")),
                        ("layoutInCell", place.get("layoutInCell", "1")),
                        ("allowOverlap", place.get("allowOverlap", "1"))):
        anchor.set(name, str(value))
    simple = OxmlElement("wp:simplePos")
    simple.set("x", "0")
    simple.set("y", "0")
    anchor.append(simple)
    offset, wrap_side = reconcile_float(place, width_emu, params)
    for axis, tag, fallback in (("horizontal", "wp:positionH", "column"),
                                ("vertical", "wp:positionV", "paragraph")):
        spec = place.get(axis) or {}
        node = OxmlElement(tag)
        node.set("relativeFrom", str(spec.get("relativeFrom") or fallback))
        if spec.get("align"):
            child = OxmlElement("wp:align")
            child.text = str(spec["align"])
        else:
            child = OxmlElement("wp:posOffset")
            child.text = str(offset if axis == "horizontal"
                             and offset is not None
                             else (spec.get("offsetEmu") or "0"))
        node.append(child)
        anchor.append(node)
    for tag in ("wp:extent", "wp:effectExtent"):
        found = inline.find(qn(tag))
        if found is not None:
            anchor.append(found)
    if wrap_side is not None:
        node = OxmlElement("wp:wrapSquare")
        node.set("wrapText", wrap_side)
        anchor.append(node)
    elif place.get("wrapXml"):
        anchor.append(parse_xml(str(place["wrapXml"])))
    else:
        anchor.append(OxmlElement(f'wp:{place.get("wrap") or "wrapNone"}'))
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr"):
        found = inline.find(qn(tag))
        if found is not None:
            anchor.append(found)
    graphic = inline.find(qn("a:graphic"))
    if graphic is not None:
        anchor.append(graphic)
    inline.getparent().replace(inline, anchor)


CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CHART_CT = ("application/vnd.openxmlformats-officedocument"
            ".drawingml.chart+xml")
XLSX_CT = ("application/vnd.openxmlformats-officedocument"
           ".spreadsheetml.sheet")
CHART_RT = ("http://schemas.openxmlformats.org/officeDocument/2006"
            "/relationships/chart")
PACKAGE_RT = ("http://schemas.openxmlformats.org/officeDocument/2006"
              "/relationships/package")


def chart_workbook(spec: dict[str, Any]) -> bytes:
    """The chart's numbers as a real workbook, not just a cache in the XML.

    The values were read off a 133 dpi bitmap because the source states none —
    they are an estimate and they are allowed to be, but an estimate that
    nobody can see or correct is indistinguishable from a measurement. Word
    opens this on 「编辑数据」.
    """
    from io import BytesIO
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.cell(row=1, column=1, value=spec.get("categoryAxisTitle") or "")
    for index, name in enumerate(s["name"] for s in spec["series"]):
        sheet.cell(row=1, column=index + 2, value=name)
    for row, category in enumerate(spec["categories"]):
        sheet.cell(row=row + 2, column=1, value=category)
        for index, series in enumerate(spec["series"]):
            sheet.cell(row=row + 2, column=index + 2,
                       value=series["values"][row])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def chart_xml(spec: dict[str, Any]) -> bytes:
    """A bar chart carrying only what the source figure carries.

    Word's defaults add a title, data labels and a second set of gridlines.
    None of those are on the page being reproduced, so each is written off
    explicitly rather than left to whatever the template feels like.
    """
    categories = spec["categories"]
    count = len(categories)

    def cat_ref(_: int) -> str:
        points = "".join(f'<c:pt idx="{i}"><c:v>{escape(c)}</c:v></c:pt>'
                         for i, c in enumerate(categories))
        return ('<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$%d</c:f><c:strCache>'
                '<c:ptCount val="%d"/>%s</c:strCache></c:strRef></c:cat>'
                % (count + 1, count, points))

    series_xml = []
    for index, series in enumerate(spec["series"]):
        column = chr(ord("B") + index)
        points = "".join(f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>'
                         for i, v in enumerate(series["values"]))
        series_xml.append(
            f'<c:ser><c:idx val="{index}"/><c:order val="{index}"/>'
            f'<c:tx><c:strRef><c:f>Sheet1!${column}$1</c:f><c:strCache>'
            f'<c:ptCount val="1"/><c:pt idx="0"><c:v>{escape(series["name"])}'
            '</c:v></c:pt></c:strCache></c:strRef></c:tx>'
            f'<c:spPr><a:solidFill><a:srgbClr val="{series["colour"]}"/>'
            '</a:solidFill><a:ln><a:noFill/></a:ln></c:spPr>'
            f'{cat_ref(index)}'
            f'<c:val><c:numRef><c:f>Sheet1!${column}$2:${column}${count + 1}'
            f'</c:f><c:numCache><c:formatCode>General</c:formatCode>'
            f'<c:ptCount val="{count}"/>{points}</c:numCache></c:numRef>'
            '</c:val></c:ser>')

    font = (f'<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr '
            f'sz="{int(spec.get("fontPt", 9) * 100)}" b="0" i="0">'
            f'<a:solidFill><a:srgbClr val="000000"/></a:solidFill>'
            f'<a:latin typeface="{spec.get("fontAscii", "Times New Roman")}"/>'
            f'<a:ea typeface="{spec.get("fontCn", "宋体")}"/></a:defRPr>'
            '</a:pPr><a:endParaRPr lang="zh-CN"/></a:p></c:txPr>')

    def axis_title(text: str, rotated: bool) -> str:
        if not text:
            return ""
        rot = ' rot="-5400000" vert="horz"' if rotated else ""
        return ('<c:title><c:tx><c:rich>'
                f'<a:bodyPr{rot}/><a:lstStyle/><a:p><a:pPr><a:defRPr '
                f'sz="{int(spec.get("fontPt", 9) * 100)}" b="0">'
                f'<a:latin typeface="{spec.get("fontAscii", "Times New Roman")}"/>'
                f'<a:ea typeface="{spec.get("fontCn", "宋体")}"/></a:defRPr></a:pPr>'
                f'<a:r><a:t>{escape(text)}</a:t></a:r></a:p></c:rich></c:tx>'
                '<c:overlay val="0"/></c:title>')

    grid = spec.get("gridlineColour", "D8D9DA")
    axis_line = ('<c:spPr><a:ln w="6350"><a:solidFill>'
                 f'<a:srgbClr val="{grid}"/></a:solidFill></a:ln></c:spPr>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{CHART_NS}" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<c:roundedCorners val="0"/><c:chart>'
        # No auto title: the page prints its own caption under the figure.
        '<c:autoTitleDeleted val="1"/>'
        '<c:plotArea><c:layout/>'
        '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        '<c:varyColors val="0"/>'
        + "".join(series_xml) +
        # Measured off the source figure: bars 8 px wide on a 12.3 px pitch
        # inside a group, groups 33 px apart. Word's defaults draw fat bars
        # that touch, which is a different picture of the same numbers.
        f'<c:gapWidth val="{spec.get("gapWidthPercent", 400)}"/>'
        f'<c:overlap val="{spec.get("overlapPercent", -50)}"/>'
        '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>'
        '<c:catAx><c:axId val="111111111"/><c:scaling>'
        '<c:orientation val="minMax"/></c:scaling><c:delete val="0"/>'
        '<c:axPos val="b"/>'
        + axis_title(spec.get("categoryAxisTitle", ""), False) +
        axis_line + '<c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
        '<c:tickLblPos val="nextTo"/>' + font +
        '<c:crossAx val="222222222"/></c:catAx>'
        '<c:valAx><c:axId val="222222222"/><c:scaling>'
        '<c:orientation val="minMax"/>'
        f'<c:max val="{spec["valueAxis"]["max"]}"/>'
        f'<c:min val="{spec["valueAxis"]["min"]}"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="l"/>'
        f'<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill>'
        f'<a:srgbClr val="{grid}"/></a:solidFill></a:ln></c:spPr>'
        '</c:majorGridlines>'
        + axis_title(spec.get("valueAxisTitle", ""), True) +
                # 「12 000」, not 「12,000」: the source separates thousands with a
        # space, as Chinese school textbooks do. The backslash escapes it.
        '<c:numFmt formatCode="#\\ ##0" sourceLinked="0"/>'
        + axis_line + '<c:majorTickMark val="out"/>'
        '<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>' + font +
        '<c:crossAx val="111111111"/>'
        f'<c:majorUnit val="{spec["valueAxis"]["step"]}"/></c:valAx>'
        '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>'
        '</c:plotArea>'
        '<c:legend><c:legendPos val="b"/><c:overlay val="0"/>' + font +
        '</c:legend><c:plotVisOnly val="1"/>'
        '<c:dispBlanksAs val="gap"/></c:chart>'
        '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>'
        # Without this the workbook relationship has nothing pointing at it and
        # Word drops the part on its first save — the numbers become a cache
        # inside the chart that 「编辑数据」 can no longer open, which is the one
        # property this whole substitution exists to provide.
        '<c:externalData r:id="__WORKBOOK_RID__">'
        '<c:autoUpdate val="0"/></c:externalData>'
        '</c:chartSpace>').encode("utf-8")


WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def sphere_fill(light: str, dark: str) -> str:
    """A ball, not a flat disc: a circular gradient lit from the upper left."""
    return (f'<a:gradFill><a:gsLst>'
            f'<a:gs pos="0"><a:srgbClr val="FFFFFF"/></a:gs>'
            f'<a:gs pos="35000"><a:srgbClr val="{light}"/></a:gs>'
            f'<a:gs pos="100000"><a:srgbClr val="{dark}"/></a:gs>'
            f'</a:gsLst><a:path path="circle">'
            f'<a:fillToRect l="30000" t="25000" r="70000" b="75000"/>'
            f'</a:path></a:gradFill>')


def vector_figure(spec: dict[str, Any], index: int) -> str:
    """A figure redrawn from its own measurements as Word shapes.

    The source prints this one as a 121 dpi bitmap, which is the resolution the
    labels are unreadable at. Drawn as shapes it is vector at any zoom, and
    every position here was measured off that bitmap rather than eyeballed —
    the geometry is the source's, only the rendering is ours.
    """
    palette = spec.get("palette") or {}
    scale = float(spec.get("emuPerUnit", 9525))
    parts = []
    for order, item in enumerate(spec["shapes"], start=1):
        x, y = int(item["x"] * scale), int(item["y"] * scale)
        cx, cy = int(item["w"] * scale), int(item.get("h", item["w"]) * scale)
        name = f"s{index}_{order}"
        if item["kind"] == "sphere":
            light, dark = palette[item["colour"]]
            body = (f'<wps:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
                    f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                    f'<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'
                    f'{sphere_fill(light, dark)}<a:ln><a:noFill/></a:ln>'
                    '</wps:spPr><wps:bodyPr/>')
        elif item["kind"] == "arrow":
            body = (f'<wps:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
                    f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                    '<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
                    f'<a:ln w="{int(item.get("weight", 1.2) * 12700)}" cap="flat">'
                    # The source draws this arrow blue and the 「+」 beside it in
                    # the same blue; forcing both to black was a rendering
                    # decision standing in for a source one.
                    f'<a:solidFill><a:srgbClr val="{item.get("colour", "000000")}"/>'
                    '</a:solidFill>'
                    '<a:tailEnd type="triangle" w="med" len="med"/></a:ln>'
                    '</wps:spPr><wps:bodyPr/>')
        else:                                        # label
            size = int(float(item.get("pt", 9)) * 100)
            body = (f'<wps:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
                    f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                    '<a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>'
                    '<wps:txbx><w:txbxContent><w:p><w:pPr>'
                    '<w:spacing w:before="0" w:after="0" w:line="240" '
                    'w:lineRule="auto"/><w:jc w:val="center"/></w:pPr><w:r><w:rPr>'
                    f'<w:rFonts w:ascii="{spec.get("fontAscii", "Times New Roman")}" '
                    f'w:eastAsia="{spec.get("fontCn", "宋体")}"/>'
                    f'<w:sz w:val="{int(float(item.get("pt", 9)) * 2)}"/>'
                    + (f'<w:color w:val="{item["colour"]}"/>'
                       if item.get("colour") else "")
                    + '</w:rPr>'
                    f'<w:t xml:space="preserve">{escape(item["text"])}</w:t>'
                    '</w:r></w:p></w:txbxContent></wps:txbx>'
                    '<wps:bodyPr rot="0" anchor="ctr" lIns="0" tIns="0" '
                    'rIns="0" bIns="0" anchorCtr="0"/>')
            del size
        parts.append(f'<wps:wsp><wps:cNvPr id="{index * 100 + order}" '
                     f'name="{name}"/><wps:cNvSpPr/>{body}</wps:wsp>')
    width = int(spec["widthUnits"] * scale)
    height = int(spec["heightUnits"] * scale)
    return (f'<wpg:wgp xmlns:wpg="{WPG_NS}" xmlns:wps="{WPS_NS}">'
            f'<wpg:cNvGrpSpPr/><wpg:grpSpPr><a:xfrm>'
            f'<a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/>'
            f'<a:chOff x="0" y="0"/><a:chExt cx="{width}" cy="{height}"/>'
            f'</a:xfrm></wpg:grpSpPr>{"".join(parts)}</wpg:wgp>')


def place_vector_figure(run: Any, spec: dict[str, Any], index: int) -> None:
    scale = float(spec.get("emuPerUnit", 9525))
    width = int(spec["widthUnits"] * scale)
    height = int(spec["heightUnits"] * scale)
    run._r.append(parse_xml(
        '<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'xmlns:a="{A_NS}">'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{8000 + index}" name="Figure {index}" '
        f'descr="{escape(str(spec.get("description") or ""))}"/>'
        '<a:graphic><a:graphicData '
        f'uri="{WPG_NS}">{vector_figure(spec, index)}</a:graphicData></a:graphic>'
        '</wp:inline></w:drawing>'))


def place_chart(run: Any, document: Document, spec: dict[str, Any],
                index: int) -> None:
    """A figure that is a chart, compiled as Word's own chart.

    The source prints it as a 133 dpi bitmap whose numbers appear nowhere in
    the text. Redrawing it as a picture would only make a sharper picture;
    as a chart the values live in an embedded workbook where they can be read
    and corrected, and the whole thing is vector at any zoom.
    """
    from docx.opc.part import Part
    from docx.opc.packuri import PackURI

    package = document.part.package
    chart_part = Part(PackURI(f"/word/charts/chart{index}.xml"), CHART_CT,
                      chart_xml(spec), package)
    book_part = Part(PackURI(f"/word/embeddings/chart{index}.xlsx"), XLSX_CT,
                     chart_workbook(spec), package)
    # The id is only known after the relationship exists, and the XML has to
    # carry it, so the placeholder is filled in here rather than guessed.
    book_id = chart_part.relate_to(book_part, PACKAGE_RT)
    chart_part._blob = chart_part.blob.replace(
        b"__WORKBOOK_RID__", book_id.encode("ascii"))
    chart_id = document.part.relate_to(chart_part, CHART_RT)
    width = int(round(float(spec["widthMm"]) * 36000))
    height = int(round(float(spec["heightMm"]) * 36000))
    run._r.append(parse_xml(
        '<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{9000 + index}" name="Chart {index}" '
        f'descr="{escape(str(spec.get("description") or ""))}"/>'
        '<a:graphic><a:graphicData '
        f'uri="{CHART_NS}"><c:chart xmlns:c="{CHART_NS}" '
        f'r:id="{chart_id}"/></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing>'))


def place_picture(run: Any, image_path: Path, spec: dict[str, Any],
                  default_width_mm: float,
                  params: dict[str, Any] | None = None) -> None:
    """Insert a picture honouring the source's crop and displayed extent.

    A source figure is often one wide strip cropped down to the single device a
    cell needs (a:srcRect). Dropping the crop draws the whole strip scaled into
    the cropped region's width, which is illegible — and the height must then
    come from the source extent too, because the natural aspect ratio no longer
    applies.
    """
    crop = spec.get("crop") or {}
    width_mm = float(spec.get("width_mm") or default_width_mm)
    height_mm = spec.get("height_mm")
    if crop and height_mm:
        picture = run.add_picture(str(image_path), width=Mm(width_mm),
                                  height=Mm(float(height_mm)))
    else:
        picture = run.add_picture(str(image_path), width=Mm(width_mm))
    if not crop:
        # Anchoring rebuilds the drawing element, so it has to come after any
        # work that reaches through the inline proxy.
        if spec.get("anchor"):
            float_picture(picture, spec["anchor"],
                          int(Mm(width_mm)), params)
        return
    blip_fill = picture._inline.graphic.graphicData.pic.blipFill
    src_rect = blip_fill.find(qn("a:srcRect"))
    if src_rect is None:
        src_rect = OxmlElement("a:srcRect")
        # a:srcRect must precede a:stretch inside a:blipFill.
        blip_fill.insert(len(blip_fill.findall(qn("a:blip"))), src_rect)
    for edge in ("l", "t", "r", "b"):
        value = crop.get(edge)
        if value:
            src_rect.set(edge, str(int(round(float(value)))))
    if spec.get("anchor"):
        float_picture(picture, spec["anchor"], int(Mm(width_mm)), params)


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def condition_em(text: str, params: dict[str, Any]) -> float:
    """How wide a condition label is, in ems of its own size.

    Counting characters instead would say 「101kPa，-183°C」 is 14 ems when it
    is 7.4, ask for an arrow 2.7× too long, and push the chain onto a second
    line. The same all-characters-are-one-em assumption had already been
    wrong once, on the option columns, where every option carries a capital
    letter; both now read one set of ratios out of the registry.
    """
    ratios = (((params.get("wordStyleRegistry") or {})
               .get("derivation") or {}).get("base") or {}).get("charWidthEm") or {}
    full = float(ratios.get("fullWidth", 1.0))
    half = float(ratios.get("halfWidth", 0.5))
    upper = float(ratios.get("upperCaseLatin", 0.7))
    total = 0.0
    for letter in text:
        if unicodedata.east_asian_width(letter) in ("W", "F"):
            total += full
        elif letter.isupper():
            total += upper
        else:
            total += half
    return total


def reaction_arrow(segment: dict[str, Any], params: dict[str, Any]) -> Any:
    """A reaction arrow as Word's own equation, condition above the line.

    The source draws this two ways and neither is text: a floating line shape
    carrying the condition as its own text, or the whole thing as one small
    bitmap. Written out as 「——点燃→」 it is at least text, but the condition
    lands beside the arrow instead of over it, which is not how the equation
    reads. Word has a native form for exactly this — an upper limit over an
    arrow — and it stays editable, scales with the type, and exports to PDF as
    text rather than as a picture.
    """
    spec = (params.get("wordStyleRegistry") or {}).get("reactionArrow") or {}
    size = int(round(float(((params.get("wordStyleRegistry") or {})
                            .get("paragraphStyles") or {})
                           .get("CZ_Body", {}).get("sizePt", 12)) * 2))
    drop = int(spec.get("conditionSizeDropHalfPt", 6))
    ascii_font = str(spec.get("fontAscii") or "Times New Roman")
    cjk_font = str(spec.get("fontCn") or "宋体")

    def run(text: str, half: int, scale: int = 0) -> str:
        # OMML defaults to a maths italic, which would set 「点燃」 slanted —
        # the spec does not use italic anywhere. m:nor turns it upright.
        upright = "<m:rPr><m:nor/></m:rPr>" if spec.get("upright", True) else ""
        stretch = f'<w:w w:val="{scale}"/>' if scale and scale != 100 else ""
        return (f"<m:r>{upright}<w:rPr>"
                f'<w:rFonts w:ascii="{ascii_font}" w:hAnsi="{ascii_font}"'
                f' w:eastAsia="{cjk_font}"/><w:sz w:val="{half}"/>{stretch}'
                f"</w:rPr><m:t>{escape(text)}</m:t></m:r>")

    # The arrow has to be at least as wide as the condition sitting on it, or
    # 「点燃」 overhangs a stub and reads as if the condition were crossing the
    # arrow out. It is stretched, not assembled: a shaft built from repeats
    # does not join the head in Word — it prints as a stray dash, 「–⟶」 — and
    # a repeated arrow gives two arrowheads. Measured in Word at 12pt: the
    # glyph is 14.1pt and a 9pt CJK condition character is 9pt wide.
    condition_pt = (size - drop) / 2
    widest = max((condition_em(str(segment.get(key) or "").strip(), params)
                  for key in ("over", "under")), default=0.0)
    natural = size / 2 * float(spec.get("widthEm", 1.175))
    # Merely matching the condition's width is not visibly longer — the first
    # attempt asked for exactly that, got 128%, and drew an arrow the same
    # width as 「点燃」, which reads as no change at all. The margin is the
    # rule, not a nicety; and a conditionless arrow is stretched too, or a
    # 14pt stub does not read as a reaction arrow.
    wanted = widest * condition_pt * float(spec.get("conditionMarginPercent", 135)) / 100
    scale = max(int(spec.get("minScalePercent", 170)),
                int(-(-wanted * 100 // natural)) if widest else 0)
    # The ceiling is a width, not a percentage: 「一枚箭头不超过版心四分之一」
    # survives a change of body size, where a bare 340% silently stops meaning
    # the same thing. It is expressed back as a percentage only because w:w is.
    ceiling = float(spec.get("maxWidthDxa") or 0)
    if ceiling:
        scale = min(scale, int(ceiling / (natural * 20) * 100))
    # And a second ceiling that is not ours: w:w is defined only up to 600%,
    # and Word does not clamp an out-of-range value — it discards the whole
    # attribute and draws the glyph at its natural width, which is the one
    # outcome that looks like the code never ran.
    scale = min(scale, int(spec.get("wordMaxScalePercent", 600)))
    glyph = str(spec.get("glyph") or "⟶")
    core = run(glyph, size, scale)
    # The condition belongs over the shaft, not over the whole glyph: the head
    # sticks out to the right and should not pull the label with it. OMML
    # centres a limit on its base, so an invisible spacer rides after the
    # condition and pushes the limit's centre back to the left by half its
    # width. Calibrated against Word's own export — 27% of the arrow puts the
    # condition on the shaft's midpoint, where 0% leaves it 2.4pt to the right.
    pad = int(spec.get("headPhantomPercent", 0))
    spacer = ""
    if pad:
        spacer = ('<m:phant><m:phantPr><m:show m:val="0"/>'
                  '<m:zeroAsc m:val="1"/><m:zeroDesc m:val="1"/></m:phantPr>'
                  f'<m:e>{run(glyph, size, max(1, round(scale * pad / 100)))}</m:e>'
                  "</m:phant>")
    for key, wrapper in (("over", "limUpp"), ("under", "limLow")):
        label = str(segment.get(key) or "").strip()
        if label:
            core = (f"<m:{wrapper}><m:e>{core}</m:e>"
                    f"<m:lim>{run(label, max(2, size - drop))}{spacer}</m:lim>"
                    f"</m:{wrapper}>")
    word = qn("w:t").split("}")[0][1:]
    return parse_xml(f'<m:oMath xmlns:m="{MATH_NS}" xmlns:w="{word}">{core}</m:oMath>')


def add_segments(paragraph: Any, block: dict[str, Any],
                 params: dict[str, Any] | None = None) -> None:
    segments = block.get("segments")
    if segments is None:
        segments = [{"text": str(block.get("text") or ""), "run_type": "plain"}]
    if not isinstance(segments, list) or not segments:
        raise BlueprintError(f"Block {block.get('id')} has no text segments")
    for segment in segments:
        if not isinstance(segment, dict):
            raise BlueprintError(f"Invalid segment in block {block.get('id')}")
        if segment.get("kind") == "inline_image":
            image_path = Path(str(segment.get("path") or ""))
            if not image_path.is_file():
                raise BlueprintError(
                    f"Inline image segment in block {block.get('id')} is missing: "
                    f"{image_path}"
                )
            place_picture(paragraph.add_run(), image_path, segment, 8.0, params)
            continue
        if segment.get("kind") == "reaction_arrow":
            paragraph._p.append(reaction_arrow(segment, params or {}))
            continue
        run = paragraph.add_run(str(segment.get("text") or ""))
        run_type = str(segment.get("run_type") or "plain")
        if run_type not in RUN_STYLE_IDS:
            raise BlueprintError(f"Unknown run_type {run_type!r} in block {block.get('id')}")
        set_run_style(run, RUN_STYLE_IDS[run_type])


def toc_field_code(params: dict[str, Any]) -> str:
    """The TOC's style list, built from the registry rather than typed here.

    Three places used to state the depth and no two agreed: every style
    carried a tocLevel down to 4, this field named three styles, and
    tocGranularity.studentDefaultLevels said two. Reading the styles is the
    only version that cannot drift from the styles.
    """
    styles = ((params or {}).get("wordStyleRegistry") or {}).get("paragraphStyles") or {}
    listed = sorted(
        ((int(spec["tocLevel"]), str(spec.get("name") or key))
         for key, spec in styles.items()
         if isinstance(spec, dict) and spec.get("tocLevel")),
        key=lambda pair: pair[0])
    pairs = ",".join(f"{name},{level}" for level, name in listed)
    return f' TOC \\h \\z \\t "{pairs}" '


def add_toc(document: Document, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    title = document.add_paragraph()
    set_paragraph_style(title, "CZ_TocTitle")
    title.add_run("目录")
    field_paragraph = document.add_paragraph()
    set_paragraph_style(field_paragraph, "CZ_Body")
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = toc_field_code(params or {})
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, end):
        run = OxmlElement("w:r")
        run.append(element)
        field_paragraph._p.append(run)
    return [
        {
            "semanticType": "toc-title",
            "styleId": "CZ_TocTitle",
            "text": "目录",
            "sourceStatus": LAYOUT_SOURCE_STATUS,
            "reviewStatus": "approved",
        },
        {
            "semanticType": "toc-field",
            "styleId": "CZ_Body",
            "text": "TOC field",
            "sourceStatus": LAYOUT_SOURCE_STATUS,
            "reviewStatus": "approved",
        },
    ]


def source_record(
    block: dict[str, Any],
    source_hash_cache: dict[str, tuple[tuple[int, int], str]] | None = None,
) -> dict[str, Any]:
    source = block.get("source")
    if not isinstance(source, dict):
        raise BlueprintError(f"Block {block.get('id')} is missing source provenance")
    status = str(source.get("status") or "")
    if status == LAYOUT_SOURCE_STATUS:
        if source.get("path") not in {None, ""}:
            raise BlueprintError(
                f"Layout block {block.get('id')} must not declare a content source path"
            )
        return source
    if status not in CONTENT_SOURCE_STATUSES:
        raise BlueprintError(
            f"Block {block.get('id')} violates {SOURCE_POLICY_ID}: "
            f"content source status {status!r} is not student_word or original_word"
        )
    if source.get("frozen") is not True:
        raise BlueprintError(f"Block {block.get('id')} source must be frozen")
    source_path = Path(str(source.get("path") or ""))
    if not source_path.is_absolute():
        raise BlueprintError(f"Block {block.get('id')} source Word path must be absolute")
    if source_path.suffix.lower() not in {".doc", ".docx"}:
        raise BlueprintError(
            f"Block {block.get('id')} content source must be a .doc or .docx Word file"
        )
    if not source_path.is_file():
        raise BlueprintError(f"Block {block.get('id')} source Word does not exist: {source_path}")
    declared_hash = str(source.get("sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(declared_hash):
        raise BlueprintError(f"Block {block.get('id')} source must declare a SHA-256")
    before = source_path.stat()
    source_state = (before.st_size, before.st_mtime_ns)
    cache_key = str(source_path)
    cached = (source_hash_cache or {}).get(cache_key)
    if cached is not None and cached[0] == source_state:
        actual_hash = cached[1]
    else:
        actual_hash = sha256_file(source_path)
        after = source_path.stat()
        after_state = (after.st_size, after.st_mtime_ns)
        if after_state != source_state:
            raise BlueprintError(
                f"HOLD_INPUT_DRIFT: Block {block.get('id')} source Word changed while hashing"
            )
        if source_hash_cache is not None:
            source_hash_cache[cache_key] = (source_state, actual_hash)
    if actual_hash != declared_hash:
        raise BlueprintError(
            f"HOLD_INPUT_DRIFT: Block {block.get('id')} source Word SHA-256 mismatch"
        )
    locator = source.get("locator")
    if not isinstance(locator, dict):
        raise BlueprintError(f"Block {block.get('id')} source must declare a Word locator")
    locator_kind = str(locator.get("kind") or "")
    locator_value = locator.get("value")
    if locator_kind not in SOURCE_LOCATOR_KINDS or locator_value in {None, ""}:
        raise BlueprintError(
            f"Block {block.get('id')} source Word locator must contain a supported kind and value"
        )
    return source


def visible_text(block: dict[str, Any]) -> str:
    if isinstance(block.get("segments"), list):
        return "".join(
            str(item.get("text") or "")
            for item in block["segments"]
            if isinstance(item, dict)
        )
    if block.get("type") == "table":
        return " ".join(
            (
                "".join(
                    str(item.get("text") or "")
                    for item in cell.get("segments") or []
                    if isinstance(item, dict)
                )
                if isinstance(cell, dict)
                else str(cell)
            )
            for row in (block.get("rows") or [])
            for cell in (row if isinstance(row, list) else [])
        )
    return str(block.get("text") or "")


def inline_image_segments(block: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for index, segment in enumerate(block.get("segments") or [], start=1):
        if isinstance(segment, dict) and segment.get("kind") == "inline_image":
            result.append((f"segment-{index}", segment))
    if block.get("type") == "table":
        for row_index, row in enumerate(block.get("rows") or [], start=1):
            if not isinstance(row, list):
                continue
            for column_index, cell in enumerate(row, start=1):
                if not isinstance(cell, dict):
                    continue
                for segment_index, segment in enumerate(
                    cell.get("segments") or [],
                    start=1,
                ):
                    if (
                        isinstance(segment, dict)
                        and segment.get("kind") == "inline_image"
                    ):
                        result.append(
                            (
                                f"cell-{row_index}-{column_index}-segment-{segment_index}",
                                segment,
                            )
                        )
    return result


def source_locator_key(source: dict[str, Any]) -> tuple[str, str]:
    locator = source.get("locator") or {}
    return str(source.get("path") or ""), str(locator.get("value") or "")


def validate_source_object_rules(
    blueprint: dict[str, Any],
    source_hash_cache: dict[str, tuple[tuple[int, int], str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    title_paragraphs = blueprint.get("sourceTitleParagraphs")
    if not isinstance(title_paragraphs, list) or not title_paragraphs:
        raise BlueprintError(
            "Blueprint sourceTitleParagraphs must register every source title paragraph"
        )
    title_keys: set[tuple[str, str]] = set()
    for index, record in enumerate(title_paragraphs, start=1):
        if not isinstance(record, dict):
            raise BlueprintError("Every sourceTitleParagraphs record must be an object")
        record_id = str(record.get("id") or f"source-title-{index}")
        source = source_record(
            {"id": record_id, "source": record.get("source")},
            source_hash_cache,
        )
        locator = source.get("locator") or {}
        if locator.get("kind") != "paragraph":
            raise BlueprintError(
                f"Source title record {record_id} must use a paragraph locator"
            )
        key = source_locator_key(source)
        if key in title_keys:
            raise BlueprintError(f"Duplicate source title paragraph registration: {key}")
        title_keys.add(key)
        if record.get("handling") != SOURCE_TITLE_HANDLING:
            raise BlueprintError(
                f"Source title record {record_id} handling must be "
                f"{SOURCE_TITLE_HANDLING!r}"
            )
        if not str(record.get("canonicalBlockId") or ""):
            raise BlueprintError(
                f"Source title record {record_id} must declare canonicalBlockId"
            )

    exclusions = blueprint.get("sourceObjectExclusions")
    if not isinstance(exclusions, list):
        raise BlueprintError("Blueprint sourceObjectExclusions must be a list")
    exclusion_keys: set[tuple[str, str]] = set()
    for index, record in enumerate(exclusions, start=1):
        if not isinstance(record, dict):
            raise BlueprintError("Every sourceObjectExclusions record must be an object")
        record_id = str(record.get("id") or f"source-exclusion-{index}")
        classification = str(record.get("classification") or "")
        if classification not in SUPPORTED_SOURCE_EXCLUSION_CLASSIFICATIONS:
            raise BlueprintError(
                f"Source exclusion {record_id} has an unsupported classification"
            )
        if record.get("review_status") != "approved":
            raise BlueprintError(
                f"Source exclusion {record_id} must declare review_status='approved'"
            )
        source = source_record(
            {"id": record_id, "source": record.get("source")},
            source_hash_cache,
        )
        locator = source.get("locator") or {}
        key = source_locator_key(source)
        if key in exclusion_keys:
            raise BlueprintError(f"Duplicate source object exclusion: {key}")
        exclusion_keys.add(key)
        if classification == TITLE_DECORATION_CLASSIFICATION:
            if locator.get("kind") != "image":
                raise BlueprintError(
                    f"Source exclusion {record_id} must use an image locator"
                )
            if not any(
                key[0] == title_path
                and key[1].startswith(
                    (
                        f"{title_locator}/drawing[",
                        f"{title_locator}/vml-image[",
                        f"{title_locator}/alternateContent[",
                    )
                )
                for title_path, title_locator in title_keys
            ):
                raise BlueprintError(
                    f"Source exclusion {record_id} is not inside a registered title paragraph"
                )
            if not str(record.get("titleVisualTextEvidenceId") or ""):
                raise BlueprintError(
                    f"Source exclusion {record_id} must reference "
                    "titleVisualTextEvidenceId"
                )
    return title_paragraphs, exclusions


def validate_blueprint(
    blueprint: dict[str, Any],
    source_hash_cache: dict[str, tuple[tuple[int, int], str]] | None = None,
) -> list[dict[str, Any]]:
    if blueprint.get("schemaVersion") != "chengziclass.semantic-handout-blueprint.v1":
        raise BlueprintError("Unsupported or missing blueprint schemaVersion")
    if blueprint.get("edition") != "student":
        raise BlueprintError("This compiler invocation is student-edition only")
    if blueprint.get("sourcePolicy") != SOURCE_POLICY_ID:
        raise BlueprintError(
            f"Blueprint sourcePolicy must be exactly {SOURCE_POLICY_ID!r}"
        )
    if blueprint.get("contentFidelityPolicy") != CONTENT_FIDELITY_POLICY_ID:
        raise BlueprintError(
            "Blueprint contentFidelityPolicy must be exactly "
            f"{CONTENT_FIDELITY_POLICY_ID!r}"
        )
    source_documents = blueprint.get("sourceDocuments")
    if not isinstance(source_documents, list) or not source_documents:
        raise BlueprintError(
            "Blueprint sourceDocuments must freeze the complete selected Word source set"
        )
    source_document_index: dict[str, str] = {}
    for index, record in enumerate(source_documents, start=1):
        if not isinstance(record, dict):
            raise BlueprintError("Every sourceDocuments record must be an object")
        path = Path(str(record.get("path") or ""))
        digest = str(record.get("sha256") or "").lower()
        if (
            not path.is_absolute()
            or path.suffix.lower() not in {".doc", ".docx"}
            or not SHA256_PATTERN.fullmatch(digest)
        ):
            raise BlueprintError(
                f"sourceDocuments[{index}] must declare an absolute Word path and SHA-256"
            )
        if not path.is_file():
            raise BlueprintError(f"sourceDocuments source does not exist: {path}")
        path_key = str(path)
        if path_key in source_document_index:
            raise BlueprintError(f"Duplicate sourceDocuments path: {path}")
        before = path.stat()
        source_state = (before.st_size, before.st_mtime_ns)
        cached = (source_hash_cache or {}).get(path_key)
        if cached is not None and cached[0] == source_state:
            actual = cached[1]
        else:
            actual = sha256_file(path)
            after = path.stat()
            after_state = (after.st_size, after.st_mtime_ns)
            if after_state != source_state:
                raise BlueprintError(
                    f"HOLD_INPUT_DRIFT: sourceDocuments Word changed while hashing: {path}"
                )
            if source_hash_cache is not None:
                source_hash_cache[path_key] = (source_state, actual)
        if actual != digest:
            raise BlueprintError(
                f"HOLD_INPUT_DRIFT: sourceDocuments SHA-256 mismatch: {path}"
            )
        source_document_index[path_key] = digest
    title_paragraphs, exclusions = validate_source_object_rules(
        blueprint,
        source_hash_cache,
    )
    review_queue = blueprint.get("sourceObjectReviewQueue") or []
    if not isinstance(review_queue, list):
        raise BlueprintError("sourceObjectReviewQueue must be a list when present")
    validated_review_queue: list[dict[str, Any]] = []
    for index, record in enumerate(review_queue, start=1):
        if not isinstance(record, dict):
            raise BlueprintError("Every sourceObjectReviewQueue record must be an object")
        record_id = str(record.get("id") or f"source-review-{index}")
        if record.get("review_status") != "approved":
            raise BlueprintError(
                f"Source review record {record_id} must declare review_status='approved'"
            )
        if record.get("disposition") != "opaque-preserve":
            raise BlueprintError(
                f"Source review record {record_id} has no approved disposition"
            )
        source = source_record(
            {"id": record_id, "source": record.get("source")},
            source_hash_cache,
        )
        validated_review_queue.append({**record, "source": source})
    for record in [*title_paragraphs, *exclusions, *validated_review_queue]:
        source = record.get("source") or {}
        registered_hash = source_document_index.get(str(source.get("path") or ""))
        if registered_hash != str(source.get("sha256") or "").lower():
            raise BlueprintError(
                f"Source object {record.get('id')} is not bound to sourceDocuments"
            )
    title_keys = {
        source_locator_key(record["source"])
        for record in title_paragraphs
    }
    exclusion_keys = {
        source_locator_key(record["source"])
        for record in exclusions
    }
    blocks = blueprint.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise BlueprintError("Blueprint blocks must be a non-empty list")
    seen: set[str] = set()
    visible_locator_owners: dict[tuple[str, str, str], str] = {}
    for block in blocks:
        if not isinstance(block, dict):
            raise BlueprintError("Every block must be an object")
        block_id = str(block.get("id") or "")
        if not block_id or block_id in seen:
            raise BlueprintError(f"Missing or duplicate block id: {block_id!r}")
        seen.add(block_id)
        block_type = str(block.get("type") or "")
        if block_type not in set(BLOCK_STYLE_IDS) | {"table", "page_break", "chart", "vector_figure"}:
            raise BlueprintError(f"Unknown block type {block_type!r} in {block_id}")
        page_break_before = block.get("page_break_before", False)
        if not isinstance(page_break_before, bool):
            raise BlueprintError(
                f"Block {block_id} page_break_before must be a boolean"
            )
        if page_break_before and block_type in {"table", "page_break"}:
            raise BlueprintError(
                f"Block {block_id} cannot apply page_break_before to {block_type}"
            )
        source = source_record(block, source_hash_cache)
        if source.get("status") in CONTENT_SOURCE_STATUSES:
            registered_hash = source_document_index.get(str(source.get("path") or ""))
            if registered_hash != str(source.get("sha256") or "").lower():
                raise BlueprintError(
                    f"Block {block_id} source is not bound to sourceDocuments"
                )
        source_key = source_locator_key(source)
        if source_key in exclusion_keys:
            raise BlueprintError(
                f"Block {block_id} includes an explicitly excluded source object"
            )
        if source.get("status") == LAYOUT_SOURCE_STATUS and block_type not in {
            "answer_line",
            "layout_spacer",
            "page_break",
        }:
            raise BlueprintError(
                f"Block {block_id} uses layout provenance for visible instructional content"
            )
        if source.get("status") in CONTENT_SOURCE_STATUSES and (
            block.get("review_status") != "approved"
        ):
            raise BlueprintError(
                f"Block {block_id} must explicitly declare review_status='approved'"
            )
        text = visible_text(block)
        marker = next((item for item in FORBIDDEN_STUDENT_MARKERS if item in text), None)
        if marker:
            raise BlueprintError(f"Forbidden student marker {marker!r} in block {block_id}")
        if block.get("contains_answer") or block.get("contains_teacher_hint"):
            raise BlueprintError(f"Student block {block_id} declares forbidden answer/teacher content")

        if source.get("status") in CONTENT_SOURCE_STATUSES:
            # A locator may produce several visible blocks when each takes a
            # declared part of it — one 「A．… B．…」 paragraph is four options,
            # and CZ_Num_ChoiceAlpha requires each to be its own block. Two
            # blocks claiming the same part, or the whole, is still a clash.
            # The coverage gate applies the same rule to source objects.
            part = str(source.get("objectPart") or "")
            owner_key = (*source_key, part)
            previous_owner = visible_locator_owners.get(owner_key)
            if previous_owner is not None:
                raise BlueprintError(
                    "One Word source locator may produce only one visible block: "
                    f"{source_key} is owned by {previous_owner!r} and {block_id!r}"
                )
            visible_locator_owners[owner_key] = block_id
        for segment_label, segment in inline_image_segments(block):
            image_path = Path(str(segment.get("path") or ""))
            if not image_path.is_file():
                raise BlueprintError(
                    f"Inline image segment {block_id}[{segment_label}] is missing: "
                    f"{image_path}"
                )
            segment_source = source_record(
                {
                    "id": f"{block_id}-{segment_label}",
                    "source": segment.get("source"),
                },
                source_hash_cache,
            )
            registered_hash = source_document_index.get(
                str(segment_source.get("path") or "")
            )
            if registered_hash != str(segment_source.get("sha256") or "").lower():
                raise BlueprintError(
                    f"Inline image segment {block_id}[{segment_label}] is not bound "
                    "to sourceDocuments"
                )
            if (segment_source.get("locator") or {}).get("kind") != "image":
                raise BlueprintError(
                    f"Inline image segment {block_id}[{segment_label}] must use "
                    "an image source locator"
                )
            segment_key = source_locator_key(segment_source)
            previous_owner = visible_locator_owners.get(segment_key)
            if previous_owner is not None:
                raise BlueprintError(
                    "One Word source locator may produce only one visible object: "
                    f"{segment_key} is owned by {previous_owner!r} and "
                    f"{block_id}[{segment_label}]"
                )
            visible_locator_owners[segment_key] = (
                f"{block_id}[inline-image-{segment_label}]"
            )

    block_by_id = {str(block["id"]): block for block in blocks}
    canonical_by_source: dict[str, str] = {}
    canonical_by_title_key: dict[tuple[str, str], str] = {}
    for index, record in enumerate(title_paragraphs, start=1):
        record_id = str(record.get("id") or f"source-title-{index}")
        canonical_id = str(record["canonicalBlockId"])
        canonical = block_by_id.get(canonical_id)
        if canonical is None:
            raise BlueprintError(
                f"Source title record {record_id} canonicalBlockId "
                f"{canonical_id!r} does not exist"
            )
        if canonical.get("type") not in CANONICAL_TITLE_BLOCK_TYPES:
            raise BlueprintError(
                f"Source title record {record_id} canonical block {canonical_id!r} "
                "must be chapter or heading1"
            )
        title_source = record["source"]
        canonical_source = canonical.get("source") or {}
        title_path = str(title_source.get("path") or "")
        if str(canonical_source.get("path") or "") != title_path:
            raise BlueprintError(
                f"Source title record {record_id} and canonical block "
                f"{canonical_id!r} must bind the same source Word"
            )
        prior = canonical_by_source.setdefault(title_path, canonical_id)
        if prior != canonical_id:
            raise BlueprintError(
                f"Source Word {title_path} declares more than one canonical "
                f"visible title block: {prior!r}, {canonical_id!r}"
            )
        title_key = source_locator_key(title_source)
        canonical_by_title_key[title_key] = canonical_id
        visible_owner = visible_locator_owners.get(title_key)
        if visible_owner is not None and visible_owner != canonical_id:
            raise BlueprintError(
                f"Source title paragraph {title_key} was compiled again as "
                f"visible block {visible_owner!r}; only {canonical_id!r} is allowed"
            )

    try:
        title_visual_evidence = validate_evidence_records(
            blueprint.get("sourceTitleVisualTextEvidence"),
            canonical_block_text={
                block_id: visible_text(block)
                for block_id, block in block_by_id.items()
                if block.get("type") in CANONICAL_TITLE_BLOCK_TYPES
            },
        )
    except TitleVisualTextEvidenceError as exc:
        raise BlueprintError(str(exc)) from exc
    evidence_by_source_key: dict[tuple[str, str], str] = {}
    for evidence_id, evidence in title_visual_evidence.items():
        evidence_source = source_record(
            {"id": evidence_id, "source": evidence.get("source")},
            source_hash_cache,
        )
        evidence_key = source_locator_key(evidence_source)
        if evidence_key in evidence_by_source_key:
            raise BlueprintError(
                f"More than one title visual evidence record binds {evidence_key}"
            )
        evidence_by_source_key[evidence_key] = evidence_id
        registered_hash = source_document_index.get(
            str(evidence_source.get("path") or "")
        )
        if registered_hash != str(evidence_source.get("sha256") or "").lower():
            raise BlueprintError(
                f"Title visual evidence {evidence_id} is not bound to sourceDocuments"
            )
        title_matches = [
            canonical_id
            for (title_path, title_locator), canonical_id
            in canonical_by_title_key.items()
            if evidence_key[0] == title_path
            and evidence_key[1].startswith(
                (
                    f"{title_locator}/drawing[",
                    f"{title_locator}/vml-image[",
                    f"{title_locator}/alternateContent[",
                )
            )
        ]
        if len(title_matches) != 1:
            raise BlueprintError(
                f"Title visual evidence {evidence_id} does not resolve to exactly "
                "one registered title paragraph"
            )
        if evidence.get("canonicalBlockId") != title_matches[0]:
            raise BlueprintError(
                f"Title visual evidence {evidence_id} canonicalBlockId does not "
                "match its registered source title"
            )

    excluded_title_visual_keys: set[tuple[str, str]] = set()
    for index, exclusion in enumerate(exclusions, start=1):
        if exclusion.get("classification") != TITLE_DECORATION_CLASSIFICATION:
            continue
        exclusion_id = str(exclusion.get("id") or f"source-exclusion-{index}")
        evidence_id = str(exclusion.get("titleVisualTextEvidenceId") or "")
        evidence = title_visual_evidence.get(evidence_id)
        if evidence is None:
            raise BlueprintError(
                f"Source exclusion {exclusion_id} references missing title visual evidence"
            )
        evidence_key = source_locator_key(evidence.get("source") or {})
        if evidence_key != source_locator_key(
            exclusion.get("source") or {}
        ):
            raise BlueprintError(
                f"Source exclusion {exclusion_id} and title visual evidence "
                f"{evidence_id} must bind the same image"
            )
        if evidence.get("decision") == "mixed_content":
            raise BlueprintError(
                f"HOLD_EXCLUDED_CONTENT_OBJECT: mixed title visual "
                f"{evidence_id} cannot be excluded"
            )
        if evidence_key in excluded_title_visual_keys:
            raise BlueprintError(
                f"Title visual evidence {evidence_id} is referenced more than once"
            )
        excluded_title_visual_keys.add(evidence_key)

    for evidence_key, evidence_id in evidence_by_source_key.items():
        evidence = title_visual_evidence[evidence_id]
        if evidence.get("decision") == "mixed_content":
            if evidence_key in excluded_title_visual_keys:
                raise BlueprintError(
                    f"HOLD_EXCLUDED_CONTENT_OBJECT: {evidence_id}"
                )
            if evidence_key not in visible_locator_owners:
                raise BlueprintError(
                    f"HOLD_TITLE_VISUAL_UNMATERIALIZED: mixed title visual "
                    f"{evidence_id} must be preserved as visible content"
                )
        elif evidence_key not in excluded_title_visual_keys:
            raise BlueprintError(
                f"HOLD_TITLE_VISUAL_UNINSPECTED: title visual {evidence_id} "
                "has no approved exclusion disposition"
            )

    for source_key, owner in visible_locator_owners.items():
        if any(
            source_key[0] == title_path
            and source_key[1].startswith(
                (
                    f"{title_locator}/drawing[",
                    f"{title_locator}/vml-image[",
                    f"{title_locator}/alternateContent[",
                )
            )
            for title_path, title_locator in title_keys
        ) and source_key not in evidence_by_source_key:
            raise BlueprintError(
                f"HOLD_TITLE_VISUAL_UNINSPECTED: source-title image {source_key} "
                f"compiled by {owner!r} has no visual evidence"
            )

    substitutions = blueprint.get("sourceObjectSubstitutions") or []
    if not isinstance(substitutions, list):
        raise BlueprintError("sourceObjectSubstitutions must be a list when present")
    for index, record in enumerate(substitutions, start=1):
        if not isinstance(record, dict):
            raise BlueprintError("Every sourceObjectSubstitutions record must be an object")
        record_id = str(record.get("id") or f"source-substitution-{index}")
        if record.get("review_status") != "approved":
            raise BlueprintError(
                f"Source substitution {record_id} must declare review_status='approved'"
            )
        target_id = str(record.get("targetBlockId") or "")
        target = block_by_id.get(target_id)
        if target is None:
            raise BlueprintError(
                f"Source substitution {record_id} targetBlockId does not exist"
            )
        replacement = str(record.get("replacementText") or "")
        if not replacement or replacement not in visible_text(target):
            raise BlueprintError(
                f"Source substitution {record_id} replacementText is not materialized "
                f"in target block {target_id}"
            )
        substitution_source = source_record(
            {"id": record_id, "source": record.get("source")},
            source_hash_cache,
        )
        registered_hash = source_document_index.get(
            str(substitution_source.get("path") or "")
        )
        if registered_hash != str(substitution_source.get("sha256") or "").lower():
            raise BlueprintError(
                f"Source substitution {record_id} is not bound to sourceDocuments"
            )
        if (substitution_source.get("locator") or {}).get("kind") not in {
            "shape",
            "paragraph",
        }:
            raise BlueprintError(
                f"Source substitution {record_id} must bind a shape or textbox paragraph"
            )
        substitution_key = source_locator_key(substitution_source)
        previous_owner = visible_locator_owners.get(substitution_key)
        if previous_owner is not None:
            raise BlueprintError(
                "One Word source locator may produce only one visible object: "
                f"{substitution_key} is owned by {previous_owner!r} and {record_id!r}"
            )
        visible_locator_owners[substitution_key] = record_id
    return blocks


TABLE_STYLE_ID = "CZ_Table_Standard"


def ensure_table_style(document: Document, params: dict[str, Any]) -> str:
    """Materialise the registry's table style in full.

    A private style that states only some properties is not a private style:
    whatever it leaves out comes from Word's built-in — 「Table Grid」 was
    supplying the borders, the cell margins and the header treatment, so the
    registry's own numbers never reached the page. Everything the registry
    declares for a table is written here.
    """
    registry = params.get("wordStyleRegistry") or {}
    spec = (registry.get("objects") or {}).get(TABLE_STYLE_ID) or {}
    shape = (registry.get("paragraphStyles") or {}).get("tables") or {}
    existing = next((style for style in document.styles
                     if style.style_id == TABLE_STYLE_ID), None)
    if existing is not None:
        return TABLE_STYLE_ID

    element = OxmlElement("w:style")
    element.set(qn("w:type"), "table")
    element.set(qn("w:styleId"), TABLE_STYLE_ID)
    element.set(qn("w:customStyle"), "1")
    name = OxmlElement("w:name")
    name.set(qn("w:val"), "橙子表格")
    element.append(name)

    properties = OxmlElement("w:tblPr")
    borders = OxmlElement("w:tblBorders")
    visible = shape.get("visibleBorders") or {}
    size = int(round(float(spec.get("borderPt", 0.5)) * 8))
    colour = str(spec.get("borderColor") or "auto").lstrip("#")
    for side in visible.get("sides") or ("top", "left", "bottom", "right",
                                        "insideH", "insideV"):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:val"), str(visible.get("val") or "single"))
        edge.set(qn("w:sz"), str(size))
        edge.set(qn("w:space"), str(visible.get("space") or 0))
        edge.set(qn("w:color"), colour)
        borders.append(edge)
    properties.append(borders)

    margins = OxmlElement("w:tblCellMar")
    for side, key in (("top", "cellMarginTopPt"), ("left", "cellMarginLeftPt"),
                      ("bottom", "cellMarginBottomPt"),
                      ("right", "cellMarginRightPt")):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(int(round(float(spec.get(key, 2)) * 20))))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    properties.append(margins)

    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), str(shape.get("layoutType") or "fixed"))
    properties.append(layout)
    element.append(properties)
    document.styles.element.append(element)
    return TABLE_STYLE_ID


def apply_grid(table: Any, grid: list[Any] | None, params: dict[str, Any],
               columns: int) -> None:
    """Give the columns the widths the source gave them.

    python-docx lays a new table out in equal columns, so 「实验步骤 / 现象 /
    结论」 came out three equal thirds however the source had proportioned it.
    The source's own ratios are kept and scaled to our body width, the same way
    a figure keeps its proportions and follows our type size.
    """
    try:
        widths = [int(value) for value in (grid or []) if value]
    except (TypeError, ValueError):
        widths = []
    if len(widths) != columns or sum(widths) <= 0:
        return
    shape = ((params.get("wordStyleRegistry") or {}).get("paragraphStyles")
             or {}).get("tables") or {}
    body = int(shape.get("bodyWidthDxa") or sum(widths))
    scaled = [max(1, round(value * body / sum(widths))) for value in widths]
    scaled[-1] += body - sum(scaled)

    element = table._tbl
    grid_element = element.find(qn("w:tblGrid"))
    if grid_element is not None:
        element.remove(grid_element)
    grid_element = OxmlElement("w:tblGrid")
    for value in scaled:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(value))
        grid_element.append(column)
    properties = element.tblPr
    properties.addnext(grid_element)

    # python-docx already writes a w:tblW; appending a second one left the part
    # with two, in the wrong order for the w:tblPr sequence.
    table_width = properties.find(qn("w:tblW"))
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        properties.append(table_width)
    table_width.set(qn("w:w"), str(body))
    table_width.set(qn("w:type"), "dxa")

    # The row's own w:tc elements, not python-docx's row.cells: that accessor
    # resolves a vertically merged column to the cell that began the merge, so
    # the 36 continuation cells here were never the ones being written, and the
    # span accumulator counted the originating row's spans rather than this
    # row's. Measured on the built master it changes nothing today — every cell
    # already carries a width python-docx wrote, and a fixed-layout table takes
    # its widths from w:tblGrid anyway — but it is the same mistake that split
    # the 木炭 table, and 「currently harmless」 is not a reason to keep it.
    for row in element.findall(qn("w:tr")):
        seen = 0
        for cell in row.findall(qn("w:tc")):
            properties = cell.find(qn("w:tcPr"))
            span_element = (properties.find(qn("w:gridSpan"))
                            if properties is not None else None)
            span = int(span_element.get(qn("w:val"))) if span_element is not None else 1
            cell_width = sum(scaled[seen:seen + span])
            seen += span
            if properties is None:
                properties = OxmlElement("w:tcPr")
                cell.insert(0, properties)
            node = properties.find(qn("w:tcW"))
            if node is None:
                node = OxmlElement("w:tcW")
                insert_tc_property(properties, node)
            node.set(qn("w:w"), str(cell_width))
            node.set(qn("w:type"), "dxa")


# w:tcPr is a fixed sequence, not a bag: a property appended after the ones
# that must follow it makes Word reject the part rather than reorder it.
TC_PROPERTY_ORDER = (
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd",
    "noWrap", "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark",
    "headers",
)


def insert_tc_property(properties: Any, element: Any) -> None:
    def rank(node: Any) -> int:
        name = str(node.tag).rsplit("}", 1)[-1]
        return TC_PROPERTY_ORDER.index(name) if name in TC_PROPERTY_ORDER else -1

    place = rank(element)
    follower = next((child for child in properties if rank(child) > place), None)
    if follower is not None:
        follower.addprevious(element)
    else:
        properties.append(element)


def keep_row_with_next(row: Any) -> None:
    """Glue a row to the one below it.

    Closing 「允许跨页断行」 stops a row splitting in half but not a table
    splitting between rows, and 45 pages still opened with a table carried over
    from the page before. Keeping every row but the last with its successor
    moves the whole table to the next page instead. A table taller than the
    text area still breaks — that is the page running out, not the rule
    missing.

    Written against the row's own w:tc elements rather than python-docx's
    row.cells, which resolves a vertically merged column to the cell that
    started the merge. A row whose 「实验步骤」 and 「表达式」 columns continue a
    merge from above therefore kept two paragraphs Word never saw as its own,
    while its actual continuation cells held an empty paragraph with no
    keepNext — and one paragraph without it is enough for Word to break after
    the row. The 木炭 table split between its third and fourth row for exactly
    this reason, with the XML looking, cell for cell, as though the rule had
    been applied.
    """
    for cell in row._tr.findall(qn("w:tc")):
        for paragraph in cell.findall(qn("w:p")):
            set_on_off(paragraph.get_or_add_pPr(), "w:keepNext", True)


def apply_row_properties(row: Any, properties: dict[str, Any],
                         params: dict[str, Any] | None = None) -> None:
    """Row height, header repetition and page-splitting, as the source set them.

    A height is why an answer box is an answer box: the tallest row in this set
    is 1882 twips because four lines have to be written in it, and without the
    height every box comes out one line tall. The blueprint has already scaled
    the number to our type size; the rule that says whether it is a minimum or
    an exact height travels verbatim, because rewriting it would change what
    the number means.
    """
    # A row that breaks across pages splits a reading from its own heading and
    # an answer box from the question above it. The spec closes Word's
    # 「允许跨页断行」 for every row, so this is not read off the source.
    shape = ((params or {}).get("wordStyleRegistry", {})
             .get("paragraphStyles", {}).get("tables") or {})
    forbid_split = bool(shape.get("rowCantSplit"))
    if not properties and not forbid_split:
        return
    element = row._tr.get_or_add_trPr()
    if forbid_split or properties.get("cantSplit"):
        element.append(OxmlElement("w:cantSplit"))
    height = properties.get("heightTwips")
    if height:
        node = OxmlElement("w:trHeight")
        node.set(qn("w:val"), str(int(height)))
        if properties.get("heightRule"):
            node.set(qn("w:hRule"), str(properties["heightRule"]))
        element.append(node)
    if properties.get("isHeader"):
        element.append(OxmlElement("w:tblHeader"))


def apply_cell_shape(element: Any, value: dict[str, Any],
                     params: dict[str, Any]) -> None:
    """Where a cell's text sits, and the edges it draws differently.

    Vertical alignment first: Word reads an absent one as top, and the source
    leaves 168 cells there on purpose, so it is stated rather than assumed.

    Then the borders. Our table style rules every cell the same way, which is
    what 300 of the 315 bordered cells here want. The blueprint carries only
    the departures — a dotted rule, a diagonal splitting a corner header — and
    they are drawn at our weight and our colour: the source decided *that* the
    edge differs, the spec decides what a line looks like.

    This works on the cell element rather than on python-docx's _Cell so a
    vertically merged cell can be given its properties too. Those cells are
    reached only through the cell above them, and the 9 that had been centred
    and the one dotted rule among them were dropped for that reason.
    """
    properties = element.get_or_add_tcPr()
    alignment = str(value.get("vAlign") or "").lower()
    if alignment in ("top", "center", "bottom"):
        existing = properties.find(qn("w:vAlign"))
        if existing is not None:
            properties.remove(existing)
        node = OxmlElement("w:vAlign")
        node.set(qn("w:val"), alignment)
        insert_tc_property(properties, node)

    edges = value.get("edges") or {}
    if not edges:
        return
    registry = params.get("wordStyleRegistry") or {}
    spec = (registry.get("objects") or {}).get(TABLE_STYLE_ID) or {}
    size = int(round(float(spec.get("borderPt", 0.5)) * 8))
    colour = str(spec.get("borderColor") or "auto").lstrip("#")
    holder = properties.find(qn("w:tcBorders"))
    if holder is None:
        holder = OxmlElement("w:tcBorders")
        insert_tc_property(properties, holder)
    for side in ("top", "left", "bottom", "right", "tl2br", "tr2bl"):
        if side not in edges:
            continue
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:val"), str(edges[side] or "single"))
        node.set(qn("w:sz"), str(size))
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), colour)
        holder.append(node)


NUMBER_FORMATS = {
    "decimal-dot": ("decimal", "%1."),
    "chinese-paren-decimal": ("decimal", "(%1)"),
    "upper-alpha-dot": ("upperLetter", "%1."),
    "circled-decimal": ("decimalEnclosedCircle", "%1"),
}


class Numbering:
    """Real Word numbering, with one instance per restart point.

    Literal 「1．」「A．」「(1)」 in the text look right and behave wrongly: join
    ten lessons into one file and every question number restarts at whatever
    the source happened to print, and editing one question means renumbering
    every one after it by hand. Word numbering continues and restarts on its
    own — but only if each restart point gets its own num instance, because a
    numId is a single running counter across the document.
    """

    def __init__(self, document: Document, params: dict[str, Any]) -> None:
        self.document = document
        self.specs = ((params.get("wordStyleRegistry") or {})
                      .get("numbering") or {})
        self.abstract: dict[str, int] = {}
        self.instances: dict[tuple[str, str], int] = {}
        self.part = None
        try:
            self.part = document.part.numbering_part.element
        except (AttributeError, KeyError, NotImplementedError):
            self.part = None
        self.next_abstract = 9000
        self.next_num = 9000

    def _abstract_for(self, name: str) -> int | None:
        if name in self.abstract:
            return self.abstract[name]
        spec = self.specs.get(name)
        if spec is None or self.part is None:
            return None
        shape, text = NUMBER_FORMATS.get(
            str(spec.get("format")), ("decimal", str(spec.get("format") or "%1")))
        element = OxmlElement("w:abstractNum")
        element.set(qn("w:abstractNumId"), str(self.next_abstract))
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), "0")
        for tag, value in (("w:start", "1"), ("w:numFmt", shape),
                           ("w:lvlText", text), ("w:lvlJc", "left")):
            node = OxmlElement(tag)
            node.set(qn("w:val"), value)
            level.append(node)
        properties = OxmlElement("w:pPr")
        indent = OxmlElement("w:ind")
        indent.set(qn("w:left"), str(int(spec.get("textStartDxa")
                                         or spec.get("leftIndentDxa") or 0)))
        indent.set(qn("w:hanging"), str(int(spec.get("hangingDxa") or 0)))
        properties.append(indent)
        level.append(properties)
        element.append(level)
        self.part.insert(0, element)
        self.abstract[name] = self.next_abstract
        self.next_abstract += 1
        return self.abstract[name]

    def instance(self, name: str, restart_key: str,
                 start: int = 1) -> int | None:
        """The numId to use for this style at this restart point."""
        key = (name, restart_key)
        if key in self.instances:
            return self.instances[key]
        abstract = self._abstract_for(name)
        if abstract is None:
            return None
        element = OxmlElement("w:num")
        element.set(qn("w:numId"), str(self.next_num))
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), str(abstract))
        element.append(reference)
        # A second w:num over the same w:abstractNum does not restart anything:
        # Word treats them as one running list, which is how a question's first
        # sub-question came out as 「(3)」 under the question after the one that
        # had (1) and (2). The restart has to be said, not implied.
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), "0")
        # Not always 1. Where a list carries on across something the carve
        # split into two blocks, the source's next printed number is 3, not 1,
        # and starting the run at 1 renumbers the book instead of typesetting
        # it. 137 of 1438 printed numbers disagreed with the source while this
        # said 1 unconditionally.
        start_element = OxmlElement("w:startOverride")
        start_element.set(qn("w:val"), str(max(1, int(start))))
        override.append(start_element)
        element.append(override)
        self.part.append(element)
        self.instances[key] = self.next_num
        self.next_num += 1
        return self.instances[key]


def apply_numbering(paragraph: Any, block: dict[str, Any],
                    numbering: "Numbering | None") -> None:
    spec = block.get("numbering")
    if not spec or numbering is None:
        return
    number = numbering.instance(str(spec.get("style") or ""),
                                str(spec.get("restart") or ""),
                                int(spec.get("start") or 1))
    if number is None:
        return
    properties = paragraph._p.get_or_add_pPr()
    marks = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), str(spec.get("level") or 0))
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(number))
    marks.append(level)
    marks.append(identifier)
    properties.append(marks)


def cell_span(value: Any) -> int:
    """How many grid columns a cell occupies."""
    if not isinstance(value, dict):
        return 1
    try:
        span = int(value.get("gridSpan") or 1)
    except (TypeError, ValueError):
        raise BlueprintError(f"Invalid gridSpan: {value.get('gridSpan')!r}")
    if span < 1:
        raise BlueprintError(f"Invalid gridSpan: {value.get('gridSpan')!r}")
    return span


FIGURE_TYPES = {"image", "chart", "vector_figure"}
# Block kinds whose own style states no text start and which belong to the
# item above them; read from the registry at build time, listed here only as
# the fallback when a caller passes no parameters.
INHERIT_ITEM_INDENT = {"callout_subpoint"}
# Where a search backwards for 「which item am I inside」 has to give up: past a
# question's own opening there is no enclosing item left to compare against.
BOUNDARY_STOP = {"exercise", "exercise_group_title", "chapter",
                 "heading1", "heading2", "heading3", "heading4", "heading5"}


def figure_indent(params: dict[str, Any] | None, previous_type: str | None,
                  block: dict[str, Any] | None = None,
                  blocks: list[dict[str, Any]] | None = None,
                  index: int = 0) -> int:
    """Where a standalone figure's left edge sits.

    The source puts every paragraph at the left margin, including the ones
    holding pictures. The private spec gives a numbered stem a hanging indent
    so its wrapped lines align under the text instead of under the number —
    and the figure, left where the source had it, then stuck out to the left
    of the number itself. The figure belongs to the item, so it starts where
    the item's text starts.
    """
    standard = ((params or {}).get("wordStyleRegistry") or {}).get("figureIndentStandard")
    if not standard:
        return 0
    # The blueprint states the owner. Falling back to the paragraph above is
    # only for a blueprint built before it did.
    # The owner's own block, not the figure's. Passing the figure while naming
    # the owner's type read the owner's style but the figure's numbering — and
    # a figure never carries any — so 24 pictures belonging to (1)(2)(3)
    # sub-questions took the continuation style's 360 instead of the
    # sub-question numbering's 720. Two independent computations disagreeing is
    # what surfaced it; neither number was picked, the mixing was.
    owner_id = ((block or {}).get("figureOwner") or {}).get("blockId")
    owner = next((b for b in (blocks or []) if str(b.get("id")) == str(owner_id)), None)
    recorded = ((block or {}).get("figureOwner") or {}).get("blockType")
    if owner is not None:
        return item_text_start(params, recorded or "", blocks or [],
                               (blocks or []).index(owner))
    return item_text_start(params, recorded or previous_type or "",
                           blocks or [], index)


def item_text_start(params: dict[str, Any] | None, kind: str,
                    blocks: list[dict[str, Any]], index: int) -> int:
    """Where the text of the item this block belongs to begins.

    「跟着它所属那一项的正文起点走」 was read as 「take the owner style's
    leftIndentDxa」, and four of the styles that can own something — the callout
    subpoint, the callout title and body, plain body — never declare one. Absent
    key and 「the left margin」 are not the same statement, but `or 0` says they
    are, and 18 figures printed hard against the margin while every line around
    them sat at 360. So a style that does not state a text start does not have
    one: look outward to the item that does.

    Every callout subpoint in this volume sits inside a numbered item, so this
    resolves for all of them; a block genuinely outside any item resolves to
    the margin, which is then a real answer rather than a missing one.
    """
    styles = ((params or {}).get("wordStyleRegistry") or {}).get("paragraphStyles") or {}

    numbering = ((params or {}).get("wordStyleRegistry") or {}).get("numbering") or {}
    definitions = numbering.get("definitions") or numbering

    def stated(block: dict[str, Any] | None, block_type: str) -> int | None:
        # A numbered item's text begins after its number, and that distance is
        # declared by the numbering definition rather than by the paragraph
        # style — CZ_Body states no indent at all, yet its ①②③ items start at
        # 720. Reading only the style made every figure under one of them sit
        # at the margin. Third place this pipeline has had one fact written in
        # two locations with only one of them read.
        marker = ((block or {}).get("numbering") or {}).get("style")
        spec = definitions.get(marker) if marker else None
        if isinstance(spec, dict) and spec.get("textStartDxa") is not None:
            return int(spec["textStartDxa"] or 0)
        spec = styles.get(BLOCK_STYLE_IDS.get(block_type or "") or "")
        if not spec or "leftIndentDxa" not in spec:
            return None
        return int(spec["leftIndentDxa"] or 0)

    here = stated(blocks[index] if index < len(blocks) else None, kind)
    if here is None and index < len(blocks):
        here = stated(blocks[index], str(blocks[index].get("type") or ""))
    if here is not None:
        return here
    for step in range(index - 1, -1, -1):
        found = stated(blocks[step], str(blocks[step].get("type") or ""))
        if found is not None:
            return found
    return 0


def place_figure_paragraph(paragraph: Any, block: dict[str, Any],
                           params: dict[str, Any] | None,
                           previous_type: str | None,
                           blocks: list[dict[str, Any]] | None,
                           index: int) -> None:
    """Where a figure's paragraph sits, whatever the figure is made of.

    A chart and a drawing are substitutions: they stand exactly where the
    bitmap they replace stood. Placing them by a different rule — centred,
    because they happen to be drawn rather than embedded — silently moved one
    of them off its item's text start, and the difference showed up only
    against the previously released page.
    """
    paragraph.alignment = {
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
    }.get(str(block.get("alignment") or "").lower(), WD_ALIGN_PARAGRAPH.LEFT)
    indent = figure_indent(params, previous_type, block, blocks or [], index)
    if indent:
        paragraph.paragraph_format.left_indent = Twips(indent)


def block_height(registry: dict[str, Any], block: dict[str, Any]) -> float:
    """Roughly how much vertical space a block asks for, in twips.

    Rough is enough: the decision is 「is this worth reserving a third of a
    page for」, and a line either side does not change that answer.
    """
    if str(block.get("type") or "") in FIGURE_TYPES:
        millimetres = float(block.get("heightMm") or block.get("height_mm") or 0)
        if not millimetres:
            figure = block.get("figure") or block.get("chart") or {}
            millimetres = float(figure.get("heightUnits", 0) or figure.get("heightMm", 0) or 30)
        return millimetres * 56.6929134
    styles = registry.get("paragraphStyles") or {}
    spec = styles.get(BLOCK_STYLE_IDS.get(str(block.get("type") or "")) or "") or {}
    width = float(((registry.get("page") or {}).get("bodyWidthDxa") or 9411))
    width -= float(spec.get("leftIndentDxa") or 0)
    size = float(spec.get("sizePt") or 12) * 20
    # Third place the same assumption had to be dislodged: counting characters
    # rather than measuring them says 「A．O2　B．N2　C．H2　D．CO2」 fills two
    # lines when it fills one. The option columns and the reaction arrow were
    # already reading these ratios; a height estimate that did not was going to
    # keep reserving space for lines that are not there.
    ems = sum(condition_em(str(s.get("text") or ""), {"wordStyleRegistry": registry})
              for s in block.get("segments") or [])
    lines = max(1.0, (ems * size) / max(width, 1.0))
    # 「Single」 line spacing is the font's line height, not the point size. For
    # 宋体/Times New Roman it measures 1.30× the size, so a 12pt line at 1.75
    # occupies 27.3pt and not the 21pt the size alone suggests. Reading the
    # size as the line made every paragraph 30% short, which emptied the
    # height ceiling of meaning and let the chain drag whole groups of
    # sub-questions onto the next page.
    metrics = ((registry.get("keepTogetherStandard") or {}).get("metrics") or {})
    single = float(metrics.get("singleLineHeightPerPt") or 1.0)
    multiple = float(spec.get("lineMultiple")
                     or (float(spec.get("lineDxa") or 420) / 240.0))
    line = size * single * multiple
    return lines * line + float(spec.get("beforeDxa") or 0) + float(spec.get("afterDxa") or 0)


def keep_lines(registry: dict[str, Any], block: dict[str, Any]) -> bool:
    """Whether this block must not be torn across a page.

    keepNext binds a block to the one after it; nothing bound a block to
    itself, so a stem could print two lines at the foot of a page and send the
    rest — with everything keepNext had gathered behind it — to the next,
    leaving a third of a page blank. The tear bought nothing.

    Capped at the same height every other binding is capped at: a stem taller
    than that is better torn than moved whole, because moving it whole digs a
    hole worse than the break it avoids.
    """
    rule = (registry.get("keepTogetherStandard") or {}).get("keepLinesStandard") or {}
    if str(block.get("type") or "") not in set(rule.get("appliesToTypes") or ()):
        return False
    ceiling = float(rule.get("maxHeightDxa") or 0)
    return not ceiling or block_height(registry, block) <= ceiling


def is_annotation(registry: dict[str, Any], block: dict[str, Any]) -> bool:
    """Whether this block annotates the thing above it.

    「注：其中布袋子可换成……」 is about the apparatus table it follows, not
    about whatever the page happens to put after it.
    """
    rule = (registry.get("keepTogetherStandard") or {}).get("annotationBinding") or {}
    if str(block.get("type") or "") not in set(rule.get("appliesToTypes") or ()):
        return False
    text = "".join(str(s.get("text") or "") for s in (block.get("segments") or []))
    text = text.lstrip().lstrip("（(【[")
    return any(text.startswith(str(marker)) for marker in (rule.get("markers") or ()))


def annotation_binds_backward(registry: dict[str, Any],
                              blocks: list[dict[str, Any]], index: int) -> bool:
    """Whether the block at `index` must hold on to an annotation below it."""
    rule = (registry.get("keepTogetherStandard") or {}).get("annotationBinding") or {}
    if index + 1 >= len(blocks):
        return False
    if str(blocks[index].get("type") or "") not in set(rule.get("referentTypes") or ()):
        return False
    return is_annotation(registry, blocks[index + 1])


def keep_with_next(params: dict[str, Any] | None, blocks: list[dict[str, Any]],
                   index: int) -> bool:
    """Whether this paragraph must stay on the page with what follows it.

    A figure belongs with the text that introduces it — 「实验装置如图」 and the
    apparatus must not be a page apart. But keeping only the paragraph directly
    above a figure strands the head of the chain: 「【探究一】探究氧气的物理性质」
    sat alone at the foot of one page while its sentence and its diagram, bound
    to each other, moved to the next.

    So the binding propagates backwards, and it is capped. 「A question must
    never span a page」 sounds right and is not achievable — a question with a
    figure, four sub-questions and answer lines can exceed a page on its own,
    and forcing it whole would open a hole instead. What must not happen is a
    break between a stem and its first piece of content; past four blocks the
    break falls between sub-questions, where it belongs.
    """
    registry = (params or {}).get("wordStyleRegistry") or {}
    standard = registry.get("keepTogetherStandard") or {}
    chain = standard.get("chainPropagation") or {}
    allowed = set(standard.get("figureFollowsAppliesTo") or [])
    cap = int(chain.get("maxBlocks") or 0)
    ceiling = float(chain.get("maxHeightDxa") or 0)
    pulling = set(standard.get("pullingTypes") or FIGURE_TYPES)
    levels = chain.get("itemLevels") or {}
    mine = enclosing_level(blocks, index, levels)
    here = str(blocks[index].get("type") or "")
    # Direction first, and it is decided by what the block is about. An
    # annotation holds on to what it annotates and lets go of everything after
    # it — bound forward instead, it takes the referent's page away with it.
    if annotation_binds_backward(registry, blocks, index):
        return True
    if is_annotation(registry, blocks[index]):
        return False
    if group_binds(registry, blocks, index):
        return True
    if stem_binds(registry, blocks, index):
        return True
    if BLOCK_STYLE_IDS.get(here) not in allowed:
        return False
    # The budget counts what would be dragged along, not what it is dragged to.
    # A figure or a table moves as one block whatever its size — that is not a
    # choice we make — so charging its height to the budget only meant that the
    # taller it was, the less able it was to keep its own introduction with it.
    height = block_height(registry, blocks[index])
    for step in range(1, max(cap, 1) + 1):
        if index + step >= len(blocks):
            return False
        following = blocks[index + step]
        kind = str(following.get("type") or "")
        if kind in pulling:
            return True
        # Sideways stops the chain; downwards does not. Meeting a question at
        # our own level means the figure past it is that question's, and
        # dragging our sub-questions to keep a stranger's figure company is how
        # four of them left a 124pt hole behind. Meeting the question that
        # 「拓展培优」 itself introduces is the opposite case, and treating the
        # two alike undid this rule's own reason for existing.
        if kind in levels and mine is not None and levels[kind] <= mine:
            return False
        if chain.get("stopsAtSibling") and opens_sibling(blocks, index, index + step):
            return False
        height += block_height(registry, following)
        # Height, not block count. Four short paragraphs and 「a twenty-line
        # stem plus three paragraphs」 differ several-fold in what they ask the
        # page to reserve; keeping the second whole digs a hole in the page
        # above that is worse than the break it avoids.
        if ceiling and height > ceiling:
            return False
        if not cap or BLOCK_STYLE_IDS.get(kind) not in allowed:
            return False
    return False


def enclosing_level(blocks: list[dict[str, Any]], index: int,
                    levels: dict[str, Any]) -> int | None:
    """How deep in the document's tree this block sits.

    A block that is not itself a heading or a stem inherits the level of the
    nearest one above it: a sub-question belongs to its question. Used to tell
    a step downwards from a step sideways.
    """
    for step in range(index, -1, -1):
        kind = str(blocks[step].get("type") or "")
        if kind in levels:
            return int(levels[kind])
    return None


def opens_sibling(blocks: list[dict[str, Any]], index: int, at: int) -> bool:
    """Whether block `at` starts the item after the one block `index` sits in.

    ①②③④⑤ under one question share a numbering style and a restart key; that
    pair is what says 「same list」, and a block carrying it is the next item
    rather than more of this one. Crossing that line is how ⑤'s diagram
    dragged ③ and ④ onto the following page and left 271pt empty behind them,
    when neither of them introduces anything.

    Descending a level is not crossing it: 「【探究一】」 carries no numbering,
    so the (1) beneath it is its content, not its sibling, and that chain — the
    one this whole rule was written for — is untouched.
    """
    following = (blocks[at].get("numbering") or {}) if at < len(blocks) else {}
    if not following.get("style"):
        return False
    for step in range(index, -1, -1):
        mine = blocks[step].get("numbering") or {}
        if mine.get("style"):
            return (mine.get("style") == following.get("style")
                    and mine.get("restart") == following.get("restart"))
        if step < index and str(blocks[step].get("type") or "") in BOUNDARY_STOP:
            return False
    return False


def stem_binds(registry: dict[str, Any], blocks: list[dict[str, Any]],
               index: int) -> bool:
    """The line that opens an item stays with that item's first content.

    「A break between a stem and its first piece of content is the worst of
    them all」 was adjudicated on 2026-08-08 and implemented for exactly one
    kind of first content — a figure — then extended to a second, options.
    When it was anything else, nothing held: question 14's stem printed alone
    at the foot of one page while its ①②③④ and all four options went to the
    next, and question 15's stem sat alone above four apparatus diagrams.

    Which blocks open an item is not re-derived here: a block carries its own
    numbering, and the carve decided that.
    """
    standard = registry.get("keepTogetherStandard") or {}
    rule = standard.get("stemFirstContent") or {}
    if not rule or index + 1 >= len(blocks):
        return False
    if not (blocks[index].get("numbering") or {}).get("style"):
        return False
    following = blocks[index + 1]
    kind = str(following.get("type") or "")
    # A figure or table is already held by the rule written for it, and an
    # item that is immediately followed by another item of its own rank has no
    # content of its own to keep.
    if kind in set(standard.get("pullingTypes") or FIGURE_TYPES):
        return False
    levels = (standard.get("chainPropagation") or {}).get("itemLevels") or {}
    mine = enclosing_level(blocks, index, levels)
    if kind in levels and mine is not None and levels[kind] <= mine:
        return False
    if opens_sibling(blocks, index, index + 1):
        return False
    ceiling = float((standard.get("chainPropagation") or {}).get("maxHeightDxa") or 0)
    height = block_height(registry, blocks[index]) + block_height(registry, following)
    return not ceiling or height <= ceiling


def group_binds(registry: dict[str, Any], blocks: list[dict[str, Any]],
                index: int) -> bool:
    """A run of same-kind blocks, and the line introducing it, stay together.

    Options are the obvious case — splitting 「A．… B．…」 from 「C．… D．…」 asks
    the reader to turn a page to finish comparing four answers to one question,
    which is the whole point of options being options. Answer lines and callout
    bodies are the same shape of thing, and were written as data rather than as
    two more branches: the previous version named only options, so the other
    two sat on the same census table and were skipped until someone looked.

    Capped like every other binding: a group taller than a third of the text
    area is left to break inside itself rather than open a larger hole above.
    """
    standard = registry.get("keepTogetherStandard") or {}
    ceiling = float((standard.get("chainPropagation") or {}).get("maxHeightDxa") or 0)
    here = str(blocks[index].get("type") or "")
    following = blocks[index + 1] if index + 1 < len(blocks) else None
    if following is None:
        return False
    next_kind = str(following.get("type") or "")
    for entry in (standard.get("groupBindings") or {}).get("entries") or []:
        bound = set(entry.get("boundTypes") or ())
        if next_kind not in bound:
            continue
        if here not in bound and here not in set(entry.get("stemTypes") or ()):
            continue
        height = block_height(registry, blocks[index])
        for step in range(index + 1, len(blocks)):
            if str(blocks[step].get("type") or "") not in bound:
                break
            height += block_height(registry, blocks[step])
        if not ceiling or height <= ceiling:
            return True
    return False


def page_break_wanted(block: dict[str, Any], params: dict[str, Any] | None,
                      previous_type: str | None) -> bool:
    """Whether this heading starts a new page.

    A heading that appears in the table of contents is a place a reader is
    told to turn to, so it should be the first thing on the page they turn
    to. The exception carries the weight: a heading that directly follows its
    own parent heading must not break, or the parent prints alone on a page
    with nothing under it.
    """
    registry = (params or {}).get("wordStyleRegistry") or {}
    wanted = break_before_styles(registry)
    style = BLOCK_STYLE_IDS.get(str(block.get("type") or ""))
    if style not in wanted:
        return False
    parent = BLOCK_STYLE_IDS.get(previous_type or "")
    return parent not in wanted


def break_before_styles(registry: dict[str, Any]) -> set[str]:
    """Which styles open a new page — read off the table of contents.

    The rule is 「a heading a reader is told to turn to should be the first
    thing on the page they turn to」, so the set is exactly the headings that
    appear in the contents: the ones carrying a tocLevel. Kept as a derivation
    rather than a second hand-maintained list, because a list beside the thing
    it describes is a list that drifts from it — the same shape of failure as
    a rule written in one vocabulary and checked in another.
    """
    styles = registry.get("paragraphStyles") or {}
    derived = {name for name, spec in styles.items()
               if isinstance(spec, dict) and spec.get("tocLevel")}
    standard = registry.get("pageBreakStandard") or {}
    return derived or set(standard.get("breakBefore") or ())


def add_block(
    document: Document,
    block: dict[str, Any],
    semantic_records: list[dict[str, Any]],
    source_hash_cache: dict[str, tuple[tuple[int, int], str]],
    params: dict[str, Any] | None = None,
    numbering: "Numbering | None" = None,
    previous_type: str | None = None,
    next_type: str | None = None,
    all_blocks: list[dict[str, Any]] | None = None,
    block_index: int = 0,
) -> None:
    block_id = str(block["id"])
    block_type = str(block["type"])
    source = source_record(block, source_hash_cache)
    emitted_table = None
    if block_type == "page_break":
        paragraph = document.add_paragraph()
        set_paragraph_style(paragraph, "CZ_LayoutSpacer")
        paragraph.add_run().add_break(WD_BREAK.PAGE)
        style_id = "CZ_LayoutSpacer"
    elif block_type == "table":
        rows = block.get("rows") or []
        if not rows or not all(isinstance(row, list) for row in rows):
            raise BlueprintError(f"Table block {block_id} has invalid rows")
        # A row is rectangular once its spans are counted: a cell carrying
        # gridSpan 2 occupies two grid columns, and a vertically merged cell is
        # still present in every row it covers. Requiring equal cell counts
        # rejected every real table in the source — 79 of the cells in one
        # chemistry topic are merged, and 「实验步骤 / 现象 / 结论」 only reads
        # correctly because the step cell spans its two rows.
        widths = {sum(cell_span(value) for value in row) for row in rows}
        if len(widths) != 1 or min(widths) < 1:
            raise BlueprintError(f"Table block {block_id} must be rectangular")
        width = widths.pop()
        table = document.add_table(rows=len(rows), cols=width)
        apply_grid(table, block.get("grid"), params or {}, width)
        # Assigned by style id through the element: looking a style up by id
        # is deprecated in python-docx, and by name it would depend on the
        # display name staying put.
        table_style = OxmlElement("w:tblStyle")
        table_style.set(qn("w:val"), TABLE_STYLE_ID)
        table._tbl.tblPr.insert(0, table_style)
        header_rows = int(block.get("header_rows") or 0)
        row_properties = block.get("rowProperties") or []
        for row_index, values in enumerate(rows):
            apply_row_properties(
                table.rows[row_index],
                row_properties[row_index] if row_index < len(row_properties) else {},
                params or {})
            column = 0
            for value in values:
                col_index = column
                span = cell_span(value)
                column += span
                cell = table.cell(row_index, col_index)
                if span > 1:
                    cell = cell.merge(table.cell(row_index, col_index + span - 1))
                    cell.text = ""
                # Held before any vertical merge: afterwards this cell is only
                # reachable through the one above it, and its own alignment and
                # edges would have nowhere to be written.
                element = cell._tc
                if isinstance(value, dict):
                    apply_cell_shape(element, value, params or {})
                merge = str(value.get("vMerge") or "") if isinstance(value, dict) else ""
                if merge == "continue":
                    # Carried by the cell above; merging joins them and Word
                    # keeps the upper cell's content.
                    above = table.cell(row_index - 1, col_index)
                    above.merge(cell)
                    continue
                paragraph = cell.paragraphs[0]
                set_paragraph_style(
                    paragraph,
                    "CZ_TableHeader" if row_index < header_rows else "CZ_TableText",
                )
                # Where the text sits across the cell. The source centres 263
                # of its cell paragraphs, and a reading centred under its own
                # heading is not the same table as one flushed left.
                across = {
                    "center": WD_ALIGN_PARAGRAPH.CENTER,
                    "right": WD_ALIGN_PARAGRAPH.RIGHT,
                    "left": WD_ALIGN_PARAGRAPH.LEFT,
                    "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
                }.get(str(value.get("alignment") or "").lower()
                      if isinstance(value, dict) else "")
                if across is not None:
                    paragraph.alignment = across
                if isinstance(value, dict):
                    if not isinstance(value.get("segments"), list):
                        raise BlueprintError(
                            f"Table block {block_id} cell {row_index + 1},"
                            f"{col_index + 1} has invalid segments"
                        )
                    # One paragraph per source paragraph. Writing them all into
                    # the cell's first paragraph ran three numbered objectives
                    # into a single line.
                    pieces = value.get("paragraphs") or [value["segments"]]
                    for order, piece in enumerate(pieces):
                        if order:
                            paragraph = cell.add_paragraph()
                            set_paragraph_style(
                                paragraph,
                                "CZ_TableHeader" if row_index < header_rows
                                else "CZ_TableText")
                            if across is not None:
                                paragraph.alignment = across
                        add_segments(
                            paragraph,
                            {
                                "id": (
                                    f"{block_id}-cell-{row_index + 1}-"
                                    f"{col_index + 1}-{order + 1}"
                                ),
                                "segments": piece,
                            },
                            params,
                        )
                else:
                    paragraph.add_run(str(value))
        shape = ((params or {}).get("wordStyleRegistry", {})
                 .get("paragraphStyles", {}).get("tables") or {})
        if shape.get("rowKeepWithNext"):
            for row in table.rows[:-1]:
                keep_row_with_next(row)
        emitted_table = table
        style_id = "CZ_TableText"
        paragraph = document.add_paragraph()
        set_paragraph_style(paragraph, "CZ_LayoutSpacer")
        add_semantic_bookmark(paragraph, block_id, block_type)
    elif block_type == "vector_figure":
        paragraph = document.add_paragraph()
        set_paragraph_style(paragraph, "CZ_ImageBlock")
        # A substitution stands where the bitmap stood. Centring it because it
        # is drawn rather than placed moved 图 4.16 from its item's text start
        # to the middle of the line, which is a change the source never asked
        # for — the picture it replaces sat at x=80.4 with everything else.
        place_figure_paragraph(paragraph, block, params, previous_type,
                               all_blocks, block_index)
        add_block.figure_index = getattr(add_block, "figure_index", 0) + 1
        place_vector_figure(paragraph.add_run(), block["figure"],
                            add_block.figure_index)
        style_id = "CZ_ImageBlock"
        add_semantic_bookmark(paragraph, block_id, block_type)
    elif block_type == "chart":
        paragraph = document.add_paragraph()
        set_paragraph_style(paragraph, "CZ_ImageBlock")
        place_figure_paragraph(paragraph, block, params, previous_type,
                               all_blocks, block_index)
        add_block.chart_index = getattr(add_block, "chart_index", 0) + 1
        place_chart(paragraph.add_run(), document, block["chart"],
                    add_block.chart_index)
        style_id = "CZ_ImageBlock"
        add_semantic_bookmark(paragraph, block_id, block_type)
    elif block_type == "image":
        image_path = Path(str(block.get("path") or ""))
        if not image_path.exists():
            raise BlueprintError(f"Image block {block_id} is missing file: {image_path}")
        paragraph = document.add_paragraph()
        set_paragraph_style(paragraph, "CZ_ImageBlock")
        # The source centres 56 of its figures and leaves the rest at the left
        # margin; forcing every one to the centre moved 259 of them.
        place_figure_paragraph(paragraph, block, params, previous_type,
                               all_blocks, block_index)
        place_picture(paragraph.add_run(), image_path, block, 150.0, params)
        style_id = "CZ_ImageBlock"
        add_semantic_bookmark(paragraph, block_id, block_type)
    else:
        paragraph = document.add_paragraph()
        style_id = BLOCK_STYLE_IDS[block_type]
        if block_type == "choice":
            style_id = CHOICE_COLUMN_STYLE_IDS.get(
                int(block.get("columns") or 1), style_id)
        set_paragraph_style(paragraph, style_id)
        # A subpoint belongs to the item it sits inside — 「【空气中氧气含量的
        # 测定】…」 is part of question 17, not a thing beside it — so its text
        # begins where the item's text begins. Its style states no indent, and
        # for a figure that omission is now read as 「look outward」 rather than
        # 「the margin」; the line that owns the figure has to move with it, or
        # the picture ends up indented past its own caption.
        if block_type in INHERIT_ITEM_INDENT:
            inherited = item_text_start(params, block_type,
                                        all_blocks or [], block_index)
            if inherited:
                paragraph.paragraph_format.left_indent = Twips(inherited)
        apply_numbering(paragraph, block, numbering)
        add_segments(paragraph, block, params)
        add_semantic_bookmark(paragraph, block_id, block_type)
    # One decision, asked once, for every kind of block. How it is carried out
    # differs — a paragraph takes keepNext itself, a table needs its last row
    # bound to the spacer that follows it — but which blocks keep with what
    # comes next is a single question, and asking it in one branch only was how
    # 「注：…」 came to be the one thing a table could not hold on to.
    if paragraph is not None and keep_with_next(params, all_blocks or [], block_index):
        set_on_off(paragraph._p.get_or_add_pPr(), "w:keepNext", True)
        if emitted_table is not None and emitted_table.rows:
            keep_row_with_next(emitted_table.rows[-1])
    registry = (params or {}).get("wordStyleRegistry") or {}
    if paragraph is not None and keep_lines(registry, block):
        set_on_off(paragraph._p.get_or_add_pPr(), "w:keepLines", True)
    if paragraph is not None and page_break_wanted(block, params, previous_type):
        properties = paragraph._p.get_or_add_pPr()
        if properties.find(qn("w:pageBreakBefore")) is None:
            properties.insert(0, OxmlElement("w:pageBreakBefore"))
    if block.get("page_break_before"):
        set_on_off(paragraph._p.get_or_add_pPr(), "w:pageBreakBefore", True)
    semantic_records.append(
        {
            "blockId": block_id,
            "semanticType": block_type,
            "paragraphStyleId": style_id,
            "text": visible_text(block),
            "sourceStatus": source["status"],
            "sourcePath": source.get("path"),
            "sourceSha256": source.get("sha256"),
            "sourceLocatorKind": (source.get("locator") or {}).get("kind"),
            "sourceLocator": (source.get("locator") or {}).get("value"),
            "topic": block.get("topic"),
            "section": block.get("section"),
            "reviewStatus": block.get("review_status", "approved"),
            "inlineImageSources": [
                {
                    "label": label,
                    "source": segment.get("source"),
                }
                for label, segment in inline_image_segments(block)
            ],
        }
    )


def add_headers_and_footers(
    document: Document,
    blueprint: dict[str, Any],
    params: dict[str, Any],
) -> None:
    page_header = (params.get("modules") or {}).get("pageHeader") or {}
    widths = [
        int(page_header.get("leftRegionWidthDxa") or 4705),
        int(page_header.get("rightRegionWidthDxa") or 4706),
    ]
    left_odd = str(
        page_header.get("leftTextOdd")
        or blueprint.get("headerLeft")
        or "橙子教室·暑假班讲义"
    )
    left_even = str(page_header.get("leftTextEven") or left_odd)
    right_toc = str(page_header.get("rightTextToc") or "目录")
    # 页眉右区。规范:写「所在目录中的讲级标题」,随页变化,由 STYLEREF 域实现。
    # headerRightStyleRef 给出标题的字符/段落样式 id;缺省则退回字面量,
    # 已付印的册子行为不变。字面量默认值保留是历史包袱——它让一本物理讲义
    # 的每一页页眉都印过「八年级化学」,直到 2026-08-14 才被发现。
    right_body = str(blueprint.get("headerRight") or "八年级化学")
    right_style_ref = blueprint.get("headerRightStyleRef") or None
    document.settings.odd_and_even_pages_header_footer = True
    for index, section in enumerate(document.sections):
        right_text = right_toc if index == 0 else right_body
        section.header.is_linked_to_previous = False
        section.even_page_header.is_linked_to_previous = False
        add_header_table(
            section.header,
            left_text=left_odd,
            right_text=right_text,
            widths=widths,
            right_style_ref=None if index == 0 else right_style_ref,
        )
        add_header_table(
            section.even_page_header,
            left_text=left_even,
            right_text=right_text,
            right_style_ref=None if index == 0 else right_style_ref,
            widths=widths,
        )
        if index == 0:
            section.footer.is_linked_to_previous = False
            section.even_page_footer.is_linked_to_previous = False
            add_page_footer(section.footer)
            add_page_footer(section.even_page_footer)
            set_page_number_start(section, 1)
        else:
            section.footer.is_linked_to_previous = True
            section.even_page_footer.is_linked_to_previous = True
            set_page_number_start(section, None)


def validate_source_coverage_chain(
    *,
    blueprint_path: Path,
    source_object_manifest_path: Path,
    source_coverage_report_path: Path,
) -> dict[str, str]:
    source_object_manifest = load_json(source_object_manifest_path)
    source_coverage_report = load_json(source_coverage_report_path)
    blueprint_hash = sha256_file(blueprint_path)
    manifest_hash = sha256_file(source_object_manifest_path)
    if source_coverage_report.get("status") != "pass":
        raise BlueprintError("Source-object coverage report did not pass")
    if source_object_manifest.get("blueprintSha256") != blueprint_hash:
        raise BlueprintError(
            "HOLD_INPUT_DRIFT: source-object manifest blueprint hash mismatch"
        )
    if source_coverage_report.get("blueprintSha256") != blueprint_hash:
        raise BlueprintError(
            "HOLD_INPUT_DRIFT: source coverage report blueprint hash mismatch"
        )
    if source_coverage_report.get("sourceObjectManifestSha256") != manifest_hash:
        raise BlueprintError(
            "HOLD_INPUT_DRIFT: source coverage report manifest hash mismatch"
        )
    return {
        "sourceObjectManifestSha256": manifest_hash,
        "sourceCoverageReportSha256": sha256_file(source_coverage_report_path),
    }


def build(
    *,
    blueprint_path: Path,
    output_path: Path,
    params_path: Path,
    semantic_manifest_path: Path,
    source_ledger_path: Path,
    report_path: Path,
    source_object_manifest_path: Path | None = None,
    source_coverage_report_path: Path | None = None,
) -> dict[str, Any]:
    blueprint = load_json(blueprint_path)
    params = load_json(params_path)
    source_hash_cache: dict[str, tuple[tuple[int, int], str]] = {}
    blocks = validate_blueprint(blueprint, source_hash_cache)
    source_coverage_hashes: dict[str, str] = {}
    if source_object_manifest_path is not None or source_coverage_report_path is not None:
        if source_object_manifest_path is None or source_coverage_report_path is None:
            raise BlueprintError(
                "Source object manifest and coverage report must be supplied together"
            )
        source_coverage_hashes = validate_source_coverage_chain(
            blueprint_path=blueprint_path,
            source_object_manifest_path=source_object_manifest_path,
            source_coverage_report_path=source_coverage_report_path,
        )
    document = Document()
    enforce_current_docx_compatibility(document, params)
    enforce_document_typography_defaults(document, params)
    install_registered_styles(document, params)
    ensure_table_style(document, params)
    numbering = Numbering(document, params)
    apply_page_setup(document, params)
    body = document._element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    semantic_records = add_toc(document, params)
    document.add_section(WD_SECTION.NEW_PAGE)
    apply_page_setup(document, params)
    previous_type: str | None = None
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        add_block(document, block, semantic_records, source_hash_cache, params,
                  numbering, previous_type,
                  str(following.get("type")) if following else None,
                  blocks, index)
        previous_type = str(block.get("type") or "")
    add_headers_and_footers(document, blueprint, params)
    # 内容写完之后才删——「用没用」要按最终正文算,不能按写之前算。
    stripped_styles = strip_inherited_unused_styles(document, params)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    output_hash = sha256_file(output_path)
    semantic_manifest = {
        "strippedInheritedStyles": stripped_styles,
        "schemaVersion": "chengziclass.semantic-handout-manifest.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "blueprintPath": str(blueprint_path),
        "blueprintSha256": sha256_file(blueprint_path),
        "outputPath": str(output_path),
        "outputSha256": output_hash,
        "edition": "student",
        "sourcePolicy": SOURCE_POLICY_ID,
        "contentFidelityPolicy": CONTENT_FIDELITY_POLICY_ID,
        "sourceTitleParagraphs": blueprint["sourceTitleParagraphs"],
        "sourceTitleVisualTextEvidence": blueprint[
            "sourceTitleVisualTextEvidence"
        ],
        "sourceObjectExclusions": blueprint["sourceObjectExclusions"],
        "sourceObjectReviewQueue": blueprint.get("sourceObjectReviewQueue") or [],
        "sourceObjectSubstitutions": blueprint.get("sourceObjectSubstitutions") or [],
        "sourceDocuments": blueprint["sourceDocuments"],
        **source_coverage_hashes,
        "paragraphCount": len(semantic_records),
        "records": semantic_records,
    }
    semantic_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    semantic_manifest_path.write_text(
        json.dumps(semantic_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    source_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with source_ledger_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "content_id",
                "topic",
                "section",
                "content_type",
                "source_path",
                "source_sha256",
                "source_locator_kind",
                "source_locator",
                "source_status",
                "student_use",
                "review_status",
                "notes",
            ],
        )
        writer.writeheader()
        for record in semantic_records:
            writer.writerow(
                {
                    "content_id": record.get("blockId"),
                    "topic": record.get("topic") or "",
                    "section": record.get("section") or "",
                    "content_type": record.get("semanticType"),
                    "source_path": record.get("sourcePath") or "",
                    "source_sha256": record.get("sourceSha256") or "",
                    "source_locator_kind": record.get("sourceLocatorKind") or "",
                    "source_locator": record.get("sourceLocator") or "",
                    "source_status": record.get("sourceStatus"),
                    "student_use": "included",
                    "review_status": record.get("reviewStatus"),
                    "notes": "",
                }
            )
            for segment_index, segment_record in enumerate(
                record.get("inlineImageSources") or [],
                start=1,
            ):
                segment_source = segment_record.get("source") or {}
                locator = (segment_source or {}).get("locator") or {}
                writer.writerow(
                    {
                        "content_id": (
                            f"{record.get('blockId')}-"
                            f"{segment_record.get('label') or segment_index}"
                        ),
                        "topic": record.get("topic") or "",
                        "section": record.get("section") or "",
                        "content_type": "inline_image",
                        "source_path": (segment_source or {}).get("path") or "",
                        "source_sha256": (segment_source or {}).get("sha256") or "",
                        "source_locator_kind": locator.get("kind") or "",
                        "source_locator": locator.get("value") or "",
                        "source_status": (segment_source or {}).get("status") or "",
                        "student_use": "included-inline",
                        "review_status": record.get("reviewStatus"),
                        "notes": "position preserved inside the owning paragraph",
                    }
                )
        for exclusion in blueprint["sourceObjectExclusions"]:
            source = exclusion["source"]
            locator = source["locator"]
            writer.writerow(
                {
                    "content_id": exclusion["id"],
                    "topic": exclusion.get("topic") or "",
                    "section": exclusion.get("section") or "",
                    "content_type": exclusion["classification"],
                    "source_path": source.get("path") or "",
                    "source_sha256": source.get("sha256") or "",
                    "source_locator_kind": locator.get("kind") or "",
                    "source_locator": locator.get("value") or "",
                    "source_status": source.get("status") or "",
                    "student_use": "excluded",
                    "review_status": exclusion.get("review_status") or "",
                    "notes": exclusion.get("reason") or "",
                }
            )
        for evidence in blueprint["sourceTitleVisualTextEvidence"]:
            source = evidence["source"]
            locator = source["locator"]
            writer.writerow(
                {
                    "content_id": evidence["id"],
                    "topic": "",
                    "section": "",
                    "content_type": "source-title-visual-text-evidence",
                    "source_path": source.get("path") or "",
                    "source_sha256": source.get("sha256") or "",
                    "source_locator_kind": locator.get("kind") or "",
                    "source_locator": locator.get("value") or "",
                    "source_status": source.get("status") or "",
                    "student_use": (
                        "title-text-preserved"
                        if evidence.get("containsTitleText")
                        else "decoration-reviewed"
                    ),
                    "review_status": evidence.get("review_status") or "",
                    "notes": (
                        f"decision={evidence.get('decision')}; "
                        f"titleText={evidence.get('titleText') or ''}; "
                        f"canonicalBlockId={evidence.get('canonicalBlockId')}; "
                        f"resolver={evidence.get('resolverId')}@"
                        f"{evidence.get('resolverVersion')}; "
                        f"cacheKey={evidence.get('cacheKey')}"
                    ),
                }
            )
        for substitution in blueprint.get("sourceObjectSubstitutions") or []:
            source = substitution["source"]
            locator = source["locator"]
            writer.writerow(
                {
                    "content_id": substitution["id"],
                    "topic": substitution.get("topic") or "",
                    "section": substitution.get("section") or "",
                    "content_type": "source-object-substitution",
                    "source_path": source.get("path") or "",
                    "source_sha256": source.get("sha256") or "",
                    "source_locator_kind": locator.get("kind") or "",
                    "source_locator": locator.get("value") or "",
                    "source_status": source.get("status") or "",
                    "student_use": "included-as-reviewed-text",
                    "review_status": substitution.get("review_status") or "",
                    "notes": (
                        f"target={substitution.get('targetBlockId')}; "
                        f"replacement={substitution.get('replacementText')}"
                    ),
                }
            )
    report = {
        "schemaVersion": "chengziclass.semantic-handout-build-report.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "pass",
        "outputPath": str(output_path),
        "outputSha256": output_hash,
        "blockCount": len(blocks),
        "semanticRecordCount": len(semantic_records),
        "sourcePolicy": SOURCE_POLICY_ID,
        "contentFidelityPolicy": CONTENT_FIDELITY_POLICY_ID,
        "sourceTitleParagraphCount": len(blueprint["sourceTitleParagraphs"]),
        "sourceTitleVisualTextEvidenceCount": len(
            blueprint["sourceTitleVisualTextEvidence"]
        ),
        "sourceObjectExclusionCount": len(blueprint["sourceObjectExclusions"]),
        "sourceObjectSubstitutionCount": len(
            blueprint.get("sourceObjectSubstitutions") or []
        ),
        "inlineImageSegmentCount": sum(
            len(record.get("inlineImageSources") or [])
            for record in semantic_records
        ),
        "sourceDocumentCount": len(blueprint["sourceDocuments"]),
        # Registry properties declared by the spec but not yet materialised by
        # ensure_style(). Reported rather than dropped in silence.
        "unimplementedStyleSpecKeys": dict(sorted(UNIMPLEMENTED_SPEC_KEYS.items())),
        "unimplementedStyleSpecKeyCount": len(UNIMPLEMENTED_SPEC_KEYS),
        **source_coverage_hashes,
        "titleDecorationExclusionCount": sum(
            item.get("classification") == TITLE_DECORATION_CLASSIFICATION
            for item in blueprint["sourceObjectExclusions"]
        ),
        "studentWordBlockCount": sum(
            1 for item in semantic_records if item.get("sourceStatus") == "student_word"
        ),
        "originalWordBlockCount": sum(
            1 for item in semantic_records if item.get("sourceStatus") == "original_word"
        ),
        "layoutBlockCount": sum(
            1 for item in semantic_records if item.get("sourceStatus") == "layout"
        ),
        "studentForbiddenMarkerCount": 0,
        "wordAuthorityBoundary": (
            "This file is an isolated semantic content candidate. Microsoft Word "
            "native clean-open, field update, pagination, save, and visual acceptance "
            "are mandatory before formal installation or PDF export."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This compiler is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py."
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--semantic-manifest", type=Path, required=True)
    parser.add_argument("--source-ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-object-manifest", type=Path, required=True)
    parser.add_argument("--source-coverage-report", type=Path, required=True)
    args = parser.parse_args()
    report = build(
        blueprint_path=args.blueprint,
        output_path=args.output,
        params_path=args.params,
        semantic_manifest_path=args.semantic_manifest,
        source_ledger_path=args.source_ledger,
        report_path=args.report,
        source_object_manifest_path=args.source_object_manifest,
        source_coverage_report_path=args.source_coverage_report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
