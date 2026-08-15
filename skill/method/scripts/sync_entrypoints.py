#!/usr/bin/env python3
"""三个入口壳(README.md / SKILL.md / AGENTS.md)共享同一正文,从 README.md 生成另外两份。

为什么:不同宿主认不同的入口文件名(Claude Code 认 SKILL.md、Codex 认 AGENTS.md、
其他认 README.md)。若三份各写一遍正文,改一处漏两处是必然的——同一事实三处真源。
所以正文只在 README.md;本脚本把它的正文拷进另外两个壳,各壳只保留自己的头。

用法:  python3 skill/method/scripts/sync_entrypoints.py [--check]
--check 只比对不写:三份正文不一致即退出 1(导出器自检会调它)。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MARK = "\n---\n\n"


def body_of(text: str, kind: str) -> str:
    if kind == "skill":                       # frontmatter 之后
        return text.split("---", 2)[2].lstrip("\n") if text.startswith("---") else text
    # README / AGENTS:第一个 "\n---\n\n" 之后
    i = text.find(MARK)
    return text[i + len(MARK):] if i >= 0 else text


def head_of(text: str, kind: str) -> str:
    if kind == "skill":
        parts = text.split("---", 2)
        return "---" + parts[1] + "---\n\n" if text.startswith("---") else ""
    i = text.find(MARK)
    return text[: i + len(MARK)] if i >= 0 else ""


def main() -> int:
    check = "--check" in sys.argv
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    body = body_of(readme, "readme")
    drift = []
    for name, kind in (("SKILL.md", "skill"), ("AGENTS.md", "agents")):
        p = ROOT / name
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        if body_of(cur, kind) != body:
            drift.append(name)
            if not check:
                p.write_text(head_of(cur, kind) + body, encoding="utf-8")
    if check:
        print("入口正文一致" if not drift else f"入口正文漂移:{drift}(跑本脚本不带 --check 可同步)")
        return 0 if not drift else 1
    print(f"已同步:{drift or '无需改动'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
