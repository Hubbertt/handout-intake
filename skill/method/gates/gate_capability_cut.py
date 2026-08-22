#!/usr/bin/env python3
"""GATE_CAPABILITY_CUT:原子化不得消费编制成册的产物。

PM 2026-08-22 定:要的是**两个技能**——原子化、编制成册。
切口不是设计出来的,是实测出来的:按 consumes/produces 算依赖闭包,
接口面只有三个产物,**反向边 0 条**。

★这道门守的不是「现在是不是 0 条」,而是「以后还是不是 0 条」。
  反向边一出现,两个能力就再也拆不开了——而它出现的方式通常很温和:
  原子化那侧顺手读一个蓝图产物,当时看着挺方便,拆的时候才发现拆不动。
  等到拆不动才发现,已经晚了;所以这条边现在就要有人守。

反过来是允许的:compose 消费 atomise 的产物,正是链条的方向
(原子化 → 导入题库 → 私有规范 → 编制成册)。
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def norm(artifact):
    return artifact.split("@")[0].rstrip("'")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=Path,
                    default=Path(__file__).resolve().parents[1] / "steps.v1.json")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    data = json.loads(args.steps.read_text(encoding="utf-8"))
    steps = data["steps"]

    untagged = [s["id"] for s in steps if not s.get("capability")]
    producers = defaultdict(set)
    for s in steps:
        for a in s.get("produces", []):
            producers[norm(a)].add(s.get("capability"))

    backward = []
    for s in steps:
        if s.get("capability") != "atomise":
            continue
        for a in s.get("consumes", []):
            owners = producers.get(norm(a), set())
            # 只由 compose 生产、atomise 却要消费 → 反向边
            if owners and owners == {"compose"}:
                backward.append({"step": s["id"], "artifact": norm(a),
                                 "producedBy": sorted(
                                     p["id"] for p in steps
                                     if norm(a) in [norm(x) for x in p.get("produces", [])])})

    failures = []
    if untagged:
        failures.append(f"{len(untagged)} 步没有标 capability:{untagged}。"
                        f"没标就不在任何一个能力里,拆的时候没人知道它该跟谁走。")
    for b in backward:
        failures.append(f"反向边:atomise 的 {b['step']} 消费了只由 compose 生产的 "
                        f"{b['artifact']}(出自 {b['producedBy']})")

    report = {"gate": "GATE_CAPABILITY_CUT",
              "status": "fail" if failures else "pass",
              "counts": {"atomise": sum(1 for s in steps if s.get("capability") == "atomise"),
                         "compose": sum(1 for s in steps if s.get("capability") == "compose")},
              "interface": sorted({norm(a) for s in steps if s.get("capability") == "compose"
                                   for a in s.get("consumes", [])
                                   if producers.get(norm(a)) == {"atomise"}}),
              "backwardEdges": backward,
              "failures": failures}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
