#!/usr/bin/env python3
"""安装向导:探测环境 → 列出缺项 → 逐项请求授权 → 安装 → 渲染样式预览。

**为什么是向导,不是脚本。**
使用方 2026-08-15 定:「智能体拿到后,应该是先问用户几个问题的,比如缺环境之类的,
让用户授权,这就是安装向导。装好后,根据有的样式模板渲染预览。」

三条纪律:
  探得出   每一项依赖都可机器判定,不靠人记得。缺什么、为什么要、怎么装,列在一处。
  先问再装 任何写盘/联网动作都逐项授权。--yes 是「全部同意」的显式表达,不是默认。
  装完即渲 依赖齐了立刻真渲染一次样式清单——**渲染图不进包,在目标环境渲一次才算数**。
           包里带的图是别人机器上的样子,不是你的。

用法:
  install_wizard.py                 交互:探测并逐项询问
  install_wizard.py --probe-only    只探测,写报告,不装
  install_wizard.py --yes           全部同意(自动化场景)
  install_wizard.py --skip-render   装完不渲染
退出码 0=环境就绪 1=有缺项未装或探测失败
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REQ = json.loads((HERE / "requirements.json").read_text(encoding="utf-8"))
REPORT = HERE / "probe-report.json"
PATHS = HERE / "paths.json"


def ask(question: str, auto_yes: bool) -> bool:
    if auto_yes:
        print(f"  [--yes] {question} → 同意")
        return True
    if not sys.stdin.isatty():
        print(f"  [非交互,未授权] {question} → 跳过(用 --yes 全部同意)")
        return False
    return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")


def probe_python() -> dict:
    out = {}
    for role, spec in REQ["python"].items():
        if not isinstance(spec, dict):
            continue
        need = tuple(int(x) for x in spec["minVersion"].split("."))
        candidates = ["python3", f"python{spec['minVersion']}", "python3.12", "python3.13", "python3.14"]
        # PATH 之外的常见位置:官方安装包 / Homebrew / pyenv / conda。
        # 只查 PATH 会把装了的报成没装。找到的写进 paths.json,下次不再猜。
        extra_dirs = [Path("/Library/Frameworks/Python.framework/Versions"),
                      Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                      Path.home() / ".pyenv/versions", Path.home() / "miniconda3/bin",
                      Path.home() / "anaconda3/bin"]
        env_hint = os.environ.get("HANDOUT_INTAKE_PYTHON")
        found = None
        exes = ([env_hint] if env_hint else []) + [shutil.which(c) for c in candidates]
        for d in extra_dirs:
            if d.exists():
                exes += [str(x) for x in sorted(d.glob("**/bin/python3.1[2-9]"))[:6]]
                exes += [str(x) for x in sorted(d.glob("python3.1[2-9]"))[:6]]
        for exe in exes:
            if not exe or not Path(exe).exists():
                continue
            try:
                v = subprocess.run([exe, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
                if tuple(int(x) for x in v.split(".")[:2]) >= need:
                    has_pip = subprocess.run([exe, "-m", "pip", "--version"],
                                             capture_output=True, text=True, timeout=20).returncode == 0
                    cand = {"exe": exe, "version": v, "hasPip": has_pip}
                    # 优先选带 pip 的:没有 pip 的解释器装不了包,向导后半段全废。
                    # ★本机就有一个精简 3.12 没有 pip——首版向导选了它,pip 静默失败,
                    #   而报告里 installed 与 declined 都是空的,缺项就这么丢了。
                    if has_pip:
                        found = cand
                        break
                    found = found or cand
            except Exception:
                continue
        out[role] = {"required": spec["minVersion"], "found": found, "why": spec["why"],
                     "ok": found is not None,
                     "install": (None if found else
                                 "任选其一:① brew install python@3.12  ② 官网 python.org 下载安装包  "
                                 "③ 已装在别处则设环境变量 HANDOUT_INTAKE_PYTHON=/path/to/python3.12")}
    return out


def probe_packages(python_exe: str | None) -> list[dict]:
    rows = []
    for pkg in REQ["pythonPackages"]:
        ok = False
        if python_exe:
            r = subprocess.run([python_exe, "-c", f"import {pkg['importName']}"],
                               capture_output=True, text=True)
            ok = r.returncode == 0
        rows.append({**pkg, "ok": ok})
    return rows


def probe_apps() -> list[dict]:
    rows = []
    for app in REQ["applications"]:
        name = app["name"]
        # 应用可能装在 /Applications 的一层子目录里(Adobe Acrobat DC/Adobe Acrobat.app)。
        # 只看顶层会把装了的报成没装——探测方法太窄,报出来的「缺」就是假的。
        pats = [f"{name}*.app", f"*/{name}*.app", f"{name.split()[0]}*.app", f"*/{name.split()[0]}*.app"]
        for extra in (app.get("altNames") or []):
            pats += [f"{extra}*.app", f"*/{extra}*.app"]
        candidates = []
        for pat in pats:
            candidates += list(Path("/Applications").glob(pat))
        candidates = [c for c in candidates if "Uninstall" not in c.name and "Distiller" not in c.name]
        found = str(candidates[0]) if candidates else None
        row = {**app, "found": found, "ok": found is not None}
        if name == "Microsoft Word" and found:
            sandbox = Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents"
            row["sandboxWritable"] = sandbox.exists() and os.access(sandbox, os.W_OK)
            row["ok"] = row["ok"] and row["sandboxWritable"]
            if not row["sandboxWritable"]:
                row["fix"] = f"启动一次 Word 让它创建容器目录;或检查 {sandbox} 可写。"
        rows.append(row)
    return rows


def probe_fonts() -> list[dict]:
    """从样式模板目录里所有参数表的 fontStandard.declared 收集字体名并逐个探测实体文件。"""
    declared: dict[str, set] = {}
    for params in (ROOT / "styles").glob("*/params.json"):
        try:
            d = json.loads(params.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 模板可用性只看**样式实际引用**的字体(fontCn/fontAscii/fontCs),
        # 不看 fontStandard.declared——那张表是 PDF 字体审计的白名单,里面有
        # Cambria Math(Word 公式引擎自带,不是系统字体,没有任何样式引用它)。
        # 首版按 declared 判,三个模板在本机全被判「不可用」,而它们明明渲得出来。
        # ★判据看错了表,报出来的「缺」就是假的——比漏报更坏,它让人去装不需要的东西。
        reg = d.get("wordStyleRegistry") or {}
        for coll in ("paragraphStyles", "characterStyles"):
            for spec in (reg.get(coll) or {}).values():
                if not isinstance(spec, dict) or spec.get("visualPassThrough"):
                    continue
                for key in ("fontCn", "fontAscii", "fontCs"):
                    f = spec.get(key)
                    if f:
                        declared.setdefault(str(f), set()).add(params.parent.name)
    ALIASES = {"宋体": ["Songti", "SimSun", "STSong"], "黑体": ["Hei", "SimHei", "STHeiti", "Heiti"],
               "Times New Roman": ["Times New Roman", "TimesNewRoman"], "Arial": ["Arial"],
               "Cambria Math": ["Cambria Math", "CambriaMath"], "圆体-简": ["Yuanti", "STYuanti"]}
    dirs = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"), Path.home() / "Library/Fonts",
            Path("/System/Library/AssetsV2/PreinstalledAssetsV2/InstallWithOs")]
    rows = []
    for font, users in sorted(declared.items()):
        hits = []
        for d in dirs:
            if not d.exists():
                continue
            for pat in ALIASES.get(font, [font]):
                hits += [str(p) for p in d.rglob(f"*{pat}*") if p.suffix.lower() in (".ttf", ".ttc", ".otf")]
        # 只在按需目录(非 InstallWithOs)命中的算「按需 Subsets」,不算实体
        real = [h for h in hits if "AssetsV2" not in h or "InstallWithOs" in h]
        rows.append({"font": font, "usedBy": sorted(users), "files": real[:3],
                     "ok": bool(real),
                     "why": "参数表声明的字体必须是本机实体字体,不是按需 Subsets——"
                            "圆体-简那次声明了却画不出。" if not real else ""})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--yes", action="store_true", help="全部同意(自动化)")
    ap.add_argument("--skip-render", action="store_true")
    args = ap.parse_args()

    print("== handout-intake 安装向导 ==\n")
    py = probe_python()
    engine_exe = (py.get("engine") or {}).get("found", {}) or {}
    pkgs = probe_packages(engine_exe.get("exe"))
    apps = probe_apps()
    fonts = probe_fonts()

    missing = []
    print("Python:")
    if not (py.get("engine") or {}).get("ok"):
        print("  提示:若 ≥3.12 装在非常规位置,设 HANDOUT_INTAKE_PYTHON=/path/to/python3.12 后重跑。")
    for role, r in py.items():
        pip_note = "" if not r["found"] else ("  [有 pip]" if r["found"].get("hasPip") else "  [无 pip → 需 ensurepip]")
        print(f"  {'✓' if r['ok'] else '✗'} {role:8} 需要 ≥{r['required']}  "
              f"{'找到 '+r['found']['exe']+' ('+r['found']['version']+')' if r['found'] else '未找到'}{pip_note}")
        if not r["ok"]:
            print(f"      安装方式:{r['install']}")
            missing.append({"kind": "python", "role": role, "fix": r["install"]})
    print("\nPython 包:")
    for p in pkgs:
        print(f"  {'✓' if p['ok'] else '✗'} {p['name']:12} {p['why']}")
        if not p["ok"]:
            missing.append({"kind": "package", **p})
    print("\n应用:")
    for a in apps:
        extra = "" if a.get("sandboxWritable", True) else "  (沙盒目录不可写)"
        print(f"  {'✓' if a['ok'] else '✗'} {a['name']:16} [{a['role']}] {a.get('found') or '未找到'}{extra}")
        if not a["ok"]:
            missing.append({"kind": "app", **a})
    # 字体属于**模板**,不属于环境。使用方 2026-08-15 定:「本机没有字体,说明这个
    # 样式模板他用不了,换掉就可以了。」所以字体缺失不是「缺项待装」,而是
    # 「这套模板在本机不可用」——向导要说的是可选的有哪些,不是去装字体。
    print("\n样式模板可用性(按声明字体在本机是否为实体字体判定):")
    by_tpl: dict[str, list] = {}
    for f in fonts:
        for t in f["usedBy"]:
            by_tpl.setdefault(t, []).append(f)
    templates_ok, templates_bad = [], []
    if not by_tpl:
        print("  (styles/ 下还没有模板,跳过)")
    for tpl, fs in sorted(by_tpl.items()):
        bad = [f["font"] for f in fs if not f["ok"]]
        if bad:
            templates_bad.append({"template": tpl, "missingFonts": bad})
            print(f"  ✗ {tpl:28} 本机缺字体 {','.join(bad)} → **不可用,请换模板**")
        else:
            templates_ok.append(tpl)
            print(f"  ✓ {tpl:28} 全部字体可用")
    if by_tpl and not templates_ok:
        # 一套都不可用才算环境缺项:那时用户没得选。
        missing.append({"kind": "no-usable-template",
                        "templates": templates_bad,
                        "fix": "所有模板都要求本机没有的字体。二选一:① 新增一套用本机字体的模板"
                               "(styles/new_template.py --kind pack);② 安装其中一套要的字体后重跑向导。"})

    installed, declined = [], []
    if not args.probe_only and missing:
        print(f"\n共 {len(missing)} 项缺失。逐项询问:")
        for m in missing:
            if m["kind"] == "package" and engine_exe.get("exe"):
                cmd = [engine_exe["exe"], "-m", "pip", "install", m["name"]]
                if not engine_exe.get("hasPip"):
                    fix = (f"该解释器没有 pip。先执行 {engine_exe['exe']} -m ensurepip --upgrade,"
                           f"或改用带 pip 的 ≥3.12(设 HANDOUT_INTAKE_PYTHON)。")
                    declined.append({**m, "reason": "engine 解释器无 pip", "fix": fix})
                    print(f"  ✗ {m['name']}: {fix}")
                    continue
                if ask(f"安装 Python 包 {m['name']}?  ({' '.join(cmd)})", args.yes):
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    rec = {**m, "returncode": r.returncode, "stderrTail": r.stderr[-300:]}
                    if r.returncode == 0:
                        installed.append(rec)
                        print(f"  ✓ 已装 {m['name']}")
                    else:
                        declined.append({**rec, "reason": "pip 失败"})
                        print(f"  ✗ {m['name']} 安装失败:{r.stderr[-160:].strip()}")
                else:
                    declined.append({**m, "reason": "用户未授权"})
            else:
                # 应用与字体不由本向导代装:它们要么要管理员权限,要么涉及许可。
                # 如实列出怎么装,让用户自己动手——代装是把授权范围悄悄扩大。
                declined.append({**m, "reason": "本向导不代装应用/字体,请按 fix 提示手动安装"})
                print(f"  ⚠ {m.get('name') or m.get('font') or m.get('role')}: {m.get('fix') or m.get('why') or '需手动安装'}")

    if not PATHS.exists():
        PATHS.write_text(json.dumps({
            "_what": "vendor 脚本的可移植路径配置。环境变量优先于本文件。",
            "HANDOUT_INTAKE_HOME": str(ROOT),
            "HANDOUT_INTAKE_MATERIALS_ROOT": str(ROOT / "volumes"),
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n已写 {PATHS.name}(可手改)")

    still = [m for m in missing if not any(i.get("name") == m.get("name") and i.get("returncode") == 0
                                           for i in installed)]
    # 就绪 = 必需项齐(Python 引擎 / Python 包 / Word)。
    # 字体缺失与 Acrobat 缺失是**警告**,不阻塞:渲染恰恰能让用户看见缺字体的实际后果,
    # 而付印才需要 Acrobat。首版把「缺一个几乎不用的公式字体」判成 not-ready、
    # 于是不渲染——用户什么都看不到,只得到一句「仍缺 1 项」。
    blocking = [m for m in still if m["kind"] in ("python", "package")
                or (m["kind"] == "app" and m.get("role") == "必需")]
    advisory = [m for m in still if m not in blocking]
    ready = not blocking
    report = {"schemaVersion": "handout-intake.probe-report.v1",
              "probedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
              "python": py, "packages": pkgs, "applications": apps, "fonts": fonts,
              "usableTemplates": templates_ok, "unusableTemplates": templates_bad,
              "installed": installed, "declined": declined, "stillMissing": still,
              "blocking": blocking, "advisory": advisory, "ready": ready}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if advisory:
        print("\n⚠ 非阻塞缺项(不影响运行,付印/特定字体时需要):")
        for m in advisory:
            print(f"   {m.get('name') or m.get('font')}: {m.get('fix') or m.get('why') or ''}")
    print(f"\n{'✓ 环境就绪' if ready else '✗ 仍缺 '+str(len(blocking))+' 项必需依赖'}  →  报告 {REPORT}")

    if ready and not args.skip_render and not args.probe_only:
        renderer = ROOT / "styles" / "render_catalog.py"
        if renderer.exists():
            print("\n环境就绪,渲染样式预览(渲染图不进包,在你的机器上渲一次才算数)…")
            r = subprocess.run([sys.executable, str(renderer)], text=True)
            return 0 if r.returncode == 0 else 1
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
