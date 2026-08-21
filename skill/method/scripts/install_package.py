#!/usr/bin/env python3
"""把本包安装到目标位置,并留下可核对的安装台账。

**为什么需要它。** 这个包有两处:开发树(改代码的地方)与安装树(真正被跑的地方,
Claude Code 的 `.claude/skills/handout-intake` 就是指向它的符号链接)。
两者之间原先靠人手敲 `rsync`——2026-08-20 一天里我敲了六次,**漏一次,册子就在跑旧代码,
而没有任何东西会说话**。当天确实撞上过一次同类:引擎改了、册没重跑,产物还是旧代码的产物。

约定「开发目录只改代码不直接用」写在 AGENT_ENTRYPOINT.md 里,但写在文档里的纪律
没有门守着等于没有。本脚本把安装变成一次**可复现、可核对**的操作:

  install  导出(带自检)→ 同步到目标 → 写 INSTALLED.json(逐文件 sha256)
  --check  只核不写:目标是否与它自己的台账一致、台账是否与当前源一致

两问分开报,因为处置不同:
  · 目标与自己的台账不一致 → **有人手改了安装树**(安装树是产物,不该手改)
  · 台账与当前源不一致     → 源改了没重装(正常,重装即可)

不删目标里的额外文件:`volumes/`、`styles/compositions/`、`runtime/probe-report.json`
都是在目标那边长出来的,属于使用方,不属于包。**同步不是镜像**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
LEDGER = "INSTALLED.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digests(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == LEDGER:
            continue
        out[str(path.relative_to(root))] = sha256(path)
    return out


def export(tmp: Path) -> Path:
    out = tmp / "pkg"
    proc = subprocess.run(
        [sys.executable, str(PACKAGE_ROOT / "skill/method/scripts/export_package.py"),
         "--out", str(out)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"导出自检未过,拒绝安装:\n{proc.stdout}\n{proc.stderr}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", required=True, type=Path, help="安装树")
    ap.add_argument("--check", action="store_true", help="只核不写")
    args = ap.parse_args()
    target = args.to

    with tempfile.TemporaryDirectory() as td:
        fresh = export(Path(td))
        want = digests(fresh)

        if args.check:
            ledger_path = target / LEDGER
            problems = []
            if not ledger_path.exists():
                problems.append({"kind": "NO_LEDGER",
                                 "why": (f"{target} 里没有 {LEDGER}。它可能是手敲 rsync 装的——"
                                         "那样就无从知道装的是哪一版。跑一次 install 补上。")})
                have = {}
            else:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                have = ledger.get("files") or {}
                # ① 安装树 vs 它自己的台账:不一致 = 有人手改了产物
                for rel, digest in sorted(have.items()):
                    now = target / rel
                    if not now.exists():
                        problems.append({"kind": "TARGET_FILE_MISSING", "file": rel})
                    elif sha256(now) != digest:
                        problems.append({"kind": "TARGET_MODIFIED", "file": rel,
                                         "why": "安装树里的文件与安装台账不符——安装树是产物,不该手改。"})
            # ② 台账 vs 当前源:不一致 = 源改了没重装
            for rel, digest in sorted(want.items()):
                if have.get(rel) != digest:
                    problems.append({"kind": "SOURCE_NEWER", "file": rel,
                                     "why": "源已改而未重装。安装树在跑旧代码,且不会因此报错。"})
            for rel in sorted(set(have) - set(want)):
                problems.append({"kind": "NO_LONGER_SHIPPED", "file": rel,
                                 "why": "包里已不再分发此文件,安装树里还留着。"})
            status = "in-sync" if not problems else "drift"
            print(f"GATE_INSTALL_IN_SYNC: {status}   ({target})")
            kinds: dict[str, int] = {}
            for p in problems:
                kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
            for kind, count in sorted(kinds.items()):
                print(f"   {kind:22} {count}")
            for p in problems[:12]:
                print(f"     · [{p['kind']}] {p.get('file', '')}")
            return 0 if status == "in-sync" else 1

        target.mkdir(parents=True, exist_ok=True)
        for rel in want:
            src, dst = fresh / rel, target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        version = (fresh / "VERSION").read_text(encoding="utf-8").strip()
        (target / LEDGER).write_text(json.dumps({
            "schemaVersion": "handout-intake.installed.v1",
            "version": version,
            "installedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "installedFrom": str(PACKAGE_ROOT),
            "_notMirrored": ("只覆盖包分发的文件,不删目标里的额外物:volumes/、"
                             "styles/compositions/、runtime/probe-report.json 都是在目标那边"
                             "长出来的,属于使用方。同步不是镜像。"),
            "files": want,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"已安装 {version} → {target}  ({len(want)} 个文件,台账 {LEDGER})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
