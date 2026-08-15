#!/usr/bin/env python3
"""Where the pages break, read off Word's own export.

Pagination is Word's, not ours: nothing in the source or the blueprint says
where a page ends. Simulating it has been tried on this project once, for
numbering, and reported 140 mismatches that did not exist. So this reads the
PDF Word produced and asks only what a reader would ask — did a question get
torn from its options, did a table lose its header, did a heading end up alone
at the foot of a page.

The two inputs are the Word master and the PDF exported from it. Blocks are
recovered from the master's own semantic bookmarks, so the audit needs neither
the blueprint nor the run directory and can be pointed at any accepted master.

Deliberately conservative: a page boundary is judged only when the block above
it and the block below it are neighbours in the document. Anything else means
something a text extractor cannot see — a figure, a table — sits between them,
and the pair either side of the break is not the pair being read. An earlier
version without that restriction filed 「stem | figure | options」 as a stem
torn from its options and 「last option of one question | first option of the
next」 as one question split in half, and the miscount was large enough to
argue for reverting a correct fix.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import fitz
from lxml import etree

# The judgement — which pairings are defects, how much tail whitespace is worth
# warning about, where the text frame ends — is registry data, never literals
# here. Named so the preflight can see that this script reads it.
DEFAULT_PARAMS = "summer_class_module_parameters.current.json"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
BOOKMARK = re.compile(r"CZSEM_(.+)_(b\d+)$")
SEPARATORS = re.compile(r"[\s　．.，,。、：:；;（）()【】]+")
LOOKAHEAD = 60


class PageBreakAuditError(RuntimeError):
    pass


def normalise(text: str) -> str:
    return SEPARATORS.sub("", text or "")


def style_keys(archive: ZipFile, declared: dict[str, Any]) -> dict[str, str]:
    """Style id in the saved file → the key the registry files it under.

    Two rewrites sit between a rule and the document it governs. The registry
    names its styles CZ_Heading2; Word stores that under a display name
    (橙子二级标题) and, on save, replaces the id with one of its own (afff).
    So a rule written as 「CZ_Heading2」 cannot be checked by id and cannot be
    checked by name — it has to be routed back through the registry, which is
    the only place the two are recorded together.

    Comparing a block type against this list instead is what the first version
    did, and 「heading2」 never once equalled 「CZ_Heading2」: the exemption was
    dead from the day it was written and the report looked the same either way.
    """
    display = {str(spec.get("name")): key
               for key, spec in declared.items()
               if isinstance(spec, dict) and spec.get("name")}
    root = etree.fromstring(archive.read("word/styles.xml"))
    keys = {}
    for style in root.findall(W + "style"):
        name = style.find(W + "name")
        ident = style.get(W + "styleId")
        if name is None or not ident:
            continue
        keys[ident] = display.get(name.get(W + "val"), ident)
    return keys


def style_of(paragraph: Any, keys: dict[str, str]) -> str | None:
    ppr = paragraph.find(W + "pPr")
    if ppr is None:
        return None
    style = ppr.find(W + "pStyle")
    if style is None:
        return None
    return keys.get(style.get(W + "val"), style.get(W + "val"))


def blocks_of(docx: Path,
              declared: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The master's blocks in reading order, with the type each was built as.

    A table's bookmark sits on the spacer paragraph the compiler writes after
    it, so the table is held back until that spacer names it; its cells are its
    text. Everything else carries its own bookmark.

    The style id is recorded alongside the type because the two are different
    vocabularies for the same thing, and rules are written in the style one.
    Asking whether a block is one the standard breaks before used to compare a
    block type against a list of style ids — an equality that can never hold,
    so the 「一课一页起」 exemption never once fired.
    """
    archive = ZipFile(docx)
    names = style_keys(archive, declared or {})
    body = etree.fromstring(archive.read("word/document.xml")).find(W + "body")
    found: list[dict[str, Any]] = []
    pending: str | None = None
    for element in body:
        tag = etree.QName(element).localname
        if tag == "tbl":
            pending = "".join(element.itertext())
            continue
        if tag != "p":
            continue
        mark = None
        for start in element.findall(W + "bookmarkStart"):
            matched = BOOKMARK.match(str(start.get(W + "name") or ""))
            if matched:
                mark = (matched.group(2), matched.group(1))
        if mark is None:
            continue
        if mark[1] == "table" and pending is not None:
            found.append({"id": mark[0], "type": "table", "style": None,
                          "text": normalise(pending)})
            pending = None
            continue
        found.append({"id": mark[0], "type": mark[1],
                      "style": style_of(element, names),
                      "text": normalise("".join(element.itertext()))})
    if pending is not None:
        raise PageBreakAuditError("表格后面没有跟着它的语义书签,块序列对不上")
    if not found:
        raise PageBreakAuditError(f"{docx} 里没有语义书签,不是本流程编译出来的母版")
    return found


