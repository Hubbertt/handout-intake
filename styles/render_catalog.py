#!/usr/bin/env python3
"""渲染样式模板库的预览:每个模板 × 每个场景 → 一张 PNG + 一份 catalog.json。

**渲染图不进包。** 使用方 2026-08-15 定:「技能和样式模板都安装好后,应该在安装环境下
真实渲染一次;每次改样式都应该渲染一次。」包里带的图是别人机器上的样子——
字体不同、Word 版本不同,看着一样其实不一样。

流程与生产完全同源:用真实 params.json 建样式 → 样张 docx → Word 沙盒 → PDF → PNG。
**样张与成品必须同源**,否则验的是样张不是成品。

过期判定按 params.json 的 sha256:改了参数,旧图就作废,不靠人记得重渲。
用 --force 强制全部重渲。

依赖:python-docx(建样张)、Microsoft Word(渲染)、sips(macOS 自带,PDF→PNG)。
LibreOffice 不可替代——它忽略 w:w、公式字距也不同,据它判断会错(使用方定)。

用法:
  render_catalog.py [--template <id>] [--force] [--engine-python <path>]
退出码 0=全部渲染完成 1=有模板渲染失败或依赖缺失
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RENDERS = HERE / "renders"
CATALOG = HERE / "catalog.json"
WORD_SANDBOX = Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents/handout-intake-renders"

BUILD_SAMPLE = r'''
import json, sys, importlib.util
from pathlib import Path
params_path, template_path, out_path, vendor = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("bsh", Path(vendor) / "build_semantic_handout_from_blueprint.py")
b = importlib.util.module_from_spec(spec); sys.modules["bsh"] = b
sys.path.insert(0, vendor); spec.loader.exec_module(b)
import docx
from docx.enum.style import WD_STYLE_TYPE
P = json.loads(Path(params_path).read_text(encoding="utf-8"))
T = json.loads(Path(template_path).read_text(encoding="utf-8"))
reg = P["wordStyleRegistry"]
ps, cs = reg["paragraphStyles"], reg.get("characterStyles") or {}
d = docx.Document()
# 用真实参数建样式——样张与成品同源
made = set()
for sid, spec_ in ps.items():
    if isinstance(spec_, dict) and not spec_.get("visualPassThrough"):
        try:
            b.ensure_style(d, sid, spec_, WD_STYLE_TYPE.PARAGRAPH); made.add(sid)
        except Exception as e:
            print(f"  skip {sid}: {e}", file=sys.stderr)
try:
    b.enforce_document_typography_defaults(d, P)
except Exception as e:
    print(f"  docDefaults: {e}", file=sys.stderr)
scene_id = sys.argv[5]
scene = next(s for s in T["renderScenes"] if s["id"] == scene_id)
for sid, text in scene["blocks"]:
    p = d.add_paragraph(text)
    for st in d.styles:
        if st.style_id == sid:
            p.style = st; break
d.save(out_path)
print(json.dumps({"styles": len(made), "blocks": len(scene["blocks"])}))
'''

EXPORT_PDF = '''
on exportDoc(inputPath, outputPath)
    tell application "Microsoft Word"
        launch
        set docRef to open file name inputPath read only true add to recent files false
        delay 2
    end tell
    tell application "Microsoft Word"
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
exportDoc("{docx}", "{pdf}")
'''


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def render_scene(engine: str, template_dir: Path, template: dict, scene: dict, out_dir: Path) -> dict:
    tid = template["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    WORD_SANDBOX.mkdir(parents=True, exist_ok=True)
    stem = f"{tid}--{scene['id']}"
    docx_p = WORD_SANDBOX / f"{stem}.docx"
    pdf_p = WORD_SANDBOX / f"{stem}.pdf"
    png_p = out_dir / f"{scene['id']}.png"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(BUILD_SAMPLE)
        script = fh.name
    try:
        r = subprocess.run([engine, script, str(template_dir / template["params"]),
                            str(template_dir / "template.json"), str(docx_p),
                            str(ROOT / "skill" / "vendor"), scene["id"]],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return {"scene": scene["id"], "status": "build-failed", "stderr": r.stderr[-600:]}
        if pdf_p.exists():
            pdf_p.unlink()
        r2 = subprocess.run(["osascript", "-"], input=EXPORT_PDF.format(docx=docx_p, pdf=pdf_p),
                            capture_output=True, text=True, timeout=300)
        if r2.returncode != 0 or not pdf_p.exists():
            return {"scene": scene["id"], "status": "word-export-failed", "stderr": r2.stderr[-400:]}
        r3 = subprocess.run(["/usr/bin/sips", "-s", "format", "png", "--resampleWidth", "1400",
                             str(pdf_p), "--out", str(png_p)], capture_output=True, text=True)
        if r3.returncode != 0 or not png_p.exists():
            return {"scene": scene["id"], "status": "png-failed", "stderr": r3.stderr[-300:]}
        return {"scene": scene["id"], "title": scene.get("title"), "status": "ok",
                "png": str(png_p.relative_to(HERE)), "blocks": len(scene["blocks"])}
    finally:
        os.unlink(script)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--engine-python", help="≥3.12 且装有 python-docx 的解释器;默认读 runtime/probe-report.json")
    args = ap.parse_args()

    engine = args.engine_python or os.environ.get("HANDOUT_INTAKE_PYTHON")
    if not engine:
        rep = ROOT / "runtime" / "probe-report.json"
        if rep.exists():
            engine = ((json.loads(rep.read_text(encoding="utf-8")).get("python") or {})
                      .get("engine") or {}).get("found", {}) or {}
            engine = engine.get("exe") if isinstance(engine, dict) else None
    if not engine:
        print(json.dumps({"status": "refused",
                          "why": "没有可用的引擎解释器(≥3.12,装有 python-docx)。"
                                 "先跑 runtime/install_wizard.py,或传 --engine-python。"},
                         ensure_ascii=False, indent=1))
        return 1
    if not shutil.which("osascript"):
        print(json.dumps({"status": "refused", "why": "渲染须 Microsoft Word(经 osascript)。"},
                         ensure_ascii=False))
        return 1

    catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {"templates": {}}
    results, failed = {}, False
    for tdir in sorted(HERE.glob("*/")):
        tfile = tdir / "template.json"
        if not tfile.exists():
            continue
        template = json.loads(tfile.read_text(encoding="utf-8"))
        tid = template["id"]
        if args.template and tid != args.template:
            continue
        params_sha = sha(tdir / template["params"])
        prev = (catalog.get("templates") or {}).get(tid) or {}
        if not args.force and prev.get("paramsSha256") == params_sha and prev.get("status") == "ok":
            print(f"  = {tid}: 参数未变(sha 相同),沿用已有预览")
            results[tid] = prev
            continue
        print(f"  ▶ {tid}: 渲染 {len(template.get('renderScenes') or [])} 个场景…")
        out_dir = RENDERS / tid
        if out_dir.exists():
            shutil.rmtree(out_dir)
        scenes = [render_scene(engine, tdir, template, sc, out_dir)
                  for sc in (template.get("renderScenes") or [])]
        ok = all(s["status"] == "ok" for s in scenes) and bool(scenes)
        failed = failed or not ok
        for s in scenes:
            print(f"     {'✓' if s['status']=='ok' else '✗'} {s['scene']}  {s.get('png') or s.get('stderr','')[:120]}")
        results[tid] = {"id": tid, "name": template.get("name"),
                        "description": template.get("description"),
                        "paramsSha256": params_sha,
                        "renderedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "renderedOn": os.uname().nodename,
                        "engine": engine, "scenes": scenes,
                        "status": "ok" if ok else "failed"}
    catalog = {"schemaVersion": "handout-intake.style-catalog.v1",
               "what": "样式模板清单。每个模板的预览在本机渲染,附 params 的 sha256;参数一变预览即过期。",
               "note": "★预览图不进包:renderedOn 记着是哪台机器渲的。发给别人时只发 styles/<id>/(不含 renders/),对方装好后自己渲一次。",
               "templates": results}
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\ncatalog → {CATALOG}  ({len(results)} 个模板)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
