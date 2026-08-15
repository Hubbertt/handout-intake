#!/usr/bin/env python3
"""Read-only QA for standard PDFs assembled around Word content PDFs."""

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
import hashlib
import os
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageFont

from summer_scope_filter import active_scope, filter_item_map, merge_extra


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
PDF_ROOT = RUN_DIR / "standard-pdf"
OUT_DIR = RUN_DIR / "standard-pdf-qa"
REPORT = OUT_DIR / "standard_pdf_binding_qa.json"
ASSEMBLY_REPORT = PDF_ROOT / "standard_pdf_export_report.json"

PDFS = filter_item_map(merge_extra({
    "g07_en": {
        "pdf": PDF_ROOT / "g07_en/2026-暑假班-七年级-上册-英语-学生版-讲义.pdf",
        "expectedFirstBodyText": "目录",
        "forbiddenTocPageMarkers": ["第一部分", "核心词汇", "单词预习", "课堂练习"],
    },
    "g07_cn": {
        "pdf": PDF_ROOT / "g07_cn/2026-暑假班-七年级-上册-语文-学生版-讲义.pdf",
        "expectedFirstBodyText": "目录",
    },
    "g08_ph": {
        "pdf": PDF_ROOT / "g08_ph/2026-暑假班-八年级-上册-物理-学生版-讲义.pdf",
        "expectedFirstBodyText": "目录",
    },
    "g08_en": {
        "pdf": PDF_ROOT / "g08_en/2026-暑假班-八年级-上册-英语-学生版-讲义.pdf",
        "expectedFirstBodyText": "目录",
        "forbiddenTocPageMarkers": ["第一部分", "核心词汇", "单词预习", "课堂练习"],
    },
    "g08_cn": {
        "pdf": PDF_ROOT / "g08_cn/2026-暑假班-八年级-上册-语文-学生版-讲义.pdf",
        "expectedFirstBodyText": "目录",
    },
    "g08_ch": {
        "pdf": PDF_ROOT / "g08_ch/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.pdf",
        "expectedFirstBodyText": "目录",
    },
    "g08_ch_t34": {
        "pdf": PDF_ROOT / "g08_ch_t34/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.pdf",
        "expectedFirstBodyText": "目录",
    },
}, lambda key, entry: {
    "pdf": PDF_ROOT / key / entry["pdfName"],
    "expectedFirstBodyText": entry.get("expectedFirstBodyText", "目录"),
    **({"forbiddenTocPageMarkers": entry["forbiddenTocPageMarkers"]}
       if entry.get("forbiddenTocPageMarkers") else {}),
}))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_binding_assembly() -> dict[str, Any]:
    if not ASSEMBLY_REPORT.exists():
        raise SystemExit(f"missing PDF binding assembly report: {ASSEMBLY_REPORT}")
    report = json.loads(ASSEMBLY_REPORT.read_text(encoding="utf-8"))
    if (
        report.get("engine")
        != "PDF binding assembly from Microsoft Word content-master PDFs"
        or report.get("status") != "ready"
        or report.get("policyId")
        != "chengziclass.pdf-binding-assembly-after-word-export.v1"
    ):
        raise SystemExit(
            "standard PDFs are not registered outputs of the current binding assembly; "
            f"current status={report.get('status')} engine={report.get('engine')} "
            f"policy={report.get('policyId')}"
        )
    return report


def assembly_result_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in report.get("results", []) if item.get("status") == "pass"}


def page_nonwhite_ratio(page: fitz.Page, dpi: int = 72) -> float:
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    white = Image.new("RGB", img.size, "white")
    diff = ImageChops.difference(img, white).convert("L")
    bbox = diff.point(lambda x: 255 if x > 12 else 0).getbbox()
    if bbox is None:
        return 0.0
    mask = diff.point(lambda x: 1 if x > 12 else 0)
    return sum(mask.getdata()) / (img.width * img.height)


def render_page(page: fitz.Page, out: Path, label: str, dpi: int = 96) -> None:
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 24)
    except Exception:
        font = ImageFont.load_default()
    draw.rectangle([0, 0, img.width, 36], fill="white")
    draw.text((12, 6), label, fill="black", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)


