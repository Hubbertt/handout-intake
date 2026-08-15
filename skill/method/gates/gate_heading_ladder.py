#!/usr/bin/env python3
"""GATE_HEADING_LADDER:标题梯队的五条不变量必须成立。

**起因。** 参数表 wordStyleRegistry.headingLadder 写着五条不变量,措辞明确、
理由充分——而**没有任何脚本在检查它们**。写在规范里、没人验证,
与验收项对账查出的缺口是同一个形状:两张表各自都齐,中间没人对。

它比一般的缺口更危险:这五条是**跨样式**的关系式。任何人改一级标题的
字号或间距,都可能在另一级上破掉关系,而单看被改的那一级完全正常。
2026-08-15 使用方要「竖线与文本等高」时就撞上了它——竖线高度在 Word 里
恰好等于整块高度,而整块高度被这条网格钉死;若无本门,把三级压到 350 缇
会同时破坏「整行数」和「逐级不增」,且两处都不会有人吭声。

五条判据(原文见参数表):
  ① 字号逐级不增
  ② 整块高度(段前+标称行+段后)逐级不增
  ③ 整块高度是 gridDxa 的整数倍
  ④ 段前 ≥ 段后
  ⑤ 进目录的最小一级必须比不进目录的最大一级字号更大

标称行 = round(lineMultiple × sizePt × 20),规则由参数表自己给出,不在此另立。

用法:
  gate_heading_ladder.py --params <参数表>
退出码 0=五条全部成立 1=有不变量被破坏
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    registry = params.get("wordStyleRegistry") or {}
    ladder = registry.get("headingLadder") or {}
    styles = registry.get("paragraphStyles") or {}
    grid = ladder.get("gridDxa")

    rungs = []
    for sid, spec in styles.items():
        level = spec.get("outlineLevel")
        if not isinstance(level, int):
            continue                      # body / None 不是梯队的一级
        size, mult = spec.get("sizePt"), spec.get("lineMultiple")
        before, after = spec.get("beforeDxa"), spec.get("afterDxa")
        missing = [k for k, v in (("sizePt", size), ("lineMultiple", mult),
                                  ("beforeDxa", before), ("afterDxa", after))
                   if v is None]
        rungs.append({"styleId": sid, "level": level, "sizePt": size,
                      "lineRule": spec.get("lineRule"), "lineDxa": spec.get("lineDxa"),
                      "shaded": bool(spec.get("paragraphShading")),
                      "lineMultiple": mult, "beforeDxa": before, "afterDxa": after,
                      "tocLevel": spec.get("tocLevel"), "missing": missing})
    rungs.sort(key=lambda r: r["level"])

    findings = []
    for r in rungs:
        if r["missing"]:
            # 不猜:缺键就无法判定,如实报出并使整门失败,不跳过这一级。
            findings.append({"kind": "incomplete-rung", "styleId": r["styleId"],
                             "missing": r["missing"],
                             "why": "这一级缺必填键,梯队关系无法判定。"
                                    "跳过它等于让「没写值」成为绕过本门的姿势。"})
            continue
        # exact 行距时行高就是 lineDxa(缇),不再是倍数×字号——带底纹的标题用 exact 让文字在色块里垂直居中
        # (Word 段落底纹只覆盖行盒不含段前后,auto 行距下中文字形靠上,只有 exact 能居中)。
        r["nominalLineDxa"] = (int(r["lineDxa"]) if r.get("lineRule") == "exact" and r.get("lineDxa")
                               else round(r["lineMultiple"] * r["sizePt"] * 20))
        r["blockDxa"] = r["beforeDxa"] + r["nominalLineDxa"] + r["afterDxa"]
        # ④ 段前 ≥ 段后:空白归上方,标题贴着自己管辖的内容。
        # ★这条只对**没有底纹**的标题成立。带整行底纹时,段前段后都在色块**里面**,
        #   色块外的白空间是 0——「贴不贴内容」与这两个数无关,它们只决定文字在色块里的上下位置。
        #   而中文字形在 Word 行盒里本身靠上,要让文字视觉居中,恰恰需要 段后 > 段前 去抵消。
        #   使用方 2026-08-16 两次指出「概念构建」偏上,实测偏 10px;门原先把这条套在带底纹的标题上,
        #   等于禁止它居中。门写得对,适用范围写宽了——豁免带底纹的,不是放宽全部。
        if r["beforeDxa"] < r["afterDxa"] and not r.get("shaded"):   # ④
            findings.append({"kind": "spacing-below-exceeds-above", "styleId": r["styleId"],
                             "beforeDxa": r["beforeDxa"], "afterDxa": r["afterDxa"],
                             "why": "段前 < 段后。空白应归属于上方,标题贴着自己管辖的内容。"})
        if grid and r["blockDxa"] % grid:                        # ③
            findings.append({"kind": "block-off-grid", "styleId": r["styleId"],
                             "blockDxa": r["blockDxa"], "gridDxa": grid,
                             "rows": round(r["blockDxa"] / grid, 3),
                             "why": "整块高度不是网格的整数倍,标题会把它下面的正文顶离网格。"
                                    "★同时:Word 里段落左边框的高度就等于整块高度,"
                                    "所以这条网格也决定了竖线能有多贴文字。"})

    ok = [r for r in rungs if not r["missing"]]
    for a, b in zip(ok, ok[1:]):
        if b["sizePt"] > a["sizePt"]:                            # ①
            findings.append({"kind": "size-increases-down-ladder",
                             "upper": a["styleId"], "lower": b["styleId"],
                             "upperSizePt": a["sizePt"], "lowerSizePt": b["sizePt"]})
        if b["blockDxa"] > a["blockDxa"]:                        # ②
            findings.append({"kind": "block-increases-down-ladder",
                             "upper": a["styleId"], "lower": b["styleId"],
                             "upperBlockDxa": a["blockDxa"], "lowerBlockDxa": b["blockDxa"],
                             "why": "下一级的整块比上一级还高,视觉层级与语义层级倒置。"})

    in_toc = [r for r in ok if r.get("tocLevel") is not None]
    out_toc = [r for r in ok if r.get("tocLevel") is None]
    if in_toc and out_toc:                                       # ⑤
        smallest_in = min(in_toc, key=lambda r: r["sizePt"])
        largest_out = max(out_toc, key=lambda r: r["sizePt"])
        if smallest_in["sizePt"] <= largest_out["sizePt"]:
            findings.append({"kind": "toc-boundary-invisible",
                             "smallestInToc": smallest_in["styleId"],
                             "sizePt": smallest_in["sizePt"],
                             "largestOutOfToc": largest_out["styleId"],
                             "outSizePt": largest_out["sizePt"],
                             "why": "目录是读者的检索面。能查到的东西和查不到的东西"
                                    "长得一样,阶梯就是骗人的。"})

    report = {"gate": "GATE_HEADING_LADDER", "gridDxa": grid,
              "rungs": rungs, "findings": findings,
              "status": "pass" if not findings else "fail",
              "shape": "跨样式的关系式最容易悄悄破:改一级的值,破在另一级上,"
                       "而单看被改的那一级完全正常。"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({"gridDxa": grid,
                      "rungs": [{k: r.get(k) for k in
                                 ("styleId", "sizePt", "blockDxa")} for r in rungs],
                      "findings": findings, "status": report["status"]},
                     ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
