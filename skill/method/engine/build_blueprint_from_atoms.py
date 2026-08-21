#!/usr/bin/env python3
"""Turn carved atoms into a compiler blueprint.

The join between the two halves of the work: the carve says what each object
*is*, the mapping table says which private style it becomes, and the compiler
owns the styles themselves. Nothing here invents typography.

Written as a single ordered pass over the carved blocks rather than a walk of
the carve's tree plus its questions. The first version did the latter and
silently dropped 133 source objects — every paragraph inside a question except
its stem, every paragraph inside a text box, every shape. Walking the blocks
makes coverage structural: each source object is either emitted, declared a
carrier whose payload is owned elsewhere, or explicitly excluded, and all three
sets are written out so the decision is auditable rather than implicit.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import re
import sys
import importlib.util
import json
from pathlib import Path
from typing import Any


def load_contract(pipeline: Path):
    """The title-visual-text contract lives with the compiler; the evidence it
    wants is exactly what a reader does when they open the banner and read it,
    recorded so the reading is reproducible instead of asserted."""
    # 契约随包走(skill/vendor/),不再指向生产线的 scripts/formal。
    # 使用方 2026-08-15 定「技能包与样式模板解耦」;之前包借用生产线的文件,
    # 而生产线不知道自己被借用——P6 的 24 个缺口有一半根在这里。
    # 参数 pipeline 保留但不再用于定位:调用方各处仍传它,签名一变全要改。
    vendor = Path(__file__).resolve().parents[1] / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    import semantic_title_visual_text_plugin_contract as contract
    return contract


SUBSCRIPT_OPEN, SUBSCRIPT_SHUT = chr(0xE000), chr(0xE001)
# 反应箭头:哨兵里包着条件文字,交给编排端出 Word 原生公式。和下标同一套办法——
# 切分记录的是「这里是什么」,不是「印成什么样」。
ARROW_OPEN, ARROW_SHUT = chr(0xE002), chr(0xE003)

# 「1．」「A．」「(1)」 are the source's printed markers. Word numbering prints
# its own, so the literal one has to come off the text — but only here, at the
#编排 end: the carve's role detection, option splitting and answer pairing all
# key on those very characters, so they must stay in the atoms.
MARKER_PATTERNS = {
    "CZ_Num_ExerciseDecimal": re.compile(r"^\s*\d{1,2}\s*[．.]\s*"),
    "CZ_Num_ChoiceAlpha": re.compile(r"^\s*[A-H]\s*[．.]\s*"),
    "CZ_Num_SubQuestionParen": re.compile(r"^\s*[（(]\s*\d{1,2}\s*[)）]\s*"),
    "CZ_Num_CircledNote": re.compile(r"^\s*[①-⑳]\s*"),
}


def strip_marker(segments: list[dict[str, Any]], style: str
                 ) -> list[dict[str, Any]]:
    """Take the printed marker off, however many runs it is spread across.

    「1．」 is often two runs — the digit and the full stop — so matching inside
    a single segment silently leaves 「1」 behind next to Word's own 「1.」.
    """
    pattern = MARKER_PATTERNS.get(style)
    if not pattern:
        return segments
    head = ""
    for segment in segments:
        if "text" not in segment:
            continue          # a picture before the marker is not the marker
        # Stop at an answer blank. Every marker pattern ends in \s*, so a blank
        # sitting right after 「（1）」 was read as the spacing behind the marker
        # and stripped along with it — the source's 「（1）＿＿＿：（一氧化碳…」
        # arrived with no line to write on.
        if segment.get("run_type") == "fill_blank":
            break
        head += segment["text"]
        if len(head) > 12:
            break
    found = pattern.match(head)
    if not found:
        return segments
    remaining = found.end()
    out: list[dict[str, Any]] = []
    for segment in segments:
        if remaining <= 0 or "text" not in segment:
            out.append(segment)
            continue
        text = segment["text"]
        if len(text) <= remaining:
            remaining -= len(text)
            continue
        out.append({**segment, "text": text[remaining:]})
        remaining = 0
    return out or [{"text": "", "run_type": "plain"}]


def paints(shading: Any) -> bool:
    """Whether a shading actually puts colour on the page.

    A run shaded FFFFFF is shaded white, which on white paper is not shading at
    all — Word writes it wherever a source has been pasted through a table or a
    highlighter has been cleared. Reading 「有 w:shd」 as 「有底纹」 painted 131
    runs yellow that the source shows plain, including a chapter title.
    """
    if not shading:
        return False
    fill = str(shading if isinstance(shading, str)
               else shading.get("fill") or "").upper()
    return fill not in ("", "AUTO", "FFFFFF")


# 字符级判据里两个与出版社绑定的色值,和一个此前根本不存在的判据。
#
# 这三行原本写死在 run_type_for 里:0070C0 是沪科版的试卷来源标注,1F4E79 是沪科版
# 的例题标签,而斜体没有任何分支——落到 plain。第一本非沪科版的册子(八年级物理)
# 因此静默丢掉了全部字符级语义:源里 254 个斜体 run(物理量符号 f/A/u/v/O/S)、
# 93 个 114599 色 run(栏目标签),成品里是 0 和 0,而九步流程、二十项合规检查、
# 三道门全部通过——没有任何东西响。
#
# 默认值保持沪科版原样,已付印的册子重跑结果不变;册级差异由 schema 的 runMarks 覆盖。
# 与 optionMarkers.chars 同一条治理办法:判据出代码、进 schema。
RUN_MARKS = {
    "sourceTagColor": "0070C0",
    "topicLabelColor": "1F4E79",
}


def configure_run_marks(schema: Any) -> None:
    """把册级的字符判据从 schema 装进来。缺省即沿用沪科版的值。"""
    declared = (getattr(schema, "raw", {}) or {}).get("runMarks") or {}
    for key in RUN_MARKS:
        if declared.get(key):
            RUN_MARKS[key] = str(declared[key]).upper()


# 教材层级的两套词表,同样与出版社绑定。
#
# titleRoleLevels 决定「这个标题角色属于树的哪一层」,repeats() 靠它判断某层标题
# 是否已经排过;pathPrefixLevels 决定「目录名以什么开头就属于哪一层」,tree_of()
# 在注册表没有树字段时靠它回退。两张表原本写死在函数体里,里面是沪科版的
# 主题/专题/课题/跨学科。
#
# 物理册没触发它们,不是因为代码通用,是因为物理是平铺的(每讲独立,没有被反复
# 重述的上层标题)。换一套有层级而词不同的教材——比如人教版的 单元/章/节——
# 这两处会静默失效:.get(role) 返 None,去重分支从不进入,而报告里和「本来就
# 没有重复标题」长得一模一样。这正是登记册 P8「判据恒假」的形状。
#
# 默认值保持沪科版原样,已付印的册子重跑结果不变;册级差异由 schema 的 hierarchy 覆盖。
HIERARCHY: dict[str, Any] = {
    "titleRoleLevels": {"专题标题": "topic", "课题标题": "subject"},
    "pathPrefixLevels": [("主题", "theme"), ("专题复习", "subject"), ("专题", "topic"),
                         ("课题", "subject"), ("跨学科", "subject")],
}


def configure_hierarchy(schema: Any) -> None:
    """把册级的层级词表从 schema 装进来。

    空字典与「没声明」必须分开:没声明 → 沿用沪科版默认;显式声明为空 → 这册
    本来就没有需要去重的上层标题(物理册即如此)。写成 `or 默认` 会把后者悄悄
    变成前者,那是登记册 P1「缺失≠零值」。
    """
    declared = (getattr(schema, "raw", {}) or {}).get("hierarchy")
    if declared is None:
        return
    if "titleRoleLevels" in declared:
        HIERARCHY["titleRoleLevels"] = dict(declared["titleRoleLevels"] or {})
    if "pathPrefixLevels" in declared:
        HIERARCHY["pathPrefixLevels"] = [tuple(pair) for pair
                                         in (declared["pathPrefixLevels"] or [])]


def run_type_for(marks: dict[str, Any], text: str) -> str:
    """Translate a source run's fingerprint into a private run type.

    Order matters: an underlined blank is a fill-in line, an underlined word is
    emphasis.
    """
    vertical = marks.get("vertAlign")
    if vertical == "subscript":
        return "chemical_subscript"
    if vertical == "superscript":
        return "chemical_superscript"
    colour = str(marks.get("color") or "").upper()
    # Before the shading test, deliberately. 「（24-25八年级下·上海虹口·期中）」
    # is where a question came from, not part of it; the source writes all 132
    # of them in bright blue and puts grey shading behind 11, so reading the
    # shading first painted those 11 yellow and left the other 121 as body
    # text — the same label printed two different ways, neither of them right.
    if colour and colour == RUN_MARKS["sourceTagColor"]:
        return "source_tag"
    if marks.get("highlight") or paints(marks.get("shading")):
        return "highlight"
    if marks.get("underline"):
        return "fill_blank" if not text.strip() else "emphasis_underline"
    if marks.get("emphasisMark"):
        return "emphasis_mark"
    if marks.get("bold") and colour and colour == RUN_MARKS["topicLabelColor"]:
        return "topic_label"
    if marks.get("bold"):
        return "emphasis"
    # 斜体排在最后,只接住原本会落到 plain 的那些:加粗+斜体仍是 emphasis,
    # 加粗+斜体+标签色仍是 topic_label——上面两条先走。所以这一条的行为增量
    # 恰好等于「纯斜体不再被当成无装饰」,对已付印的沪科册零影响(那册纯斜体为 0)。
    #
    # 物理量符号与几何点名按排版惯例是斜体:f 焦距、u 物距、v 像距、AO 线段。
    # 丢掉斜体不只是不好看——v 和 v、f 和 f 在正体下与普通字母无从分辨。
    if marks.get("italic"):
        return "italic"
    return "plain"


def runs_of(text: str, marks: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Split a carved string into runs, honouring the sentinels.

    Two kinds travel as sentinels rather than as characters: a chemical
    subscript, and a reaction arrow with its condition. Both are structure, and
    keeping them structural is what lets the compiler print a real subscript
    and a real equation instead of a lookalike.
    """
    kind = run_type_for(marks or {}, text)
    segments: list[dict[str, Any]] = []
    for piece in split_arrows(text):
        if "arrow" in piece:
            segments.append(piece["arrow"])
            continue
        rest = piece["text"]
        while SUBSCRIPT_OPEN in rest:
            head, _, rest = rest.partition(SUBSCRIPT_OPEN)
            body, _, rest = rest.partition(SUBSCRIPT_SHUT)
            if head:
                segments.append({"text": head, "run_type": kind})
            if body:
                segments.append({"text": body, "run_type": "chemical_subscript"})
        if rest:
            segments.append({"text": rest, "run_type": kind})
    return segments or [{"text": "", "run_type": "plain"}]


