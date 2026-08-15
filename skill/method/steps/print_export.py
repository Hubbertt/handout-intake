#!/usr/bin/env python3
"""付印线·导出(工序 s6a-export-pdf):用 Word 原生引擎把当前 Word 导成内容 PDF。

**为什么不直接调 vendor 的 main()。**
export_summer_word_standard_pdfs.main() 写的报告 schema 是 word-complete-pdf-export.v2,
而下一步装订要求的是 word-content-master-pdf-export.v1。生产线里两者能接上,
是因为 run_summer_pdf_four_step_workflow 自己驱动 export_with_word 并写后一种报告——
它从不调那个 main()。首版照抄 main(),装订步当场拒收:
「content PDFs are not registered as accepted Microsoft Word content-master output」。
本步照四步编排器的写法:调能力函数,写装订步认的那份报告。

前置(由 consumes 强制):净开探针、合规审计、结构清单必须是**当前 Word** 的。

用法:
  print_export.py --workspace X [--volume V]
退出码 0=导出成功且报告 ready 1=否则
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime

from _bootstrap import chain_from_argv  # noqa: E402
import _printline as P  # noqa: E402

CHAIN = chain_from_argv(__doc__)


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    cfg = P.volume_print_config(CHAIN)
    if cfg["missing"]:
        print(json.dumps({"step": "s6a-export-pdf", "status": "unbound",
                          "missing": cfg["missing"]}, ensure_ascii=False, indent=1))
        return 1
    m = P.bind_export(CHAIN, cfg["key"])
    import fitz  # PyMuPDF,由安装向导保证

    key, docx = cfg["key"], CHAIN.only("word")
    out_dir = m.CONTENT_ROOT / key
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = out_dir / f"{docx.stem}.pdf"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    preflight, failed = {}, False
    try:
        preflight = m.require_current_docx(docx)
        preflight["structureManifest"] = m.require_accepted_structure_manifest(
            key, docx, m.STRUCTURE_MANIFEST_DIR)
        preflight["wordNativeOpen"] = m.require_clean_open_report(docx, preflight)
        m.archive_existing_pdf(exported, timestamp)
        result = m.export_with_word(docx, exported)
    except Exception as exc:
        result = {"returncode": 2, "pdfExists": False, "stdoutTail": "", "stderrTail": str(exc)}

    export_hash = sha256(exported) if exported.exists() else None
    page_count = None
    if exported.exists():
        with fitz.open(exported) as pdf:
            page_count = pdf.page_count
    item = {**result, "key": key, "docx": str(docx), "docxSha256": sha256(docx),
            "wordExportPdf": str(exported), "wordExportPdfSha256": export_hash,
            "pageCount": page_count, "preflight": preflight}
    item["status"] = ("pass" if result.get("returncode") == 0 and result.get("pdfExists")
                      and export_hash is not None else "fail")
    ready = item["status"] == "pass"
    report = {
        "schemaVersion": "chengziclass.word-content-master-pdf-export.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timestamp": timestamp,
        "engine": "Microsoft Word content-master PDF export",
        "status": "ready" if ready else "failed",
        "activeScope": {"keys": [key]},
        "writeBoundary": "Microsoft Word content export only. TOC/body content and visible PAGE "
                         "fields are unchanged; cover/back-cover and binding/parity pages are "
                         "added only in the next registered PDF assembly stage.",
        "results": [item],
        "summary": {"pdfCount": 1, "passed": int(ready), "failed": int(not ready), "ready": ready},
    }
    m.CONTENT_ROOT.mkdir(parents=True, exist_ok=True)
    m.REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"step": "s6a-export-pdf", "status": report["status"], "pages": page_count,
                      "pdf": str(exported) if ready else None,
                      "error": None if ready else (result.get("stderrTail") or "")[-400:]},
                     ensure_ascii=False, indent=1))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
