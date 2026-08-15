#!/usr/bin/env python3
"""Read every native Word page and fail on unexpected blank content pages.

The document is copied into Microsoft Word's container sandbox before Word is
opened.  The project candidate is never opened or saved by Word.  This audit is
an internal stage of the unique summer handout workflow and creates read-only
page evidence only; it cannot generate or modify PDF content.
"""

from __future__ import annotations

# ---- 环境定位(handout-intake vendor 化时加入)------------------------------------
# 本文件拷自生产线 scripts/formal,那里写死本机路径是合理的——它只在这一台机器跑。
# 进包后不行:「智能体拿到就能用」的前提是不把一台机器的布局编进方法。
# 规则:环境变量优先,其次 runtime/paths.json,最后才是可移植的默认(Path.home)。
# 找不到时如实报缺,不猜。
import os as _os
from pathlib import Path as _P
def _hi_env(name, default=None):
    v = _os.environ.get(name)
    if v: return _P(v)
    cfg = _P(__file__).resolve().parents[2] / "runtime" / "paths.json"
    if cfg.exists():
        try:
            import json as _j
            v = _j.loads(cfg.read_text(encoding="utf-8")).get(name)
            if v: return _P(_os.path.expanduser(v))
        except Exception:
            pass
    return _P(default) if default is not None else None
# ----------------------------------------------------------------------------

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
DEFAULT_SANDBOX_ROOT = Path(
    str(_P.home() / "Library/Containers/com.microsoft.Word/Data/Documents") + "/"
    "ChengziClassWordProbe/native-page-audit"
)