def lines_of(pdf: Path, frame: dict[str, Any],
             toc_entry: str | None = None) -> list[list[tuple[float, float, str]]]:
    """Body lines, page by page. Table-of-contents entries are not body lines.

    The contents page prints the headings verbatim, and a text extractor
    cannot tell the copy from the original: 「课题1 空气的成分」 in the contents
    and the heading itself on the next page read as one block appearing on two
    consecutive pages, which is indistinguishable from one block torn in half.
    """
    top = float(frame.get("headerBelowPt") or 40.0)
    bottom = float(frame.get("footerAbovePt") or 795.0)
    leader = re.compile(toc_entry) if toc_entry else None
    document = fitz.open(pdf)
    pages = []
    for index in range(document.page_count):
        rows = []
        for block in document[index].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if leader is not None and leader.search(text):
                    continue
                if text and top < line["bbox"][1] < bottom:
                    rows.append((round(line["bbox"][1], 1),
                                 round(line["bbox"][3], 1), text))
        rows.sort()
        pages.append(rows)
    return pages


def ink_bottoms(pdf: Path, frame: dict[str, Any]) -> list[float]:
    """How far down each page anything at all was printed.

    Text, drawn lines and images together — a page whose lower half holds an
    apparatus diagram is not blank, however little text reaches down there.
    Measured from the last text line instead, thirteen of this volume's
    twenty-nine whitespace warnings were pages with a figure on them.
    """
    top = float(frame.get("headerBelowPt") or 40.0)
    bottom = float(frame.get("footerAbovePt") or 795.0)
    document = fitz.open(pdf)
    lowest = []
    for index in range(document.page_count):
        page = document[index]
        low = top
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if "".join(s["text"] for s in line["spans"]).strip():
                    if top < line["bbox"][1] < bottom:
                        low = max(low, min(line["bbox"][3], bottom))
        for drawing in page.get_drawings():
            rect = drawing["rect"]
            if top < rect.y0 < bottom:
                low = max(low, min(rect.y1, bottom))
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if top < rect.y0 < bottom:
                    low = max(low, min(rect.y1, bottom))
        lowest.append(low)
    return lowest


def map_pages(pages: list[list[tuple[float, float, str]]],
              blocks: list[dict[str, Any]],
              tally: dict[str, int] | None = None) -> list[list[int]]:
    """Which blocks each page shows, by a pointer that only moves forward.

    A line matching nothing is skipped rather than resynchronising the pointer:
    one odd line — a formula rendered as glyphs, a figure caption — must not be
    able to drag the mapping out of step with the rest of the document.
    """
    pointer = 0
    mapped: list[list[int]] = []
    for rows in pages:
        hits: list[int] = []
        for _, _, text in rows:
            key = normalise(text)[:8]
            if len(key) < 3:
                continue
            if tally is not None:
                tally["lines"] = tally.get("lines", 0) + 1
            for candidate in range(pointer, min(len(blocks), pointer + LOOKAHEAD)):
                if key in blocks[candidate]["text"]:
                    hits.append(candidate)
                    pointer = candidate
                    if tally is not None:
                        tally["matched"] = tally.get("matched", 0) + 1
                    break
        mapped.append(hits)
    return mapped


