#!/usr/bin/env python3
"""Everything the blueprint declares must be findable in the document.

Every silent failure this pipeline has had takes the same shape: a decision is
recorded, the step that carries it out quietly does not, and the page still
renders — just not as decided. A registered picture substitution that never
matched left the bitmap in place; an arrow stretch Word rejected printed an
unstretched stub; a style named in the keep-together standard printed without
keepNext. None of them crashed, and none of them were caught by a gate,
because every gate asked 「is the output well formed」 and none asked 「is the
output what was decided」.

This asks the second question. It compares the blueprint against the compiled
master and fails when a declaration has no counterpart:

* every block reaches the document, identified by its own semantic bookmark
* every reaction arrow becomes an equation, not text
* every registered substitution reaches the document as the block that names it
* a question's option rows all carry the same number of options

The last is an invariant nobody had ever checked. It holds today across 351
option groups; a rule with no gate is a rule that holds until it does not.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from lxml import etree


ARROW_GLYPHS = "⟶→⇌↑↓"


def squash(text: str) -> str:
    """Text with the things that legitimately differ taken out.

    Tabs separate option columns and are layout, not content. A reaction arrow
    is a segment in the blueprint and an equation in the document, so its glyph
    exists on one side only; the condition riding on it is compared, the glyph
    is not.
    """
    stripped = "".join(c for c in (text or "") if c not in ARROW_GLYPHS)
    return re.sub(r"\s+", "", stripped)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
BOOKMARK = re.compile(r"CZSEM_(.+)_(b\d+)$")
# The block-type → style map the compiler uses; restated only because this gate
# runs standalone. Kept in sync by the compiler's own tests.
STYLE_OF = {"exercise": "CZ_ExerciseStem", "exercise_continuation": "CZ_ExerciseContinuation",
            "list_continuation": "CZ_ListContinuation", "body": "CZ_Body",
            "callout_title": "CZ_CalloutTitle", "callout_subpoint": "CZ_CalloutSubpoint",
            "callout_body": "CZ_CalloutBody", "choice": "CZ_ChoiceOption",
            "heading1": "CZ_Heading1", "heading2": "CZ_Heading2", "heading3": "CZ_Heading3",
            "heading4": "CZ_Heading4", "heading5": "CZ_Heading5",
            "exercise_group_title": "CZ_ExerciseGroupTitle", "chapter": "CZ_ChapterTitle"}


def declared(blueprint: dict[str, Any]) -> dict[str, Any]:
    blocks = blueprint["blocks"]
    arrows = 0
    for block in blocks:
        segments = list(block.get("segments") or [])
        for row in block.get("rows") or []:
            for cell in row:
                segments.extend(cell.get("segments") or [])
        arrows += sum(1 for s in segments if s.get("kind") == "reaction_arrow")
    return {
        "blockIds": {str(b["id"]) for b in blocks},
        "arrows": arrows,
        "substitutions": {str(b["substitutedSourceImage"]) for b in blocks
                          if b.get("substitutedSourceImage")},
        "substitutionBlocks": {str(b["id"]) for b in blocks
                               if b.get("substitutedSourceImage")},
    }


def observed(docx: Path) -> dict[str, Any]:
    part = etree.fromstring(ZipFile(docx).read("word/document.xml"))
    found: set[str] = set()
    for start in part.iter(W + "bookmarkStart"):
        matched = BOOKMARK.match(str(start.get(W + "name") or ""))
        if matched:
            found.add(matched.group(2))
    return {"blockIds": found,
            "arrows": sum(1 for _ in part.iter(MATH + "oMath"))}


def styles_of(docx: Path) -> dict[str, dict[str, Any]]:
    """Each style's name, its parent, and the left indent it states."""
    part = etree.fromstring(ZipFile(docx).read("word/styles.xml"))
    found: dict[str, dict[str, Any]] = {}
    for style in part.iter(W + "style"):
        name = style.find(W + "name")
        based = style.find(W + "basedOn")
        indent = style.find(f"{W}pPr/{W}ind")
        found[str(style.get(W + "styleId"))] = {
            "name": str(name.get(W + "val")) if name is not None else "",
            "basedOn": str(based.get(W + "val")) if based is not None else None,
            "left": (int(indent.get(W + "left")) if indent is not None
                     and indent.get(W + "left") else None),
            "bold": style.find(f"{W}rPr/{W}b") is not None,
        }
    return found