class NativePageAuditError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def word_page_records(stage: Path, timeout: int) -> tuple[int, list[dict[str, int]]]:
    script = f'''
set docPath to {applescript_string(str(stage))}
set docRef to missing value
try
    tell application "Microsoft Word"
        launch
        set docRef to open file name docPath add to recent files false read only true
        set pageCount to compute statistics docRef statistic statistic pages
        if pageCount < 1 then error "invalid native page count"
        set fullRange to text object of docRef
        set docEnd to end of content of fullRange
        set inlinePageCounts to {{}}
        set floatingPageCounts to {{}}
        repeat pageCount times
            set end of inlinePageCounts to 0
            set end of floatingPageCounts to 0
        end repeat
        repeat with shapeIndex from 1 to count of inline shapes of docRef
            set shapePage to 0
            repeat with attemptIndex from 1 to 10
                try
                    set shapeRange to text object of inline shape shapeIndex of docRef
                    set shapePage to (get range information shapeRange information type active end page number) as integer
                    exit repeat
                on error
                    delay 0.2
                end try
            end repeat
            if shapePage is 0 then error "unmapped inline shape " & (shapeIndex as text)
            if shapePage is greater than or equal to 1 and shapePage is less than or equal to pageCount then
                set item shapePage of inlinePageCounts to (item shapePage of inlinePageCounts) + 1
            end if
        end repeat
        repeat with shapeIndex from 1 to count of shapes of docRef
            set shapePage to 0
            repeat with attemptIndex from 1 to 10
                try
                    set shapeRange to anchor of shape shapeIndex of docRef
                    set shapePage to (get range information shapeRange information type active end page number) as integer
                    exit repeat
                on error
                    delay 0.2
                end try
            end repeat
            if shapePage is 0 then error "unmapped floating shape " & (shapeIndex as text)
            if shapePage is greater than or equal to 1 and shapePage is less than or equal to pageCount then
                set item shapePage of floatingPageCounts to (item shapePage of floatingPageCounts) + 1
            end if
        end repeat
        set pageStartRange to create range docRef start 0 end 0
        set outText to "PAGES=" & (pageCount as text) & linefeed
        repeat with pageIndex from 1 to pageCount
            set startPos to start of content of pageStartRange
            if pageIndex < pageCount then
                set nextRange to go to next pageStartRange what goto a page item
                set endPos to (start of content of nextRange) - 1
            else
                set endPos to docEnd
            end if
            if endPos < startPos then set endPos to startPos
            set pageRange to create range docRef start startPos end endPos
            set pageText to content of pageRange
            set charCount to count characters of pageText
            set wordCount to count of words of pageRange
            set paragraphCount to count of paragraphs of pageRange
            set fieldCount to count of fields of pageRange
            set inlineCount to item pageIndex of inlinePageCounts
            set tableCount to count of tables of pageRange
            set shapeCount to item pageIndex of floatingPageCounts
            set outText to outText & (pageIndex as text) & tab & (startPos as text) & tab & (endPos as text) & tab & (charCount as text) & tab & (wordCount as text) & tab & (paragraphCount as text) & tab & (fieldCount as text) & tab & (inlineCount as text) & tab & (tableCount as text) & tab & (shapeCount as text) & linefeed
            if pageIndex < pageCount then set pageStartRange to nextRange
        end repeat
        close docRef saving no
    end tell
on error errText number errNumber
    try
        tell application "Microsoft Word"
            if docRef is not missing value then close docRef saving no
        end tell
    end try
    error errText number errNumber
end try
return outText
'''
    # Word's object model intermittently raises Apple Event errors (-1728,
    # -2700) while walking page ranges and shape anchors of long documents;
    # the failure is transient and identical input converges on a clean read,
    # so retry the bounded native read before failing.
    proc = None
    last_error = ""
    for attempt in range(3):
        try:
            proc = subprocess.run(
                ["osascript", "-"],
                input=script,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NativePageAuditError(
                f"word-native-page-audit-timeout-after-{timeout}s"
            ) from exc
        if proc.returncode == 0:
            break
        last_error = proc.stderr.strip() or proc.stdout.strip() or f"exit-{proc.returncode}"
        if "execution error" not in last_error:
            break
        time.sleep(2)
    if proc is None or proc.returncode != 0:
        raise NativePageAuditError(
            "word-native-page-audit-failed: " + last_error
        )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("PAGES="):
        raise NativePageAuditError("missing-native-page-count-readback")
    raw_count = lines[0].split("=", 1)[1]
    if not raw_count.isdigit():
        raise NativePageAuditError(f"invalid-native-page-count: {raw_count!r}")
    page_count = int(raw_count)
    fields = (
        "page",
        "start",
        "end",
        "characters",
        "words",
        "paragraphs",
        "fields",
        "inlineShapes",
        "tables",
        "shapes",
    )
    records: list[dict[str, int]] = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) != len(fields) or not all(value.isdigit() for value in values):
            raise NativePageAuditError(f"invalid-native-page-record: {line!r}")
        records.append(dict(zip(fields, map(int, values), strict=True)))
    if len(records) != page_count:
        raise NativePageAuditError(
            f"native-page-record-count-mismatch: pages={page_count}, "
            f"records={len(records)}"
        )
    if [record["page"] for record in records] != list(range(1, page_count + 1)):
        raise NativePageAuditError("native-page-record-order-mismatch")
    if any(
        current["start"] <= previous["start"]
        for previous, current in zip(records, records[1:])
    ):
        raise NativePageAuditError("native-page-start-offsets-not-increasing")
    return page_count, records