def make_contact_sheet(images: list[Path], out: Path, cols: int = 3) -> None:
    thumbs = []
    for path in images:
        img = Image.open(path).convert("RGB")
        img.thumbnail((360, 510))
        canvas = Image.new("RGB", (380, 550), "white")
        canvas.paste(img, ((380 - img.width) // 2, 20))
        thumbs.append(canvas)
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 380, rows * 550), "white")
    for i, img in enumerate(thumbs):
        sheet.paste(img, ((i % cols) * 380, (i // cols) * 550))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def bottom_page_number_candidates(page: fitz.Page) -> list[str]:
    bottom_y = page.rect.height - 54
    candidates: list[tuple[float, str]] = []
    for block in page.get_text("blocks"):
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        normalized = " ".join(str(text).split())
        if y0 >= bottom_y and normalized.isdigit():
            candidates.append(((x0 + x1) / 2, normalized))
    center_x = page.rect.width / 2
    candidates.sort(key=lambda item: abs(item[0] - center_x))
    return [value for _center, value in candidates]


def bottom_page_number(page: fitz.Page) -> str | None:
    candidates = bottom_page_number_candidates(page)
    return candidates[0] if candidates else None


def qa_pdf(
    key: str,
    cfg: dict[str, Any],
    assembly: dict[str, Any],
) -> dict[str, Any]:
    pdf = cfg["pdf"]
    doc = fitz.open(pdf)
    page_count = doc.page_count
    expected_page_count = int(assembly["pageCount"])
    content_page_count = int(assembly["contentPageCount"])
    first_content_page = int(assembly["firstContentPage"])
    last_content_page = first_content_page + content_page_count - 1
    blank_pages = sorted(int(page) for page in assembly["blankPagesExpected"])
    cover_pages = [int(page) for page in (assembly.get("coverPages") or [1])]
    back_cover_pages = [
        int(page)
        for page in (assembly.get("backCoverPages") or [assembly["backCoverPage"]])
    ]
    front_cover_page = cover_pages[0]
    back_cover_page = back_cover_pages[-1]
    front_blank_pages = [page for page in blank_pages if page < first_content_page]
    visible_number_start = 1
    visible_number_end = content_page_count
    # A Word-side even-page parity pad is the last content page: it must be a
    # pure blank page and is excluded from the visible footer-number sequence.
    content_end_pad_page = None
    if 1 <= last_content_page <= page_count:
        pad_candidate = doc[last_content_page - 1]
        if (
            not pad_candidate.get_text("text").strip()
            and page_nonwhite_ratio(pad_candidate) <= 0.003
        ):
            content_end_pad_page = last_content_page
            visible_number_end = content_page_count - 1
    sizes = []
    bad_sizes = []
    for i, page in enumerate(doc, 1):
        w, h = page.rect.width, page.rect.height
        sizes.append((round(w, 2), round(h, 2)))
        if not (594 <= w <= 596 and 841 <= h <= 843):
            bad_sizes.append({"page": i, "width": w, "height": h})

    selected = [
        *cover_pages,
        first_content_page,
        min(page_count, last_content_page),
        *back_cover_pages,
    ]
    selected.extend(blank_pages)
    selected = sorted(set(p for p in selected if 1 <= p <= page_count))

    page_info = []
    rendered = []
    for page_no in selected:
        page = doc[page_no - 1]
        text = page.get_text("text").strip()
        ratio = page_nonwhite_ratio(page)
        out = OUT_DIR / key / f"page-{page_no:03d}.png"
        render_page(page, out, f"{key} p{page_no}/{page_count}")
        rendered.append(out)
        page_info.append(
            {
                "page": page_no,
                "textPreview": text[:120],
                "nonwhiteRatio": round(ratio, 5),
                "rendered": str(out),
            }
        )

    blank_failures = []
    for page_no in blank_pages:
        page = doc[page_no - 1]
        text = page.get_text("text").strip()
        ratio = page_nonwhite_ratio(page)
        if text or ratio > 0.003:
            blank_failures.append({"page": page_no, "textPreview": text[:80], "nonwhiteRatio": ratio})

    page_number_failures = []
    skipped_blank_number_failures = []
    for physical_page in range(first_content_page, last_content_page + 1):
        if physical_page == content_end_pad_page:
            continue
        expected = str(visible_number_start + physical_page - first_content_page)
        candidates = bottom_page_number_candidates(doc[physical_page - 1]) if 1 <= physical_page <= page_count else []
        actual = candidates[0] if candidates else None
        if len(candidates) != 1 or actual != expected:
            page_number_failures.append(
                {
                    "physicalPage": physical_page,
                    "expected": expected,
                    "actual": actual,
                    "candidates": candidates,
                    "reason": "duplicate-or-missing-footer-number" if len(candidates) != 1 else "wrong-footer-number",
                }
            )
    for physical_page in blank_pages:
        candidates = bottom_page_number_candidates(doc[physical_page - 1]) if 1 <= physical_page <= page_count else []
        if candidates:
            skipped_blank_number_failures.append(
                {
                    "physicalPage": physical_page,
                    "actual": candidates[0],
                    "candidates": candidates,
                }
            )

    first_content_text = doc[first_content_page - 1].get_text("text").strip() if page_count >= first_content_page else ""
    forbidden_toc_markers = [
        marker for marker in cfg.get("forbiddenTocPageMarkers", [])
        if marker in first_content_text
    ]
    cover_ratios = {page: page_nonwhite_ratio(doc[page - 1]) for page in cover_pages}
    back_ratios = {page: page_nonwhite_ratio(doc[page - 1]) for page in back_cover_pages}
    cover_ratio = min(cover_ratios.values())
    back_ratio = min(back_ratios.values())
    pdf_sha256 = sha256_file(pdf)
    expected_pdf_sha256 = str(assembly.get("standardPdfSha256") or "")
    expected_word_sha256 = str(assembly.get("docxSha256") or "")
    exported_word_sha256 = sha256_file(Path(assembly["docx"]))
    content_pdf_path = Path(assembly["contentPdf"])
    expected_content_pdf_sha256 = str(assembly.get("contentPdfSha256") or "")
    actual_content_pdf_sha256 = sha256_file(content_pdf_path)
    content_render_mismatches = []
    with fitz.open(content_pdf_path) as content_doc:
        if content_doc.page_count != content_page_count:
            content_render_mismatches.append(
                {
                    "reason": "content-page-count-mismatch",
                    "expected": content_page_count,
                    "actual": content_doc.page_count,
                }
            )
        else:
            for index in range(content_page_count):
                source_pix = content_doc[index].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
                assembled_pix = doc[first_content_page - 1 + index].get_pixmap(
                    matrix=fitz.Matrix(1, 1),
                    alpha=False,
                )
                if (
                    source_pix.width != assembled_pix.width
                    or source_pix.height != assembled_pix.height
                    or hashlib.sha256(source_pix.samples).digest()
                    != hashlib.sha256(assembled_pix.samples).digest()
                ):
                    content_render_mismatches.append(
                        {
                            "contentPage": index + 1,
                            "assembledPhysicalPage": first_content_page + index,
                        }
                    )

    contact = OUT_DIR / key / "contact-sheet-key-pages.png"
    make_contact_sheet(rendered, contact)
    doc.close()

    checks = {
        "a4": not bad_sizes,
        "coverNonblank": cover_ratio > 0.005,
        "frontBlanks": not any(f["page"] in front_blank_pages for f in blank_failures),
        "contentEndParityPadBlankWhenPresent": (
            content_end_pad_page is None
            or not any(f["page"] == content_end_pad_page for f in blank_failures)
        ),
        "bodyStartsAfterFrontBlanks": cfg["expectedFirstBodyText"] in first_content_text,
        "tocPageOwnsPage": not forbidden_toc_markers,
        "pageCountMatchesAssemblyReport": content_page_count > 0 and page_count == expected_page_count,
        "visibleWordPageNumbersContinuous": (
            visible_number_end - visible_number_start + 1
            == content_page_count - (1 if content_end_pad_page else 0)
            and not page_number_failures
        ),
        "bindingBlankPagesUnnumbered": not skipped_blank_number_failures,
        "bindingBlankPagesBlank": not any(f["page"] in blank_pages for f in blank_failures),
        "backCoverNonblank": back_ratio > 0.005,
        "wordSourceHashMatchesAssemblyReport": exported_word_sha256 == expected_word_sha256,
        "contentPdfHashMatchesAssemblyReport": (
            bool(expected_content_pdf_sha256)
            and actual_content_pdf_sha256 == expected_content_pdf_sha256
        ),
        "standardPdfHashMatchesAssemblyReport": bool(expected_pdf_sha256) and pdf_sha256 == expected_pdf_sha256,
        "assembledContentPagesVisuallyIdentical": not content_render_mismatches,
    }
    return {
        "key": key,
        "pdf": str(pdf),
        "pageCount": page_count,
        "contentPageCountComputed": content_page_count,
        "bindingAssembly": {
            "wordSha256": expected_word_sha256,
            "pageCount": expected_page_count,
            "firstContentPhysicalPage": first_content_page,
            "lastContentPhysicalPage": last_content_page,
            "visiblePageNumberStart": visible_number_start,
            "visiblePageNumberEnd": visible_number_end,
            "frontCoverPhysicalPage": front_cover_page,
            "backCoverPhysicalPage": back_cover_page,
        },
        "wordAndContentPdf": {
            "docx": assembly.get("docx"),
            "docxSha256": exported_word_sha256,
            "contentPdf": str(content_pdf_path),
            "contentPdfSha256": actual_content_pdf_sha256,
            "standardPdfSha256": expected_pdf_sha256,
            "actualStandardPdfSha256": pdf_sha256,
        },
        "uniquePageSizes": sorted({f"{w} x {h}" for w, h in sizes}),
        "badSizes": bad_sizes[:10],
        "blankPagesExpected": blank_pages,
        "blankFailures": blank_failures,
        "contentPageNumberFailures": page_number_failures[:20],
        "contentSkippedBlankNumberFailures": skipped_blank_number_failures[:20],
        "contentRenderMismatches": content_render_mismatches[:20],
        "tocPageForbiddenMarkers": forbidden_toc_markers,
        "coverNonwhiteRatio": round(cover_ratio, 5),
        "backCoverNonwhiteRatio": round(back_ratio, 5),
        "selectedPages": page_info,
        "contactSheet": str(contact),
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
    }


def run_binding_qa(
    pdfs: dict[str, dict[str, Any]],
    assembly_report: dict[str, Any],
) -> dict[str, Any]:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise RuntimeError(
            "DIRECT_INVOCATION_BLOCKED: start from "
            "run_summer_word_prepress_workflow.py --pdf-release"
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    assembly_map = assembly_result_map(assembly_report)
    missing_assemblies = sorted(set(pdfs) - set(assembly_map))
    if missing_assemblies:
        raise SystemExit(
            f"missing PDF binding assembly results for active keys: {missing_assemblies}"
        )
    results = [
        qa_pdf(key, cfg, assembly_map[key])
        for key, cfg in pdfs.items()
    ]
    report = {
        "schemaVersion": "chengziclass.standard-pdf-binding-assembly-qa.v3",
        "activeScope": active_scope(),
        "standardPdfSource": {
            "report": str(ASSEMBLY_REPORT),
            "reportSha256": sha256_file(ASSEMBLY_REPORT),
            "engine": assembly_report.get("engine"),
            "status": assembly_report.get("status"),
            "policyId": assembly_report.get("policyId"),
        },
        "rule": (
            "Read-only QA of the registered PDF binding assembly. The Word content pages "
            "must render identically; only the declared cover, back-cover, binding blanks "
            "and parity blank may be added."
        ),
        "results": results,
        "summary": {
            "pdfCount": len(results),
            "passed": sum(1 for r in results if r["status"] == "pass"),
            "failed": sum(1 for r in results if r["status"] != "pass"),
        },
    }
    report["summary"]["ready"] = (
        bool(results)
        and report["summary"]["failed"] == 0
        and len(results) == len(pdfs)
    )
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    report = run_binding_qa(PDFS, require_binding_assembly())
    print(REPORT)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
