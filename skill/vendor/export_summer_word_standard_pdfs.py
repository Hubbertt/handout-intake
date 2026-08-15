#!/usr/bin/env python3
"""Export content PDFs from accepted formal Word masters.

The Word master owns only TOC/body content, visible PAGE fields, pagination,
and editable content layout. This module asks Microsoft Word to render that
content sequence. It never adds, removes, stamps, reorders, or repairs PDF
pages. Cover/back-cover and binding/parity blanks are added only by the
registered PDF assembly stage. LibreOffice/headless conversion is diagnostic-
only because it can reflow Chinese layout, section breaks, headers, footers,
and TOC tab stops differently from Word.

On macOS, Word opens only a sandbox copy under its own container, exports the
complete PDF there, and Python copies the accepted PDF back into the project.
The ``content-pdf`` staging directory contains the pre-binding Word export.
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

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from summer_scope_filter import active_scope, filter_doc_map, merge_extra
from summer_word_contract import (
    WORD_PROBE_ROOT,
    WordGateError,
    require_accepted_structure_manifest,
    require_current_docx,
)


CHENGZI_ROOT = _hi_env("HANDOUT_INTAKE_HOME", str(_P.home()))
ROOT = CHENGZI_ROOT / "projects/shared-assets/CZClassRoom/data/teaching-materials"
CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
FORMAL_ROOT = ROOT / "library/教辅资料/上海"
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
CONTENT_ROOT = RUN_DIR / "content-pdf"
REPORT = CONTENT_ROOT / "content_pdf_export_report.json"
WORD_CLEAN_OPEN_REPORT = RUN_DIR / "word_native_open_clean_probe_report.json"
STRUCTURE_MANIFEST_DIR = RUN_DIR / "structure-manifest"
SOFFICE = _hi_env("HANDOUT_INTAKE_SOFFICE", "/usr/local/bin/soffice")
ALLOW_LIBREOFFICE_DIAGNOSTIC = "CHENGZI_ALLOW_LIBREOFFICE_DIAGNOSTIC"
WORD_APP = Path("/Applications/Microsoft Word.app")
WORD_EXPORT_SANDBOX = WORD_PROBE_ROOT / "PdfExport"

DOCS = filter_doc_map(merge_extra({
    "g07_en": FORMAL_ROOT / "初中/七年级/上册/英语/word/2026-暑假班-七年级-上册-英语-学生版-讲义.docx",
    "g07_cn": FORMAL_ROOT / "初中/七年级/上册/语文/word/2026-暑假班-七年级-上册-语文-学生版-讲义.docx",
    "g08_ph": FORMAL_ROOT / "初中/八年级/上册/物理/word/2026-暑假班-八年级-上册-物理-学生版-讲义.docx",
    "g08_en": FORMAL_ROOT / "初中/八年级/上册/英语/word/2026-暑假班-八年级-上册-英语-学生版-讲义.docx",
    "g08_cn": FORMAL_ROOT / "初中/八年级/上册/语文/word/2026-暑假班-八年级-上册-语文-学生版-讲义.docx",
    "g08_ch": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.docx",
    "g08_ch_t34": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.docx",
}, lambda key, entry: Path(entry["docx"])))


def require_internal_invocation() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise RuntimeError(
            "DIRECT_INVOCATION_BLOCKED: start from "
            "run_summer_word_prepress_workflow.py --pdf-release"
        )


def applescript_string(value: Path | str) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def pdf_page_count(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("rb") as stream:
        return len(PdfReader(stream).pages)


def archive_existing_pdf(pdf: Path, timestamp: str) -> Path | None:
    if not pdf.exists():
        return None
    cache = CONTENT_ROOT / "_cache" / pdf.parent.name
    cache.mkdir(parents=True, exist_ok=True)
    archived = cache / f"{pdf.stem}-before-content-word-export-{timestamp}{pdf.suffix}"
    shutil.move(str(pdf), str(archived))
    return archived


def require_clean_open_report(docx: Path, gate: dict[str, object]) -> dict[str, object]:
    if not WORD_CLEAN_OPEN_REPORT.exists():
        raise WordGateError(
            f"Missing Word clean-open report: {WORD_CLEAN_OPEN_REPORT}. "
            "Run probe_word_native_open_clean.py before PDF export."
        )
    report = json.loads(WORD_CLEAN_OPEN_REPORT.read_text(encoding="utf-8"))
    expected_path = str(docx)
    expected_hash = gate["sha256"]
    for item in report.get("results", []):
        raw_path = item.get("path")
        if not raw_path:
            continue
        path = Path(str(raw_path))
        path_keys = {str(raw_path)}
        if path.is_absolute():
            path_keys.add(str(path.resolve()))
        else:
            path_keys.add(str((CHENGZI_ROOT / path).resolve()))
            path_keys.add(str((ROOT / path).resolve()))
        if expected_path not in path_keys:
            continue
        if item.get("sha256") != expected_hash:
            raise WordGateError(
                f"Word clean-open report is stale for {docx}: "
                f"report sha256={item.get('sha256')}, current sha256={expected_hash}."
            )
        if item.get("status") != "pass":
            raise WordGateError(f"Word native clean-open failed: {item.get('status')}")
        return {
            "report": str(WORD_CLEAN_OPEN_REPORT),
            "status": item.get("status"),
            "openedAt": item.get("openedAt"),
            "sha256": item.get("sha256"),
            "windows": item.get("windows"),
        }
    raise WordGateError(f"Word clean-open report has no current entry for {docx}.")


def export_with_word(docx: Path, pdf: Path, timeout: int = 600) -> dict:
    require_internal_invocation()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    if not WORD_APP.exists():
        return {
            "returncode": 127,
            "pdfExists": False,
            "stdoutTail": "",
            "stderrTail": f"Microsoft Word not found at {WORD_APP}",
        }
    sandbox_dir = WORD_EXPORT_SANDBOX / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox_docx = sandbox_dir / docx.name
    sandbox_pdf = sandbox_dir / pdf.name
    shutil.copy2(docx, sandbox_docx)

    script = f"""
