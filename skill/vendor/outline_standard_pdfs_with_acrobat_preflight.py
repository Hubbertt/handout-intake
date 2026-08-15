#!/usr/bin/env python3
"""Outline accepted standard PDFs with Adobe Acrobat Preflight.

This is the formal print-PDF path for the summer handout workflow. It uses the
Acrobat Preflight profile named "将字体转换为空心" and deliberately avoids SVG
reconstruction, rasterization, LibreOffice, Preview, or PyMuPDF redraw output.
PyMuPDF is used only for splitting, recombining, and QA inspection.
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
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

from summer_scope_filter import active_scope, item_in_scope, merge_extra


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
STANDARD_ROOT = RUN_DIR / "standard-pdf"
OUTLINED_ROOT = RUN_DIR / "outlined-pdf"
WORK_ROOT = RUN_DIR / "acrobat-outline-work"
REPORT_PATH = OUTLINED_ROOT / "acrobat_preflight_outline_report.json"
STANDARD_EXPORT_REPORT = STANDARD_ROOT / "standard_pdf_export_report.json"
BINDING_QA_REPORT = RUN_DIR / "standard-pdf-qa/standard_pdf_binding_qa.json"
PROFILE_NAME = "将字体转换为空心"
TEXT_OP_RE = re.compile(rb"(?<![A-Za-z])(?:BT|ET|Tf|Tj|TJ)(?![A-Za-z])")
DATA_VOLUME = Path("/System/Volumes/Data")
BYTES_PER_GIB = 1024**3
DEFAULT_MIN_FREE_GB = 120.0
DEFAULT_MAX_ACROBAT_RSS_GB = 12.0
DEFAULT_RESTART_EVERY_PAGES = 0
ACROBAT_APP_PATTERN = "/Applications/Adobe Acrobat DC/Adobe Acrobat.app"


@dataclass(frozen=True)
class Material:
    key: str
    standard_pdf: Path
    output_pdf: Path


MATERIALS: dict[str, Material] = {
    "g07_en": Material(
        "g07_en",
        STANDARD_ROOT / "g07_en/2026-暑假班-七年级-上册-英语-学生版-讲义.pdf",
        OUTLINED_ROOT / "g07_en/2026-暑假班-七年级-上册-英语-学生版-讲义.pdf",
    ),
    "g07_cn": Material(
        "g07_cn",
        STANDARD_ROOT / "g07_cn/2026-暑假班-七年级-上册-语文-学生版-讲义.pdf",
        OUTLINED_ROOT / "g07_cn/2026-暑假班-七年级-上册-语文-学生版-讲义.pdf",
    ),
    "g08_ph": Material(
        "g08_ph",
        STANDARD_ROOT / "g08_ph/2026-暑假班-八年级-上册-物理-学生版-讲义.pdf",
        OUTLINED_ROOT / "g08_ph/2026-暑假班-八年级-上册-物理-学生版-讲义.pdf",
    ),
    "g08_en": Material(
        "g08_en",
        STANDARD_ROOT / "g08_en/2026-暑假班-八年级-上册-英语-学生版-讲义.pdf",
        OUTLINED_ROOT / "g08_en/2026-暑假班-八年级-上册-英语-学生版-讲义.pdf",
    ),
    "g08_cn": Material(
        "g08_cn",
        STANDARD_ROOT / "g08_cn/2026-暑假班-八年级-上册-语文-学生版-讲义.pdf",
        OUTLINED_ROOT / "g08_cn/2026-暑假班-八年级-上册-语文-学生版-讲义.pdf",
    ),
    "g08_ch": Material(
        "g08_ch",
        STANDARD_ROOT / "g08_ch/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.pdf",
        OUTLINED_ROOT / "g08_ch/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.pdf",
    ),
    "g08_ch_t34": Material(
        "g08_ch_t34",
        STANDARD_ROOT / "g08_ch_t34/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.pdf",
        OUTLINED_ROOT / "g08_ch_t34/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.pdf",
    ),
}
# 外部注册的册按同一条路径规则落位:standard-pdf/<key>/<pdfName> → outlined/<key>/<pdfName>。
# 规则与内置项完全一致,不给试制件开小灶——开了小灶,试制件走通也证明不了生产件走得通。
MATERIALS = merge_extra(MATERIALS, lambda key, entry: Material(
    key,
    STANDARD_ROOT / key / entry["pdfName"],
    OUTLINED_ROOT / key / entry["pdfName"],
))
MATERIALS = {key: material for key, material in MATERIALS.items() if item_in_scope(material, key)}


def require_internal_invocation() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise RuntimeError(
            "DIRECT_INVOCATION_BLOCKED: start from "
            "run_summer_word_prepress_workflow.py --pdf-release"
        )


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def acrobat_pids() -> list[int]:
    proc = subprocess.run(
        ["pgrep", "-f", ACROBAT_APP_PATTERN],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return [int(pid) for pid in proc.stdout.split() if pid.strip().isdigit()]


def acrobat_rss_gb() -> float:
    proc = subprocess.run(
        ["ps", "-axo", "rss=,command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    rss_kb = 0
    for line in proc.stdout.splitlines():
        if ACROBAT_APP_PATTERN not in line:
            continue
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit():
            rss_kb += int(parts[0])
    return rss_kb * 1024 / BYTES_PER_GIB


def resource_snapshot() -> dict[str, float]:
    usage = shutil.disk_usage(DATA_VOLUME)
    return {
        "dataVolumeFreeGB": round(usage.free / BYTES_PER_GIB, 2),
        "dataVolumeUsedGB": round(usage.used / BYTES_PER_GIB, 2),
        "acrobatRssGB": round(acrobat_rss_gb(), 2),
    }


def resource_guard_message(snapshot: dict[str, float], *, min_free_gb: float, max_acrobat_rss_gb: float) -> str | None:
    if snapshot["dataVolumeFreeGB"] < min_free_gb:
        return (
            f"resource guard: Data volume free space {snapshot['dataVolumeFreeGB']}GB "
            f"is below required {min_free_gb}GB"
        )
    if snapshot["acrobatRssGB"] > max_acrobat_rss_gb:
        return (
            f"resource guard: Acrobat RSS {snapshot['acrobatRssGB']}GB "
            f"is above allowed {max_acrobat_rss_gb}GB"
        )
    return None


def ensure_resource_budget(*, min_free_gb: float, max_acrobat_rss_gb: float, context: str) -> dict[str, float]:
    snapshot = resource_snapshot()
    message = resource_guard_message(snapshot, min_free_gb=min_free_gb, max_acrobat_rss_gb=max_acrobat_rss_gb)
    if message:
        reset_acrobat_processes()
        raise SystemExit(f"{context}: {message}")
    return snapshot


def reset_acrobat_processes() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Adobe Acrobat" to quit'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    time.sleep(3)
    if not acrobat_pids():
        return
    subprocess.run(
        ["pkill", "-9", "-f", ACROBAT_APP_PATTERN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    time.sleep(2)


def applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def require_standard_sources(
    materials: dict[str, Material] | None = None,
) -> dict[str, Any]:
    selected_materials = MATERIALS if materials is None else materials
    checks: dict[str, Any] = {}
    for report_path in (STANDARD_EXPORT_REPORT, BINDING_QA_REPORT):
        if not report_path.exists():
            raise SystemExit(f"missing required preflight report: {report_path}")
        checks[str(report_path)] = json.loads(report_path.read_text(encoding="utf-8"))
    assembly_report = checks[str(STANDARD_EXPORT_REPORT)]
    binding_qa_report = checks[str(BINDING_QA_REPORT)]
    export_summary = assembly_report.get("summary", {})
    qa_summary = binding_qa_report.get("summary", {})
    if (
        assembly_report.get("schemaVersion")
        != "chengziclass.standard-pdf-binding-assembly.v1"
        or assembly_report.get("engine")
        != "PDF binding assembly from Microsoft Word content-master PDFs"
        or assembly_report.get("policyId")
        != "chengziclass.pdf-binding-assembly-after-word-export.v1"
        or not export_summary.get("ready")
        or export_summary.get("failed") != 0
    ):
        raise SystemExit(f"standard PDF export is not accepted: {export_summary}")
    if (
        binding_qa_report.get("schemaVersion")
        != "chengziclass.standard-pdf-binding-assembly-qa.v3"
        or (binding_qa_report.get("standardPdfSource") or {}).get("policyId")
        != "chengziclass.pdf-binding-assembly-after-word-export.v1"
        or (binding_qa_report.get("standardPdfSource") or {}).get("reportSha256")
        != sha256(STANDARD_EXPORT_REPORT)
        or qa_summary.get("failed") != 0
        or not qa_summary.get("ready")
    ):
        raise SystemExit(f"standard PDF binding QA is not accepted: {qa_summary}")
    accepted_keys = {
        str(item.get("key"))
        for item in binding_qa_report.get("results", [])
        if isinstance(item, dict)
        and item.get("status") == "pass"
        and all((item.get("checks") or {}).values())
    }
    missing_keys = sorted(set(selected_materials) - accepted_keys)
    if missing_keys:
        raise SystemExit(
            f"standard PDF binding QA is missing accepted active keys: {missing_keys}"
        )
    return checks


def backup_existing_output(timestamp: str, *, force: bool) -> Path | None:
    if not OUTLINED_ROOT.exists():
        return None
    backup = RUN_DIR / f"outlined-pdf-before-acrobat-preflight-{timestamp}"
    if backup.exists():
        raise SystemExit(f"backup destination already exists: {backup}")
    shutil.copytree(OUTLINED_ROOT, backup)
    if force:
        shutil.rmtree(OUTLINED_ROOT)
    return backup


def split_pages(src: Path, page_dir: Path) -> int:
    page_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(src)
    for index in range(doc.page_count):
        out = fitz.open()
        out.insert_pdf(doc, from_page=index, to_page=index)
        out.save(page_dir / f"page-{index + 1:04d}.pdf", garbage=4, deflate=True)
        out.close()
    count = doc.page_count
    doc.close()
    return count


def run_acrobat_preflight(
    src: Path,
    dst: Path,
    timeout: int,
    *,
    min_free_gb: float,
    max_acrobat_rss_gb: float,
) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    resource_before = ensure_resource_budget(
        min_free_gb=min_free_gb,
        max_acrobat_rss_gb=max_acrobat_rss_gb,
        context=f"before Acrobat preflight for {src.name}",
    )
    js = (
        f"var out={json.dumps(str(dst), ensure_ascii=False)};"
        f"var profile=Preflight.getProfileByName({json.dumps(PROFILE_NAME, ensure_ascii=False)});"
        "if(!profile){throw new Error('missing Acrobat Preflight profile');}"
        "var result=this.preflight(profile, false, app.thermometer);"
        "this.saveAs(out);"
        "'OK '+profile.name+' fixed='+result.numFixed+' notFixed='+result.numNotFixed+"
        "' errors='+result.numErrors+' warnings='+result.numWarnings+' infos='+result.numInfos;"
    )
    script = f"""