def paragraphs_of(docx: Path, styles: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every bookmarked paragraph, with the left edge it actually prints at.

    The left edge is what the eye compares, so it is resolved the way Word
    resolves it: the paragraph's own w:ind if it states one, otherwise the
    nearest ancestor style that does.
    """
    part = etree.fromstring(ZipFile(docx).read("word/document.xml"))

    def inherited(style_id: str | None) -> int:
        seen: set[str] = set()
        while style_id and style_id in styles and style_id not in seen:
            seen.add(style_id)
            stated = styles[style_id]["left"]
            if stated is not None:
                return stated
            style_id = styles[style_id]["basedOn"]
        return 0

    found: dict[str, dict[str, Any]] = {}
    for para in part.iter(W + "p"):
        block_id = None
        for start in para.findall(W + "bookmarkStart"):
            matched = BOOKMARK.match(str(start.get(W + "name") or ""))
            if matched:
                block_id = matched.group(2)
        if block_id is None:
            continue
        properties = para.find(W + "pPr")
        style_node = properties.find(W + "pStyle") if properties is not None else None
        style_id = str(style_node.get(W + "val")) if style_node is not None else None
        own = properties.find(W + "ind") if properties is not None else None
        left = (int(own.get(W + "left")) if own is not None and own.get(W + "left")
                else inherited(style_id))
        runs = []
        for run in para.findall(W + "r"):
            run_properties = run.find(W + "rPr")
            style = (run_properties.find(W + "rStyle")
                     if run_properties is not None else None)
            runs.append(str(style.get(W + "val")) if style is not None else None)
        direct = {tag for tag in ("ind", "spacing", "jc")
                  if properties is not None and properties.find(W + tag) is not None}
        bare = set()
        for run in para.findall(W + "r"):
            run_properties = run.find(W + "rPr")
            if run_properties is None:
                continue
            if run_properties.find(W + "rStyle") is not None:
                continue
            bare |= {tag for tag in ("b", "sz", "color", "rFonts", "highlight",
                                     "u", "vertAlign")
                     if run_properties.find(W + tag) is not None}
        found[block_id] = {"left": left, "styleId": style_id, "directTags": direct,
                           "directRuns": bare,
                           "text": "".join(para.itertext()).strip(), "runs": runs}
    return found


def text_block_emu(docx: Path) -> float:
    part = etree.fromstring(ZipFile(docx).read("word/document.xml"))
    for section in part.iter(W + "sectPr"):
        size = section.find(W + "pgSz")
        margin = section.find(W + "pgMar")
        if size is None or margin is None:
            continue
        width = int(size.get(W + "w")) - int(margin.get(W + "left")) - int(margin.get(W + "right"))
        return width / 1440 * 914400
    return 0.0


def invariants(blueprint: dict[str, Any], docx: Path,
               registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Properties asserted in the registry, checked against the document.

    Each of these was decided, written down, and until now enforced by nothing
    — the pre-check that found them asked, for every property claimed, which
    structure makes violating it impossible, and for five of thirteen the
    answer was 「none」. They are grouped here rather than fixed one by one
    because 「declared and not enforced」 is one defect wearing several faces.
    """
    styles = styles_of(docx)
    paragraphs = paragraphs_of(docx, styles)
    blocks = {str(b["id"]): b for b in blueprint["blocks"]}
    failures: list[dict[str, Any]] = []

    # 黄底一律加粗 — expressed by pointing the highlight run at the bold member
    # of the family, so the non-bold member must not appear in the document.
    plain_yellow = {sid for sid, spec in styles.items()
                    if spec["name"] == "橙子高亮" and not spec["bold"]}
    used = [b for b, para in paragraphs.items()
            if plain_yellow & {r for r in para["runs"] if r}]
    if used:
        failures.append({"code": "highlight-not-bold", "count": len(used),
                         "ids": sorted(used)[:20],
                         "why": "黄底一律加粗;成品里仍有 run 用着不加粗的那个高亮样式"})

    # The text a block prints must be the text the blueprint holds for it. The
    # first version of this check restated one rule's predicate — 「a callout
    # title must not end in a colon」 — and restated it wider than the rule,
    # which only ever targeted short whole-line titles; it then failed a
    # 【查阅资料】 paragraph whose sentence legitimately ends in one. Comparing
    # against the blueprint has one definition instead of two, and catches any
    # text drift rather than this one rule's.
    UNNUMBERED = {"callout_title", "callout_subpoint", "callout_body", "choice",
                  "heading1", "heading2", "heading3", "heading4", "heading5"}
    drifted = []
    for block_id, para in paragraphs.items():
        block = blocks.get(block_id) or {}
        if str(block.get("type") or "") not in UNNUMBERED:
            continue
        wanted = "".join(
            str(s.get("text") or "") if s.get("kind") != "reaction_arrow"
            else str(s.get("over") or "") + str(s.get("under") or "")
            for s in block.get("segments") or []).strip()
        if wanted and squash(wanted) != squash(para["text"]):
            drifted.append({"blockId": block_id, "blueprint": wanted[:40],
                            "document": para["text"][:40]})
    if drifted:
        failures.append({"code": "block-text-drift", "count": len(drifted),
                         "cases": drifted[:20],
                         "why": "成品里这一块的文字与蓝图不一致"})

    # A figure starts where its item's text starts. Compared against the item's
    # text start computed independently from the document — walking back to the
    # nearest block whose *style* states an indent — and not against the owner
    # paragraph's printed left edge: three of these figures are owned by a
    # table, which has no text start at all, and the owner being at the margin
    # is a separate question from the figure being in the right place.
    order = [b for b in (str(x["id"]) for x in blueprint["blocks"]) if b in paragraphs]
    position = {b: i for i, b in enumerate(order)}
    offside = []
    for block_id, block in blocks.items():
        if str(block.get("type") or "") not in ("image", "chart", "vector_figure"):
            continue
        if block_id not in position:
            continue
        # 「states zero」 and 「states nothing」 are the same thing once Word has
        # saved the file, so the document alone cannot answer where an item's
        # text starts — reading it off the artifact put a figure under a
        # heading at 720 when the heading declares 0. Per the audit rule, a
        # quantity that cannot be computed is not filled in with the one that
        # happens to fit: the registry's own declaration is used, which is also
        # the compiler's, so there is one definition rather than two.
        start = None
        if registry is not None:
            numbering = registry.get("numbering") or {}
            definitions = numbering.get("definitions") or numbering
            for earlier in reversed(order[:position[block_id]]):
                block = blocks.get(earlier) or {}
                # Two places declare a text start — the numbering definition
                # for a numbered item, the paragraph style otherwise — and
                # reading only one of them is what made these two computations
                # disagree by 360. Same pair the compiler reads, in the same
                # order, so there is one definition and not two.
                marker = ((block.get("numbering") or {}).get("style"))
                marked = definitions.get(marker) if marker else None
                if isinstance(marked, dict) and marked.get("textStartDxa") is not None:
                    start = int(marked["textStartDxa"] or 0)
                    break
                spec = (registry.get("paragraphStyles") or {}).get(
                    STYLE_OF.get(str(block.get("type") or "")) or "")
                if spec is not None and "leftIndentDxa" in spec:
                    start = int(spec["leftIndentDxa"] or 0)
                    break
        if start is not None and paragraphs[block_id]["left"] != start:
            offside.append({"blockId": block_id,
                            "figure": paragraphs[block_id]["left"],
                            "itemTextStart": start})
    if offside:
        failures.append({"code": "figure-off-item-text-start",
                         "count": len(offside), "cases": offside[:20],
                         "why": "图跟着它所属那一项的正文起点走"})

    # Direct paragraph formatting is forbidden except where the registry lists
    # an exception, and until now nothing enforced that — the policy text had
    # even fallen behind what the pipeline legitimately does, naming neither
    # the figure indent nor the callout subpoint's. A rule with no gate drifts
    # from practice and nobody finds out.
    policy = (registry or {}).get("directFormattingPolicy") or {}
    allowed_kinds = {"image", "chart", "vector_figure", "callout_subpoint"}
    stray = []
    for block_id, para in paragraphs.items():
        kind = str((blocks.get(block_id) or {}).get("type") or "")
        if kind in allowed_kinds:
            continue
        node = para.get("directTags") or ()
        if node:
            stray.append({"blockId": block_id, "type": kind, "tags": sorted(node)})
    if policy and stray:
        failures.append({"code": "direct-formatting-outside-policy",
                         "count": len(stray), "cases": stray[:20],
                         "why": "段落级直接格式只允许出现在 directFormattingPolicy 列出的例外上"})
    # run 级直接格式:没有字符样式的 run 不得自带字体/字号/字重/颜色
    bare = [{"blockId": b, "tags": sorted(para["directRuns"])}
            for b, para in paragraphs.items() if para.get("directRuns")]
    if bare:
        failures.append({"code": "run-formatting-without-character-style",
                         "count": len(bare), "cases": bare[:20],
                         "why": "字重字号颜色一律走注册字符样式,不写在 run 上"})

    # 浮动图必须落在版心内
    body = text_block_emu(docx)
    part = etree.fromstring(ZipFile(docx).read("word/document.xml"))
    outside = []
    for anchor in part.iter(f"{{{DRAWING}}}anchor"):
        position = anchor.find(f"{{{DRAWING}}}positionH")
        extent = anchor.find(f"{{{DRAWING}}}extent")
        if position is None or extent is None:
            continue
        offset = position.find(f"{{{DRAWING}}}posOffset")
        if offset is None or position.get("relativeFrom") != "column":
            continue
        left, width = float(offset.text or 0), float(extent.get("cx") or 0)
        if left < -1 or (body and left + width > body + 1):
            outside.append({"leftMm": round(left / 36000, 1),
                            "widthMm": round(width / 36000, 1),
                            "bodyMm": round(body / 36000, 1)})
    if outside:
        failures.append({"code": "float-outside-text-block",
                         "count": len(outside), "cases": outside[:20],
                         "why": "浮动图必须夹在版心内"})
    return failures


def option_rows(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows of one question must all hold the same number of options."""
    uneven: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for block in list(blueprint["blocks"]) + [{"type": "__end__"}]:
        if str(block.get("type") or "") == "choice":
            group.append(block)
            continue
        if group:
            counts = []
            for member in group:
                text = "".join(str(s.get("text") or "")
                               for s in member.get("segments") or [])
                counts.append(len([p for p in text.split("\t") if p.strip()]))
            if len(set(counts)) > 1:
                uneven.append({"blockId": group[0]["id"], "counts": counts})
        group = []
    return uneven


def annotation_bindings(blueprint: dict[str, Any], docx: Path,
                        registry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """An annotation must be held by what it annotates, and hold nothing itself.

    Checked on both sides because either one alone lets the defect through: a
    referent that does not hold on lets the annotation drift to the next page,
    and an annotation that holds on to what follows drags the whole chain over,
    taking its own referent's page with it. 「注：其中布袋子……」 was the second
    case — bound forward to a table it has nothing to do with.
    """
    rule = ((registry or {}).get("keepTogetherStandard") or {}).get("annotationBinding") or {}
    markers = tuple(str(m) for m in (rule.get("markers") or ()))
    kinds = set(rule.get("appliesToTypes") or ())
    referents = set(rule.get("referentTypes") or ())
    # An unreadable rule is reported, never treated as 「nothing to check」.
    # The first version of this function looked one level too deep, found
    # nothing, and passed a document that has the very defect it was written
    # for — a gate that disables itself when its rule goes missing is the
    # silent-pass failure wearing a new face.
    if not markers or not kinds or not referents:
        return [{"annotation": None, "referent": None,
                 "why": "注册表里读不到 keepTogetherStandard.annotationBinding,"
                        "此时「没有发现问题」不成立"}]
    blocks = blueprint["blocks"]

    def is_note(block: dict[str, Any]) -> bool:
        if str(block.get("type") or "") not in kinds:
            return False
        text = "".join(str(s.get("text") or "")
                       for s in (block.get("segments") or []))
        return text.lstrip().lstrip("（(【[").startswith(markers)

    body = etree.fromstring(ZipFile(docx).read("word/document.xml")).find(W + "body")
    keeps: dict[str, bool] = {}
    rows_keep: dict[str, bool] = {}
    last_table = None
    for element in body:
        tag = etree.QName(element).localname
        if tag == "tbl":
            last_table = element
            continue
        if tag != "p":
            continue
        block_id = None
        for start in element.findall(W + "bookmarkStart"):
            matched = BOOKMARK.match(str(start.get(W + "name") or ""))
            if matched:
                block_id = matched.group(2)
        if block_id is None:
            continue
        properties = element.find(W + "pPr")
        node = properties.find(W + "keepNext") if properties is not None else None
        keeps[block_id] = node is not None and node.get(W + "val") not in ("0", "false")
        if last_table is not None:
            rows = last_table.findall(W + "tr")
            cells = rows[-1].findall(W + "tc") if rows else []
            bound = [p for cell in cells for p in cell.findall(W + "p")]
            rows_keep[block_id] = bool(bound) and all(
                (p.find(W + "pPr") is not None
                 and p.find(W + "pPr").find(W + "keepNext") is not None)
                for p in bound)
            last_table = None

    broken: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not is_note(block) or index == 0:
            continue
        note_id = str(block.get("id"))
        before = blocks[index - 1]
        referent_id = str(before.get("id"))
        if str(before.get("type") or "") in referents:
            if not keeps.get(referent_id):
                broken.append({"annotation": note_id, "referent": referent_id,
                               "why": "所注对象没有 keepNext,注解会被留在下一页"})
            elif str(before.get("type")) == "table" and not rows_keep.get(referent_id, True):
                broken.append({"annotation": note_id, "referent": referent_id,
                               "why": "表格最后一行没有绑住,Word 会断在表与注之间"})
        if keeps.get(note_id):
            broken.append({"annotation": note_id, "referent": referent_id,
                           "why": "注解自己带了 keepNext,会把后面的内容连同自己一起推走"})
    return broken


def audit(blueprint_path: Path, docx: Path,
          registry: dict[str, Any] | None = None) -> dict[str, Any]:
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    want, got = declared(blueprint), observed(docx)
    failures: list[dict[str, Any]] = []
    missing = sorted(want["blockIds"] - got["blockIds"])
    if missing:
        failures.append({"code": "block-not-in-document",
                         "count": len(missing), "ids": missing[:20]})
    extra = sorted(got["blockIds"] - want["blockIds"])
    if extra:
        failures.append({"code": "document-block-not-declared",
                         "count": len(extra), "ids": extra[:20]})
    if want["arrows"] != got["arrows"]:
        failures.append({"code": "reaction-arrow-not-an-equation",
                         "declared": want["arrows"], "found": got["arrows"],
                         "why": "声明的反应箭头必须在成品里是 oMath;数目对不上说明有的退回成了文字"})
    lost = sorted(want["substitutionBlocks"] - got["blockIds"])
    if lost:
        failures.append({"code": "substitution-block-not-in-document",
                         "count": len(lost), "ids": lost})
    failures.extend(invariants(blueprint, docx, registry))
    detached = annotation_bindings(blueprint, docx, registry)
    if detached:
        failures.append({"code": "annotation-binding-wrong-direction",
                         "count": len(detached), "cases": detached[:20],
                         "why": "注解粘的方向要取自语义指向:向前粘住所注对象,不向后粘"})
    uneven = option_rows(blueprint)
    if uneven:
        failures.append({"code": "option-rows-uneven", "count": len(uneven),
                         "cases": uneven[:20],
                         "why": "同一道题的每一行选项数量必须相同"})
    return {
        "schemaVersion": "chengziclass.semantic-output-declaration-gate.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "blueprint": str(blueprint_path), "docx": str(docx),
        "status": "fail" if failures else "pass",
        "checked": {"blocks": len(want["blockIds"]), "arrows": want["arrows"],
                    "invariants": 7,
                    "substitutions": len(want["substitutions"]),
                    "optionGroups": sum(1 for _ in _groups(blueprint))},
        "failures": failures,
    }


def _groups(blueprint: dict[str, Any]):
    run = 0
    for block in blueprint["blocks"]:
        if str(block.get("type") or "") == "choice":
            run += 1
        elif run:
            yield run
            run = 0
    if run:
        yield run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blueprint", required=True, type=Path)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--parameters", type=Path,
                        help="summer_class_module_parameters.current.json")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    registry = None
    if args.parameters and args.parameters.is_file():
        registry = (json.loads(args.parameters.read_text(encoding="utf-8"))
                    .get("wordStyleRegistry") or {})
    report = audit(args.blueprint, args.docx, registry)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(args.report)
    print(json.dumps({"status": report["status"], **report["checked"],
                      "failures": [f["code"] for f in report["failures"]]},
                     ensure_ascii=False, indent=2))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
