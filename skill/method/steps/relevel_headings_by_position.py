#!/usr/bin/env python3
"""按结构位置重定标题级——不按文字模式。

**为什么。** 物理册的切分规则按文字模式判级:中文编号「一、」→ 五级、
知识点NN → 四级。而同一个文字形态在这册里承担两种结构角色:

  概念构建 之下的「一、X」  是栏目下的顶层主题(14 处)
  知识点NN 之下的「一、X」  是知识点下的子项(2 处,真的深一级)

第11讲里「一、平面镜成像特点」两处都出现——概念构建里一次、知识点01 下面一次,
一字不差。**文字形态定不了级**,这是铁证。

后果:14 处「一、X」直接挂在栏目(二级)下却拿到四级,大纲里三级空缺;
而它们与深研精炼里的「知识点NN」是同一结构位置,却一个四级一个三级。

**判据取自源文自己的信号,不是我们的偏好。** 源文所有段落都是 Normal 样式、
无大纲级别,唯一信号是加粗与字号,而两者一致:

  概念构建:  一、X 加粗;其下 1. X **一处都不加粗**  → 只有一层标题
  深研精炼:  知识点NN 加粗;其下 1. X **35 处全部加粗** → 两层标题

深研精炼比概念构建多一层标题。这不是我们定的层级,是源文本来就有的。

规则改为按最近的栏目祖先(heading2)与最近的标题祖先判定,与上面的加粗证据
逐条对得上。命中数必须等于源文数出来的 14 与 35——对不上就是判据错了,
本步会如实报出而不静默按新数字走。

用法:
  relevel_headings_by_position.py --workspace X [--volume V] [--dry-run]
"""

from __future__ import annotations

import json
import sys

from _bootstrap import chain_from_argv  # noqa: E402

# --dry-run 由本步自己吃掉:_bootstrap 的解析器不认它,留在 argv 里会直接报错。
DRY = "--dry-run" in sys.argv
if DRY:
    sys.argv = [a for a in sys.argv if a != "--dry-run"]
CHAIN = chain_from_argv(__doc__)
BLUEPRINT = CHAIN.path_for('blueprint.substituted')
MAPPING = CHAIN.path_for('mapping.own')

HEADINGS = ("chapter", "heading1", "heading2", "heading3", "heading4", "heading5")


def text_of(block: dict) -> str:
    return "".join(s.get("text", "") for s in (block.get("segments") or [])
                   if isinstance(s, dict))


def flatten(node, out):
    if isinstance(node, dict):
        if node.get("type") and str(node.get("id", "")).startswith("b"):
            out.append(node)
        for value in node.values():
            flatten(value, out)
    elif isinstance(node, list):
        for value in node:
            flatten(value, out)


def main() -> int:
    rules = (json.loads(MAPPING.read_text(encoding="utf-8"))
             .get("headingPositionLevels") or {})
    if not rules:
        print(json.dumps({"step": "relevel-headings-by-position",
                          "status": "no-rules",
                          "why": "私有规范里没有 headingPositionLevels。"
                                 "本步不猜:没有规则就不动任何块。"},
                         ensure_ascii=False, indent=1))
        return 0

    blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
    blocks: list[dict] = []
    flatten(blueprint, blocks)

    banner_of, parent_of = {}, {}
    banner = parent = None
    for block in blocks:
        kind = block["type"]
        banner_of[id(block)] = banner
        parent_of[id(block)] = parent
        if kind == "heading2":
            banner, parent = text_of(block), None
        elif kind in HEADINGS or kind == "callout_title":
            parent = block

    changes, counts = [], {}
    for block in blocks:
        section = banner_of[id(block)]
        parent_block = parent_of[id(block)]
        parent_kind = parent_block["type"] if parent_block else None
        parent_text = text_of(parent_block) if parent_block else ""
        for rule in rules.get("rules") or []:
            if block["type"] != rule["from"]:
                continue
            if rule.get("section") and rule["section"] not in (section or ""):
                continue
            if rule.get("parentType") and parent_kind != rule["parentType"]:
                continue
            if rule.get("parentStartsWith") and not parent_text.startswith(
                    rule["parentStartsWith"]):
                continue
            # 源文的加粗信号一路带到了这里:整段加粗 → 全部 segment 是 emphasis。
            # **判据用源文自己的信号,不用文字模式。** 位置相同的三种块——标题、
            # 【例1】题干、整句描述——只有这一项能把它们分开;靠「【例」前缀」或
            # 「句子长度」去分,是拿我们的印象替换源文的标记。
            need = rule.get("allSegmentsRunType")
            if need:
                segs = [s for s in (block.get("segments") or [])
                        if isinstance(s, dict) and (s.get("text") or "").strip()]
                if not segs or any(s.get("run_type") != need for s in segs):
                    continue
            counts[rule["id"]] = counts.get(rule["id"], 0) + 1
            changes.append({"block": block["id"], "rule": rule["id"],
                            "from": block["type"], "to": rule["to"],
                            "text": text_of(block)[:30],
                            "section": section, "parent": parent_text[:24]})
            block["_releveledTo"] = rule["to"]
            break

    # 命中数必须等于源文数出来的期望值。对不上就是判据错了——
    # 这时按新数字走,等于用「跑出来的结果」替换「量出来的事实」。
    expected = rules.get("expectedCounts") or {}
    mismatch = {k: {"expected": v, "actual": counts.get(k, 0)}
                for k, v in expected.items() if counts.get(k, 0) != v}
    report = {"step": "relevel-headings-by-position",
              "counts": counts, "expected": expected, "mismatch": mismatch,
              "changed": len(changes),
              "status": "fail" if mismatch else "ok",
              "note": "判据取自源文自己的加粗与字号信号,不是我们的偏好。"
                      "命中数与源文数出来的不符即判据错,如实报出不静默按新数字走。"}
    if mismatch:
        report["sample"] = changes[:10]
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1

    if not DRY:
        for block in blocks:
            to = block.pop("_releveledTo", None)
            if to:
                block["type"] = to
        BLUEPRINT.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    else:
        for block in blocks:
            block.pop("_releveledTo", None)
        report["dryRun"] = True
    report["sample"] = changes[:6]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