def undeclared_fonts(pdf: Path, standard: dict[str, Any]) -> list[dict[str, Any]]:
    """Fonts in the export that nobody declared.

    Where a declared font does not cover a character, Word substitutes one and
    says nothing. Four such fonts were setting text in this volume — one of
    them on 225 of 229 pages — and none of the twenty compliance checks, nine
    workflow steps or two gates looked at fonts at all. Substitution is not
    forbidden here; going unrecorded is.
    """
    allowed = {name.split("+")[-1].replace(" ", "")
               for name in (standard.get("declared") or [])}
    allowed |= {name.replace(" ", "") for name in (standard.get("observedFallbacks") or {})}
    # Word 名与 PDF 里的 PostScript 名不是同一个字符串(宋体→SimSun、黑体→SimHei…)。
    # 这张对应表**原先硬编码在本函数里**,与本项目「规则出代码进 schema 数据」相反,
    # 而且是会悄悄过期的那类:声明表加了新字体、代码里的别名没加,
    # 门就会把自家字体报成未声明。现改从参数数据读。
    # 数据里没有该键时保留旧内置表,以免旧参数表跑本脚本时行为突变;
    # 两者的并集在迁移当天实测逐字符相等。
    aliases = standard.get("postScriptAliases")
    if isinstance(aliases, dict) and any(isinstance(v, list) for v in aliases.values()):
        for names in aliases.values():
            if isinstance(names, list):
                allowed |= {str(n).replace(" ", "") for n in names}
    else:
        allowed |= {"SimSun", "TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT",
                    "TimesNewRomanPS-ItalicMT", "TimesNewRomanPS-BoldItalicMT",
                    "CambriaMath", "Cambria", "STYuanti-SC-Bold", "STYuanti-SC-Regular"}
    document = fitz.open(pdf)
    seen: dict[str, int] = {}
    for index in range(document.page_count):
        for font in document[index].get_fonts(full=True):
            base = str(font[3]).split("+")[-1]
            if base.replace(" ", "") not in allowed:
                seen[base] = seen.get(base, 0) + 1
    return [{"font": name, "pages": count} for name, count in sorted(seen.items())]


