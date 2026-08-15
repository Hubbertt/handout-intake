#!/usr/bin/env python3
"""每个步骤脚本的开头三行。

搬进包之前,这些脚本各自在文件头写死一个 TRIAL 常量指向某一册的目录。那样写
一册能跑、第二册就得改代码——而「改代码换一册」正是这套方法要消灭的东西。

现在改成:步骤脚本一个路径都不写,只按逻辑 id 向 Chain 要。
  src = CHAIN.only("source")          现存的、恰好一个
  out = CHAIN.path_for("census")      该写到哪(还不存在)
  media = CHAIN.dir_for("media")      多文件产物的目录

id → 路径由公有默认加册级 bindings 两层决定,与 check_chain / run_chain 共用
同一份解析。步骤脚本因此对「这一册在哪」一无所知,换册只换 bindings.json。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _chain import Chain, ChainError  # noqa: E402,F401


def chain_from_argv(doc: str | None = None) -> Chain:
    ap = argparse.ArgumentParser(description=doc,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True, type=Path,
                    help="工作区根目录")
    ap.add_argument("--volume", help="册 id;工作区只有一册时可省")
    args = ap.parse_args()
    if not args.workspace.is_dir():
        raise SystemExit(f"工作区不存在:{args.workspace}")
    try:
        return Chain(args.workspace, args.volume)
    except ChainError as exc:
        raise SystemExit(f"绑定有问题:{exc}")
