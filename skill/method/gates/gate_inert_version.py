#!/usr/bin/env python3
"""GATE_INERT_VERSION:夺权版本里不许出现改进值。

**为什么需要它。**
docDefaults1 的 valuePolicy 写着:「取当前生效值,不取『我认为对的值』。
夺回所有权与改进取值是两件事:混在一起做,落地后出现差异就分不清是夺权引起的
还是改值引起的。」

这条规则此前只是一句话。而在写下它的同一天、同一份文件里,同一个人违反了两次:
  firstLineChars  现值 0 被写成 200(规范原文的「2 字符」)
  color           现值 auto 被写成 000000
两次都是拿「我认为对的」替换「现在是的」。第一次由使用方发现,第二次自查发现。

**靠记性的规则等于没有规则。** 所以把它变成判据。

判据形状:version 以 `-inert` 结尾时,pendingImprovements 里每个 checks[].path
在参数表里的现值必须 == inert 且 != proposed。改进项一旦被提前实现,现值就会
等于 proposed,门立刻报出。

**不猜。** 缺 checks 的改进项直接判为 unverifiable 并使整道门失败——
不是跳过。跳过会让「没写 checks」变成绕过本门的合法姿势,
那正是判据恒假的制造方式。

用法:
  gate_inert_version.py --params <参数表>
退出码 0=通过 1=有改进值提前落地或有项无法核查
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MISSING = object()


def dig(node, path: str):
    cur = node
    for part in path.split("/"):
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    root = params.get("docDefaults1") or {}
    version = str(root.get("version") or "")
    pending = root.get("pendingImprovements") or []

    findings, checked = [], 0
    if not version.endswith("-inert"):
        report = {"gate": "GATE_INERT_VERSION", "version": version,
                  "status": "not-applicable",
                  "why": "版本不以 -inert 结尾,本门只约束夺权版本。"}
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    for i, entry in enumerate(pending):
        item = entry.get("item") or f"#{i}"
        checks = entry.get("checks")
        if not checks:
            findings.append({"item": item, "kind": "unverifiable",
                             "why": "该改进项没有 checks,门无法核查。"
                                    "**不跳过**:跳过会让「没写 checks」成为绕过本门的"
                                    "合法姿势,那正是判据恒假的制造方式。"})
            continue
        for ck in checks:
            path = ck.get("path")
            actual = dig(params, path)
            checked += 1
            if actual is MISSING:
                findings.append({"item": item, "path": path, "kind": "path-missing",
                                 "why": "登记的路径在参数表里不存在。"
                                        "要么参数表改了结构而登记没跟,要么登记一开始就写错——"
                                        "两者都使这条改进项失去可核查性。"})
                continue
            if actual != ck.get("inert"):
                findings.append({"item": item, "path": path, "kind": "drifted-from-inert",
                                 "expected": ck.get("inert"), "actual": actual,
                                 "why": "现值已不等于登记的『当前生效值』。"})
            if "proposed" in ck and ck["proposed"] is not None \
                    and actual == ck["proposed"]:
                findings.append({"item": item, "path": path, "kind": "improvement-landed-early",
                                 "proposed": ck["proposed"], "actual": actual,
                                 "why": "改进值已提前落地在夺权版本里。"
                                        "夺权版本应为零渲染变化;混入改值后,"
                                        "落地差异就分不清是哪一个引起的。"})

    report = {
        "gate": "GATE_INERT_VERSION",
        "params": str(args.params), "version": version,
        "improvements": len(pending), "checkpoints": checked,
        "findings": findings,
        "status": "pass" if not findings else "fail",
        "policy": root.get("inertPolicy"),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("version", "improvements", "checkpoints", "findings", "status")},
                     ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
