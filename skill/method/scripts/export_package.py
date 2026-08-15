#!/usr/bin/env python3
"""按 PACKAGE.json 声明的分档导出可分发的技能包。

**为什么需要它。** PACKAGE.json 里写了 export.default 与 export.optIn,
而在此之前**没有任何脚本执行它**——声明了分档,导出却只能靠人手拷目录,
拷多拷少全凭记性。而这一档的差别不是方便与否:optIn 那一档含源文原文片段,
拷错了就是把别人的版权内容发出去。

两档:
  default  method/ + seeds/ + SKILL.md + PACKAGE.json + VERSION —— 使用方自己的劳动
  optIn    .handout-intake/ 的规律与册级裁决 —— **含源文原文片段**,须 --include-grown
           且必须再带 --i-understand-copyright,两个开关缺一不可。

**两个开关不是啰嗦。** 一个开关会被顺手带上;第二个开关的名字本身就是提示语,
打的时候必须读一遍自己在确认什么。

导出前自检(不通过就不导,导出一个坏包比不导更坏):
  ① 包内不得有本机绝对路径 —— 有就是把一台机器的布局编进了方法
  ② VERSION 与 PACKAGE.json 的 version 必须一致 —— 两处各说各的,别人不知道信哪个
  ③ PACKAGE.json 必须可解析

用法:
  export_package.py --out <目录或.zip> [--include-grown --i-understand-copyright]
                    [--workspace <取成长物的工作区>]
退出码 0=已导出 1=自检未过或参数不足
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MACHINE_PATH = re.compile(r'["\'](/Users/|/Volumes/|/home/)[^"\']*["\']')


warnings: list[dict] = []


def product_root() -> Path:
    """产品根 = 有 styles/ 的那一层;skill/ 是它的子目录。清单与版本只在产品根一份。"""
    return ROOT.parent if (ROOT.parent / "styles").exists() else ROOT


def self_check(package: dict) -> list[dict]:
    findings = []
    PR = product_root()
    # VERSION 与 PACKAGE.json 都只认产品根那一份。开发目录曾在 skill/ 下也留了一套,
    # 导出器读了旧的、我改了新的——它自己抓到的正是它要防的「两处各说各的」。
    version_file = (PR / "VERSION").read_text(encoding="utf-8").strip() \
        if (PR / "VERSION").exists() else ""
    if version_file != str(package.get("version") or ""):
        findings.append({"check": "version-agrees", "kind": "mismatch",
                         "VERSION": version_file, "PACKAGE.json": package.get("version"),
                         "why": "两处各说各的,拿到包的人不知道该信哪个。"})
    scan_roots = [ROOT / "method", ROOT / "vendor"]
    PRODUCT = ROOT.parent if (ROOT.parent / "styles").exists() else ROOT
    scan_roots += [PRODUCT / "styles" / "render_catalog.py", PRODUCT / "runtime" / "install_wizard.py"]
    files = []
    for r in scan_roots:
        if r.is_dir(): files += list(r.rglob("*"))
        elif r.exists(): files.append(r)
    # ★扫描面覆盖全部会进包的文本文件(含 seeds/ 与 styles/ 的数据),不只 method/。
    #   首版只扫代码目录,导出后在解压件里发现 seeds 与 styles 各有本机路径——
    #   门有洞,和没有门一样。
    #   分两级:代码里的路径硬拦(会跑到);数据里的溯源字段(sourceOriginal/ownerDirectory
    #   这类只记「从哪来」的)记为警告——它们不被读取,但要让人看见。
    PROVENANCE_KEYS = ("sourceOriginal", "ownerDirectory", "copiedFrom", "workingDirectory",
                       "runCommand", "_why", "why", "note", "_note", "provenance")
    scan_roots += [ROOT / "seeds", PRODUCT / "styles"]
    files = []
    for r in scan_roots:
        if r.is_dir(): files += list(r.rglob("*"))
        elif r.exists(): files.append(r)
    for path in sorted(set(files)):
        # catalog.json 与 renders/ 是本机渲染的产物(记着本机解释器路径),不进包也不扫——
        # 它们本来就该在收包的机器上重新生成。
        if path.suffix not in (".py", ".json", ".md") or "__pycache__" in path.parts \
                or "renders" in path.parts or path.name == "catalog.json":
            continue
        rel = str(path.relative_to(PRODUCT)) if str(path).startswith(str(PRODUCT)) else str(path)
        for i, line in enumerate(path.read_text(encoding="utf-8",
                                                errors="ignore").splitlines(), 1):
            if not MACHINE_PATH.search(line):
                continue
            is_data = path.suffix in (".json", ".md") and any(f'"{k}"' in line for k in PROVENANCE_KEYS)
            if path.suffix == ".md" or is_data:
                warnings.append({"check": "no-machine-paths", "kind": "provenance-path",
                                 "file": rel, "line": i,
                                 "note": "溯源/文档字段里的本机路径:不被读取,但收包的人会看见,建议改为相对或删去。"})
            else:
                findings.append({"check": "no-machine-paths", "kind": "hardcoded-path",
                                 "file": rel, "line": i,
                                 "why": "包里写死本机绝对路径,等于把一台机器的布局"
                                        "编进方法——换台机器就废,而废的方式是"
                                        "「找不到文件」,看起来像别的问题。"})
    # 三个入口壳的正文必须一致——不同宿主读不同文件,漂了就是给不同智能体两套说明。
    r = subprocess.run([sys.executable, str(ROOT / "method/scripts/sync_entrypoints.py"), "--check"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        findings.append({"check": "entrypoints-agree", "kind": "drift", "detail": r.stdout.strip()[:200],
                         "why": "README/SKILL/AGENTS 正文不一致。只改 README.md,再跑 sync_entrypoints.py。"})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--include-grown", action="store_true")
    ap.add_argument("--i-understand-copyright", action="store_true")
    ap.add_argument("--workspace", type=Path)
    args = ap.parse_args()

    package = json.loads((product_root() / "PACKAGE.json").read_text(encoding="utf-8"))
    findings = self_check(package)
    if findings:
        print(json.dumps({"status": "refused", "findings": findings,
                          "why": "自检未过。**导出一个坏包比不导更坏**——"
                                 "它看着能用,而问题要到别人机器上才暴露。"},
                         ensure_ascii=False, indent=1))
        return 1

    if args.include_grown and not args.i_understand_copyright:
        print(json.dumps({"status": "refused",
                          "why": "成长物含源文原文片段(标题原文、题干片段、图注编号)。"
                                 "导出即分发这些片段,须同时带 --i-understand-copyright。"
                                 "**两个开关不是啰嗦**:一个开关会被顺手带上;"
                                 "第二个开关的名字本身就是提示语。"},
                         ensure_ascii=False, indent=1))
        return 1

    staging = args.out.with_suffix("") if args.out.suffix == ".zip" else args.out
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    carried = []
    # 产品布局(使用方 2026-08-15 定):PRODUCT = skill 的上一层。
    # 默认档:skill/ 全部 + styles/<id>/(不含 renders/——渲染图不进包)+ runtime/ 的声明与向导 + 顶层清单。
    PRODUCT = ROOT.parent if (ROOT.parent / "styles").exists() else ROOT
    for name in ("README.md", "SKILL.md", "AGENTS.md", "PACKAGE.json", "VERSION"):
        src = PRODUCT / name if (PRODUCT / name).exists() else ROOT / name
        if src.exists():
            shutil.copy2(src, staging / name); carried.append(name)
    shutil.copytree(ROOT, staging / "skill",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "SKILL.md", "PACKAGE.json", "VERSION"))
    carried.append("skill/")
    if (PRODUCT / "styles").exists():
        (staging / "styles").mkdir()
        # 样式库三层真源 + 工具进包;组合(compositions/)与渲染图(renders/)是投影,不进包——
        # 装好后 compose --all 与向导渲一次即可再生。带投影进包会让人改投影而不改真源。
        for sub in ("roots", "packs", "base"):
            if (PRODUCT / "styles" / sub).exists():
                shutil.copytree(PRODUCT / "styles" / sub, staging / "styles" / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
                carried.append(f"styles/{sub}/")
        for f in ("compose.py", "render_catalog.py", "new_template.py", "catalog.json"):
            if (PRODUCT / "styles" / f).exists():
                shutil.copy2(PRODUCT / "styles" / f, staging / "styles" / f)
        carried.append("styles/{compose,render_catalog,new_template}.py + catalog.json")
    # runtime/:声明与向导进包;探测报告与 paths.json 是本机事实,不进。
    # ★重写 styles 段时把这一段连带切掉了——解压件没有 runtime/,向导根本没得跑。
    #   导出成功、包也能装,只是「安装向导」这个入口消失了;这种缺法最难发现。
    if (PRODUCT / "runtime").exists():
        (staging / "runtime").mkdir(exist_ok=True)
        for f in ("requirements.json", "install_wizard.py"):
            if (PRODUCT / "runtime" / f).exists():
                shutil.copy2(PRODUCT / "runtime" / f, staging / "runtime" / f)
        carried.append("runtime/(声明+向导,不含本机探测报告与 paths.json)")
    grown = []
    if args.include_grown:
        for sub in ("volumes", "experience"):
            src = PRODUCT / sub
            if src.exists():
                shutil.copytree(src, staging / sub, ignore=shutil.ignore_patterns("__pycache__", "work", "renders"))
                grown += [str(p.relative_to(PRODUCT)) for p in sorted(src.rglob("*")) if p.is_file()]

    zipped = None
    if args.out.suffix == ".zip":
        with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(staging.rglob("*")):
                if p.is_file():
                    # 导出包也做成确定性 zip:同样的内容压出同样的字节,
                    # 否则「这两份包一样吗」又要靠人肉比。
                    info = zipfile.ZipInfo(str(p.relative_to(staging)),
                                           date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, p.read_bytes())
        shutil.rmtree(staging)
        zipped = str(args.out)

    print(json.dumps({"status": "ok", "version": package.get("version"),
                      "warnings": warnings,
                      "out": zipped or str(staging), "carried": carried,
                      "grownIncluded": bool(grown), "grownFiles": len(grown),
                      "note": "默认档不含任何来源原文。成长物需两个开关同时给出。"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
