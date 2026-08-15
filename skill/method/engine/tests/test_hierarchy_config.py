#!/usr/bin/env python3
"""层级词表出代码进 schema 之后,证明它真的通电。

**为什么必须是单测,不能靠端到端。** 去重要两个条件同时成立:角色能映射到某一层,
且该文档的 tree 里有那一层。物理册两个都不成立(角色只有「讲次标题」,tree 恒空),
所以在物理册上把 titleRoleLevels 改成任何值,产物都一个字节不变——端到端跑法
自证不出来。这正是登记册 P8「判据恒假」的形状:改了没反应,和"本来就没有重复
标题"长得一模一样。

于是分两层证:
  1. 装载层——configure_hierarchy 读没读到、"没声明"与"声明为空"分不分得开
  2. 使用层——repeats/tree_of 读的是不是 HIERARCHY,而不是残留的字面量

运行:  python3.12 test_hierarchy_config.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "build_blueprint_from_atoms.py"
spec = importlib.util.spec_from_file_location("bbfa", ENGINE)
bbfa = importlib.util.module_from_spec(spec)
sys.modules["bbfa"] = bbfa
spec.loader.exec_module(bbfa)

# 移进包之前写死在函数体里的那两张表。默认值必须与它们逐字相同,
# 否则已付印的沪科册重跑结果会变——这一条是本次搬迁的回归红线。
PRE_MOVE_ROLE_LEVELS = {"专题标题": "topic", "课题标题": "subject"}
PRE_MOVE_PATH_PREFIXES = [("主题", "theme"), ("专题复习", "subject"), ("专题", "topic"),
                          ("课题", "subject"), ("跨学科", "subject")]

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


class FakeSchema:
    def __init__(self, raw):
        self.raw = raw


def reset() -> None:
    bbfa.HIERARCHY["titleRoleLevels"] = dict(PRE_MOVE_ROLE_LEVELS)
    bbfa.HIERARCHY["pathPrefixLevels"] = list(PRE_MOVE_PATH_PREFIXES)


print("=== 1 装载层 ===")

reset()
check("默认值与搬迁前的字面量逐字相同(沪科册回归红线)",
      bbfa.HIERARCHY["titleRoleLevels"] == PRE_MOVE_ROLE_LEVELS
      and bbfa.HIERARCHY["pathPrefixLevels"] == PRE_MOVE_PATH_PREFIXES)

reset()
bbfa.configure_hierarchy(FakeSchema({"roles": []}))
check("schema 没有 hierarchy 键 → 沿用默认(化学 schema 正是这种)",
      bbfa.HIERARCHY["titleRoleLevels"] == PRE_MOVE_ROLE_LEVELS,
      f"{len(bbfa.HIERARCHY['titleRoleLevels'])} 条")

reset()
bbfa.configure_hierarchy(FakeSchema({"hierarchy": {"titleRoleLevels": {},
                                                   "pathPrefixLevels": []}}))
check("schema 显式声明为空 → 真的清空(物理 schema 正是这种)",
      bbfa.HIERARCHY["titleRoleLevels"] == {}
      and bbfa.HIERARCHY["pathPrefixLevels"] == [],
      "「没声明」与「声明为空」必须分得开 —— P1 缺失≠零值")

reset()
bbfa.configure_hierarchy(FakeSchema({"hierarchy": {"titleRoleLevels": {"单元标题": "topic"}}}))
check("换一套词表 → 装得进去,且未声明的那半保持默认",
      bbfa.HIERARCHY["titleRoleLevels"] == {"单元标题": "topic"}
      and bbfa.HIERARCHY["pathPrefixLevels"] == PRE_MOVE_PATH_PREFIXES)

print("\n=== 2 使用层:tree_of ===")

reset()
doc = {"path": "/x/主题二 常见的物质/专题3 空气/课题1 空气的成分/a.docx"}
check("默认词表下,沪科目录仍解析出三层",
      bbfa.tree_of(doc) == {"theme": "主题二 常见的物质", "topic": "专题3 空气",
                            "subject": "课题1 空气的成分"},
      str(bbfa.tree_of(doc)))

bbfa.HIERARCHY["pathPrefixLevels"] = [("单元", "topic")]
check("换词表后,沪科目录不再被解析(证明读的是 HIERARCHY 不是字面量)",
      bbfa.tree_of(doc) == {}, str(bbfa.tree_of(doc)))

bbfa.HIERARCHY["pathPrefixLevels"] = [("单元", "topic")]
check("新词表能解析新教材的目录",
      bbfa.tree_of({"path": "/y/单元三 力/b.docx"}) == {"topic": "单元三 力"})

print("\n=== 3 使用层:repeats 去重 ===")

reset()
builder = bbfa.Builder({}, Path("/tmp"), {})
builder.source = {"path": "/x/a.docx", "sha256": "0" * 64}
builder.tree = {"topic": "专题3 空气"}
builder.blocks = []
excluded: list[str] = []
builder.exclude = lambda locator, why, kind: excluded.append(why)
builder.stream_segments = lambda block, keep: None

blk = {"id": "b1", "locator": {"kind": "paragraph", "value": "p[1]"}, "segments": []}
first = builder.repeats(blk, "专题标题")
second = builder.repeats(blk, "专题标题")
check("同一层标题:第一次排出,第二次判为重复",
      first is False and second is True,
      f"first={first} second={second} 排除理由={excluded}")

builder.tree = {"topic": "专题3 空气"}
builder.open_topic = None
bbfa.HIERARCHY["titleRoleLevels"] = {}
check("清空词表后,同一个角色不再被识别为层(证明读的是 HIERARCHY)",
      builder.repeats(blk, "专题标题") is False)

reset()
builder.open_topic = None
check("物理册的角色在默认词表下本就不匹配 —— 这是结构性不可达,不是配置错",
      builder.repeats(blk, "讲次标题") is False)

print()
if FAILED:
    print(f"**{len(FAILED)} 项未通过**: {FAILED}")
    raise SystemExit(1)
print("全部通过:层级词表已出代码进 schema,装载与使用两层都自证过。")
