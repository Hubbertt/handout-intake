#!/usr/bin/env python3
"""切分 schema 的合成与拆分:基底 + 册级偏离 ⇄ 完整 schema。

  compose  基底 + deviation → 完整 schema(投影,交给切分引擎)
  split    完整 schema - 基底 → deviation(把既有的全量 schema 拆成偏离,一次性)
  check    compose(split(S)) 必须 == S(拆合无损),否则拆分丢了东西

规则(与样式的 根×包 同一个思想):
  偏离覆盖基底;偏离没写的键取基底 default;基底 required 且偏离与默认都没有 → **拒绝并点名**。
  不猜:一册没写 hierarchy,不是「沿用上一册的」,是「这册还没定」。

为什么合成而不是并集:2026-08-16 逐键比对两册,4 个交集角色的正则**有意不同**(各自源文的真实差异),
硬融成一份会让识别靠猜哪条适用——正是「宁可拒绝不可猜」要防的。

用法:
  compose_schema.py compose --deviation D.json --out S.json
  compose_schema.py split   --schema S.json --out D.json
  compose_schema.py check   --schema S.json
"""
from __future__ import annotations
import argparse, json, sys
from collections import OrderedDict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "seeds" / "schema-base" / "schema-base.v1.json"
META = {"$base", "$baseSha256", "$note"}


def load(p): return json.loads(Path(p).read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
def canon(o): return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compose(dev: dict, base: dict) -> tuple[dict, list]:
    out, missing = OrderedDict(), []
    keys = base["keys"]
    for k, meta in keys.items():
        if k in dev:
            out[k] = dev[k]
        elif meta.get("default") is not None:
            out[k] = json.loads(json.dumps(meta["default"]), object_pairs_hook=OrderedDict)
        elif meta.get("required"):
            missing.append(k)
    # 偏离里基底没登记的键(册自有扩展,如化学的 reactions/conditionArrows)原样带过——基底管骨架,不禁止扩展
    for k, v in dev.items():
        if k not in keys and k not in META:
            out[k] = v
    return out, missing


def split(schema: dict, base: dict) -> dict:
    keys = base["keys"]; dev = OrderedDict()
    dev["$base"] = "seeds/schema-base/schema-base.v1.json"
    dev["$note"] = "只写与基底不同的键;与基底 default 逐字相同的键已省略,合成时取回。"
    for k, v in schema.items():
        meta = keys.get(k)
        if meta and meta.get("default") is not None and canon(v) == canon(meta["default"]):
            continue                          # 与默认相同 → 省略
        dev[k] = v
    return dev


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("compose"); a.add_argument("--deviation", required=True); a.add_argument("--out", required=True)
    b = sub.add_parser("split"); b.add_argument("--schema", required=True); b.add_argument("--out", required=True)
    c = sub.add_parser("check"); c.add_argument("--schema", required=True)
    args = ap.parse_args(); base = load(BASE)
    if args.cmd == "compose":
        out, missing = compose(load(args.deviation), base)
        if missing:
            print(json.dumps({"status": "refused", "missing": missing,
                              "why": "基底标 required、偏离没写、也没有可继承的默认。不猜:这册还没定这些。"}, ensure_ascii=False, indent=1)); return 1
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "out": args.out, "keys": len(out)}, ensure_ascii=False)); return 0
    if args.cmd == "split":
        dev = split(load(args.schema), base)
        Path(args.out).write_text(json.dumps(dev, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "out": args.out, "deviationKeys": len([k for k in dev if k not in META])}, ensure_ascii=False)); return 0
    S = load(args.schema); dev = split(S, base); back, missing = compose(dev, base)
    ok = canon(back) == canon(S) and not missing
    print(json.dumps({"status": "pass" if ok else "fail", "roundtripLossless": canon(back) == canon(S), "missing": missing,
                      "diffKeys": [k for k in set(S) | set(back) if canon(S.get(k)) != canon(back.get(k))]}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
