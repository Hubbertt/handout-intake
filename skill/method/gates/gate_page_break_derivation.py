#!/usr/bin/env python3
"""GATE_PAGE_BREAK_DERIVATION:pageBreakStandard.breakBefore(备查清单)必须等于编译器的推导结果。

换页样式集是从数据推导的(tocLevel + maxBreakLevel),breakBefore 只是把推导结果写下来备查。
两处并存就会漂——本册的分页门吃过这个亏:规则写样式名、判据比块类型,恒假到一次没生效。
本门断言两者相等;不等就是有人改了一处没改另一处。

用法:  gate_page_break_derivation.py --params <合成后的参数表> --vendor <skill/vendor 目录>
"""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path); ap.add_argument("--vendor", required=True, type=Path)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("bsh", a.vendor / "build_semantic_handout_from_blueprint.py")
    m = importlib.util.module_from_spec(spec); sys.modules["bsh"] = m; spec.loader.exec_module(m)
    params = json.loads(a.params.read_text(encoding="utf-8"))
    reg = params.get("wordStyleRegistry") or {}
    derived = sorted(m.break_before_styles(reg))
    listed = sorted((reg.get("pageBreakStandard") or {}).get("breakBefore") or [])
    ok = derived == listed
    print(json.dumps({"gate": "GATE_PAGE_BREAK_DERIVATION", "derived": derived, "listed": listed,
                      "maxBreakLevel": (reg.get("pageBreakStandard") or {}).get("maxBreakLevel"),
                      "status": "pass" if ok else "fail",
                      "why": None if ok else "备查清单与推导结果不等——两处并存就会漂,改了一处没改另一处。"},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
