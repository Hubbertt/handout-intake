#!/usr/bin/env python3
"""WMF/EMF 渲染成可放置的 PNG。

**为什么需要这一步。**
python-docx 放不了 metafile —— 它认不出 WMF/EMF 的头,直接 UnrecognizedImageError。
编译器为此留了退路:`<hash>.wmf` 若旁边有同名 `<hash>.png`,就用那张放。
它的注释写着「render_wmf.py puts it there」。

**但那个脚本从来不存在。**
2026-08-20 实测:包里没有任何叫 render_wmf 的文件。旧册 physics-a10a14 也没露过馅——
它的 19 个 WMF **全部**被 nativeTextSubstitutions 换成了原生公式文字,一个都没走到编译器。
新册 421 个 WMF,替换掉 67 个、明记保留 11 个,其余作为普通行内图留下,
于是 s5c 构建 Word 时炸在 UnrecognizedImageError。
★注释描述了一个不存在的步骤,而唯一跑过的册恰好不需要它。

**做法。**
LibreOffice headless 把 WMF 转成 PDF(保矢量),再用 PyMuPDF 按指定 dpi 栅格化,
最后裁到墨迹外框。直接 --convert-to png 会得到整张 A4、图缩在角落,且只有屏幕分辨率。

**判据。**
每个待渲染的 WMF 都必须产出非空 PNG;有一个失败就 fail。
渲染不出来而静默留着 WMF,等于把炸点推到 Word 构建那一步——那时报的是
「认不出的图片」,不是「这张图渲染失败」。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)
DPI = 300


def find_soffice() -> str | None:
    for path in SOFFICE_CANDIDATES:
        if Path(path).is_file():
            return path
    return shutil.which("soffice")


def pending(media: Path) -> list[Path]:
    """需要渲染的 metafile:没有同名 PNG 的。"""
    return sorted(p for p in media.iterdir()
                  if p.suffix.lower() in (".wmf", ".emf")
                  and not (media / (p.stem + ".png")).is_file())


def render(media: Path, files: list[Path], soffice: str) -> tuple[list[dict], list[dict]]:
    import fitz  # noqa: PLC0415  — 只在真要渲染时才需要

    done: list[dict] = []
    failed: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # 一次调用批量转,不逐个起进程:421 个文件逐个起 LibreOffice 要几十分钟。
        subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                        "--outdir", str(work), *[str(f) for f in files]],
                       check=False, capture_output=True, timeout=1800)
        for source in files:
            pdf = work / (source.stem + ".pdf")
            if not pdf.is_file():
                failed.append({"file": source.name, "why": "LibreOffice 未产出 PDF"})
                continue
            try:
                document = fitz.open(pdf)
                page = document[0]
                pixmap = page.get_pixmap(dpi=DPI)
                image = fitz.Pixmap(pixmap)          # 复制一份再裁
                rect = trim_box(pixmap)
                if rect is None:
                    failed.append({"file": source.name, "why": "渲染结果整页空白"})
                    continue
                clip = fitz.Rect(rect) * (72.0 / DPI)
                out = page.get_pixmap(dpi=DPI, clip=clip)
                target = media / (source.stem + ".png")
                out.save(target)
                done.append({"file": source.name, "png": target.name,
                             "pngBytes": target.stat().st_size,
                             "widthPx": out.width, "heightPx": out.height})
            except Exception as exc:                  # noqa: BLE001
                failed.append({"file": source.name, "why": f"{type(exc).__name__}: {exc}"})
    return done, failed


def trim_box(pixmap):
    """墨迹外框。整页空白返回 None——空白不该被当成一张图放进成品。"""
    samples = pixmap.samples
    width, height, n = pixmap.width, pixmap.height, pixmap.n
    x0, y0, x1, y1 = width, height, 0, 0
    for y in range(height):
        row = y * width * n
        for x in range(width):
            offset = row + x * n
            if min(samples[offset:offset + 3]) < 246:
                x0 = min(x0, x); x1 = max(x1, x)
                y0 = min(y0, y); y1 = max(y1, y)
    if x1 < x0 or y1 < y0:
        return None
    pad = 2
    return (max(0, x0 - pad), max(0, y0 - pad),
            min(width, x1 + 1 + pad), min(height, y1 + 1 + pad))


def run(media: Path) -> dict:
    files = pending(media)
    if not files:
        return {"status": "pass", "pending": 0,
                "note": "没有缺渲染件的 metafile。"}
    soffice = find_soffice()
    if not soffice:
        return {"status": "refused", "pending": len(files),
                "why": "找不到 LibreOffice(soffice)。渲染不了就不能声称图放得进去——"
                       "这一步宁可停,也不把 WMF 原样留给 python-docx。",
                "howToFix": "装 LibreOffice,或在能渲染的机器上跑这一册。"}
    done, failed = render(media, files, soffice)
    return {"status": "pass" if not failed else "fail",
            "pending": len(files), "rendered": len(done), "failed": failed[:20],
            "dpi": DPI, "soffice": soffice, "samples": done[:5]}


if __name__ == "__main__":
    report = run(Path(sys.argv[1]))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    raise SystemExit(0 if report["status"] == "pass" else 1)
