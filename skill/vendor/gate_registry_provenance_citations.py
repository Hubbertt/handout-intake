#!/usr/bin/env python3
"""Every value that claims to come from the spec must be findable in the spec.

The layout registry and the private spec say the same things twice, and today
they disagreed in five places at once: heading sizes off by 2–3 pt, heading
spacing wholly different, the numbered-item text start 480 in one place and
360 in another, a pagination rule the spec forbade and the compiler relied on,
and a figure-caption floor the spec gave two different values for. None of it
was caught by anything — twenty compliance checks, nine workflow steps and
three gates all passed while the two documents drifted apart.

They were found by reading the spec line by line, which is not repeatable. So
this gate does the mechanical half: a provenance note that says 「spec:」 or
「standard:」 must quote the spec, in 「…」, and the quote must appear in the
spec verbatim. A quote cannot be invented and cannot survive the sentence it
came from being rewritten — which is exactly the drift we could not see.

What this gate does NOT do: judge whether the cited sentence actually supports
the number. That still needs a reader. It only makes 「I checked」 falsifiable.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# The registry this gate is bound to, named here so the generation preflight
# can see which parameter table the script reads.
DEFAULT_PARAMS = "summer_class_module_parameters.current.json"

CLAIMS = ("spec:", "standard:")
QUOTED = re.compile(r"[「『]([^」』]{4,})[」』]")


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def blocks_with_values(registry: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            if any(numeric(v) for v in node.values()):
                found.append((path, node))
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(registry)
    return found


def audit(registry_path: Path, spec_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    spec = spec_path.read_text(encoding="utf-8")
    # Compared with whitespace and Markdown backticks removed: the spec wraps
    # lines, mixes full-width and half-width spacing, and marks values as code.
    # None of that changes what a sentence says.
    def flatten(text: str) -> str:
        return re.sub(r"[\s`]+", "", text)

    flat = flatten(spec)
    failures: list[dict[str, Any]] = []
    checked = quoted = 0

    for path, node in blocks_with_values(registry.get("wordStyleRegistry") or {}):
        notes = dict(node.get("provenanceByKey") or {})
        blanket = node.get("provenance")
        if isinstance(blanket, str):
            notes.setdefault("__block__", blanket)
        for key, note in notes.items():
            if not isinstance(note, str) or not note.startswith(CLAIMS):
                continue
            checked += 1
            marks = QUOTED.findall(note)
            if not marks:
                failures.append({
                    "code": "citation-without-quote", "path": f"{path}.{key}",
                    "note": note[:70],
                    "why": "声称出自规范却没引原文;引不出原文的出处,和没有出处一样不可核",
                })
                continue
            for mark in marks:
                quoted += 1
                if flatten(mark) not in flat:
                    failures.append({
                        "code": "quote-not-in-spec", "path": f"{path}.{key}",
                        "quote": mark[:50],
                        "why": "规范里找不到这句话:要么写错了,要么规范被改过而这里没跟上",
                    })

    return {
        "schemaVersion": "chengziclass.registry-provenance-citation-gate.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "registry": str(registry_path), "spec": str(spec_path),
        "checkedCitations": checked, "checkedQuotes": quoted,
        "status": "fail" if failures else "pass",
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.parameters, args.spec)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(args.report)
    print(json.dumps({k: report[k] for k in
                      ("status", "checkedCitations", "checkedQuotes")},
                     ensure_ascii=False, indent=2))
    for failure in report["failures"][:20]:
        print(f"  {failure['code']}  {failure['path']}  {failure.get('quote') or failure.get('note')}")
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