def audit(docx: Path, pdf: Path, standard: dict[str, Any],
          fonts: dict[str, Any] | None = None,
          declared: dict[str, Any] | None = None) -> dict[str, Any]:
    blocks = blocks_of(docx, declared)
    frame = standard.get("textFrame") or {}
    pages = lines_of(pdf, frame, standard.get("tocEntryPattern"))
    ink = ink_bottoms(pdf, frame)
    tally: dict[str, int] = {}
    mapped = map_pages(pages, blocks, tally)
    # Before judging anything, prove this PDF came from this master. Handed a
    # different book entirely, the first version matched nothing, judged zero
    # boundaries and reported pass — an audit that passes when it has read
    # nothing is the very failure it exists to catch. Normal rate is 0.97.
    read = tally.get("matched", 0) / max(tally.get("lines", 0), 1)
    floor = float(standard.get("minMatchedLineRatio") or 0)
    titles = set(standard.get("titleTypes") or ())
    stems = {"exercise", "exercise_continuation", "body", "list_continuation"}
    breaking = set(standard.get("breakBefore") or ())
    warn_at = float(standard.get("tailWhitespaceWarnPt") or 0)
    bottom = float(frame.get("footerAbovePt") or 795.0)
    findings: list[dict[str, Any]] = []
    judged = 0
    for index in range(len(pages) - 1):
        if not mapped[index] or not mapped[index + 1]:
            continue
        above, below = max(mapped[index]), min(mapped[index + 1])
        tail = bottom - ink[index]
        if warn_at and tail > warn_at:
            findings.append({"code": "tail-whitespace", "page": index + 1,
                             "blockId": blocks[above]["id"],
                             "whitespacePt": round(tail)})
        # A block that is on both sides of the break is one block torn in two.
        # Judged here, before the adjacency filter, because that filter drops
        # exactly this case: 「above == below」 can never also be 「below ==
        # above + 1」, so the table-split branch that used to live below it was
        # unreachable from the day it was written and its zero was never real.
        if above == below:
            if blocks[above]["type"] == "table":
                findings.append({"code": "table-split", "page": index + 1,
                                 "blockId": blocks[above]["id"],
                                 "whitespacePt": round(tail)})
            elif warn_at and tail > warn_at:
                # Torn and still short: each signal is ordinary alone — a
                # paragraph running past the page bottom is how text sets, and
                # whitespace is what keeping things together costs. Together
                # they mean the tear bought nothing.
                findings.append({"code": "split-with-hole", "page": index + 1,
                                 "blockId": blocks[above]["id"],
                                 "type": blocks[above]["type"],
                                 "whitespacePt": round(tail),
                                 "text": blocks[above]["text"][:40]})
        if below != above + 1:
            continue
        judged += 1
        kinds = (blocks[above]["type"], blocks[below]["type"])
        code = None
        if kinds == ("choice", "choice"):
            code = "option-row-split"
        elif kinds[0] in stems and kinds[1] == "choice":
            code = "stem-from-options"
        elif kinds[0] in titles:
            code = "title-stranded"
        if code:
            findings.append({"code": code, "page": index + 1,
                             "blockId": blocks[above]["id"],
                             "above": kinds[0], "below": kinds[1],
                             "whitespacePt": round(tail)})
    # A page that ends short because the next one must open with a lesson是
    # 「一课一页起」 being paid for, not a defect; the standard already says so.
    # Matched on the style id, which is what breakBefore is written in — the
    # block type is a second name for the same thing and never compares equal.
    for finding in findings:
        if finding["code"] != "tail-whitespace":
            continue
        following = finding["page"]
        opens = min(mapped[following]) if following < len(mapped) and mapped[following] else None
        if opens is not None and blocks[opens].get("style") in breaking:
            finding["expected"] = "next-page-opens-a-lesson"
    codes = {name: spec.get("severity", "fail")
             for name, spec in (standard.get("codes") or {}).items()}
    if fonts:
        undeclared = undeclared_fonts(pdf, fonts)
        if undeclared:
            findings.append({"code": "undeclared-font", "fonts": undeclared,
                             "why": "成品用到了 fontStandard 没有声明、也没有登记为已知替换的字体"})
            codes["undeclared-font"] = "fail"
    if floor and read < floor:
        findings.insert(0, {
            "code": "audit-unable-to-read", "matchedLineRatio": round(read, 4),
            "required": floor, "lines": tally.get("lines", 0),
            "why": "成品的文字对不回母版,说明这不是这份母版排出来的,或者文字抽取失败;"
                   "此时「没有发现缺陷」不成立"})
        codes["audit-unable-to-read"] = "fail"
    blocking = [f for f in findings
                if codes.get(f["code"], "fail") == "fail" and not f.get("expected")]
    return {
        "matchedLineRatio": round(read, 4),
        "schemaVersion": "chengziclass.summer-pdf-page-break-audit.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "docx": str(docx), "pdf": str(pdf),
        "pageCount": len(pages), "blockCount": len(blocks),
        "judgedBoundaries": judged,
        "status": "fail" if blocking else "pass",
        "summary": {code: sum(1 for f in findings if f["code"] == code)
                    for code in (standard.get("codes") or {})},
        "blockingCount": len(blocking),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--fail-on-defects", action="store_true")
    args = parser.parse_args()
    registry = json.loads(args.parameters.read_text(encoding="utf-8"))
    standard = ((registry.get("wordStyleRegistry") or {})
                .get("pageBreakStandard") or {})
    audit_standard = dict(standard.get("defectAudit") or {})
    audit_standard.setdefault("breakBefore", standard.get("breakBefore") or [])
    if not audit_standard.get("codes"):
        raise SystemExit("注册表里没有 pageBreakStandard.defectAudit,判据必须是数据")
    styles = ((registry.get("wordStyleRegistry") or {}).get("paragraphStyles") or {})
    fonts = ((registry.get("wordStyleRegistry") or {}).get("fontStandard") or {})
    report = audit(args.docx, args.pdf, audit_standard, fonts, styles)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(args.report)
    print(json.dumps({"status": report["status"],
                      "judgedBoundaries": report["judgedBoundaries"],
                      **report["summary"]}, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_defects and report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
