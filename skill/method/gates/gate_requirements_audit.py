#!/usr/bin/env python3
"""GATE_REQUIREMENTS_AUDIT:runtime/requirements.json 声明的包 == 代码实际 import 的第三方包。

三种漂法,沙盒各抓到一个(2026-08-16):
  漏声明   Pillow ——原机器恰好装着,五次「全新安装」都没暴露;沙盒的干净 3.12 一跑就露
  多声明   fontTools ——声明了但没有任何代码 import,用户白装
  条件依赖 openpyxl ——只在源文含图表时才 import,无图表的册永远不触发

判据:扫 skill/ styles/ runtime/ 下所有 .py 的顶层与函数内 import,减去标准库与包内模块,
得到第三方集合;与 requirements.json 的 importName 集合比,双向差集都必须为空。

用法:  gate_requirements_audit.py --product <产品根>
退出码 0=一致 1=有漏或有多
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

# 标准库名单只信 sys.stdlib_module_names(3.10+)。更老的解释器上**不猜**:
# 首版在 3.9 上退化为一份手写常见集,把 html/xml 当成第三方报了两条假「漏声明」——
# 门在自己的降级路径上误报,比不报更坏:它会让人去装不存在的包。
STD = set(getattr(sys, "stdlib_module_names", ()))
NOISE = {"the", "a", "an", "this", "that", "__future__"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", required=True, type=Path)
    a = ap.parse_args()
    if not STD:
        print(json.dumps({"gate": "GATE_REQUIREMENTS_AUDIT", "status": "refused",
                          "why": f"本解释器({sys.version.split()[0]})没有 sys.stdlib_module_names,无法可靠区分标准库与第三方。"
                                 "用 ≥3.10 跑本门。不猜。"}, ensure_ascii=False, indent=1))
        return 2
    P = a.product
    roots = [P / "skill", P / "styles", P / "runtime"]
    local = {p.stem for r in roots for p in r.rglob("*.py")}
    imports = set()
    for r in roots:
        for p in r.rglob("*.py"):
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("#"):
                    continue
                m = re.match(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
                if m:
                    imports.add(m.group(1))
    third = {i for i in imports if i not in STD and i not in local and i not in NOISE and not i.startswith("_")}
    req = json.loads((P / "runtime" / "requirements.json").read_text(encoding="utf-8"))
    declared = {x["importName"] for x in req["pythonPackages"]}
    missing = sorted(third - declared)      # 代码用了、没声明 → 陌生机器上会 ModuleNotFoundError
    extra = sorted(declared - third)        # 声明了、没人用 → 用户白装
    findings = ([{"kind": "undeclared-import", "module": m,
                  "why": "代码 import 了它,requirements 没声明。原机器恰好装着就永远不会暴露。"} for m in missing]
                + [{"kind": "declared-unused", "module": m,
                    "why": "声明了但没有任何代码 import,用户白装;而它在清单里和真依赖长得一样。"} for m in extra])
    print(json.dumps({"gate": "GATE_REQUIREMENTS_AUDIT", "thirdParty": sorted(third), "declared": sorted(declared),
                      "findings": findings, "status": "pass" if not findings else "fail"}, ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