with timeout of {max(timeout + 60, 180)} seconds
  tell application "Adobe Acrobat"
    set skip warnings to true
    close all docs saving no
    open POSIX file {applescript_quote(str(src))} as alias invisible true
    set js to {applescript_quote(js)}
    set resultText to do script js
    close all docs saving no
    return resultText
  end tell
end timeout
"""
    started = time.time()
    try:
        proc = subprocess.run(
            ["osascript"],
            input=script,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        returncode = proc.returncode
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = ((exc.stderr or "").strip() if isinstance(exc.stderr, str) else "") or f"osascript timed out after {timeout} seconds"
    elapsed = round(time.time() - started, 2)
    resource_after = resource_snapshot()
    guard_message = resource_guard_message(
        resource_after,
        min_free_gb=min_free_gb,
        max_acrobat_rss_gb=max_acrobat_rss_gb,
    )
    if guard_message:
        returncode = 125
        stderr = "\n".join(item for item in (stderr, guard_message) if item)
        reset_acrobat_processes()
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "elapsedSec": elapsed,
        "outExists": dst.exists(),
        "outBytes": dst.stat().st_size if dst.exists() else 0,
        "resourceBefore": resource_before,
        "resourceAfter": resource_after,
    }


def pdf_page_qa(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    doc = fitz.open(path)
    font_pages: list[int] = []
    text_pages: list[int] = []
    text_operator_pages: list[int] = []
    bad_sizes: list[dict[str, float]] = []
    render_errors: list[dict[str, Any]] = []
    for page_index, page in enumerate(doc, start=1):
        if page.get_fonts(full=True):
            font_pages.append(page_index)
        if page.get_text("text").strip():
            text_pages.append(page_index)
        for xref in page.get_contents() or []:
            stream = doc.xref_stream(xref) or b""
            if TEXT_OP_RE.search(stream):
                text_operator_pages.append(page_index)
                break
        rect = page.rect
        if not (594 <= rect.width <= 596 and 841 <= rect.height <= 843):
            bad_sizes.append({"page": page_index, "width": round(rect.width, 2), "height": round(rect.height, 2)})
        try:
            page.get_pixmap(matrix=fitz.Matrix(72 / 72, 72 / 72), alpha=False)
        except Exception as exc:  # pragma: no cover - diagnostic path
            render_errors.append({"page": page_index, "error": str(exc)})
    page_count = doc.page_count
    doc.close()
    return {
        "exists": True,
        "pageCount": page_count,
        "fontPageCount": len(font_pages),
        "fontPages": font_pages[:50],
        "extractableTextPageCount": len(text_pages),
        "extractableTextPages": text_pages[:50],
        "textOperatorPageCount": len(text_operator_pages),
        "textOperatorPages": text_operator_pages[:50],
        "badSizes": bad_sizes[:50],
        "renderErrors": render_errors[:50],
        "status": "pass"
        if not font_pages and not text_pages and not text_operator_pages and not bad_sizes and not render_errors
        else "fail",
    }


def combine_pages(page_outputs: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    combined = fitz.open()
    for path in page_outputs:
        src = fitz.open(path)
        combined.insert_pdf(src)
        src.close()
    combined.save(output, garbage=4, deflate=True)
    combined.close()


def process_material(
    material: Material,
    *,
    timeout: int,
    whole_timeout: int,
    resume: bool,
    per_page_only: bool,
    min_free_gb: float,
    max_acrobat_rss_gb: float,
    acrobat_restart_every_pages: int,
) -> dict[str, Any]:
    require_internal_invocation()
    if not material.standard_pdf.exists():
        raise SystemExit(f"missing standard PDF: {material.standard_pdf}")
    work_dir = WORK_ROOT / material.key
    status_path = work_dir / "status.jsonl"
    work_dir.mkdir(parents=True, exist_ok=True)
    source_doc = fitz.open(material.standard_pdf)
    page_count = source_doc.page_count
    source_doc.close()
    if resume and material.output_pdf.exists():
        existing_qa = pdf_page_qa(material.output_pdf)
        if existing_qa.get("status") == "pass" and existing_qa.get("pageCount") == page_count:
            return {
                "key": material.key,
                "source": str(material.standard_pdf),
                "output": str(material.output_pdf),
                "sourceSha256": sha256(material.standard_pdf),
                "outputSha256": sha256(material.output_pdf),
                "sourceBytes": material.standard_pdf.stat().st_size,
                "outputBytes": material.output_pdf.stat().st_size,
                "pageCount": page_count,
                "samePageCount": True,
                "status": "pass",
                "finalQa": existing_qa,
                "elapsedSec": 0,
                "method": "resume: existing Acrobat Preflight output revalidated",
            }
    page_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    started = time.time()
    with status_path.open("a", encoding="utf-8") as status:
        status.write(json.dumps({"stage": "start", "key": material.key, "pages": page_count, "ts": now_stamp()}, ensure_ascii=False) + "\n")
        if not per_page_only:
            whole_output = work_dir / "whole-document-acrobat.pdf"
            whole_result: dict[str, Any] | None = None
            for attempt in range(1, 3):
                acrobat = run_acrobat_preflight(
                    material.standard_pdf,
                    whole_output,
                    whole_timeout,
                    min_free_gb=min_free_gb,
                    max_acrobat_rss_gb=max_acrobat_rss_gb,
                )
                whole_qa = pdf_page_qa(whole_output)
                whole_output_is_current = whole_output.exists() and whole_output.stat().st_mtime >= material.standard_pdf.stat().st_mtime
                whole_output_valid = (
                    acrobat["outExists"]
                    and whole_output_is_current
                    and whole_qa.get("status") == "pass"
                    and whole_qa.get("pageCount") == page_count
                )
                whole_ok = whole_output_valid
                whole_result = {
                    "stage": "whole-document",
                    "key": material.key,
                    "attempt": attempt,
                    "ok": whole_ok,
                    "outputIsCurrent": whole_output_is_current,
                    "acrobat": acrobat,
                    "qa": whole_qa,
                    "out": str(whole_output),
                    "elapsedSec": round(time.time() - started, 2),
                    "ts": now_stamp(),
                }
                status.write(json.dumps(whole_result, ensure_ascii=False) + "\n")
                status.flush()
                if whole_ok:
                    material.output_pdf.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(whole_output, material.output_pdf)
                    return {
                        "key": material.key,
                        "source": str(material.standard_pdf),
                        "output": str(material.output_pdf),
                        "sourceSha256": sha256(material.standard_pdf),
                        "outputSha256": sha256(material.output_pdf),
                        "sourceBytes": material.standard_pdf.stat().st_size,
                        "outputBytes": material.output_pdf.stat().st_size,
                        "pageCount": page_count,
                        "samePageCount": True,
                        "status": "pass",
                        "finalQa": whole_qa,
                        "statusLog": str(status_path),
                        "elapsedSec": round(time.time() - started, 2),
                        "method": "Adobe Acrobat Preflight profile 将字体转换为空心, whole-document execution",
                    }
                reset_acrobat_processes()
            status.write(
                json.dumps(
                    {
                        "stage": "fallback-to-per-page",
                        "key": material.key,
                        "reason": "whole-document Acrobat output did not pass outline QA after retry",
                        "wholeResult": whole_result,
                        "ts": now_stamp(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            status.flush()
            reset_acrobat_processes()
        split_dir = work_dir / "split"
        page_out_dir = work_dir / "outlined-pages"
        split_pages(material.standard_pdf, split_dir)
        for page_no in range(1, page_count + 1):
            src_page = split_dir / f"page-{page_no:04d}.pdf"
            out_page = page_out_dir / f"page-{page_no:04d}-outlined.pdf"
            out_page_is_current = out_page.exists() and out_page.stat().st_mtime >= material.standard_pdf.stat().st_mtime
            if resume and out_page_is_current and pdf_page_qa(out_page).get("status") == "pass":
                result = {
                    "stage": "page",
                    "page": page_no,
                    "ok": True,
                    "skipped": True,
                    "outputIsCurrent": True,
                    "out": str(out_page),
                }
            else:
                result = {}
                for attempt in range(1, 3):
                    acrobat = run_acrobat_preflight(
                        src_page,
                        out_page,
                        timeout,
                        min_free_gb=min_free_gb,
                        max_acrobat_rss_gb=max_acrobat_rss_gb,
                    )
                    qa = pdf_page_qa(out_page)
                    output_is_current = out_page.exists() and out_page.stat().st_mtime >= src_page.stat().st_mtime
                    ok = acrobat["outExists"] and output_is_current and qa.get("status") == "pass" and qa.get("pageCount") == 1
                    result = {
                        "stage": "page",
                        "page": page_no,
                        "attempt": attempt,
                        "ok": ok,
                        "skipped": False,
                        "outputIsCurrent": output_is_current,
                        "acrobat": acrobat,
                        "qa": qa,
                        "out": str(out_page),
                    }
                    if ok:
                        break
                    reset_acrobat_processes()
                if not ok:
                    failures.append(result)
                elif acrobat_restart_every_pages > 0 and page_no % acrobat_restart_every_pages == 0:
                    reset_acrobat_processes()
            page_results.append(result)
            if page_no <= 3 or page_no == page_count or page_no % 20 == 0 or failures:
                status.write(
                    json.dumps(
                        {
                            "stage": "progress",
                            "key": material.key,
                            "page": page_no,
                            "pages": page_count,
                            "failures": len(failures),
                            "elapsedSec": round(time.time() - started, 2),
                            "last": result,
                            "ts": now_stamp(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                status.flush()
            if failures:
                break
    if failures:
        return {
            "key": material.key,
            "source": str(material.standard_pdf),
            "output": str(material.output_pdf),
            "pageCount": page_count,
            "status": "fail",
            "failures": failures[:20],
            "statusLog": str(status_path),
        }
    page_outputs = [page_out_dir / f"page-{page_no:04d}-outlined.pdf" for page_no in range(1, page_count + 1)]
    combine_pages(page_outputs, material.output_pdf)
    final_qa = pdf_page_qa(material.output_pdf)
    source_doc = fitz.open(material.standard_pdf)
    output_doc = fitz.open(material.output_pdf)
    same_page_count = source_doc.page_count == output_doc.page_count
    source_doc.close()
    output_doc.close()
    status = "pass" if same_page_count and final_qa.get("status") == "pass" else "fail"
    return {
        "key": material.key,
        "source": str(material.standard_pdf),
        "output": str(material.output_pdf),
        "sourceSha256": sha256(material.standard_pdf),
        "outputSha256": sha256(material.output_pdf),
        "sourceBytes": material.standard_pdf.stat().st_size,
        "outputBytes": material.output_pdf.stat().st_size,
        "pageCount": page_count,
        "samePageCount": same_page_count,
        "status": status,
        "finalQa": final_qa,
        "statusLog": str(status_path),
        "elapsedSec": round(time.time() - started, 2),
        "method": "Adobe Acrobat Preflight profile 将字体转换为空心, per-page execution, recombined in original page order",
    }


def main() -> None:
    require_internal_invocation()
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", nargs="*", default=list(MATERIALS.keys()), choices=sorted(MATERIALS.keys()))
    parser.add_argument("--force", action="store_true", help="Back up and replace the current outlined-pdf directory.")
    parser.add_argument("--resume", action="store_true", help="Reuse already passing per-page Acrobat outputs.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--whole-timeout", type=int, default=900)
    parser.add_argument("--per-page-only", action="store_true")
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help="Abort before or after Acrobat work if the internal Data volume has less free space than this.",
    )
    parser.add_argument(
        "--max-acrobat-rss-gb",
        type=float,
        default=DEFAULT_MAX_ACROBAT_RSS_GB,
        help="Abort if Adobe Acrobat processes use more resident memory than this.",
    )
    parser.add_argument(
        "--acrobat-restart-every-pages",
        type=int,
        default=DEFAULT_RESTART_EVERY_PAGES,
        help="During per-page fallback, restart Acrobat after this many successful pages. The default 0 disables page-interval restarts.",
    )
    parser.add_argument(
        "--skip-standard-source-reports",
        action="store_true",
        help="Run only the Acrobat outline step for a just-assembled standard PDF. This is for the PDF four-step workflow and does not run Word repair or PDF QA gates.",
    )
    args = parser.parse_args()

    required_reports = (
        {
            "skippedByRequest": True,
            "reason": "PDF four-step workflow: the requested PDF stage is only page numbers, blank pages, covers, and text outlining.",
        }
        if args.skip_standard_source_reports
        else require_standard_sources()
    )
    initial_resources = ensure_resource_budget(
        min_free_gb=args.min_free_gb,
        max_acrobat_rss_gb=args.max_acrobat_rss_gb,
        context="before Acrobat outline workflow",
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_existing_output(timestamp, force=args.force)
    OUTLINED_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for key in args.keys:
            material = MATERIALS[key]
            reset_acrobat_processes()
            print(f"ACROBAT_OUTLINE_START {key}", flush=True)
            result = process_material(
                material,
                timeout=args.timeout,
                whole_timeout=args.whole_timeout,
                resume=args.resume,
                per_page_only=args.per_page_only,
                min_free_gb=args.min_free_gb,
                max_acrobat_rss_gb=args.max_acrobat_rss_gb,
                acrobat_restart_every_pages=args.acrobat_restart_every_pages,
            )
            results.append(result)
            print(f"ACROBAT_OUTLINE_DONE {key} {result['status']}", flush=True)
            if result["status"] != "pass":
                break
    finally:
        reset_acrobat_processes()
    summary = {
        "pdfCount": len(results),
        "passed": sum(1 for item in results if item["status"] == "pass"),
        "failed": sum(1 for item in results if item["status"] != "pass"),
        "ready": results and all(item["status"] == "pass" for item in results) and len(results) == len(args.keys),
    }
    report = {
        "schemaVersion": "chengziclass.acrobat-preflight-outline.v1",
        "generatedAt": now_stamp(),
        "activeScope": active_scope(),
        "profileName": PROFILE_NAME,
        "resourceGuards": {
            "dataVolume": str(DATA_VOLUME),
            "minFreeGB": args.min_free_gb,
            "maxAcrobatRssGB": args.max_acrobat_rss_gb,
            "acrobatRestartEveryPages": args.acrobat_restart_every_pages,
            "initialResources": initial_resources,
            "finalResources": resource_snapshot(),
        },
        "method": "Adobe Acrobat Preflight/Print Production, per-page Convert Fonts to Outlines, recombined without rasterizing pages.",
        "forbiddenAlternativesNotUsed": [
            "SVG reconstruction",
            "whole-page rasterization",
            "system Preview export",
            "LibreOffice PDF export as outline",
            "PyMuPDF text-as-path generation",
        ],
        "standardPdfPreflightReports": required_reports,
        "backupOfPreviousOutlinedRoot": str(backup) if backup else None,
        "results": results,
        "summary": summary,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "summary": summary}, ensure_ascii=False), flush=True)
    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