def split_arrows(text: str) -> list[dict[str, Any]]:
    """Text either side of each reaction arrow, and the arrows themselves."""
    out: list[dict[str, Any]] = []
    rest = text
    while ARROW_OPEN in rest:
        head, _, rest = rest.partition(ARROW_OPEN)
        condition, _, rest = rest.partition(ARROW_SHUT)
        if head:
            out.append({"text": head})
        out.append({"arrow": {"kind": "reaction_arrow",
                              "over": condition, "run_type": "plain"}})
    if rest:
        out.append({"text": rest})
    return out


TYPED_ARROW = re.compile(r"[—–─-]{1,}\s*([^—–─\-→\s]{0,6}?)\s*→")
BARE_ARROW = re.compile(r"→")
TERM_SPLIT = re.compile(r"\s*[+＋]\s*")


# Full-width brackets only. 「五氧化二磷（符号表达式」 must be cut; 「Ca(OH)2」
# must not — the half-width pair belongs to the formula.
SIDE_BREAK = re.compile(r"[，,。；;：:\t（）]")
# The punctuation is required, not optional. Without it 「CaCO3」 loses its C
# to the option-letter rule and the data reads 「aCO3 + HCl」.
LEADING_MARKER = re.compile(r"^\s*(?:[A-DＡ-Ｄ]|\d{1,2}|[①-⑳])\s*[．.、]\s*")
PROSE = re.compile(r"[的是为把被中时后前又并这那]")


def reaction_side(text: str, which: str) -> str:
    """Where the equation stops and the sentence around it starts.

    An option reads 「A．镁+氧气——点燃→氧化镁」 and a fill-in reads 「文字表达式：
    磷+氧气——点燃→五氧化二磷（符号表达式：…）」. Taking everything up to the
    arrow puts the option letter into the first reactant and the following
    parenthesis into the last product — the equation would still print
    correctly and the data would still be wrong, which is the failure mode
    this whole layer exists to prevent.
    """
    piece = (SIDE_BREAK.split(text)[-1] if which == "left"
             else SIDE_BREAK.split(text)[0])
    if which == "left":
        piece = LEADING_MARKER.sub("", piece)
    return piece.strip()


def joins_terms(text: str) -> bool:
    """Whether one side of a bare 「→」 is a 「+」-joined list of substances.

    The book writes 「→」 for three different things: a reaction, a colour
    sequence (「无色→紫色→红色」), and a process chain (「取水→加混凝剂→过滤」).
    Only the reaction joins its terms with 「+」, so this separates all three
    without having to recognise a single substance name.
    """
    return bool(TERM_SPLIT.search(text.strip()))


def terms_of(text: str) -> list[dict[str, Any]]:
    out = []
    for piece in TERM_SPLIT.split(text.strip()):
        piece = piece.strip(" ，,。；;：:】\t").lstrip(" 【")
        state = ("gas" if "↑" in piece else
                 "precipitate" if "↓" in piece else None)
        piece = piece.replace("↑", "").replace("↓", "").strip()
        if not piece:
            continue
        term = {"text": piece,
                "notation": "symbol" if re.search(r"[A-Za-z]", piece) else "word"}
        if state:
            term["state"] = state
        out.append(term)
    return out


def reaction_of(left: str, right: str, condition: str, schema: Any,
                source_text: str) -> dict[str, Any]:
    """The reaction as data. The equation printed in Word is its projection."""
    spec = getattr(schema, "raw", {}).get("reactions", {}) if schema else {}
    conditions = (spec or {}).get("conditions") or {}
    reactants, products = terms_of(left), terms_of(right)
    # Counting settles three states and settles them for good. 「neither」 is a
    # decided negative, not a gap: every one of this book's nine is a
    # distractor in a 化合/分解 question, and a question bank that reads it as
    # 「unknown」 loses exactly the fact the question turns on.
    if len(reactants) > 1 and len(products) == 1:
        kind, basis = "combination", "counts"
    elif len(reactants) == 1 and len(products) > 1:
        kind, basis = "decomposition", "counts"
    else:
        kind, basis = "neither", "counts"
    basic, basic_basis, basic_why = None, "unresolved", None
    basic_extra: dict[str, Any] = {}
    if kind == "neither":
        key = "+".join(t["text"] for t in reactants)
        for entry in ((spec or {}).get("reactionType") or {}) \
                .get("adjudicated", {}).get("entries", []):
            if entry.get("match") == key:
                basic = entry["value"]
                # 依据分三档:解析写死的、按解析给的定义套出来的、只是教学惯例。
                # 混成一个 "adjudicated" 会让「源说的」和「我说的」看起来一样硬。
                basic_basis = entry.get("evidence", "adjudicated")
                basic_why = entry.get("why")
                basic_extra = {k: entry[k] for k in
                               ("document", "locator", "quote", "establishes",
                                "beyondSolution", "needsTeacher")
                               if k in entry}
                break
    notation = ("symbol"
                if any(t["notation"] == "symbol" for t in reactants + products)
                else "word")
    # Where the sentence around the equation has no punctuation before it —
    # 「其反应原理之一是溴化银——光→银+溴」 — the left side keeps prose the
    # equation does not own. Mechanically detectable, not mechanically
    # fixable: flagged for a person rather than trimmed by guesswork.
    # Only Chinese substance names are testable this way. A formula legally
    # runs long and carries brackets and digits — 「Ca(OH)2」 is 7 characters
    # and perfectly correct, and flagging it teaches everyone to ignore flags.
    suspect = [t["text"] for t in reactants + products
               if t["notation"] == "word"
               and (len(t["text"]) > 7 or PROSE.search(t["text"]))]
    return {
        "schemaVersion": "chengziclass.chemical-reaction.v1",
        "reactants": reactants,
        "products": products,
        "boundary": "needs-review" if suspect else "clean",
        **({"boundarySuspect": suspect} if suspect else {}),
        "condition": {"text": condition,
                      "key": conditions.get(condition, "unrecognised"
                                            if condition else "none")},
        "notation": notation,
        "reactionType": kind,
        "reactionTypeBasis": basis,
        "basicType": basic,
        "basicTypeBasis": basic_basis,
        **({"basicTypeWhy": basic_why} if basic_why else {}),
        **({"basicTypeEvidence": basic_extra} if basic_extra else {}),
        **({"basicTypeReview": "needs-teacher"}
           if basic_basis == "unreviewed-guess" else {}),
        "balanced": "not-asserted",
        "sourceText": source_text.strip(),
    }


def extract_reactions(blocks: list[dict[str, Any]], schema: Any) -> list[dict[str, Any]]:
    """Turn every typed reaction arrow into the same structure the shapes give.

    Two forms are converted. A dashed arrow 「——点燃→」 is unambiguous. A bare
    「→」 is converted only when one side is a 「+」-joined term list, which is
    what tells a reaction apart from the colour sequences and process chains
    the book also writes with 「→」. Anything that fails the test is left alone
    and reported, never silently converted or silently dropped.
    """
    dataset: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def convert(block: dict[str, Any]) -> None:
        """Replace每个字符拼的箭头 with the same structural segment a shape gives."""
        out: list[dict[str, Any]] = []
        for segment in block["segments"]:
            text = str(segment.get("text") or "")
            if (segment.get("kind") or not text
                    or segment.get("run_type") not in (None, "plain")):
                out.append(segment)
                continue
            rest = text
            while rest:
                match = TYPED_ARROW.search(rest)
                bare = None if match else BARE_ARROW.search(rest)
                if not match and not bare:
                    break
                start, end = (match or bare).span()
                condition = match.group(1).strip() if match else ""
                left = ("".join(str(s.get("text") or "") for s in out)
                        + rest[:start])
                right = rest[end:] + "".join(
                    str(s.get("text") or "")
                    for s in block["segments"][block["segments"].index(segment) + 1:])
                left_tail = reaction_side(left, "left")
                right_head = reaction_side(right, "right")
                if bare and not (joins_terms(left_tail) or joins_terms(right_head)):
                    skipped.append({"blockId": block["id"], "form": "bare",
                                    "context": (left_tail + "→" + right_head)[:60],
                                    "why": "两侧都不是「+」连接的物质项表,按裁决不转"})
                    out.append({"text": rest[:end], "run_type": "plain"})
                    rest = rest[end:]
                    continue
                if rest[:start]:
                    out.append({"text": rest[:start], "run_type": "plain"})
                arrow = {"kind": "reaction_arrow", "over": condition,
                         "run_type": "plain",
                         "normalisedFrom": "dashed" if match else "bare"}
                out.append(arrow)
                rest = rest[end:]
            if rest:
                out.append({"text": rest,
                            "run_type": segment.get("run_type", "plain")})
        block["segments"] = out or block["segments"]

    for block in blocks:
        if isinstance(block.get("segments"), list):
            convert(block)

    # One pass over the finished segments registers every arrow the same way,
    # whether it arrived as a floating shape, a registered bitmap, or dashes
    # somebody typed. The question bank must not be able to tell them apart.
    for block in blocks:
        segments = block.get("segments")
        if not isinstance(segments, list):
            continue
        for index, segment in enumerate(segments):
            if segment.get("kind") != "reaction_arrow":
                continue
            # An arrow with a condition on it is not automatically a reaction.
            # 「氧气(无色)—101kPa,-183°C→液态氧(淡蓝色)」 is a change of state:
            # the same substance on both sides, no reaction at all. Read as
            # one, it entered the bank as two reactions that do not exist,
            # with the operands run together across the second arrow. Where
            # the arrow is declared, so is what it means.
            kind = str(segment.get("chemistry") or "reaction")
            if kind != "reaction":
                skipped.append({
                    "blockId": block["id"], "form": "arrow", "chemistry": kind,
                    "context": "".join(str(s.get("text") or "") for s in segments)[:60],
                    "why": f"登记为{kind},不是化学反应,不进反应集"})
                continue
            left = "".join(str(s.get("text") or "") for s in segments[:index])
            right = "".join(str(s.get("text") or "") for s in segments[index + 1:])
            left_tail = reaction_side(left, "left")
            right_head = reaction_side(right, "right")
            condition = str(segment.get("over") or "").strip()
            for entry in ((getattr(schema, "raw", {}).get("reactions") or {})
                          .get("boundaryOverrides") or {}).get("entries") or []:
                if entry.get("blockId") != block["id"]:
                    continue
                if entry.get("side") == "reactants" and left_tail == entry.get("from"):
                    left_tail = str(entry["to"])
                elif entry.get("side") == "products" and right_head == entry.get("from"):
                    right_head = str(entry["to"])
            record = reaction_of(left_tail, right_head, condition, schema,
                                 f"{left_tail}—{condition}→{right_head}")
            record["id"] = f"rxn-{len(dataset) + 1:04d}"
            record["blockId"] = block["id"]
            record["blockType"] = block.get("type")
            record["arrowOrigin"] = segment.get("normalisedFrom", "source-shape")
            record["sourceObjectId"] = (block.get("source") or {}).get("objectId")
            segment["reaction"] = record["id"]
            dataset.append(record)
    return {"reactions": dataset, "notConverted": skipped}


