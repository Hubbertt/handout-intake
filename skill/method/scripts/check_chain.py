#!/usr/bin/env python3
"""只读校验:这张工序表描述的链是不是真的,以及工作区当前停在哪一步。

不执行任何步骤。它回答四个问题:
  1. 表自洽吗    —— 有没有引用未登记的 id、有没有悬空依赖
  2. 排得出顺序吗 —— 有没有成环
  3. 绑齐了吗    —— external 的输入是不是都被册级 bindings 绑上了
  4. 停在哪     —— 哪些产物已落地、哪些缺、哪些与上次记录相比漂了

**缺产物不算失败。** 空工作区应当报「缺 N 个产物」并退 0——那是「还没做」,
不是「表说不通」。要把缺失也当失败(例如复现对账时),加 --require-complete。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chain import Chain, ChainError  # noqa: E402


def build_report(chain: Chain) -> dict:
    dangling = chain.dangling()
    order, stuck = chain.topo()
    unbound = chain.unbound_externals()

    prior = chain.load_state()
    digests, missing, drifted, seen = {}, [], [], set()
    for step in chain.steps:
        for token in step["produces"]:
            aid = token.split("@", 1)[0]
            d = chain.digest(aid)
            if d is None:
                if aid not in seen:
                    missing.append({"artifact": aid, "producedBy": step["id"],
                                    "expectedAt": chain.pattern(aid)})
                    seen.add(aid)
                continue
            digests[aid] = d
            if aid in prior and prior[aid] != d:
                drifted.append({"artifact": aid, "was": prior[aid], "now": d})

    # 外部输入不是本链产出,但缺了照样开不了工。分开报:未绑定=不知道去哪找,
    # 已绑定但不存在=知道去哪找但那里没有。两种情况的处置完全不同。
    ext_present, ext_absent = {}, []
    for aid in sorted(chain.externals()):
        pat = chain.pattern(aid)
        if pat is None:
            continue
        d = chain.digest(aid)
        if d is None:
            ext_absent.append({"artifact": aid, "expectedAt": pat,
                               "authoredBy": chain.spec(aid).get("authoredBy")})
        else:
            ext_present[aid] = d

    consistent = not (dangling or stuck)
    return {
        "schemaVersion": "handout-intake.chain-check.v1",
        "workspace": str(chain.workspace),
        "volume": chain.volume,
        "table": str(chain.table_path),
        "tableConsistent": consistent,
        "dangling": [{"step": s, "needs": t} for s, t in dangling],
        "unorderable": stuck,
        "order": order,
        "stepCount": len(chain.steps),
        "unboundExternals": unbound,
        "externalsPresent": ext_present,
        "externalsAbsent": ext_absent,
        "present": digests,
        "missing": missing,
        "drifted": drifted,
    }


def print_text(r: dict) -> None:
    print("=== 表自洽 ===")
    if r["dangling"]:
        print(f"  **悬空依赖 {len(r['dangling'])} 个**(表写错了):")
        for d in r["dangling"]:
            print(f"     {d['step']} 要 {d['needs']},但没有任何步骤产出它,也不是 external")
    else:
        print("  悬空依赖 0")

    print(f"\n=== 拓扑排序 ===\n  可排序 {len(r['order'])} / {r['stepCount']}")
    if r["unorderable"]:
        print(f"  **成环或依赖不可满足**: {r['unorderable']}")
    else:
        print("  " + " → ".join(r["order"]))

    print("\n=== 外部输入 ===")
    print(f"  已就位 {len(r['externalsPresent'])} 个")
    if r["unboundExternals"]:
        print(f"  未绑定 {len(r['unboundExternals'])} 个"
              f"(不知道去哪找;册级 bindings.json 里补 paths.<id>):")
        for aid in r["unboundExternals"]:
            print(f"     {aid}")
    if r["externalsAbsent"]:
        print(f"  已绑定但不存在 {len(r['externalsAbsent'])} 个(知道去哪找,但那里没有):")
        for e in r["externalsAbsent"]:
            print(f"     {e['artifact']}  应在 {e['expectedAt']}  [由 {e['authoredBy']} 产出]")

    print("\n=== 产物与新鲜度 ===")
    print(f"  已落地 {len(r['present'])} 个,缺失 {len(r['missing'])} 个")
    for m in r["missing"]:
        print(f"     缺: {m['artifact']}  (由 {m['producedBy']} 产出,应在 {m['expectedAt']})")
    if r["drifted"]:
        print(f"  与上次记录相比漂移 {len(r['drifted'])} 个:")
        for d in r["drifted"]:
            print(f"     {d['artifact']}: {d['was']} → {d['now']}")
    else:
        print("  漂移 0")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--volume", help="册 id;工作区只有一册时可省")
    ap.add_argument("--table", type=Path, help="覆盖默认工序表(调试用)")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--require-complete", action="store_true",
                    help="把「缺产物」也算失败。复现对账时用。")
    ap.add_argument("--no-write-state", action="store_true",
                    help="不更新 chain-state 基线。纯查看时用。")
    args = ap.parse_args()

    if not args.workspace.is_dir():
        print(json.dumps({"ok": False, "error": f"工作区不存在:{args.workspace}"},
                         ensure_ascii=False))
        return 1
    try:
        chain = Chain(args.workspace, args.volume, args.table)
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    report = build_report(chain)
    bad = (not report["tableConsistent"]
           or (args.require_complete and bool(report["missing"])))
    report["ok"] = not bad

    if not args.no_write_state:
        report["stateWrittenTo"] = str(chain.save_state(report["present"]))

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print_text(report)
        verdict = "表自洽" if report["tableConsistent"] else "**表有问题,见上**"
        if bad and report["tableConsistent"]:
            verdict = f"表自洽,但 --require-complete 下缺 {len(report['missing'])} 个产物"
        print("\n结论:", verdict)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