def audit(
    *,
    candidate: Path,
    sandbox_root: Path,
    report_path: Path,
    timeout: int,
    complete_print_build_report: Path | None,
    semantic_build_report: Path | None = None,
) -> dict[str, Any]:
    if not candidate.is_file():
        raise NativePageAuditError(f"missing-candidate: {candidate}")
    before_hash = sha256_file(candidate)
    sandbox_root.mkdir(parents=True, exist_ok=True)
    stage = sandbox_root / (
        f"page-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{before_hash[:12]}-{candidate.name}"
    )
    if stage.exists():
        raise NativePageAuditError(f"sandbox-stage-already-exists: {stage}")
    shutil.copy2(candidate, stage)
    try:
        page_count, pages = word_page_records(stage, timeout)
        stage_hash = sha256_file(stage)
        if stage_hash != before_hash:
            raise NativePageAuditError(
                f"HOLD_INPUT_DRIFT: candidate={before_hash}, sandbox={stage_hash}"
            )
        candidate_readback_hash = sha256_file(candidate)
        if candidate_readback_hash != before_hash:
            raise NativePageAuditError(
                f"HOLD_INPUT_DRIFT: before={before_hash}, "
                f"candidateReadback={candidate_readback_hash}"
            )
        blank_pages = [
            record["page"]
            for record in pages
            if record["inlineShapes"] == 0
            and record["tables"] == 0
            and record["shapes"] == 0
            and record["fields"] == 0
            and record["characters"] <= 4
        ]
        expected_blank_pages: list[int] = []
        complete_print_evidence: dict[str, Any] | None = None
        parity_pad_evidence: dict[str, Any] | None = None
        if semantic_build_report is not None:
            if not semantic_build_report.is_file():
                raise NativePageAuditError(
                    f"missing-semantic-build-report: {semantic_build_report}"
                )
            build_value = json.loads(semantic_build_report.read_text(encoding="utf-8"))
            if str(build_value.get("outputSha256") or "") != before_hash:
                raise NativePageAuditError(
                    "HOLD_INPUT_DRIFT: semantic build report output hash is not current"
                )
            pad = build_value.get("evenPageParityPad") or {}
            if pad.get("status") == "added":
                pad_page = int(pad.get("padPage") or 0)
                if pad_page != page_count:
                    raise NativePageAuditError(
                        f"parity-pad-page-not-last: padPage={pad_page} pages={page_count}"
                    )
                expected_blank_pages = [pad_page]
                parity_pad_evidence = {
                    "buildReport": str(semantic_build_report),
                    "buildReportSha256": sha256_file(semantic_build_report),
                    "padPage": pad_page,
                }
        if complete_print_build_report is not None:
            if not complete_print_build_report.is_file():
                raise NativePageAuditError(
                    f"missing-complete-print-build-report: {complete_print_build_report}"
                )
            build = json.loads(complete_print_build_report.read_text(encoding="utf-8"))
            if (
                build.get("schemaVersion")
                != "chengziclass.summer-word-complete-print-master-build.v1"
                or build.get("status")
                != "candidate-built-awaiting-word-native-acceptance"
            ):
                raise NativePageAuditError("invalid-complete-print-build-report")
            results = build.get("results")
            if not isinstance(results, list) or len(results) != 1:
                raise NativePageAuditError(
                    "complete-print-build-report-must-contain-one-result"
                )
            build_result = results[0]
            if Path(str(build_result.get("output"))).resolve() != candidate.resolve():
                raise NativePageAuditError(
                    "complete-print-build-report-output-does-not-match-candidate"
                )
            if build_result.get("outputSha256") != before_hash:
                raise NativePageAuditError(
                    "HOLD_INPUT_DRIFT: complete-print build output hash is not current"
                )
            expected_page_count = int(build_result.get("expectedCompleteWordPages", 0))
            content_pages = int(build_result.get("contentPages", 0))
            first_content_page = int(
                build_result.get("expectedFirstContentPhysicalPage", 0)
            )
            if expected_page_count != page_count or first_content_page != 5:
                raise NativePageAuditError(
                    "complete-print-native-pagination-does-not-match-build-report"
                )
            last_content_page = first_content_page + content_pages - 1
            expected_blank_pages = [
                2,
                3,
                4,
                *range(last_content_page + 1, expected_page_count),
            ]
            complete_print_evidence = {
                "buildReport": str(complete_print_build_report),
                "buildReportSha256": sha256_file(complete_print_build_report),
                "key": build_result.get("key"),
                "expectedPageCount": expected_page_count,
                "firstContentPhysicalPage": first_content_page,
                "lastContentPhysicalPage": last_content_page,
                "frontCoverPhysicalPage": 1,
                "backCoverPhysicalPage": expected_page_count,
                "expectedBlankPages": expected_blank_pages,
            }
        near_blank_pages = [
            record["page"]
            for record in pages
            if record["page"] not in blank_pages
            and record["words"] <= 3
            and record["inlineShapes"] == 0
            and record["tables"] == 0
            and record["shapes"] == 0
            and record["characters"] <= 24
        ]
        unexpected_blank_pages = sorted(set(blank_pages) - set(expected_blank_pages))
        missing_expected_blank_pages = sorted(
            set(expected_blank_pages) - set(blank_pages)
        )
        failures = []
        if unexpected_blank_pages:
            failures.append(
                {
                    "code": "unexpected-word-native-blank-pages",
                    "pages": unexpected_blank_pages,
                }
            )
        if missing_expected_blank_pages:
            failures.append(
                {
                    "code": "expected-binding-blank-pages-contain-content",
                    "pages": missing_expected_blank_pages,
                }
            )
        report = {
            "schemaVersion": "chengziclass.word-native-page-content-audit.v1",
            "generatedAt": now_iso(),
            "status": "pass" if not failures else "fail",
            "candidatePath": str(candidate),
            "candidateSha256": before_hash,
            "sandboxBoundary": {
                "root": str(sandbox_root),
                "wordOpenedPath": str(stage),
                "projectPathOpenedDirectlyInWord": False,
                "candidateModified": False,
                "pdfGenerated": False,
            },
            "pageCount": page_count,
            "blankPages": blank_pages,
            "expectedBlankPages": expected_blank_pages,
            "completePrintEvidence": complete_print_evidence,
            "evenPageParityPadEvidence": parity_pad_evidence,
            "nearBlankPages": near_blank_pages,
            "pagesWithInlineShapes": sum(
                record["inlineShapes"] > 0 for record in pages
            ),
            "inlineShapeCount": sum(record["inlineShapes"] for record in pages),
            "floatingShapeCount": sum(record["shapes"] for record in pages),
            "pagesWithTables": sum(record["tables"] > 0 for record in pages),
            "pageRecords": pages,
            "failures": failures,
            "warnings": (
                [{"code": "near-blank-word-native-pages", "pages": near_blank_pages}]
                if near_blank_pages
                else []
            ),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = report_path.with_name(
            f".{report_path.name}.tmp-{os.getpid()}-{time.time_ns()}"
        )
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, report_path)
        stage.unlink()
        return report
    except Exception:
        # Keep failed sandbox evidence bounded to this audit root.
        raise


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This Word-native page audit is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py."
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--sandbox-root", type=Path, default=DEFAULT_SANDBOX_ROOT)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--complete-print-build-report", type=Path)
    parser.add_argument("--semantic-build-report", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    report_path = args.report.resolve()
    try:
        report = audit(
            candidate=candidate,
            sandbox_root=args.sandbox_root.resolve(),
            report_path=report_path,
            timeout=args.timeout,
            complete_print_build_report=(
                args.complete_print_build_report.resolve()
                if args.complete_print_build_report
                else None
            ),
            semantic_build_report=(
                args.semantic_build_report.resolve()
                if args.semantic_build_report
                else None
            ),
        )
    except Exception as exc:
        failure_report = {
            "schemaVersion": "chengziclass.word-native-page-content-audit.v1",
            "generatedAt": now_iso(),
            "status": "fail",
            "candidatePath": str(candidate),
            "candidateSha256": sha256_file(candidate) if candidate.is_file() else None,
            "pageCount": None,
            "blankPages": [],
            "expectedBlankPages": [],
            "completePrintEvidence": None,
            "evenPageParityPadEvidence": None,
            "nearBlankPages": [],
            "pageRecords": [],
            "failures": [
                {
                    "code": "word-native-page-content-audit-exception",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
            ],
            "warnings": [],
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = report_path.with_name(
            f".{report_path.name}.tmp-{os.getpid()}-{time.time_ns()}"
        )
        temp.write_text(
            json.dumps(failure_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, report_path)
        raise
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
