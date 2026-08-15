#!/usr/bin/env python3
"""GATE_SILENT_AUTOMATION:驱动 Word / Acrobat 的脚本必须静默——不抢前台、不弹窗。

使用方 2026-08-15 定:「pdf 和 word 的静默运行」。与既有纪律同源:
「任何应用都别抢前台:开发者工具/Word/Acrobat 一律 AppleScript 用 launch 不用 activate」。

为什么要门。今天核查时四步全部干净——它们是从生产线继承的,进包时没改坏。
但「现在干净」不等于「以后干净」:下一个人加一行 activate 调试,顺手就留下了,
而它在别人机器上的表现是**每次出片都把用户正在用的窗口挤走**。
写成判据,改坏的那一刻就报。

判据(扫 vendor/ 与 method/steps/ 里所有含 AppleScript 的 .py):
  ① 不得出现 `activate`(注释除外)
  ② tell application "Microsoft Word" 段内 open 必须带 `add to recent files false`
  ③ tell application "Adobe Acrobat" 段内 open 必须带 `invisible true`,
     preflight(...) 第二参数必须为 false(不弹交互对话)
  ④ 不得出现 display dialog / display alert / display notification
  ⑤ 交给 Word 的文件必须在它的沙盒容器内(~/Library/Containers/com.microsoft.Word/…)。
     使用方 2026-08-15 定「macOS 上文件的访问权限,应该在 app 内部才可以」——
     Word 是沙盒应用,容器外的路径会弹「授予文件访问权限」对话框,整条链就卡在那里
     (今天从 /tmp 给它文件,弹了三个)。判据:open file name 的实参不得是字面量的
     工作区/临时目录路径;必须经 WORD_PROBE_ROOT / PdfExport 沙盒目录中转。

用法:  gate_silent_automation.py --package <skill 根>
退出码 0=全部静默 1=有违反
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def strip_comments(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    findings, scanned = [], 0
    for path in sorted(list((args.package / "vendor").glob("*.py"))
                       + list((args.package / "method" / "steps").glob("*.py"))
                       + list((args.package.parent / "styles").glob("*.py"))):
        text = strip_comments(path.read_text(encoding="utf-8", errors="ignore"))
        if "tell application" not in text and "osascript" not in text:
            continue
        scanned += 1
        rel = str(path.relative_to(args.package.parent))
        for i, line in enumerate(text.splitlines(), 1):
            l = line.strip()
            # 只看 AppleScript 语境里的 activate:整行就是 activate,或 tell … to activate。
            # 首版正则漏了「行首缩进 + activate」这一形——塞进去的 activate 没被抓到,
            # 而恒假的门比没有门更坏。改为按去空白后的整行判。
            if l == "activate" or re.search(r"tell application [^\n]* to activate", l) \
                    or re.search(r'^\s*activate\s*(--.*)?$', line):
                findings.append({"file": rel, "line": i, "kind": "activate",
                                 "why": "activate 把应用抢到前台,会把用户正在用的窗口挤走。用 launch。"})
            if re.search(r"display (dialog|alert|notification)", l):
                findings.append({"file": rel, "line": i, "kind": "dialog",
                                 "why": "弹窗要人点,静默流程会卡死在这里。"})
        # Word 沙盒:open file name 的实参若是字面量的容器外路径即违规。
        # 变量名/占位符(inputPath、{applescript_string(...)})视为经中转的,不判——
        # 判它们要跑起来才知道,静态门只判静态可见的错。
        for m in re.finditer(r'open file name\s+("?)(/[^"\s]+)', text):
            lit = m.group(2)
            if "Library/Containers/com.microsoft.Word" not in lit:
                findings.append({"file": rel, "kind": "word-open-outside-sandbox",
                                 "snippet": m.group(0)[:90],
                                 "why": "Word 是沙盒应用,容器外的字面量路径会弹「授予文件访问权限」对话框,"
                                        "静默链在此卡死。经 WORD_PROBE_ROOT 中转。"})
        if "tell application \"Microsoft Word\"" in text and "WORD_PROBE_ROOT" not in text \
                and "Containers/com.microsoft.Word" not in text:
            findings.append({"file": rel, "kind": "word-driver-without-sandbox-root",
                             "why": "驱动 Word 却没有引用沙盒中转目录——文件多半是直接给的。"})
        # Word open 必须 add to recent files false
        for m in re.finditer(r'open file name [^\n]*', text):
            if "add to recent files false" not in m.group(0):
                findings.append({"file": rel, "kind": "word-open-recent",
                                 "snippet": m.group(0)[:80],
                                 "why": "Word open 不带 add to recent files false 会污染用户的最近文件列表。"})
        # Acrobat open 必须 invisible true;preflight 第二参数 false
        for m in re.finditer(r'open POSIX file [^\n]*', text):
            if "invisible true" not in m.group(0):
                findings.append({"file": rel, "kind": "acrobat-open-visible",
                                 "snippet": m.group(0)[:80],
                                 "why": "Acrobat open 不带 invisible true 会弹出窗口。"})
        # 只判 JS 里的 this.preflight(profile, <interactive>, …),不判同名的 Python 函数
        # run_acrobat_preflight(src, dst, …)——首版把后者也当成了 JS,报出 3 条假命中。
        for m in re.finditer(r'this\.preflight\(\s*[^,]+,\s*(\w+)', text):
            if m.group(1).lower() != "false":
                findings.append({"file": rel, "kind": "acrobat-preflight-interactive",
                                 "snippet": m.group(0),
                                 "why": "preflight 第二参数非 false 会弹交互对话。"})

    report = {"gate": "GATE_SILENT_AUTOMATION", "scanned": scanned,
              "findings": findings, "status": "pass" if not findings else "fail"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