on failIfRepairPromptExists()
    tell application "System Events"
        tell process "Microsoft Word"
            repeat with w in windows
                set windowName to name of w as text
                if windowName contains "显示修复" then error "Word repair state is not a valid PDF export source."
                if windowName starts with "文档" or windowName starts with "Document" then error "Word opened an untitled repaired document."
                try
                    set staticValues to (value of every static text of w) as text
                    if staticValues contains "发现无法读取" then error "Word reported unreadable content."
                    if staticValues contains "无法读取的内容" then error "Word reported unreadable content."
                    if staticValues contains "是否恢复" then error "Word asked to recover the document."
                    if staticValues contains "文件可能已经损坏" then error "Word reported a damaged file."
                    if staticValues contains "可能已经损坏" then error "Word reported a damaged file."
                end try
                if exists button "是(Y)" of w then error "Word displayed a recovery prompt."
                if exists button "是" of w then error "Word displayed a recovery prompt."
            end repeat
        end tell
    end tell
end failIfRepairPromptExists

on exportDoc(inputPath, outputPath)
    tell application "Microsoft Word"
        -- launch, never activate: 「不抢前台」 is a standing rule here.
        -- activate makes Word the frontmost application and takes the
        -- screen away from whoever is using the machine; launch only
        -- guarantees it is running, which is all `open file name` needs.
        launch
        set docRef to open file name inputPath read only true add to recent files false
        delay 2
    end tell
    tell application "Microsoft Word"
        repeat 90 times
            my failIfRepairPromptExists()
            if docRef is not missing value then exit repeat
            delay 1
        end repeat
        if docRef is missing value then
            error "Word did not finish opening the document before export timeout."
        end if
        try
            save as docRef file name outputPath file format format PDF add to recent files false
        on error errMsg number errNum
            try
                close docRef saving no
            end try
            error errMsg number errNum
        end try
        try
            close docRef saving no
        end try
    end tell
end exportDoc