def normalise_callout_titles(blocks: list[dict[str, Any]],
                             mapping: dict[str, Any]) -> None:
    """One punctuation habit for the callout heads, not two.

    The source writes 「解题要点」 40 times without a trailing colon and
    「特别提醒：」 25 times with one. Bound into a single volume they sit a few
    pages apart and read as two different kinds of thing. The rule and the
    choice are both in the mapping; this only applies them, and only to a
    title that is the whole line — 「【查阅资料】红磷燃烧生成…」 carries its
    label inline and keeps its punctuation.
    """
    rule = mapping.get("calloutTitleNormalization") or {}
    applies = set(rule.get("appliesTo") or ())
    patterns = [(re.compile(p["match"]), p["replace"])
                for p in rule.get("patterns") or []]
    if not applies or not patterns:
        return
    for block in blocks:
        if str(block.get("type") or "") not in applies:
            continue
        segments = [s for s in block.get("segments") or []
                    if not s.get("kind")]
        if not segments:
            continue
        whole = "".join(str(s.get("text") or "") for s in segments)
        for matcher, replacement in patterns:
            fixed, count = matcher.subn(replacement, whole, count=1)
            if not count or fixed == whole:
                continue
            # Trimmed off the tail, so only the last run can lose characters
            # and every other run keeps its own character style.
            trimmed = len(whole) - len(fixed)
            last = segments[-1]
            text = str(last.get("text") or "")
            last["text"] = text[:len(text) - trimmed] if trimmed <= len(text) else fixed
            break


def normalise_source_tags(blocks: list[dict[str, Any]],
                          mapping: dict[str, Any]) -> int:
    """A citation the source forgot to colour is still a citation.

    「（2025·上海杨浦·三模）」 is recognised by the colour the source paints it
    — 0070C0 — and 132 runs in this volume carry it. One does not: the source
    left that whole line as a single uncoloured run, so the citation printed
    at the stem's own size and colour. That is a defect in the source, of the
    same kind as the doubled full stop, and it is repaired here rather than
    reproduced.

    Written as a pattern, not as a one-off, because unlike a typo this has a
    definite shape: the parenthesis that opens the stem. The count it returns
    is the point — a number that rises means the source's own tagging is
    getting worse, which is worth knowing before the next volume.
    """
    rule = mapping.get("sourceTagNormalization") or {}
    matcher = re.compile(rule["match"]) if rule.get("match") else None
    applies = set(rule.get("appliesTo") or ())
    style = str(rule.get("runStyle") or "source_tag")
    if matcher is None or not applies:
        return 0
    repaired = 0
    for block in blocks:
        if str(block.get("type") or "") not in applies:
            continue
        segments = block.get("segments") or []
        if not segments or segments[0].get("run_type") == style:
            continue
        head = str(segments[0].get("text") or "")
        found = matcher.match(head)
        # Only when the citation lies wholly inside the first run: split one
        # run in two, never merge across runs that carry their own styling.
        if not found or found.end() >= len(head):
            continue
        tag, rest = head[:found.end()], head[found.end():]
        segments[0] = dict(segments[0], text=rest)
        segments.insert(0, {"text": tag, "run_type": style})
        repaired += 1
    return repaired


def apply_text_overrides(blocks: list[dict[str, Any]],
                         mapping: dict[str, Any]) -> None:
    """One-off corrections to source text, each locked to its exact wording.

    The source writes 「…与应用的关系。。」 with two full stops. Reproducing it
    prints the typo; correcting it changes the source's words. So each one is
    adjudicated singly and matched in full — no pattern, nothing that could
    quietly catch a second sentence nobody looked at.
    """
    rule = mapping.get("textOverrides") or {}
    entries = [e for e in rule.get("entries") or [] if e.get("from")]
    if not entries:
        return
    for block in blocks:
        groups = [block.get("segments") or []]
        for row in block.get("rows") or []:
            for cell in row:
                groups.append(cell.get("segments") or [])
                for piece in cell.get("paragraphs") or []:
                    groups.append(piece)
        for segments in groups:
            whole = "".join(str(s.get("text") or "") for s in segments)
            for entry in entries:
                if whole != entry["from"]:
                    continue
                trimmed = len(entry["from"]) - len(entry["to"])
                last = segments[-1]
                text = str(last.get("text") or "")
                if trimmed and trimmed <= len(text):
                    last["text"] = text[:len(text) - trimmed]
                break


def reclaim_condition_carriers(builder: "Builder") -> None:
    """A text box holding nothing but a reaction condition is that arrow's label.

    「通电」 sat in its own text box beside the equation whose arrow already
    carried it, so it printed twice — once on the arrow and once alone at the
    left margin above it. The sweep that decides whether a text box holds new
    content compares against 「what this block already prints」, and a condition
    is not in the block's text; it is attached to the arrow later, so at sweep
    time the box genuinely looked new. Settled here instead, once everything is
    assembled and the conditions exist.

    The box is not dropped — it becomes a carrier, the same standing as any
    other source object whose payload is owned elsewhere, so coverage still
    accounts for it.
    """
    conditions: dict[int, set[str]] = {}
    for index, block in enumerate(builder.blocks):
        found = {str(segment.get(key) or "").strip()
                 for segment in block.get("segments") or []
                 if segment.get("kind") == "reaction_arrow"
                 for key in ("over", "under")}
        if found - {""}:
            conditions[index] = found

    keep: list[dict[str, Any]] = []
    for index, block in enumerate(builder.blocks):
        text = "".join(str(s.get("text") or "") for s in block.get("segments") or []).strip()
        carrier = (
            block.get("type") == "callout_body"
            and "textbox" in str((block.get("source") or {}).get("locator", {}).get("value") or "")
            and text
            and any(text in conditions.get(index + step, set())
                    for step in (-2, -1, 1, 2))
        )
        if not carrier:
            keep.append(block)
            continue
        # An exclusion, not a review-queue carrier: the review queue is for
        # opaque shapes whose payload survives independently, and this box's
        # payload is a condition already set on the arrow. The same branch in
        # sweep_textboxes, for a box whose words the block already prints,
        # files it exactly here.
        builder.exclusions.append({
            "id": f"x{len(builder.exclusions) + 1:04d}",
            "classification": builder.CLASSIFICATIONS["carrier"],
            "review_status": "approved",
            "source": block["source"],
            "owner": {"blockId": block["id"], "blockType": block["type"]},
            "reason": "文本框里只有一个反应条件,该条件已排在相邻公式的箭头上,载体不再单独成段"})
    builder.blocks = keep


