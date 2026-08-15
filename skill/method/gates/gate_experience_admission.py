#!/usr/bin/env python3
"""GATE_EXPERIENCE_ADMISSION:经验层的准入四判据必须逐条满足。

MANIFEST 的准入规则写着:写进经验层的每条规律必须带
  ① 现象/判据/处置/本质
  ② 全类扫描命中数
  ③ 一道门
  ④ 破坏性自证
配不出门的只能记为观察。定稿前一律 provisional。

**这条规则本身也需要一道门。** 经验层是「下一册照着做」的依据;
一条没配门、没自证的「规律」混进去,下一册就会照着一个未经检验的判断做,
而它在文件里和真正的规律长得一模一样——正是本轮反复抓到的那个形状。

判据附带两条硬约束:
  规律里出现「配不出门」的字样 → 它自己承认该进 observations,却写在了 rules 里
  门指向的文件必须真实存在 → 写了个门名而文件不在,与没有门等价

用法:
  gate_experience_admission.py --rules <rules.v1.json> --package <包根目录>
退出码 0=全部满足 1=有条目不满足
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = ["现象", "判据", "处置", "本质", "扫全类命中数", "门", "破坏性自证"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True, type=Path)
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--volumes-root", type=Path,
                    help="volumes/ 目录;给了就校验每条规律的 fromRuns 指向已填 debrief 的运行")
    args = ap.parse_args()

    data = json.loads(args.rules.read_text(encoding="utf-8"))
    findings = []

    for rule in data.get("rules") or []:
        rid = rule.get("id", "?")
        missing = [k for k in REQUIRED if not str(rule.get(k) or "").strip()]
        if missing:
            findings.append({"rule": rid, "kind": "missing-fields", "missing": missing,
                             "why": "准入四判据缺项。缺哪一项就在哪一项上没有检验过。"})
        if rule.get("status") != "provisional":
            findings.append({"rule": rid, "kind": "not-provisional",
                             "status": rule.get("status"),
                             "why": "定稿前一律 provisional。在使用方认可之前归纳,"
                                    "会把错的选择变成带门的规则。"})
        if "配不出门" in str(rule.get("门") or "") or "无门" in str(rule.get("门") or ""):
            findings.append({"rule": rid, "kind": "self-declared-gateless",
                             "why": "它自己承认配不出门,却写在 rules 里。"
                                    "配不出门的只能记为 observations。"})
        # 门必须真实存在。写了个门名而文件不在,与没有门等价。
        for token in str(rule.get("门") or "").replace(";", " ").replace(";", " ").split():
            if token.startswith("method/") and token.endswith(".py"):
                if not (args.package / token).exists():
                    findings.append({"rule": rid, "kind": "gate-file-missing",
                                     "gate": token,
                                     "why": "登记的门文件不存在。写了个门名而文件不在,"
                                            "与没有门等价——而它在报告里看着有门。"})

    # ★闭环:经验层只收 debrief 已填的运行。
    # 使用方 2026-08-15 定「每次做完都要有记录、经验总结,做成闭环」。
    # 每条规律必须能溯到至少一次运行的 debrief(fromRuns 非空且那些 debrief.status=filled),
    # 否则它是凭印象写的——凭印象写的规律和实测归纳的规律长得一模一样。
    # 没给 --volumes-root 时不查此项(包内自测场景),但如实在报告里标 runLinkageChecked=false。
    run_linkage_checked = False
    if args.volumes_root:
        run_linkage_checked = True
        for rule in data.get("rules") or []:
            runs = rule.get("fromRuns") or []
            if not runs:
                findings.append({"rule": rule.get("id"), "kind": "no-run-linkage",
                                 "why": "规律没有关联任何运行记录(fromRuns 为空)。"
                                        "凭印象写的规律和实测归纳的规律长得一模一样——"
                                        "关联到 debrief 才分得出。"})
                continue
            for r in runs:
                deb = Path(args.volumes_root) / r / "debrief.json"
                if not deb.exists():
                    findings.append({"rule": rule.get("id"), "kind": "run-record-missing", "run": r})
                elif json.loads(deb.read_text(encoding="utf-8")).get("status") != "filled":
                    findings.append({"rule": rule.get("id"), "kind": "debrief-not-filled", "run": r,
                                     "why": "关联的运行 debrief 未填。未填的运行不算完,"
                                            "由它归纳出的规律也就没有依据。"})

    for obs in data.get("observations") or []:
        if not str(obs.get("为什么只是观察") or "").strip():
            findings.append({"rule": obs.get("id", "?"), "kind": "observation-without-reason",
                             "why": "观察必须写明为什么配不出门。不写理由,"
                                    "它与「懒得配门的规律」无法区分。"})

    report = {"gate": "GATE_EXPERIENCE_ADMISSION",
              "rules": len(data.get("rules") or []),
              "observations": len(data.get("observations") or []),
              "rejected": len(data.get("rejected") or []),
              "findings": findings,
              "runLinkageChecked": run_linkage_checked,
              "status": "pass" if not findings else "fail",
              "why": "经验层是下一册照着做的依据。没配门没自证的『规律』混进去,"
                     "下一册就会照着一个未经检验的判断做,"
                     "而它在文件里和真正的规律长得一模一样。"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("rules", "observations", "rejected", "findings", "status")},
                     ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
