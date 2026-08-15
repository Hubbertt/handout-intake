#!/usr/bin/env python3
"""把文本里的上下标字面量拆成带上下标标记的独立段。

**为什么不能靠字符替换。**
GATE_GLYPH_COVERAGE 的 characterPolicy.substitutions 是字符换字符(❌→×),
而上标不是换一个字符,是**把一个段拆成三段**并给中间那段加 run_type。
硬塞进字符替换机制,只会把 ⁸ 换成 8,丢掉上标——那不是修,是把缺陷改小。

**为什么该修。**
同一册已有 19 个公式对象走 superscript 段;这一处因为是纯文本(不是行内图)
漏在了那套机制的范围外。字面量上标有两个问题:
  字体覆盖不确定 —— 实测宋体与 Times 都没有 U+2078,Word 会悄悄回退
  语义丢失      —— 它是一个字符,不是「8 的上标」,复制、检索、朗读都不对

它原先由 characterPolicy.acceptedWithEvidence 放行,证据是「PDF 字体审计只报出
AppleColorEmoji」。**那条证据后来被证伪**:MS-Mincho 也在成品里而审计没报,
说明该审计口径本身有缝。拿一道有缝的门当证据,等于没有证据。

用法:
  substitute_literal_supersub.py --workspace X [--volume V]
"""

from __future__ import annotations

import json
import sys

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
BLUEPRINT = CHAIN.path_for('blueprint.substituted')
# 读 mapping.substitutions(公式与标题替换表),不读合并件 mapping——
# 合并件只取 private-spec 的若干节,不含本表。首版读错了地方,规则数报 0
# 而一切「正常」:**规则放对了地方,读错了地方**,又一次静默失效。
MAPPING = CHAIN.path_for('mapping.substitutions')


def load_rules() -> dict[str, dict]:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    block = mapping.get("literalSuperSubstitutions") or {}
    return {e["from"]: e for e in (block.get("objects") or []) if e.get("from")}


def split_segments(segments: list, rules: dict[str, dict]) -> tuple[list, int]:
    """把含字面量的段拆成 前 / 上下标 / 后 三段。"""
    out, changed = [], 0
    for seg in segments:
        text = seg.get("text")
        if not isinstance(text, str) or not any(ch in text for ch in rules):
            out.append(seg)
            continue
        buf = ""
        for ch in text:
            rule = rules.get(ch)
            if rule is None:
                buf += ch
                continue
            if buf:
                piece = dict(seg)
                piece["text"] = buf
                out.append(piece)
                buf = ""
            marked = dict(seg)
            marked["text"] = rule["to"]
            # 与既有 19 个公式对象同一套标记,不另起一套命名
            marked["run_type"] = ("chemical_superscript" if rule.get("form") == "superscript"
                                  else "chemical_subscript")
            marked["literalSource"] = ch
            out.append(marked)
            changed += 1
        if buf:
            piece = dict(seg)
            piece["text"] = buf
            out.append(piece)
    return out, changed


def walk(node, rules, stats):
    if isinstance(node, dict):
        segs = node.get("segments")
        if isinstance(segs, list) and any(isinstance(s, dict) and s.get("text") for s in segs):
            new, n = split_segments(segs, rules)
            if n:
                node["segments"] = new
                stats["converted"] += n
                stats["blocks"] += 1
        for value in node.values():
            walk(value, rules, stats)
    elif isinstance(node, list):
        for value in node:
            walk(value, rules, stats)


def main() -> int:
    rules = load_rules()
    blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
    stats = {"converted": 0, "blocks": 0}
    if rules:
        walk(blueprint, rules, stats)
        BLUEPRINT.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    report = {"step": "substitute-literal-supersub",
              "rules": len(rules), **stats,
              "note": "上标不是换一个字符,是把一个段拆成三段并给中间那段加 run_type。"
                      "硬塞进字符替换机制只会丢掉上标——那不是修,是把缺陷改小。"}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