def load_engine(path: Path):
    spec = importlib.util.spec_from_file_location("carve_engine", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_index(manifest: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if manifest is None or not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    found: dict[str, dict[str, Any]] = {}
    for document in data.get("sourceDocuments", []):
        for item in document.get("objects", []):
            # Both forms. serializedLocators carry the Choice/Fallback branch
            # spelling; the canonical locator is the merged one
            # (「…/textbox-paragraph[1]」), and indexing only the former leaves
            # every callout paragraph unfindable.
            # 「body/p[1]」 exists in all ten documents, so a locator alone
            # names nothing once the deliverable spans a set: every block
            # bound to the first document's objects and 2878 objects came out
            # unowned. The source hash is part of the key.
            digest = str(item.get("sourceSha256") or "")
            canonical = (item.get("locator") or {}).get("value")
            for locator in ([canonical] if canonical else []) + list(
                    item.get("serializedLocators") or []):
                found.setdefault((digest, locator), item)
    return found


class Builder:
    def __init__(self, mapping: dict[str, Any], media: Path,
                 objects: dict[str, dict[str, Any]],
                 contract: Any = None, body_size_pt: float = 12.0) -> None:
        self.mapping = mapping
        self.source: dict[str, str] = {}
        self.titled = False
        self.media = media
        self.objects = objects
        self.contract = contract
        self.body_size_pt = body_size_pt
        # Which restart point the next numbered block belongs to: options and
        # sub-questions restart per question, exercise numbers per sub-module.
        self.question_key = ""
        self.group_key = ""
        # Per numbering style: which run is open, and what the source printed
        # last in it. 「Restart after every heading」 was the obvious rule and
        # the documents disprove it — of the numbered items standing directly
        # under a heading, 12 restart and 25 carry on. Position in the outline
        # does not decide it. The source's own printed number does.
        self.run_open: dict[str, tuple[str, int]] = {}
        self.source_body_pt = 12.0
        # Where in the textbook's tree the document being read sits, and which
        # level of it has already been printed.
        # 最内层还开着的列表级:1=题号,2=题号以下的任意一级(只缩一次,共用一个缩进)
        self.list_level = 0
        self.tree: dict[str, str] = {}
        self.open_theme = ""
        self.open_topic = ""
        self.open_subject = ""
        self.title_shots: dict[int, str] = {}
        self.blocks: list[dict[str, Any]] = []
        self.exclusions: list[dict[str, Any]] = []
        self.carriers: list[dict[str, Any]] = []
        self.titles: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.claimed: set[str] = set()
        self.skipped: list[str] = []
        self.unbound: list[str] = []
        # 「…/drawing[1]/rId5」 and 「…/alternateContent[1]/shape[1]」 — the
        # relationship id and the wrapper vary, the order within the paragraph
        # does not, so a block's figures are matched positionally against the
        # manifest objects that live under it.
        self.children: dict[tuple[str, str], dict[str, list[Any]]] = {}
        # ★按 objectId 去重。object_index 把同一个对象**按 canonical locator 与
        #   serializedLocators 各建一个键**(Choice/Fallback 两种拼法都要能查到),
        #   于是遍历 objects.items() 时同一个对象会被收进来不止一次。
        #   位置匹配读的是这个列表的第 N 项,重复项一进来,序号就错位:
        #     [第一张, 第一张, 第二张, 第二张] —— order=2 拿到的还是第一张。
        #   **这个重复一直都在**,只是一个段落里只有一张图时永远只取 order 1
        #   (索引 0 正好对),所以从来没有露过头。2026-08-20 A16「力」的 p[56]
        #   一个 drawing 里装了两张图(F₂ 与 F₁,讲力的相互性的一对),它才第一次可见:
        #   两个图块认领了同一个源对象 → 1 处重复归属 + 1 处未归属。
        seen: dict[tuple[str, str, str], set[str]] = {}
        for (digest, locator), item in objects.items():
            body = locator.partition("word/document.xml:")[2]
            for marker, kind in (("/drawing[", "image"),
                                 ("/vml-image[", "image"),
                                 ("/alternateContent[", "shape"),
                                 ("/shape[", "shape")):
                if marker not in body:
                    continue
                if kind == "shape" and "/shape[" not in body:
                    continue
                head = body.split(marker)[0]
                key = (digest, head, kind)
                if item["objectId"] in seen.setdefault(key, set()):
                    break
                seen[key].add(item["objectId"])
                self.children.setdefault((digest, head), {}).setdefault(
                    kind, []).append(item)
                break

    def child_item(self, locator: str, kind: str, order: int) -> dict[str, Any] | None:
        found = (self.children.get((self.source["sha256"], locator))
                 or {}).get(kind) or []
        if order <= len(found):
            return found[order - 1]
        self.unbound.append(f"{locator}#{kind}[{order}]")
        return None

    def full_source(self, item: dict[str, Any]) -> dict[str, Any]:
        """A record the compiler will accept: the frozen source, not just an id."""
        return {"status": "original_word", "frozen": True,
                "path": self.source["path"], "sha256": self.source["sha256"],
                "objectId": item["objectId"],
                "locator": dict(item.get("locator") or {})}

    def claim_child(self, locator: str, kind: str, order: int,
                    how: str, reason: str, evidence_id: str = "") -> str | None:
        item = self.child_item(locator, kind, order)
        if item is None:
            return None
        object_id = item["objectId"]
        if object_id in self.claimed:
            return object_id
        self.claimed.add(object_id)
        if how == "block":
            return object_id
        # opaque-preserve is only valid for a shape that actually carries a
        # textbox or an image; a bare arrow, and any paragraph, must be an
        # explicit exclusion instead of a carrier.
        shape = item.get("shape") or {}
        carrier = (how == "carry" and item.get("kind") == "shape"
                   and (shape.get("hasTextbox") or shape.get("hasImage")))
        target = self.carriers if carrier else self.exclusions
        # Whose figure it is, recorded alongside why it is not printed. The
        # two are different facts: a 源方版式图 has a reason ("rebuilt as a
        # style") and an owner (the banner it decorated), and only the reason
        # was ever written down — so 「every figure belonging to 探新知」 had
        # no answer even though the answer was never in doubt.
        record = {"id": f'{"c" if carrier else "x"}{len(target) + 1:04d}',
                  "source": self.full_source(item),
                  "owner": self.owning_unit(), "reason": reason}
        if carrier:
            record["disposition"] = "opaque-preserve"
            record["review_status"] = "approved"
        else:
            # 「title decoration」 is only available inside a registered title
            # paragraph — a sub-module icon sits under an exercise group title,
            # which is not one, so it is plain source-platform furniture.
            record["classification"] = self.CLASSIFICATIONS[
                "carrier" if kind != "image"
                else ("artwork" if evidence_id else "spacer")]
            record["review_status"] = "approved"
            if evidence_id:
                # The exclusion and the reading of the image must point at each
                # other: an image whose title text we reprint ourselves is only
                # droppable *because* someone read it and said so.
                record["titleVisualTextEvidenceId"] = evidence_id
        target.append(record)
        return object_id

    def object_at(self, locator: str) -> str | None:
        item = self.objects.get(
            (self.source["sha256"], f"word/document.xml:{locator}"))
        if item is None:
            self.unbound.append(locator)
            return None
        return item["objectId"]

    FIGURE_TYPES = ("image", "chart", "vector_figure")

    def owning_unit(self) -> dict[str, Any]:
        """The semantic unit currently open: which item any figure here is part of.

        The book disposes of its figures three ways — printed, rebuilt as a
        style, kept as an opaque carrier — and each way used to record only its
        own reason. 「Why it is not printed」 is not 「whose it is」: nothing said
        which banner a 源方版式图 decorated, so 「show me every figure belonging
        to 探新知」 had no answer. Owner and disposition are separate facts and
        both are now written down.
        """
        for previous in reversed(self.blocks):
            if previous["type"] in self.FIGURE_TYPES:
                continue
            unit = {"blockId": previous["id"], "blockType": previous["type"]}
            text = "".join(str(s.get("text") or "")
                           for s in previous.get("segments") or [])
            if text.strip():
                unit["ownerText"] = text.strip()[:40]
            return unit
        return {"blockId": None, "blockType": None,
                "note": "文档开头,前面没有任何块"}

    def figure_owner(self) -> dict[str, Any]:
        """Which semantic unit a standalone figure belongs to.

        Recorded here rather than inferred at layout time from whatever
        paragraph happens to sit above. Adjacency is right for 251 of this
        book's 259 figures and wrong for the 8 that follow another figure —
        those take the previous figure as their owner, and a figure has no
        indent of its own, so they slid out to the page margin. Walking back
        past the figures finds the item all of them belong to.

        An option's own picture is not this case: it travels inside the option
        paragraph as an inline segment, positioned after its own label, so it
        is already owned by construction.
        """
        return {**self.owning_unit(),
                "resolvedBy": "nearest preceding non-figure block"}

    def provenance(self, locator: str, part: str = "",
                   kind: str = "paragraph") -> dict[str, Any]:
        object_id = self.object_at(locator)
        if object_id:
            self.claimed.add(object_id)
        record: dict[str, Any] = {
            "status": "original_word", "frozen": True,
            "path": self.source["path"], "sha256": self.source["sha256"],
            "locator": {"kind": kind,
                        "value": f"word/document.xml:{locator}"},
            "objectId": object_id}
        if part:
            record["objectPart"] = part
        return record

    ORDINALS = {
        "CZ_Num_ExerciseDecimal": re.compile(r"^\s*(\d{1,2})\s*[．.]"),
        "CZ_Num_ChoiceAlpha": re.compile(r"^\s*([A-H])\s*[．.]"),
        "CZ_Num_SubQuestionParen": re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[)）]"),
        "CZ_Num_CircledNote": re.compile(r"^\s*([①-⑳])"),
    }
    CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

    def source_ordinal(self, style: str,
                       segments: list[dict[str, Any]]) -> int | None:
        """The number the source printed on this line, as an integer."""
        pattern = self.ORDINALS.get(style)
        if not pattern:
            return None
        head = ""
        for segment in segments:
            if "text" not in segment:
                continue
            head += segment["text"]
            if len(head) > 8:
                break
        found = pattern.match(head)
        if not found:
            return None
        value = found.group(1)
        if value in self.CIRCLED:
            return self.CIRCLED.index(value) + 1
        if value.isdigit():
            return int(value)
        return "ABCDEFGH".index(value) + 1

    def numbering_run(self, style: str, locator: str,
                      segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Which run this item joins, and what number that run starts at.

        Word regenerates the marker, and the point of regenerating it is to get
        real numbering rather than characters — not to renumber the book. So the
        run follows the source: an item whose printed number is one more than
        the last continues the run; anything else opens a new one starting at
        the number the source shows. Measured against Word's own export, 137 of
        1438 numbers disagreed with the source before this.
        """
        stamp = self.source["sha256"][:12]
        ordinal = self.source_ordinal(style, segments)
        key, previous = self.run_open.get(style, ("", None))
        if ordinal is None:
            if not key:
                key = f"{stamp}:{locator}"
                self.run_open[style] = (key, None)
            return {"restart": key}
        if not key or previous is None or ordinal != previous + 1:
            key = f"{stamp}:{locator}"
            self.run_open[style] = (key, ordinal)
            return {"restart": key, "start": ordinal}
        self.run_open[style] = (key, ordinal)
        return {"restart": key}

    def add(self, style: str, locator: str, segments: list[dict[str, Any]],
            part: str = "", numbering: dict[str, Any] | None = None,
            also: list[tuple[str, str]] | None = None,
            **extra: Any) -> None:
        if numbering:
            numbering = {**numbering,
                         **self.numbering_run(str(numbering.get("style") or ""),
                                              locator, segments)}
            segments = strip_marker(segments, str(numbering.get("style") or ""))
            extra["numbering"] = numbering
        # A row of options laid out per question can draw on more than one
        # source paragraph. The coverage contract already lets several blocks
        # share one object by taking disjoint objectParts; this is the same
        # idea the other way round, and it keeps the union disjoint.
        extra_sources = [self.provenance(where, which) for where, which in (also or [])]
        if extra_sources:
            extra["additionalSources"] = extra_sources
        self.blocks.append({
            "id": f"b{len(self.blocks) + 1:04d}",
            "type": style,
            "segments": segments,
            "source": self.provenance(locator, part),
            "review_status": "approved",
            **extra})
        # Exactly one canonical visible title per source document: the source
        # title alias is about the document's own title, not about every
        # heading in it. The rest are our headings, carrying no source alias.
        if style in self.TITLE_TYPES and not self.titled:
            self.titled = True
            self.register_title(self.blocks[-1]["id"], self.blocks[-1]["source"])
            self.titled_block = self.blocks[-1]["id"]

    CLASSIFICATIONS = {
        "artwork": "non_instructional_title_decoration",
        "carrier": "non_instructional_shape_carrier",
        "spacer": "source_platform_metadata",
    }

    def exclude(self, locator: str, reason: str,
                classification: str = "spacer") -> None:
        item = self.objects.get(
            (self.source["sha256"], f"word/document.xml:{locator}"))
        if item is None or item["objectId"] in self.claimed:
            return
        object_id = item["objectId"]
        self.claimed.add(object_id)
        self.exclusions.append({
            "id": f"x{len(self.exclusions) + 1:04d}",
            "classification": self.CLASSIFICATIONS[classification],
            "review_status": "approved",
            "source": self.full_source(item),
            "owner": self.owning_unit(),
            "reason": reason})

    def carry(self, locator: str, reason: str) -> None:
        """A compatibility carrier whose payload other blocks already own."""
        item = self.objects.get(
            (self.source["sha256"], f"word/document.xml:{locator}"))
        if item is None or item["objectId"] in self.claimed:
            return
        self.claimed.add(item["objectId"])
        self.carriers.append({
            "id": f"c{len(self.carriers) + 1:04d}",
            "disposition": "opaque-preserve",
            "review_status": "approved",
            "source": self.full_source(item),
            "owner": self.owning_unit(),
            "reason": reason})

    def image_segment(self, block: dict[str, Any], order: int,
                      figure: dict[str, Any]) -> dict[str, Any]:
        object_id = self.claim_child(block["locator"], "image", order,
                                     "block", "")
        # A metafile cannot be placed — python-docx refuses the part. Where the
        # media library has a rendered copy under the same content hash, that
        # is the one to use; render_wmf.py puts it there.
        name = figure.get("render") or figure["file"]
        if str(name).lower().endswith((".wmf", ".emf")):
            drawn = self.media / (Path(name).stem + ".png")
            if drawn.is_file():
                name = drawn.name
        segment: dict[str, Any] = {
            "kind": "inline_image",
            "path": str((self.media / name).resolve()),
            "source": {"status": "original_word", "frozen": True,
                       "path": self.source["path"],
                       "sha256": self.source["sha256"],
                       "objectId": object_id,
                       "locator": {"kind": "image", "value": object_id or ""}}}
        if figure.get("widthEmu"):
            # The source's absolute size, rescaled by how our body type
            # compares with the source's. Without this every figure came out at
            # the compiler's 8mm default — a 146mm apparatus drawn 18× too
            # small — and the source's own proportions were thrown away.
            # Against the type the figure sits beside: the commonest size in
            # its own paragraph, by character count. The document-wide figure
            # is only the fallback for a paragraph that has no text of its own.
            ratio = self.body_size_pt / (block.get("sourceSizePt")
                                         or self.source_body_pt or 12.0)
            segment["widthEmu"] = figure["widthEmu"]
            segment["heightEmu"] = figure.get("heightEmu")
            segment["sourceSizePt"] = block.get("sourceSizePt")
            segment["width_mm"] = round(int(figure["widthEmu"]) / 36000 * ratio, 2)
            if figure.get("heightEmu"):
                segment["height_mm"] = round(
                    int(figure["heightEmu"]) / 36000 * ratio, 2)
        if figure.get("crop"):
            segment["crop"] = figure["crop"]
        if figure.get("anchor"):
            # A floating figure keeps where it was placed and how the text
            # flowed around it; dropped to inline, the page it belonged to
            # reflows and the figure lands somewhere else entirely.
            segment["anchor"] = figure["anchor"]
        return segment

    def sweep_textboxes(self, locator: str, printed: str) -> None:
        """Text boxes hanging off a block that no callout branch handled.

        A table cell can hold one, and so can an ordinary paragraph whose role
        has no callout body. Whatever text the block already prints is claimed
        as covered; anything else becomes its own body block rather than being
        dropped.

        「Already prints」 has to mean everything, and a reaction condition
        lives on the arrow segment rather than in the block's text. It is not
        available yet at this point — conditions are attached later — so that
        half is settled afterwards, in reclaim_condition_carriers.
        """
        digest = self.source["sha256"]

        for (source, key), item in self.objects.items():
            if source != digest or item.get("kind") != "textbox-paragraph":
                continue
            body = key.partition("word/document.xml:")[2]
            if body.split("/alternateContent")[0] != locator:
                continue
            if item["objectId"] in self.claimed:
                continue
            text = str(item.get("text") or "").strip()
            self.claimed.add(item["objectId"])
            if text and text not in printed:
                self.blocks.append({
                    "id": f"b{len(self.blocks) + 1:04d}",
                    "type": "callout_body",
                    "segments": runs_of(text),
                    "source": self.full_source(item),
                    "review_status": "approved"})
            else:
                # opaque-preserve is for shapes; a text-box paragraph whose
                # words the block already prints is an exclusion.
                self.exclusions.append({
                    "id": f"x{len(self.exclusions) + 1:04d}",
                    "classification": self.CLASSIFICATIONS["carrier"],
                    "review_status": "approved",
                    "source": self.full_source(item),
                    "owner": self.owning_unit(),
                    "reason": "文本框内容已由所在块的正文承担"})

    def sweep_shapes(self, locator: str) -> None:
        """A VML picture is two objects to the manifest: the image and the
        shape that frames it. Claiming only the image leaves every frame
        unowned — and a frame carrying an image is exactly what opaque-preserve
        is for."""
        found = (self.children.get((self.source["sha256"], locator))
                 or {}).get("shape") or []
        for order in range(1, len(found) + 1):
            self.claim_child(locator, "shape", order, "carry",
                             "承载图片的外框,图片本身已单独归属")

    def stream_segments(self, block: dict[str, Any], keep: bool) -> list[dict[str, Any]]:
        """Segments in the source's own order.

        Appending the pictures after the text put every inline figure at the
        end of its paragraph — 「汞+氧气氧化汞 →」 instead of
        「汞+氧气 →氧化汞」. Claiming still runs over the whole figure list,
        because a transcribed object leaves the stream (its text took its
        place) but still owns a source object that somebody must account for.
        """
        placed = {id(x): x for x in self.figures(block, keep=keep)}
        by_file = {}
        for segment in placed.values():
            by_file.setdefault(Path(segment["path"]).name, []).append(segment)
        out: list[dict[str, Any]] = []
        for piece in block.get("stream") or []:
            if "text" in piece:
                out.extend(runs_of(piece["text"], piece.get("marks")))
                continue
            figure = piece["figure"]
            name = figure.get("render") or figure.get("file") or ""
            queue = by_file.get(name) or []
            if queue:
                out.append(queue.pop(0))
        # Anything the stream did not carry (a figure with no offset) keeps its
        # place at the end rather than vanishing.
        for queue in by_file.values():
            out.extend(queue)
        return out

    def figures(self, block: dict[str, Any], keep: bool,
                skip: set[int] | None = None,
                evidence: dict[int, str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        images = shapes = 0
        for order, figure in enumerate(block.get("imageRefs") or [], start=1):
            if skip and order in skip:
                images += 1
                continue
            if figure.get("kind") == "transcribed":
                images = images + 1
                self.claim_child(block["locator"], "image", images, "exclude",
                                 f'内容已转写为正文文字：{figure.get("text", "")}')
            elif figure.get("kind") != "picture" or not figure.get("file"):
                shapes = shapes + 1
                self.claim_child(block["locator"], "shape", shapes, "carry",
                                 "矢量图形或反应箭头,其文字已回填进正文")
            elif not keep:
                images = images + 1
                self.claim_child(block["locator"], "image", images, "exclude",
                                 "源方版式图,按语义位重建而不重印",
                                 (evidence or self.title_shots
                                  or {}).get(images, ""))
            else:
                images = images + 1
                out.append(self.image_segment(block, images, figure))
        self.sweep_shapes(block["locator"])
        return out

    TITLE_TYPES = {"chapter", "heading1", "heading2", "heading3"}

    def register_title(self, block_id: str, source: dict[str, Any]) -> None:
        """A source title is carried as an alias of the block that renders it:
        the compiler prints our own heading, and the record says which source
        paragraph that heading stands for."""
        self.titles.append({
            "id": f"t{len(self.titles) + 1:04d}",
            "handling": "alias-only",
            "canonicalBlockId": block_id,
            "source": {k: v for k, v in source.items() if k != "objectPart"}})

    def title_evidence(self, block: dict[str, Any], entry: dict[str, Any]) -> None:
        # Evidence binds to a canonical title block, and only chapter/heading1-3
        # count as one. A sub-module heading is an exercise group title, so its
        # icon is plain decoration with nothing to bind to.
        if (self.contract is None or entry["style"] not in self.TITLE_TYPES
                or getattr(self, "titled_block", None) != self.blocks[-1]["id"]):
            return {}
        seen: dict[int, str] = {}
        slots = entry.get("slots") or {}
        spoken = " ".join(part for part in (slots.get("label") or block.get("label"),
                                            slots.get("caption") or block.get("caption"))
                          if part)
        images = 0
        for figure in block.get("imageRefs") or []:
            if figure.get("kind") == "transcribed":
                images = images + 1
                self.claim_child(block["locator"], "image", images, "exclude",
                                 f'内容已转写为正文文字：{figure.get("text", "")}')
            elif figure.get("kind") != "picture" or not figure.get("file"):
                continue
            images += 1
            item = self.child_item(block["locator"], "image", images)
            if item is None:
                continue
            locator = (item.get("locator") or {}).get("value") or ""
            media = hashlib.sha256(
                (self.media / figure["file"]).read_bytes()).hexdigest()
            fingerprint = self.contract.build_input_fingerprint(
                source_sha256=self.source["sha256"], source_locator=locator,
                media_sha256=media)
            carries = bool(spoken)
            self.evidence.append({
                "id": f"e{len(self.evidence) + 1:04d}",
                "canonicalBlockId": self.blocks[-1]["id"],
                "schemaVersion": "chengziclass.source-title-visual-text-evidence.v1",
                "semanticStage": "semantic-tagging",
                "review_status": "approved",
                "resolutionMethod": "vision-model",
                "resolverId": "chengziclass.banner-slot-transcription",
                "resolverVersion": "2026-08-06",
                "decision": "title_text_carrier" if carries else "decoration_only",
                "containsTitleText": carries,
                "titleText": spoken,
                "mediaSha256": media,
                "inputFingerprint": fingerprint,
                "cacheKey": self.contract.build_cache_key(
                    input_fingerprint=fingerprint,
                    resolver_id="chengziclass.banner-slot-transcription",
                    resolver_version="2026-08-06"),
                "source": {"status": "original_word", "frozen": True,
                           "path": self.source["path"],
                           "sha256": self.source["sha256"],
                           "objectId": item["objectId"],
                           "locator": {"kind": "image", "value": locator}}})
            seen[images] = self.evidence[-1]["id"]
        return seen

    def theme(self, name: str) -> None:
        """The outermost level of the textbook's tree, which no file states.

        主题 is a directory, not a paragraph: every one of the ten documents
        opens at 专题, so the level above it exists only in the tree. It is
        written once per 主题, against the whole document rather than any
        paragraph in it — the same aggregate provenance a chapter divider
        uses.
        """
        self.blocks.append({
            "id": f"b{len(self.blocks) + 1:04d}",
            "type": "chapter",
            "segments": runs_of(self.normalize_title(name)),
            "source": {"status": "original_word", "frozen": True,
                       "path": self.source["path"],
                       "sha256": self.source["sha256"],
                       "locator": {"kind": "range",
                                   "value": "word/document.xml:body"}},
            "review_status": "approved"})

    def repeats(self, block: dict[str, Any], role: str) -> bool:
        """Whether this title has already been set for the level it names.

        Every file in a 专题 restates the 专题 and the 课题 it belongs to, so
        compiling ten of them printed 专题3 six times and 课题2 three times,
        each one opening what looked like a new section. The tree says how many
        there are: one. The first file to reach a level prints it; the rest
        hand their title paragraph over as a repeat.
        """
        level = HIERARCHY["titleRoleLevels"].get(role)
        if not level:
            return False
        current = self.tree.get(level)
        if current and current == getattr(self, f"open_{level}", None):
            self.exclude(block["locator"],
                         f"与前一讲义同属「{current}」,该层标题只排一次", "spacer")
            self.stream_segments(block, keep=False)
            return True
        setattr(self, f"open_{level}", current)
        return False

    def normalize_title(self, text: str) -> str:
        """Write a structural title the one way, whatever the source did.

        The source disagrees with itself: 「课题01 水的性质」 next to 「课题1
        空气的成分」, and four of the six files that name 专题3 leave out the
        space after the number. Compiled into one handout those differences sit
        side by side and read as different levels rather than the same one. The
        patterns are data; the source paragraph and its own wording stay in the
        provenance, and the file on disk is untouched.
        """
        # A one-off rewrite is consulted first and is not a pattern: it names
        # one title and rewrites that title. Kept apart from the normalisation
        # patterns on purpose — those change the layout of every title alike,
        # this changes the words of exactly one, and the two must never be
        # mistaken for each other when someone asks what the source said.
        for entry in (self.mapping.get("titleOverrides") or {}).get("entries") or []:
            if text.strip() == entry["from"]:
                return str(entry["to"])
        rules = (self.mapping.get("titleNormalization") or {}).get("patterns") or []
        for rule in rules:
            fixed, count = re.subn(rule["match"], rule["replace"], text, count=1)
            if count:
                return fixed.strip()
        return text

    def heading(self, block: dict[str, Any], entry: dict[str, Any]) -> None:
        slots = entry.get("slots") or {}
        label = slots.get("label") or block.get("label") or block["text"]
        if entry.get("normalizeTitle"):
            label = self.normalize_title(label)
        segments = runs_of(label)
        caption = slots.get("caption") or block.get("caption")
        if caption:
            segments.append({"text": f"　{caption}",
                             "run_type": entry.get("captionRunStyle", "emphasis")})
        self.add(entry["style"], block["locator"], segments)
        self.title_shots = self.title_evidence(block, entry)
        self.stream_segments(block, keep=False)
        self.title_shots = {}

    def callout(self, block: dict[str, Any], entry: dict[str, Any]) -> None:
        body_style = entry.get("bodyStyle", entry["style"])
        # Pictures on the host paragraph — the icon beside 「效果检测」 in the
        # review handouts. They were built and then dropped on the floor, so
        # seven of them ended up owned by nothing and the coverage gate
        # stopped the build. They belong at the head of the box, where the
        # source draws them.
        icons = self.figures(block, keep=True)
        for item in block["textboxParagraphs"]:
            locator = (f'{block["locator"]}/alternateContent[1]'
                       f'/textbox-paragraph[{item["index"]}]')
            style = entry["style"] if item["index"] == 1 else body_style
            segments: list[dict[str, Any]] = []
            for run in item.get("runs") or []:
                segments.extend(runs_of(run["text"], run.get("marks")))
            segments = (segments or runs_of(item["text"]))
            if icons and item["index"] == 1:
                segments = icons + segments
            self.add(style, locator, segments)
        self.claim_child(block["locator"], "shape", 1, "carry",
                         "提示框外框,框内每段各自成块")
        self.exclude(block["locator"],
                     "承载提示框的段落自身无文字,内容由框内各段承担", "carrier")

    def cell_edges(self, cell: dict[str, Any],
                   table: dict[str, Any]) -> dict[str, str] | None:
        """The border relations our own table style cannot state.

        Our style draws every edge of every cell the same way, which is right
        for 300 of the 315 bordered cells here — they are single-line like the
        table itself, so copying their 6/8 eighth-points would only pull the
        source's line weight in behind our own. What it cannot state is where
        the source departed from that: 15 edges drawn dotted, and 3 diagonals
        splitting a corner header. Those are distinctions a reader sees, so
        they travel as a style token per edge and take our weight and colour
        on the way out.
        """
        borders = cell.get("borders") or {}
        if not borders:
            return None
        common = ((table.get("borders") or {}).get("top") or {}).get("style") or "single"
        edges = {edge: (spec.get("style") or "single")
                 for edge, spec in borders.items()
                 if edge in ("tl2br", "tr2bl")
                 or (spec.get("style") or "single") != common}
        return edges or None

    def row_properties(self, row: dict[str, Any]) -> dict[str, Any]:
        """A row's height as a proportion, not as a measurement.

        73 of the 191 rows here carry a height: they are answer boxes, and one
        of them is 1882 twips because a student has to write four lines in it.
        Dropping them made every box one line tall. Copying the twips instead
        would tie the writing space to the source's type size, so it rides the
        same ratio the figures do — the box keeps its proportion to the text
        that surrounds it. The rule Word was given travels verbatim: hRule
        decides whether that number is a minimum or an exact height, and
        rewriting it would change what the number means.
        """
        got: dict[str, Any] = {}
        height = row.get("height")
        if height:
            ratio = self.body_size_pt / (self.source_body_pt or 12.0)
            got["heightTwips"] = max(1, round(int(height) * ratio))
            if row.get("heightRule"):
                got["heightRule"] = row["heightRule"]
        if row.get("isHeader"):
            got["isHeader"] = True
        if row.get("cantSplit"):
            got["cantSplit"] = True
        return got

    def table(self, block: dict[str, Any]) -> None:
        table = block["table"]
        figures = list(block.get("imageRefs") or [])
        images = shapes = 0
        rows = []
        row_properties = []
        for row in table["rows"]:
            row_properties.append(self.row_properties(row))
            cells = []
            for cell in row["cells"]:
                # A cell's paragraphs stay paragraphs. Flattened into one run
                # of segments they printed as one run-on line: 「1．…性质。2．
                # 结合实例…3．了解…」 where the source had three numbered lines.
                # 39 of 552 cells hold more than one paragraph.
                paragraphs: list[list[dict[str, Any]]] = []
                segments: list[dict[str, Any]] = []
                for paragraph in cell["paragraphs"]:
                    piece: list[dict[str, Any]] = []
                    marker = paragraph.get("list")
                    if marker:
                        piece.extend(runs_of(marker["marker"] + " "))
                    for run in paragraph.get("runs") or []:
                        piece.extend(runs_of(run["text"], run.get("marks")))
                    if not paragraph.get("runs") and paragraph["text"]:
                        piece.extend(runs_of(paragraph["text"]))
                    if piece:
                        paragraphs.append(piece)
                    segments.extend(piece)
                # A cell's figure belongs in the cell, not in a block beside the
                # table: emitting it separately made the image object owned
                # twice, once by that block and once by its own segment.
                for figure in cell.get("figures") or []:
                    if figure.get("kind") == "picture" and figure.get("file"):
                        images += 1
                        segments.append(self.image_segment(block, images, figure))
                    else:
                        shapes += 1
                        self.claim_child(block["locator"], "shape", shapes,
                                         "carry", "表格内矢量图形")
                # A merged-away or genuinely blank cell still has to say so;
                # an empty segment list reads to the compiler as a mistake.
                # An absent vAlign is not an absent decision: Word reads it as
                # top, and 168 of the 686 cells rely on that. Sending nothing
                # let the compiler centre them all, which floated every short
                # label off the line its row was written on.
                entry = {"segments": segments or [{"text": "",
                                                   "run_type": "plain"}],
                         # The flat list stays: coverage, the verifier and the
                         # declaration gate all read it, and it is exactly the
                         # concatenation of these.
                         **({"paragraphs": paragraphs} if len(paragraphs) > 1 else {}),
                         "gridSpan": cell["gridSpan"],
                         "vMerge": cell["vMerge"],
                         "vAlign": cell["vAlign"] or "top"}
                alignment = next((p.get("alignment") for p in cell["paragraphs"]
                                  if p.get("alignment")), None)
                if alignment:
                    entry["alignment"] = alignment
                edges = self.cell_edges(cell, table)
                if edges:
                    entry["edges"] = edges
                cells.append(entry)
            rows.append(cells)
        self.add("table", block["locator"], [], rows=rows, grid=table["grid"],
                 rowProperties=row_properties, tableStyle=table.get("style"))
        self.sweep_textboxes(block["locator"], block["text"])
        # Sweep the manifest's own image children, not the carve's figure list:
        # that list also holds shapes and arrows, so asking for 「image[5]」 in
        # a table with three images named a child that does not exist and left
        # three locators unbound at every build.
        listed = (self.children.get((self.source["sha256"], block["locator"]))
                  or {}).get("image") or []
        for order in range(1, len(listed) + 1):
            self.claim_child(block["locator"], "image", order, "exclude",
                             "表格内未被任何单元格认领的图")
        self.sweep_shapes(block["locator"])

    def slice_stream(self, block: dict[str, Any], start: int,
                     end: int) -> list[dict[str, Any]]:
        """The runs covering a character range, marks intact.

        An option is cut out of its row by character offsets; slicing the flat
        text loses the fingerprints, so the subscripts and fill-in lines inside
        the 1367 options compiled as plain text.
        """
        out: list[dict[str, Any]] = []
        cursor = 0
        for piece in block.get("stream") or []:
            if "text" not in piece:
                continue
            text = piece["text"]
            head, tail = cursor, cursor + len(text)
            cursor = tail
            if tail <= start or head >= end:
                continue
            body = text[max(0, start - head):min(len(text), end - head)]
            if body:
                out.extend(runs_of(body, piece.get("marks")))
        return out

    def options(self, block: dict[str, Any],
                found: list[dict[str, Any]],
                rows: list[dict[str, Any]] | None = None) -> set[int]:
        """One source row, one paragraph — the way the source wrote it.

        The options are atomised for content and reassembled for layout. The
        source packs 1367 options onto 928 rows, four abreast where they are
        short and one to a line where they are long, and emitting a paragraph
        per option flattened all of that into one column and cost about 13
        pages. The row grouping travels; the column positions come from the
        registry, as tab stops on a style, never as spaces.

        The labels are text here rather than Word numbering. Word generates one
        number per paragraph, so a row carrying four options cannot carry four
        automatic labels — and A/B/C/D restart at every question, so nothing
        about assembling files across lessons depends on them. Question and
        sub-question numbers stay real, which is where it does.
        """
        taken: dict[str, set[int]] = {}
        pieces: list[list[dict[str, Any]]] = []
        for option in found:
            # A question's options are gathered across however many rows the
            # source typed them on, and an option's run offsets and figure
            # ordinals index its own row — not the first row of the group.
            row = option.get("sourceRow") or block
            span = option.get("range")
            body = (self.slice_stream(row, span[0], span[1]) if span
                    else runs_of(option["text"]))
            piece = runs_of(f'{option["label"]}．') + body
            for order in option.get("figureOrders") or []:
                figure = row["imageRefs"][order - 1]
                if figure.get("kind") == "transcribed":
                    # Already in the option's own text; placing it again would
                    # print the formula twice, once as an unplaceable WMF.
                    self.claim_child(row["locator"], "image", order, "exclude",
                                     f'内容已转写为正文文字：{figure.get("text", "")}')
                    taken.setdefault(row["locator"], set()).add(order)
                elif figure.get("file"):
                    # The option's own label, written onto its picture. The
                    # option's text has carried an objectPart all along; its
                    # picture had none, so 「which option is the apparatus
                    # diagram」 could only be answered by reading what sits to
                    # the left of it on the page. Same field, same vocabulary.
                    piece.append({**self.image_segment(row, order, figure),
                                  "objectPart": option["label"]})
                    taken.setdefault(row["locator"], set()).add(order)
            pieces.append(piece)
        if not pieces:
            return taken

        # The whole question's options, laid out by us rather than by the row
        # breaks the source happened to type. Three rules, in order: every
        # option in a row is the same width, a row holds as many as fit, and
        # every row in one question holds the same number. The third is why
        # the count must divide evenly — 「A B / C / D」 was what preserving the
        # source's grouping produced, and it reads as three different kinds of
        # option.
        columns = self.choice_columns(pieces)
        if columns > 1:
            per_row = columns
            segments: list[dict[str, Any]] = []
            emitted = 0
            def flush(start: int, stop: int,
                      body: list[dict[str, Any]]) -> None:
                members = found[start:stop]
                by_row: dict[str, list[str]] = {}
                for option in members:
                    row = option.get("sourceRow") or block
                    by_row.setdefault(row["locator"], []).append(option["label"])
                first = (members[0].get("sourceRow") or block)["locator"]
                self.add("choice", first, body, part="".join(by_row[first]),
                         also=[(where, "".join(labels))
                               for where, labels in by_row.items()
                               if where != first],
                         columns=per_row,
                         optionLabels=[o["label"] for o in members])
            for index, piece in enumerate(pieces):
                if index and index % per_row == 0:
                    flush(emitted, index, segments)
                    emitted = index
                    segments = []
                if segments:
                    segments.append({"text": "\t", "run_type": "plain"})
                segments.extend(piece)
            if segments:
                flush(emitted, len(pieces), segments)
        else:
            # Side by side they would not fit, so each takes its own line —
            # which is what the source's own row does once Word wraps it, at no
            # extra length.
            for option, piece in zip(found, pieces):
                # Its own row, not the first row of the group. Anchoring every
                # option to the group's first paragraph left the rows behind it
                # claimed by nobody — 489 source paragraphs unowned, which is
                # the coverage gate doing exactly its job.
                row = option.get("sourceRow") or block
                self.add("choice", row["locator"], piece,
                         part=option["label"], columns=1)
        return taken

    def choice_columns(self, pieces: list[list[dict[str, Any]]]) -> int:
        """How many columns this row can actually hold.

        「adaptive-4-2-1-columns」 is a rule about width, not about how many
        options happen to share a row: four options set four abreast is only
        right while all four fit. An option wider than its column pushes the
        next one to the following tab stop, so one long option would knock the
        whole row out of alignment. 30 of the 304 multi-option rows here are
        like that.
        """
        spec = (self.mapping.get("typography") or {}).get("choiceColumns") or {}
        # A column count that does not divide the option count would leave a
        # short last row, and a question whose rows hold different numbers of
        # options reads as though the options were of different kinds.
        allowed = [n for n in (spec.get("allowed") or [4, 3, 2])
                   if n > 1 and len(pieces) % n == 0]
        if len(pieces) < 2 or not allowed:
            return 1
        char = spec.get("charWidthDxa") or {}
        full = float(char.get("fullWidth", 240))
        half = float(char.get("halfWidth", 120))
        # Every option carries exactly one capital — its own label — and a
        # capital in Times New Roman is about 0.7em, not the 0.5em every
        # non-CJK character was being given. It is the one place the estimate
        # was wrong the same way every time, which is what a blanket 5% margin
        # was really covering for.
        upper = float(char.get("upperCaseLatin", half))
        available = (float(spec.get("bodyWidthDxa", 9411))
                     - float(spec.get("leftIndentDxa", 720)))
        def width(piece: list[dict[str, Any]]) -> float:
            # A picture-only option is not a narrow option. One row here is
            # four apparatus diagrams 80mm wide with nothing but 「A．」 beside
            # them; measuring the text alone called it the narrowest kind of
            # row and set it four abreast, three times the page.
            total = 0.0
            for segment in piece:
                total += sum(full if ord(letter) > 0x2E80
                             else upper if letter.isupper() and letter.isascii()
                             else half
                             for letter in str(segment.get("text") or ""))
                if segment.get("width_mm"):
                    total += float(segment["width_mm"]) * 56.6929134
            return total

        widest = max(width(piece) for piece in pieces)
        # As many per row as fit, largest first. The previous rule took the
        # narrowest band that could hold one row, which is the right question
        # when the source's row breaks are being kept and the wrong one when
        # the whole question is being laid out: asked of four options it
        # demanded all four abreast and fell to one per line when they did not
        # fit, never trying two — 85 questions that had been two-by-two came
        # out one-by-one.
        #
        # The widest option decides for the whole question. Columns are equal
        # and every row holds the same number, so a single long option is
        # enough to widen every column in the question, which is the price of
        # rows that read alike.
        for candidate in sorted(allowed, reverse=True):
            if widest <= available / candidate * float(spec.get("fitRatio", 0.95)):
                return candidate
        return 1


def tree_of(document: dict[str, Any]) -> dict[str, str]:
    """Where a file sits in the textbook, read off the directories holding it.

    「主题二 常见的物质（上）/ 专题3 空气、氧气、二氧化碳 / 课题1 空气的成分」
    is the structure, and it is stated once — in the tree — rather than ten
    times in ten documents that each restate their own 专题 and 课题 with
    slightly different spacing. The directory names are the canonical ones.
    """
    found = {level: str(document[key])
             for level, key in (("theme", "theme"), ("topic", "topic"),
                                ("subject", "lesson"), ("unit", "unit"),
                                ("period", "period"))
             if document.get(key)}
    if "theme" in found:
        return found
    # Older registries carry no tree fields; the directories still do.
    for part in Path(document["path"]).parts:
        for prefix, level in HIERARCHY["pathPrefixLevels"]:
            if part.startswith(prefix) and level not in found:
                found[level] = part
    return found


def build(lessons: list[str], engine, schema, mapping: dict[str, Any],
          registry: dict[str, Any], media: Path,
          objects: dict[str, dict[str, Any]],
          contract: Any = None) -> dict[str, Any]:
    """One blueprint over every lesson: the deliverable is a single file.

    Each source document keeps its own frozen provenance and its own single
    canonical title; the blocks run in reading order across the set.
    """
    chosen = [d for d in registry["documents"] if d["role"] == "original_word"
              and (not lessons or d["lesson"] in lessons)]
    chosen.sort(key=lambda d: (d.get("order") or 0, d["lesson"], d.get("period") or ""))
    builder = Builder(mapping, media, objects, contract,
                      mapping.get("typography", {}).get("bodySizePt", 12.0))
    documents: list[dict[str, Any]] = []

    for document in chosen:
        path = Path(document["physicalPath"])
        builder.source = {"path": str(path.resolve()), "sha256": digest(path)}
        builder.titled = False
        builder.titled_block = None
        builder.tree = tree_of(document)
        theme = builder.tree.get("theme")
        if theme and theme != builder.open_theme:
            builder.open_theme = theme
            builder.theme(theme)
        documents.append({"path": builder.source["path"],
                          "sha256": builder.source["sha256"],
                          "role": "original_word", "order": len(documents) + 1})
        diagnostics = engine.Diagnostics(schema.diagnostics)
        # Extract the pictures here rather than relying on an earlier carve
        # run having done it. Adding five documents to the set left their
        # images unextracted and the build stopped on a missing file — a step
        # that has to be remembered separately is a step that gets forgotten.
        blocks = engine.read_blocks(document["physicalPath"], schema, diagnostics,
                                    engine.MediaLibrary(media))
        carved = engine.carve(blocks, schema, diagnostics)
        # The commonest size among the running text, not among every block.
        # Headings, banners and spacers have sizes of their own; letting them
        # into the count skews the very number the figures are scaled against.
        sizes = Counter(
            block["sourceSizePt"] for block in blocks
            if block.get("sourceSizePt") and block.get("kind") == "body"
            and (block.get("text") or "").strip())
        builder.source_body_pt = sizes.most_common(1)[0][0] if sizes else 12.0
        emit_document(builder, mapping, blocks, carved, schema)

    apply_text_overrides(builder.blocks, mapping)
    reclaim_condition_carriers(builder)
    normalise_callout_titles(builder.blocks, mapping)
    # Declared, then counted, then checked against the declaration. A repair
    # that quietly starts firing more often is the source getting worse, and
    # that is worth learning here rather than from the printed page.
    repaired = normalise_source_tags(builder.blocks, mapping)
    declared = (mapping.get("sourceTagNormalization") or {}).get("repairedCount")
    if declared is not None and repaired != int(declared):
        raise SystemExit(
            f"来源标注补认 {repaired} 处,登记的是 {declared} 处。"
            "数目对不上就停:要么源的标注质量变了,要么模式吃到了不该吃的东西。")
    chemistry = extract_reactions(builder.blocks, schema)
    # A registration that never matched anything is a mistake every time —
    # a mistyped hash, or an asset that travels under a different file name.
    # It used to pass silently and leave the bitmap in place, which is the one
    # outcome nobody would notice: the page still renders, just not as decided.
    registered = {stem
                  for family in ("chartSubstitutions", "vectorFigureSubstitutions",
                                 "nativeTextSubstitutions")
                  for stem in ((schema.raw.get(family) or {}).get("objects") or {})}
    applied = {str(b["substitutedSourceImage"]) for b in builder.blocks
               if b.get("substitutedSourceImage")}
    missed = sorted(registered - applied)
    if missed:
        raise SystemExit(
            "登记了替换但没有命中任何插图(哈希写错,或素材换了文件名):"
            + "、".join(missed))

    return {
        "reactions": chemistry["reactions"],
        "reactionsNotConverted": chemistry["notConverted"],
        "schemaVersion": "chengziclass.semantic-handout-blueprint.v1",
        "edition": "student",
        "sourcePolicy": "chengziclass.student-or-original-word-only.v1",
        "contentFidelityPolicy":
            "verbatim-source-visible-text-with-explicit-exclusions.v1",
        "lessons": [d["lesson"] + (d["period"] or "") for d in chosen],
        "sourceDocuments": documents,
        "sourceTitleParagraphs": builder.titles,
        "sourceTitleVisualTextEvidence": builder.evidence,
        "exclusions": [],
        "sourceObjectExclusions": builder.exclusions,
        "sourceObjectReviewQueue": builder.carriers,
        "blocks": builder.blocks,
        "skippedRoles": sorted(set(builder.skipped)),
        "unboundLocators": sorted(set(builder.unbound)),
    }


def emit_document(builder: "Builder", mapping: dict[str, Any],
                  blocks: list[dict[str, Any]], carved: dict[str, Any],
                  schema: Any = None) -> None:
    # A new lesson file starts a new document, and its first numbered item must
    # not inherit the key the previous file left behind — the numbering would
    # carry on across a book division nobody can see.
    builder.question_key = ""
    builder.group_key = ""
    options_at: dict[str, list[dict[str, Any]]] = {}
    for question in carved["questions"]:
        for option in question["options"]:
            base = option["locator"].partition("#")[0]
            options_at.setdefault(base, []).append(option)
    for block in blocks:
        if block["role"] != "选项行":
            continue
        found = options_at.get(block["locator"])
        if not found:
            continue
        offsets = [figure.get("offset") for figure in block.get("imageRefs") or []]
        for option in found:
            option["figureOrders"] = [
                index + 1 for index, offset in enumerate(offsets)
                if any(offset == figure.get("offset")
                       for figure in option.get("figures") or [])]

    # Which restart point each numbered block belongs to. A numId is one
    # running counter, so options that restart at A per question and question
    # numbers that restart at 1 per sub-module each need their own instance.
    numbered = {"编号项": ("CZ_Num_ExerciseDecimal", "group"),
                "例题题干": ("CZ_Num_ExerciseDecimal", "group"),
                "变式题干": ("CZ_Num_ExerciseDecimal", "group"),
                "小问": ("CZ_Num_SubQuestionParen", "question"),
                "圈号项": ("CZ_Num_CircledNote", "question")}
    # A title the source wrote on two lines is one title. 「跨学科实践活动」
    # sits on its own line above the activity's name, and a review handout puts
    # 「复习讲义」 under the topic it revises; emitting each line as its own
    # heading would put two entries in the table of contents where the tree has
    # one node.
    joined = {role["id"] for role in (schema.roles if schema else [])
              if role.get("joinsNext")}
    absorbed: set[str] = set()
    # Option rows already laid out as part of an earlier row's question.
    consumed_option_rows: set[str] = set()
    for position, block in enumerate(blocks):
        if block["locator"] in absorbed:
            continue
        role = block["role"]
        entry = mapping["blocks"].get(role)
        if entry is None:
            builder.skipped.append(role)
            continue
        if role in joined:
            following = next((b for b in blocks[position + 1:]
                              if (b.get("text") or "").strip()), None)
            if following is not None:
                block = {**block,
                         "text": f'{block["text"].strip()} {following["text"].strip()}'}
                absorbed.add(following["locator"])
                builder.exclude(following["locator"],
                                "标题的第二行,已并入上一行的标题", "spacer")
        # The key has to name the document as well as the paragraph. A locator
        # is only unique inside its own file, and ten lesson files all have a
        # 「body/p[53]」 — using it bare put four unrelated stretches of the book
        # into one numbering run, so a sub-list that starts at （1） in the
        # source printed as (9), and the two-digit number then overran the tab
        # stop. 31 of the 224 keys collided this way.
        stamp = builder.source["sha256"][:12]
        if block["kind"] == "sub-module":
            builder.group_key = f'{stamp}:{block["locator"]}'
        if role in ("编号项", "例题题干", "变式题干"):
            builder.question_key = f'{stamp}:{block["locator"]}'
        if block["kind"] in ("section-banner", "sub-module", "section", "header"):
            builder.list_level = 0
            if builder.repeats(block, role):
                continue
            builder.heading(block, entry)
            continue
        if entry.get("bodyStyle") and block.get("textboxParagraphs"):
            builder.callout(block, entry)
            continue
        if block.get("table"):
            builder.list_level = 0
            builder.table(block)
            continue
        if role == "选项行" and options_at.get(block["locator"]):
            if block["locator"] in consumed_option_rows:
                continue
            # One question's options arrive spread over however many rows the
            # source typed them on. Gathering the consecutive rows first is
            # what makes 「every row holds the same number」 possible at all —
            # decided row by row, 「A B」 could never learn that C and D were
            # coming.
            group = [block]
            for later in blocks[position + 1:]:
                if later["role"] != "选项行" or not options_at.get(later["locator"]):
                    break
                group.append(later)
            options: list[dict[str, Any]] = []
            for member in group:
                options.extend({**option, "sourceRow": member}
                               for option in options_at[member["locator"]])
                consumed_option_rows.add(member["locator"])
            taken = builder.options(block, options)
            for member in group:
                builder.figures(member, keep=True,
                                skip=taken.get(member["locator"], set()))
                builder.sweep_shapes(member["locator"])
            continue
        segments = builder.stream_segments(block, keep=True)
        pictures = [x for x in segments if x.get("kind") == "inline_image"]
        # A line that is only an answer blank has no visible text and is not
        # empty: it is where the student writes. Judging by 「is there text」
        # alone excluded three of them as layout spacers.
        if (any(str(x.get("text") or "").strip() for x in segments)
                or any(x.get("run_type") == "fill_blank" for x in segments)):
            style, scope = numbered.get(role, (None, None))
            block_style = entry["style"]
            if style:
                builder.list_level = 1 if scope == "group" else 2
            elif builder.list_level and block_style == "body":
                # 「汞+氧气 —加热→ 氧化汞」 is the reaction ① describes, and it
                # carries no marker of its own, so it was left at the page edge
                # while ① sat indented above it. A line inside a list belongs
                # to the innermost item open above it and lines up with it.
                block_style = ("exercise_continuation" if builder.list_level == 1
                               else "list_continuation")
            builder.add(block_style, block["locator"], segments,
                        numbering=({"style": style,
                                    "restart": builder.group_key if scope == "group"
                                    else builder.question_key}
                                   if style else None))
        elif pictures:
            for picture in pictures:
                # A few figures are charts the source flattened into a bitmap.
                # Registered by content hash, they compile as Word's own chart
                # instead — same registration idea as the condition arrows.
                # A WMF cannot be placed, so it travels as its rasterised
                # rendition 「<hash>.render.png」 while its identity stays the
                # hash. Registering by the file stem quietly missed every
                # WMF-backed substitution — the state-change chain looked
                # unregistered and compiled as the bitmap it was meant to
                # replace, with nothing reporting a miss.
                stem = Path(picture["path"]).stem
                stem = stem[:-len(".render")] if stem.endswith(".render") else stem
                drawing = (schema.raw.get("vectorFigureSubstitutions") or {}) \
                    .get("objects", {}).get(stem)
                if drawing:
                    builder.blocks.append({
                        "id": f"b{len(builder.blocks) + 1:04d}",
                        "type": "vector_figure",
                        "figure": drawing["figure"],
                        "figureOwner": builder.figure_owner(),
                        "substitutedSourceImage": stem,
                        "segments": [{"text": "", "run_type": "plain"}],
                        # The substitution stands where the bitmap stood, including how
                        # it was aligned. Writing "center" here moved 图 4.16
                        # off its item's text start — the only reason it was
                        # centred is that we drew it instead of placing it.
                        "alignment": block.get("alignment"),
                        "source": dict(picture["source"]),
                        "review_status": "approved"})
                    continue
                # Two of the 257 「figures」 are not figures: the source set a
                # state-change chain and a data table as bitmaps. Registered
                # by hash, they compile as the text and the table they always
                # were — searchable, readable by a model, and sharp, where the
                # bitmaps were the two lowest-resolution assets in the book.
                native = (schema.raw.get("nativeTextSubstitutions") or {}) \
                    .get("objects", {}).get(stem)
                if native:
                    parts = native["blocks"]
                    for order, part in enumerate(parts, start=1):
                        source = dict(picture["source"])
                        # Several blocks standing in for one bitmap each own a
                        # named part of it, or the coverage gate would read the
                        # second one as a second claim on the same object.
                        if len(parts) > 1:
                            source["objectPart"] = str(
                                part.get("objectPart") or f"part{order}")
                        builder.blocks.append({
                            "id": f"b{len(builder.blocks) + 1:04d}",
                            "figureOwner": builder.figure_owner(),
                            "substitutedSourceImage": stem,
                            "segments": [{"text": "", "run_type": "plain"}],
                            **{k: v for k, v in part.items()
                               if k != "objectPart"},
                            "source": source,
                            "review_status": "approved"})
                    continue
                chart = (schema.raw.get("chartSubstitutions") or {}) \
                    .get("objects", {}).get(stem)
                if chart:
                    builder.blocks.append({
                        "id": f"b{len(builder.blocks) + 1:04d}",
                        "type": "chart",
                        "chart": chart["chart"],
                        "figureOwner": builder.figure_owner(),
                        "substitutedSourceImage": stem,
                        "segments": [{"text": "", "run_type": "plain"}],
                        # The substitution stands where the bitmap stood, including how
                        # it was aligned. Writing "center" here moved 图 4.16
                        # off its item's text start — the only reason it was
                        # centred is that we drew it instead of placing it.
                        "alignment": block.get("alignment"),
                        "source": dict(picture["source"]),
                        "review_status": "approved"})
                    continue
                builder.blocks.append({
                    "id": f"b{len(builder.blocks) + 1:04d}",
                    "type": "image",
                    "figureOwner": builder.figure_owner(),
                    "path": picture["path"],
                    # The compiler sizes a picture from width_mm; passing the
                    # raw EMU left every standalone figure at the full-width
                    # default.
                    **{k: v for k, v in picture.items()
                       if k in ("width_mm", "height_mm", "crop", "anchor")},
                    "segments": [{"text": "", "run_type": "plain"}],
                    "alignment": block.get("alignment"),
                    "source": dict(picture["source"]),
                    "review_status": "approved"})
        else:
            builder.exclude(block["locator"], "空段落,只承担版式间距")
        builder.sweep_textboxes(block["locator"], block["text"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--object-manifest", type=Path)
    parser.add_argument("--pipeline", type=Path,
                        help="pipeline repo, for the title-visual-text contract")
    parser.add_argument("--lesson", action="append", default=[],
                        help="repeat to pick lessons; omit for every lesson")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    engine = load_engine(args.engine)
    schema = engine.Schema(json.loads(args.schema.read_text(encoding="utf-8")))
    configure_run_marks(schema)
    configure_hierarchy(schema)
    blueprint = build(
        args.lesson, engine, schema,
        json.loads(args.mapping.read_text(encoding="utf-8")),
        json.loads(args.registry.read_text(encoding="utf-8")),
        args.media, object_index(args.object_manifest),
        load_contract(args.pipeline) if args.pipeline else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(blueprint, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps({"lessons": len(blueprint["lessons"]),
                      "blocks": len(blueprint["blocks"]),
                      "exclusions": len(blueprint["sourceObjectExclusions"]),
                      "carriers": len(blueprint["sourceObjectReviewQueue"]),
                      "skippedRoles": blueprint["skippedRoles"],
                      "unbound": len(blueprint["unboundLocators"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
