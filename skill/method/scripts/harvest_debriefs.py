#!/usr/bin/env python3
"""从各册的运行记录里收割已填的 debrief,汇成经验层的候选清单。

闭环的最后一段:运行 → 记录 → debrief(人填)→ 本脚本汇总候选 → 人挑 → 写进 experience/rules.v1.json
(带 fromRuns)→ 准入门校验。

只收 status=filled 的。pending 的列出来提醒——**未填的运行不算完**。

用法:
  harvest_debriefs.py --volumes-root <volumes/> [--out experience/candidates.json]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--volumes-root", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    filled, pending = [], []
    for deb in sorted(a.volumes_root.glob("*/runs/*/debrief.json")):
        d = json.loads(deb.read_text(encoding="utf-8"))
        rel = str(deb.parent.relative_to(a.volumes_root))
        (filled if d.get("status") == "filled" else pending).append({"run": rel, **d.get("fill", {})})
    cands = []
    for f in filled:
        for c in f.get("candidateRules") or []:
            cands.append({"candidate": c, "fromRuns": [f["run"]], "filledBy": f.get("filledBy")})
    out = {"schemaVersion": "handout-intake.experience-candidates.v1",
           "filledRuns": len(filled), "pendingRuns": [p["run"] for p in pending],
           "candidates": cands,
           "note": "候选不是规律。写进 rules.v1.json 前须过准入四判据,并保留 fromRuns 供门校验。"}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("filledRuns", "pendingRuns")}, ensure_ascii=False, indent=1))
    print(f"候选 {len(cands)} 条" + (f" → {a.out}" if a.out else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
