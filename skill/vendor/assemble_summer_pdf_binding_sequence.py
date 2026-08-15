#!/usr/bin/env python3
"""Assemble cover and binding-only pages around a Word-exported content PDF.

This module never changes TOC/body text, fonts, page numbers, tables, images,
or content pagination. It only applies the registered binding-page sequence.
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

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

from summer_scope_filter import active_scope, filter_item_map, merge_extra


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
PARAMS = _hi_env("HANDOUT_INTAKE_PARAMS_PATH", str(ROOT / "templates/summer-class-layout/summer_class_module_parameters.current.json"))
CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
PDF_BINDING_ASSEMBLY_POLICY_ID = "chengziclass.pdf-binding-assembly-after-word-export.v1"
FORMAL_ROOT = ROOT / "library/教辅资料/上海"
ASSET_ROOT = ROOT / "assets/cover-backcover/上海"
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
CONTENT_ROOT = RUN_DIR / "content-pdf"
STANDARD_ROOT = RUN_DIR / "standard-pdf"
CONTENT_REPORT = CONTENT_ROOT / "content_pdf_export_report.json"
STANDARD_REPORT = STANDARD_ROOT / "standard_pdf_export_report.json"

DOCS: dict[str, dict[str, Any]] = filter_item_map(merge_extra({
    "g07_en": {
        "docx": FORMAL_ROOT / "初中/七年级/上册/英语/word/2026-暑假班-七年级-上册-英语-学生版-讲义.docx",
        "assetDir": ASSET_ROOT / "初中/七年级/上册/英语",
        "cover": "Cover_EN_G07_S1.pdf",
        "back": "BackCover_EN_G07_S1.pdf",
    },
    "g07_cn": {
        "docx": FORMAL_ROOT / "初中/七年级/上册/语文/word/2026-暑假班-七年级-上册-语文-学生版-讲义.docx",
        "assetDir": ASSET_ROOT / "初中/七年级/上册/语文",
        "cover": "Cover_CN_G07_S1.svg",
        "back": "BackCover_CN_G07_S1.svg",
    },
    "g08_ph": {
        "docx": FORMAL_ROOT / "初中/八年级/上册/物理/word/2026-暑假班-八年级-上册-物理-学生版-讲义.docx",
        "assetDir": ASSET_ROOT / "初中/八年级/上册/物理",
        "cover": "Cover_PH_G08_S1.svg",
        "back": "BackCover_PH_G08_S1.svg",
    },
    "g08_en": {
        "docx": FORMAL_ROOT / "初中/八年级/上册/英语/word/2026-暑假班-八年级-上册-英语-学生版-讲义.docx",
        "assetDir": ASSET_ROOT / "初中/八年级/上册/英语",
        "cover": "Cover_EN_G08_S1.pdf",
        "back": "BackCover_EN_G08_S1.pdf",
    },
    "g08_cn": {
        "docx": FORMAL_ROOT / "初中/八年级/上册/语文/word/2026-暑假班-八年级-上册-语文-学生版-讲义.docx",
        "assetDir": ASSET_ROOT / "初中/八年级/上册/语文",
        "cover": "Cover_CN_G08_S1.svg",
        "back": "BackCover_CN_G08_S1.svg",
    },
    "g08_ch": {
        "docx": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.docx",
        "assetDir": ASSET_ROOT / "初中/八年级/全一册/化学",
        "cover": "Cover_CH_G08_全一册.svg",
        "back": "BackCover_CH_G08_全一册.svg",
    },
    "g08_ch_t34": {
        "docx": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.docx",
        "assetDir": ASSET_ROOT / "初中/八年级/全一册/化学",
        # 定制封皮:年级后加册次,由 build_summer_custom_cover 从底版生成。
        # 底版 Cover_CH_G08_全一册 不动,它还给其他化学全一册资料用。
        "cover": "Cover_CH_G08_第二册.svg",
        "back": "BackCover_CH_G08_全一册.svg",
    },
}, lambda key, entry: {
    "docx": Path(entry["docx"]),
    "assetDir": Path(entry["assetDir"]),
    "cover": entry["cover"],
    "back": entry["back"],
}))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_content_export() -> dict[str, Any]:
    if not CONTENT_REPORT.exists():
        raise SystemExit(f"missing Word content PDF report: {CONTENT_REPORT}")
    report = json.loads(CONTENT_REPORT.read_text(encoding="utf-8"))
    if (
        report.get("schemaVersion")
        != "chengziclass.word-content-master-pdf-export.v1"
        or report.get("engine") != "Microsoft Word content-master PDF export"
        or report.get("status") != "ready"
        or not (report.get("summary") or {}).get("ready")
    ):
        raise SystemExit(
            "content PDFs are not registered as accepted Microsoft Word content-master output; "
            f"current status={report.get('status')} engine={report.get('engine')}"
        )
    return report


def require_current_content_export(
    key: str,
    docx: Path,
    content_pdf: Path,
) -> dict[str, Any]:
    report = require_content_export()
    matches = [
        item
        for item in report.get("results", [])
        if isinstance(item, dict) and item.get("key") == key
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Word content PDF report has no unique result for {key}: matches={len(matches)}"
        )
    item = matches[0]
    expected = {
        "status": "pass",
        "docx": str(docx),
        "docxSha256": sha256_file(docx),
        "wordExportPdf": str(content_pdf),
        "wordExportPdfSha256": sha256_file(content_pdf),
    }
    drift = {
        field: {"expected": value, "actual": item.get(field)}
        for field, value in expected.items()
        if item.get(field) != value
    }
    if drift:
        raise RuntimeError(f"Word content PDF report drift for {key}: {drift}")
    return item


def content_pdf_path(key: str, docx: Path) -> Path:
    return CONTENT_ROOT / key / f"{docx.stem}.pdf"


def standard_pdf_path(key: str, docx: Path) -> Path:
    return STANDARD_ROOT / key / f"{docx.stem}.pdf"


def archive_existing_pdf(pdf: Path, timestamp: str) -> Path | None:
    if not pdf.exists():
        return None
    cache = STANDARD_ROOT / "_cache" / pdf.parent.name
    cache.mkdir(parents=True, exist_ok=True)
    archived = cache / f"{pdf.stem}-before-pdf-binding-assembly-{timestamp}{pdf.suffix}"
    shutil.move(str(pdf), str(archived))
    return archived


def save_single_page(src_pdf: Path, page_index: int, dst_pdf: Path) -> None:
    src = fitz.open(src_pdf)
    out = fitz.open()
    out.insert_pdf(src, from_page=page_index, to_page=page_index)
    dst_pdf.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst_pdf, garbage=4, deflate=True)
    out.close()
    src.close()


def ensure_derived_assets(key: str, cfg: dict[str, Any]) -> list[dict[str, str]]:
    asset_dir = Path(cfg["assetDir"])
    created: list[dict[str, str]] = []
    # Resolved the same way the binding itself resolves them. This used to
    # test the configured filename literally while the binding preferred the
    # .pdf beside it: a custom cover supplied as PDF then read as 「missing」
    # here and as 「present」 four functions later, and the run died asking for
    # a previous edition to extract from — under a name that had just changed.
    try:
        cover = preferred_asset_path(cfg, "cover")
        back = preferred_asset_path(cfg, "back")
    except FileNotFoundError:
        cover = asset_dir / cfg["cover"]
        back = asset_dir / cfg["back"]
    if cover.exists() and back.exists():
        return created

    previous = standard_pdf_path(key, cfg["docx"])
    if not previous.exists():
        raise RuntimeError(
            f"Missing PDF binding assets for {key}, and no previous standard PDF exists for extraction: {previous}"
        )
    asset_dir.mkdir(parents=True, exist_ok=True)
    if not cover.exists():
        save_single_page(previous, 0, cover)
        created.append({"kind": "cover", "asset": str(cover), "source": str(previous), "sourcePage": "1"})
    if not back.exists():
        src = fitz.open(previous)
        last_index = src.page_count - 1
        src.close()
        save_single_page(previous, last_index, back)
        created.append({"kind": "back", "asset": str(back), "source": str(previous), "sourcePage": "last"})
    return created


def preferred_asset_path(cfg: dict[str, Any], kind: str) -> Path:
    configured = Path(cfg["assetDir"]) / cfg[kind]
    pdf_candidate = configured.with_suffix(".pdf")
    if pdf_candidate.exists():
        return pdf_candidate
    if configured.exists():
        return configured
    raise FileNotFoundError(f"Missing {kind} asset: {configured}")


def open_asset_as_pdf(path: Path) -> fitz.Document:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return fitz.open(path)
    if suffix == ".svg":
        svg_doc = fitz.open(path)
        try:
            pdf_bytes = svg_doc.convert_to_pdf()
        finally:
            svg_doc.close()
        return fitz.open("pdf", pdf_bytes)
    raise RuntimeError(f"Unsupported binding asset type: {path}")


def append_scaled_page(out: fitz.Document, src: fitz.Document, page_index: int, rect: fitz.Rect) -> None:
    page = out.new_page(width=rect.width, height=rect.height)
    page.show_pdf_page(rect, src, page_index)


def add_blank_page(out: fitz.Document, rect: fitz.Rect) -> int:
    page = out.new_page(width=rect.width, height=rect.height)
    return page.number + 1


def assemble_one(key: str, cfg: dict[str, Any], timestamp: str) -> dict[str, Any]:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise RuntimeError(
            "DIRECT_INVOCATION_BLOCKED: start from "
            "run_summer_word_prepress_workflow.py --pdf-release"
        )
    docx = cfg["docx"]
    content_pdf = content_pdf_path(key, docx)
    final_pdf = standard_pdf_path(key, docx)
    if not content_pdf.exists():
        return {"key": key, "status": "fail", "reason": "content-pdf-missing", "contentPdf": str(content_pdf)}
    content_export = require_current_content_export(key, docx, content_pdf)

    derived_assets = ensure_derived_assets(key, cfg)
    cover_asset = preferred_asset_path(cfg, "cover")
    back_asset = preferred_asset_path(cfg, "back")
    archived = archive_existing_pdf(final_pdf, timestamp)

    content = fitz.open(content_pdf)
    content_pages_before_assembly = content.page_count
    if content_pages_before_assembly % 2 != 0:
        content.close()
        return {
            "key": key,
            "status": "fail",
            "reason": "content-pdf-odd-page-count; the Word content master must be padded to an even page count inside Word before export",
            "contentPdf": str(content_pdf),
            "contentPageCount": content_pages_before_assembly,
        }
    cover = open_asset_as_pdf(cover_asset)
    back = open_asset_as_pdf(back_asset)
    rect = content[0].rect
    out = fitz.open()

    append_scaled_page(out, cover, 0, rect)
    front_blank_after_first_cover = add_blank_page(out, rect)
    append_scaled_page(out, cover, 0, rect)
    front_blank_before_content = add_blank_page(out, rect)
    out.insert_pdf(content)
    rear_blank_after_content = add_blank_page(out, rect)
    append_scaled_page(out, back, 0, rect)
    rear_blank_before_final_back_cover = add_blank_page(out, rect)
    append_scaled_page(out, back, 0, rect)

    final_pdf.parent.mkdir(parents=True, exist_ok=True)
    out.save(final_pdf, garbage=4, deflate=True)
    page_count = out.page_count
    out.close()
    content_pages = content.page_count
    content.close()
    cover.close()
    back.close()

    blank_pages = [
        front_blank_after_first_cover,
        front_blank_before_content,
        rear_blank_after_content,
        rear_blank_before_final_back_cover,
    ]
    return {
        "key": key,
        "status": "pass",
        "docx": str(docx),
        "docxSha256": sha256_file(docx),
        "contentPdf": str(content_pdf),
        "contentPdfSha256": sha256_file(content_pdf),
        "contentExportReport": str(CONTENT_REPORT),
        "contentExportResultSha256": content_export.get("wordExportPdfSha256"),
        "standardPdf": str(final_pdf),
        "standardPdfSha256": sha256_file(final_pdf),
        "archivedPreviousStandardPdf": str(archived) if archived else None,
        "coverAsset": str(cover_asset),
        "backAsset": str(back_asset),
        "derivedAssets": derived_assets,
        "contentPageCount": content_pages,
        "coverPages": [1, 3],
        "frontBlankPage": front_blank_after_first_cover,
        "frontInsideBlankPage": front_blank_after_first_cover,
        "frontFlyleafBlankPages": [front_blank_after_first_cover, front_blank_before_content],
        "firstContentPage": 5,
        "contentParity": "word-even-page-count-required-no-pdf-parity-blank",
        "endBodyBlankAdded": False,
        "endBodyBlankPage": None,
        "rearFlyleafBlankPages": [rear_blank_after_content, rear_blank_before_final_back_cover],
        "fixedBlankBeforeBackCoverPage": rear_blank_before_final_back_cover,
        "backInsideBlankPage": rear_blank_before_final_back_cover,
        "backCoverPages": [page_count - 2, page_count],
        "backCoverPage": page_count,
        "blankPagesExpected": blank_pages,
        "pageCount": page_count,
    }


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This PDF binding assembler is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py --pdf-release."
        )
    params = json.loads(PARAMS.read_text(encoding="utf-8"))
    declared_order = ((params.get("modules") or {}).get("pdfBinding") or {}).get("pageOrder")
    implemented_order = [
        "front-cover",
        "front-blank-after-first-cover",
        "front-cover-second",
        "front-blank-before-content",
        "word-content-pdf-even-pages",
        "rear-blank-after-content",
        "back-cover-first",
        "rear-blank-before-final-back-cover",
        "back-cover",
    ]
    if declared_order != implemented_order:
        raise SystemExit(
            "pdfBinding.pageOrder in the parameter table does not match the "
            f"registered assembly sequence: {declared_order!r}"
        )
    STANDARD_ROOT.mkdir(parents=True, exist_ok=True)
    content_report = require_content_export()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    failed = False
    for key, cfg in DOCS.items():
        try:
            result = assemble_one(key, cfg, timestamp)
        except Exception as exc:
            result = {"key": key, "status": "fail", "error": str(exc)}
        results.append(result)
        if result.get("status") != "pass":
            failed = True
            break

    report = {
        "schemaVersion": "chengziclass.standard-pdf-binding-assembly.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timestamp": timestamp,
        "policyId": PDF_BINDING_ASSEMBLY_POLICY_ID,
        "engine": "PDF binding assembly from Microsoft Word content-master PDFs",
        "status": "failed" if failed else "ready",
        "activeScope": active_scope(),
        "source": {
            "wordContentPdfReport": str(CONTENT_REPORT),
            "contentPdfEngine": content_report.get("engine"),
            "contentPdfStatus": content_report.get("status"),
        },
        "rule": "Final standard PDF page order = front cover, front-cover-inside blank, front ordinary blank sheet recto, front ordinary blank sheet verso, Microsoft Word content PDF, optional content-end parity blank when the Word content PDF page count is odd, rear ordinary blank sheet recto, rear ordinary blank sheet verso, back-cover-inside blank, back cover. TOC and visible content page numbers are owned by Word. The first content PDF page becomes final PDF physical page 5. Binding-only blanks and covers never receive visible content page numbers.",
        "results": results,
        "summary": {
            "pdfCount": len(results),
            "passed": sum(1 for r in results if r.get("status") == "pass"),
            "failed": sum(1 for r in results if r.get("status") != "pass"),
            "ready": not failed and len(results) == len(DOCS),
        },
    }
    STANDARD_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(STANDARD_REPORT)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
