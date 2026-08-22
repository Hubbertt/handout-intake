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
EXEMPT_SNIPPETS = ("MACHINE_PATH = re.compile", "/System/Volumes/Data", "<pkg>", "<path>")
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
        # renders/ 是本机渲染的图,不进包也不扫。
        # ★catalog.json 曾一并豁免,理由写的是「本机产物不进包」——**而它进包了也进仓了**。
        #   注释与事实不符,豁免就成了漏洞:发布到 GitHub 后线上仍有一条本机解释器路径,
        #   本地自检全绿。豁免必须与「进不进包」这件事实对齐,不能各说各的。现在扫它。
        if path.suffix not in (".py", ".json", ".md") or "__pycache__" in path.parts \
                or "renders" in path.parts:
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
    # 声明的包 == 实际 import 的包。漏声明的包在陌生机器上是 ModuleNotFoundError,
    # 而在开发机上永远不会暴露(它恰好装着)——所以必须在导出时拦,不能等运行时。
    # 这道门要 ≥3.10 才准。优先用向导探到的引擎;没有就用当前解释器——门在老解释器上会拒绝而非误报。
    py = sys.executable
    rep = product_root() / "runtime" / "probe-report.json"
    if rep.exists():
        try:
            eng = ((json.loads(rep.read_text(encoding="utf-8")).get("python") or {}).get("engine") or {}).get("found") or {}
            if eng.get("exe") and Path(eng["exe"]).exists():
                py = eng["exe"]
        except Exception:
            pass
    r = subprocess.run([py, str(ROOT / "method/gates/gate_requirements_audit.py"),
                        "--product", str(product_root())], capture_output=True, text=True)
    if r.returncode == 2:
        findings.append({"check": "requirements-agree", "kind": "unverifiable",
                         "why": "没有 ≥3.10 的解释器可跑依赖审计。先跑安装向导让它探到引擎,再导出。"})
    elif r.returncode != 0:
        try:
            fs = json.loads(r.stdout).get("findings", [])
        except Exception:
            fs = [{"raw": r.stdout[-200:]}]
        findings.append({"check": "requirements-agree", "kind": "drift", "findings": fs,
                         "why": "requirements.json 与代码 import 不一致。"})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--include-grown", action="store_true")
    ap.add_argument("--i-understand-copyright", action="store_true")
    ap.add_argument("--workspace", type=Path)
    ap.add_argument("--capability", choices=("atomise", "compose"),
                    help="只导这个能力需要的东西。PM 2026-08-22 定:要的是**两个技能**。"
                         "atomise 不需要 macOS + Word,也不需要 styles/——"
                         "把它单独导出来,它就能在任何机器上装、任何机器上跑。")
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
    # ★atomise 不带 vendor/:那 15,476 行是 Word/PDF 的编制与审计实现,
    #   原子化的 14 步没有一步引用它(实测:步骤命令里 0 处,步骤代码里 0 处 import)。
    #   带上它,一个「任何机器都能跑」的包里就躺着一半跑不了的东西——
    #   而人会以为那是它的依赖,进而以为这个包也需要 Word。
    _skip = ["__pycache__", "*.pyc", "SKILL.md", "PACKAGE.json", "VERSION"]
    if args.capability == "atomise":
        _skip.append("vendor")
    shutil.copytree(ROOT, staging / "skill", ignore=shutil.ignore_patterns(*_skip))
    carried.append("skill/" + ("(不含 vendor/)" if args.capability == "atomise" else ""))
    # ★atomise 不带 styles/:样式是编制成册的事。带上它等于把 Word 那半的重量
    #   压在一个「任何机器都能跑」的包上,而它一行都用不到。
    if (PRODUCT / "styles").exists() and args.capability != "atomise":
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
    # 工序表按能力裁剪:导出的表里只留这个能力的步骤,
    # 并把它消费的、由另一个能力生产的产物**显式记下来**——
    # 那是接口面,拆包之后它得由使用方自己提供,不能装作不存在。
    if args.capability:
        import json as _json
        tbl = staging / "skill" / "method" / "steps.v1.json"
        data = _json.loads(tbl.read_text(encoding="utf-8"))
        keep = [s for s in data["steps"] if s.get("capability") == args.capability]
        produced = {a.split("@")[0].rstrip("'") for s in keep for a in s.get("produces", [])}
        needed = sorted({a.split("@")[0].rstrip("'") for s in keep for a in s.get("consumes", [])}
                        - produced)
        data["steps"] = keep
        data["exportedCapability"] = {
            "capability": args.capability,
            "stepsKept": len(keep),
            "stepsDropped": len(_json.loads((PRODUCT / "skill" / "method" / "steps.v1.json")
                                            .read_text(encoding="utf-8"))["steps"]) - len(keep),
            "mustBeProvided": needed,
            "_why": "这些产物本包不生产,由使用方提供(册的输入,或另一个能力的产物)。"
                    "★不列出来,就会在跑到那一步时报「输入不存在」——而那时人会以为是包坏了。",
        }
        tbl.write_text(_json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        carried.append(f"steps.v1.json(只留 {args.capability} 的 {len(keep)} 步)")

    if (PRODUCT / "runtime").exists():
        (staging / "runtime").mkdir(exist_ok=True)
        # ★白名单改成黑名单:runtime/ 里除「本机事实」之外**全部进包**。
        # 2026-08-21 又栽一次:vendor-consumers.json(vendoring 漂移门的登记册)放在 runtime/,
        # 而这里写死了两个文件名,于是它没进包——安装树上那道门直接 FileNotFoundError。
        # 与上面记的 styles 段那次同形:**白名单漏一个,导出照样成功**,缺法最难发现。
        # 黑名单只列本机事实,新加的包内文件默认进包,不必再记得来改这里。
        # ★本机事实:装好后在这台机器上长出来的东西,不随包分发。
        #   vendor-consumers.json 记的是「消费方装在这台机器的哪个目录」——
        #   2026-08-22 实测它进了包,里面躺着 /Users/Shared/ChengziClass/quiz/…。
        #   随包发的是 .example(占位符版),真表由使用方自己填。
        LOCAL_FACTS = {"probe-report.json", "paths.json", "vendor-consumers.json", "venv"}
        for f in sorted(p.name for p in (PRODUCT / "runtime").iterdir() if p.is_file()):
            if f in LOCAL_FACTS or f.startswith("."):
                continue
            shutil.copy2(PRODUCT / "runtime" / f, staging / "runtime" / f)
        carried.append("runtime/(除本机探测报告与 paths.json 外全部)")
    grown = []
    if args.include_grown:
        for sub in ("volumes", "experience"):
            src = PRODUCT / sub
            if src.exists():
                shutil.copytree(src, staging / sub, ignore=shutil.ignore_patterns("__pycache__", "work", "renders"))
                grown += [str(p.relative_to(PRODUCT)) for p in sorted(src.rglob("*")) if p.is_file()]

    zipped = None
    # ★最后一道扫描:扫 **staging 里实际会进包的每个文本文件**,不扫手列的目录清单。
    #   门有洞的方式一直是同一种:清单是手列的,而进包的东西在变——
    #   2026-08-21 seeds/ 与 styles/ 漏扫过一次(注释里写着「门有洞,和没有门一样」),
    #   2026-08-22 runtime/ 又漏一次(vendor-consumers.json 带着本机路径进了包)。
    #   两次都是补清单,补完下次换个目录照样漏。**扫产物,清单就不会漂。**
    # requirements 按能力裁剪 —— 否则原子化单包的探针仍会要 Word。
    if args.capability:
        import json as _j
        rq = staging / "runtime" / "requirements.json"
        if rq.exists():
            rd = _j.loads(rq.read_text(encoding="utf-8"))
            keep = lambda x: x.get("capability", "both") in (args.capability, "both")
            rd["pythonPackages"] = [x for x in rd.get("pythonPackages", []) if keep(x)]
            rd["applications"] = [x for x in rd.get("applications", []) if keep(x)]
            rd["_exportedCapability"] = args.capability
            rq.write_text(_j.dumps(rd, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            carried.append(f"requirements.json(只留 {args.capability} 需要的)")

    staged_hits = []
    for f in sorted(staging.rglob("*")):
        if not f.is_file() or f.suffix not in (".py", ".json", ".md", ".txt", ".sh") \
                or "__pycache__" in f.parts or "renders" in f.parts:
            continue
        rel = str(f.relative_to(staging))
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if MACHINE_PATH.search(line) and not any(x in line for x in EXEMPT_SNIPPETS):
                staged_hits.append({"file": rel, "line": i, "sample": line.strip()[:100]})
    if staged_hits:
        shutil.rmtree(staging, ignore_errors=True)
        print(json.dumps({"status": "refused", "check": "staged-no-machine-paths",
                          "hits": staged_hits[:10], "total": len(staged_hits),
                          "why": "**进包的文件里有本机绝对路径。** 这一遍扫的是暂存区里"
                                 "实际要打包的东西,不是手列的目录——所以它不会漏。"},
                         ensure_ascii=False, indent=1))
        return 1

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
