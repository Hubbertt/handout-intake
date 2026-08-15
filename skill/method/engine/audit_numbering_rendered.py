#!/usr/bin/env python3
"""The number on our page against the number on the source's page.

An earlier version of this check simulated Word's counters from the compiled
XML and reported 140 disagreements. Screenshotting three of them showed two
were printing correctly — the simulation was wrong, not the document. So the
measurement moved to the only place that cannot be wrong about what Word
prints: the PDF Word itself exported.

Both sides are read the same way. Every line that begins with a marker is
recorded as (marker, rest-of-line); the two documents are joined on the rest,
normalised for width and whitespace, and only where exactly one line in each
carries that text — an ambiguous join proves nothing and is skipped rather
than guessed at.
"""
from __future__ import annotations
import argparse, collections, json, re, unicodedata
from pathlib import Path
import fitz

MARKER = re.compile(r"^\s*(\d{1,2}\s*[．.]|[A-H]\s*[．.]|[（(]\s*\d{1,2}\s*[)）]|[①-⑳])\s*")


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def ordinal(marker: str) -> str:
    """The number itself, without the punctuation around it.

    Word writes 「1.」 where the source typed 「1．」 and 「(2)」 where it typed
    「（2）」. That is the marker being regenerated, which is what the spec asks
    for; comparing the punctuation would drown the real disagreements — 358
    reported, of which the overwhelming majority were a full-width stop.
    """
    marker = marker.strip()
    for index, glyph in enumerate(CIRCLED, start=1):
        if glyph in marker:
            return str(index)
    digits = re.sub(r"\D", "", marker)
    return digits or marker


def lines(pdf: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = collections.defaultdict(list)
    for page in fitz.open(pdf):
        for raw in page.get_text().split("\n"):
            hit = MARKER.match(raw)
            if not hit:
                continue
            rest = raw[hit.end():]
            key = re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", rest))
            if len(key) >= 8:
                found[key].append(ordinal(hit.group(1)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", nargs="+", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    ours = lines(args.output)
    theirs: dict[str, list[str]] = collections.defaultdict(list)
    for pdf in args.source:
        for key, marks in lines(pdf).items():
            theirs[key].extend(marks)

    findings, compared, ambiguous = [], 0, 0
    for key, marks in theirs.items():
        mine = ours.get(key)
        if not mine:
            continue
        if len(marks) != 1 or len(mine) != 1:
            ambiguous += 1
            continue
        compared += 1
        if marks[0] != mine[0]:
            findings.append({"sourcePrinted": marks[0], "wePrinted": mine[0],
                             "text": key[:56]})
    report = {"schemaVersion": "chengziclass.numbering-rendered.v1",
              "sourceMarkedLines": len(theirs), "outputMarkedLines": len(ours),
              "compared": compared, "ambiguousSkipped": ambiguous,
              "findingCount": len(findings), "findings": findings}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "findings"},
                     ensure_ascii=False, indent=1))
    for item in findings[:25]:
        print(f"  源印 {item['sourcePrinted']:>5s} → 我们印 {item['wePrinted']:>5s}   {item['text']}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
