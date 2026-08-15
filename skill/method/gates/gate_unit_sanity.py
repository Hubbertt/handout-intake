#!/usr/bin/env python3
"""GATE_UNIT_SANITY:带单位的参数值必须落在该单位下有意义的区间。

**起因是一条从写下那天起就没生效过的规范。**
私有规范原文:「连续阅读材料、语文长文和说明性文本可使用首行缩进 2 字符」。
参数表写成 firstLineChars: 2。而 OOXML 的 firstLineChars 单位是**百分之一字符**,
「2 字符」应写 200。写 2 即 0.02 字符 ≈ 0.0035mm,等于没配。

这是**判据恒假长在参数上的样子**,而且比判据恒假更难发现:
判据恒假至少还能靠「命中数长期为 0」起疑;参数恒假连个计数都没有,
声明在、溯源在、规范原文在,页面上就是没有缩进,谁也不会联想到是单位写错。

它的形状是可扫全类的:**规范原文的数字被原样抄进不同单位的字段**——
抄了数字,没抄单位。所以判据不能只问「这个键有没有值」,
要问「这个数在这个单位下有没有意义」。

用法:
  gate_unit_sanity.py --params <参数表>
退出码 0=通过 1=有值落在无意义区间
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 键 → (单位说明, 合理区间, 换算提示)
# 区间取「人可能有意配置的范围」,不是「合法范围」:0 一律合法(明确表示不缩进),
# 而 1–99 百分之一字符这种值,没有任何排版意图能解释它。
UNIT_RULES = {
    "firstLineChars": ("百分之一字符", (100, 800),
                       "N 字符 = N×100。规范原文写「2 字符」应是 200,不是 2。"),
    "leftIndentChars": ("百分之一字符", (100, 800), "同上"),
    "rightIndentChars": ("百分之一字符", (100, 800), "同上"),
    "hangingChars": ("百分之一字符", (100, 800), "同上"),
    "firstLineDxa": ("缇(1/1440 英寸)", (100, 2880),
                     "12pt 正文两字符约 480 缇。小于 100 缇不足 0.2mm,不构成缩进。"),
    "leftIndentDxa": ("缇", (100, 5760), "同上"),
    "hangingDxa": ("缇", (100, 2880), "同上"),
    "sizePt": ("磅", (5, 72), "正文教材字号常在 9–18 磅。"),
    "characterSpacingTwips": ("缇", (10, 200), "字距微调常在 10–100 缇。"),
}


def walk(node, path, out):
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, f"{path}/{key}" if path else key, out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, f"{path}[{i}]", out)
    else:
        out.append((path, node))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    flat: list[tuple[str, object]] = []
    walk(params, "", flat)

    findings = []
    checked = 0
    for path, value in flat:
        key = path.rsplit("/", 1)[-1]
        rule = UNIT_RULES.get(key)
        if not rule or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        checked += 1
        unit, (lo, hi), hint = rule
        if value == 0:
            continue                       # 0 是明确的「不设」,合法
        if not (lo <= value <= hi):
            findings.append({"path": path, "value": value, "unit": unit,
                             "sane": f"{lo}–{hi}", "hint": hint,
                             "why": "这个数在这个单位下没有排版意义,"
                                    "多半是规范原文的数字被原样抄进了不同单位的字段。"})

    report = {
        "gate": "GATE_UNIT_SANITY",
        "params": str(args.params),
        "checked": checked,
        "findings": findings,
        "status": "pass" if not findings else "fail",
        "shape": "规范原文的数字被原样抄进不同单位的字段——抄了数字,没抄单位。"
                 "判据因此不能只问「这个键有没有值」,要问「这个数在这个单位下有没有意义」。",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("checked", "findings", "status")},
                     ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