exportDoc({applescript_string(sandbox_docx)}, {applescript_string(sandbox_pdf)})
"""
    try:
        proc = subprocess.run(
            ["osascript", "-"],
            input=script,
            cwd=str(RUN_DIR),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        copied_to_project = False
        if proc.returncode == 0 and sandbox_pdf.exists():
            shutil.copy2(sandbox_pdf, pdf)
            copied_to_project = True
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        return {
            "returncode": proc.returncode,
            "pdfExists": pdf.exists(),
            "pdfBytes": pdf.stat().st_size if pdf.exists() else None,
            "pageCount": pdf_page_count(pdf),
            "stdoutTail": proc.stdout[-1200:],
            "stderrTail": proc.stderr[-1200:],
            "wordSandbox": {
                "openedDocxCopy": str(sandbox_docx),
                "exportedPdfCopy": str(sandbox_pdf),
                "copiedToProject": copied_to_project,
                "sandboxRetained": not copied_to_project,
            },
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "pdfExists": pdf.exists(),
            "pdfBytes": pdf.stat().st_size if pdf.exists() else None,
            "pageCount": pdf_page_count(pdf),
            "stdoutTail": (exc.stdout or "")[-1200:],
            "stderrTail": (exc.stderr or "")[-1200:] + f"\nTimed out after {timeout}s",
            "wordSandbox": {
                "openedDocxCopy": str(sandbox_docx),
                "exportedPdfCopy": str(sandbox_pdf),
                "copiedToProject": False,
                "sandboxRetained": True,
            },
        }


def main() -> None:
    require_internal_invocation()
    CONTENT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.environ.get(ALLOW_LIBREOFFICE_DIAGNOSTIC) != "1":
        results = []
        failed = False
        for key, docx in DOCS.items():
            out_dir = CONTENT_ROOT / key
            pdf = out_dir / f"{docx.stem}.pdf"
            preflight: dict[str, object] = {}
            try:
                preflight = require_current_docx(docx)
                preflight["structureManifest"] = require_accepted_structure_manifest(
                    key, docx, STRUCTURE_MANIFEST_DIR
                )
                preflight["wordNativeOpen"] = require_clean_open_report(docx, preflight)
            except Exception as exc:
                result = {
                    "returncode": 2,
                    "pdfExists": False,
                    "stdoutTail": "",
                    "stderrTail": str(exc),
                    "preflight": preflight,
                }
                archived = None
            else:
                archived = archive_existing_pdf(pdf, timestamp)
                result = export_with_word(docx, pdf)
                result["preflight"] = preflight
            result.update(
                {
                    "key": key,
                    "docx": str(docx),
                    "pdf": str(pdf),
                    "archivedPreviousPdf": str(archived) if archived else None,
                }
            )
            results.append(result)
            if result["returncode"] != 0 or not result["pdfExists"]:
                failed = True
                break
        report = {
            "schemaVersion": "chengziclass.word-complete-pdf-export.v2",
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "timestamp": timestamp,
            "engine": "Microsoft Word content-master PDF export",
            "exportMethod": "Microsoft Word AppleScript save as PDF using Word's own rendering engine from a Word-container sandbox copy; LibreOffice is not used. The PDF is an exact render of the accepted TOC-and-body Word content master. No PDF-side content/page-number/layout repair is allowed; cover/back-cover and binding/parity blanks are added only by the registered assembly stage.",
            "diagnosticBoundary": "Inspect the Word master first. Diagnose the export chain only when the Word-native display is accepted and the exported PDF has a material visual or pagination difference.",
            "status": "failed" if failed else "ready",
            "activeScope": active_scope(),
            "structureManifestGate": {
                "required": True,
                "manifestDir": str(STRUCTURE_MANIFEST_DIR),
                "ready": (
                    not failed
                    and len(results) == len(DOCS)
                    and all(
                        (r.get("preflight") or {}).get("structureManifest", {}).get("status") == "pass"
                        for r in results
                    )
                ),
            },
            "results": results,
            "diagnosticFallback": {
                "enabledByEnv": ALLOW_LIBREOFFICE_DIAGNOSTIC,
                "allowedUse": "diagnostic-only; not accepted for formal QA, outlining, or formal library installation",
            },
            "summary": {
                "exported": sum(1 for r in results if r["returncode"] == 0 and r["pdfExists"]),
                "failed": sum(1 for r in results if r["returncode"] != 0 or not r["pdfExists"]),
                "ready": not failed and len(results) == len(DOCS),
            },
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(REPORT)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        if failed:
            raise SystemExit(2)
        return

    results = []
    for key, docx in DOCS.items():
        out_dir = CONTENT_ROOT / key
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = RUN_DIR / "lo-profiles" / f"{timestamp}-{key}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(SOFFICE),
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(out_dir),
            str(docx),
        ]
        env = os.environ.copy()
        env["HOME"] = str(RUN_DIR)
        proc = subprocess.run(cmd, cwd=str(RUN_DIR), env=env, text=True, capture_output=True, timeout=240)
        pdf = out_dir / f"{docx.stem}.pdf"
        results.append({
            "key": key,
            "docx": str(docx),
            "pdf": str(pdf),
            "returncode": proc.returncode,
            "pdfExists": pdf.exists(),
            "stdoutTail": proc.stdout[-1200:],
            "stderrTail": proc.stderr[-1200:],
        })
        if proc.returncode != 0 or not pdf.exists():
            REPORT.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(2)
    report = {
            "schemaVersion": "chengziclass.word-complete-pdf-export.v2",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timestamp": timestamp,
        "engine": "LibreOffice headless PDF conversion",
        "activeScope": active_scope(),
        "status": "diagnostic-only",
        "scope": "Diagnostic export only. Do not use these PDFs for formal QA, outlining, or formal PDF installation.",
        "results": results,
        "summary": {
            "exported": len(results),
            "failed": sum(1 for r in results if r["returncode"] != 0 or not r["pdfExists"]),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(REPORT)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
