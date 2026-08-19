#!/usr/bin/env python3
"""讲边界识别:一处真源。

首版这条规则同时写在 fingerprint.py / census.py / split_and_normalise.py 三个
步骤里,每份都是 `pStyle == '3'` 加一个写死的 `WANT = {10..14}`。两个都是**某一份
源文件的事实被写进了产品**:

  - `pStyle == '3'` 只对 2026 物理学生版母本成立。同一套教材的教师版正文讲标题
    **根本没有 pStyle**(目录行是 17、正文是无样式),旧规则在它上面命中 0 个,
    于是 fingerprint 拿 0 作分母,抛 ZeroDivisionError —— 报错报在除法上,
    而真正的问题在两百行之前。
  - `WANT = {10..14}` 是某一册的范围。第二册要做别的讲就得改代码,
    而「改代码换一册」正是 _bootstrap 的 docstring 里说要消灭的东西。
    路径那一层治好了,范围和样式 id 这一层没治。

现在的判据不依赖样式,只依赖结构。每一条都是被某一份源打脸之后才加上的:

  ① 段落文本形如「第NN讲 <标题>」
  ② 段内不含域代码(PAGEREF / HYPERLINK / fldChar / instrText)—— 排除**域生成的**目录
  ③ 下一非空段也是讲标题、且**标题不同** —— 当前这条是**手打目录**的一行,跳过
  ④ 下一非空段也是讲标题、且**标题相同** —— 后一条是重复行,留当前这条
  ⑤ 与已收的前一条同名者跳过(兜底)

②③ 都是「排除目录」,但排的是两种目录:新学生合并本的目录是**手打的纯文本**
(`第02讲  长度的测量19`,页码直接打在标题后面),没有任何域代码,② 一个都滤不掉,
于是 20 个目录行 + 20 个正文标题 = 40。③ 才接得住它。

④ 的存在是因为 ③ 会误伤:学生版母本在 `第18讲 摩擦力` 之后紧跟一个无样式的
`第19讲  摩擦力`(**重复且带错号**),两条相邻,③ 会把真的那条当目录扔掉。
目录里相邻两行必然不同名,重复行必然同名——这就是 ③④ 的分界。
旧的 pStyle 规则是**碰巧**把重复行滤掉的,碰巧不是判据。

实测(三份形态各不同的源,全对):
  新教师版 20/20 · 新学生合并本 20/20 · 旧学生版母本 19/19,讲号均连号递增。
  旧规则(pStyle=='3')在新教师版上是 0。
"""
from __future__ import annotations

import re

LESSON_RE = re.compile(r'^第(\d{1,2})讲\s*(\S.*)$')
_FIELD_TOKENS = ('PAGEREF', 'HYPERLINK', '<w:fldChar', 'w:instrText')


def is_field_xml(paragraph_xml: str) -> bool:
    """段落原始 XML 里是否有域代码。目录项就是域。"""
    return any(token in paragraph_xml for token in _FIELD_TOKENS)


def pick_headings(items):
    """items: 可迭代的 (index, text, is_field)。返回 [(index, number, title)]。

    title 是**原文**,不做规范化——它会进档名与登记,改了就是悄悄改产物。
    去空白只用于判重(「弹力 重力」与「弹力　重力」是同一条),不写回返回值。

    需要看「下一段」,所以先物化成列表:目录行只有跟它后面那一段比较才认得出来。
    """
    rows = [(index, (text or '').strip(), bool(field)) for index, text, field in items]
    nonempty = [k for k, (_, text, _) in enumerate(rows) if text]

    def head_key(k):
        """第 k 行若是讲标题,返回去空白的标题;否则 None。"""
        _, text, field = rows[k]
        if field:
            return None
        match = LESSON_RE.match(text)
        return re.sub(r'\s+', '', match.group(2)) if match else None

    out: list[tuple[int, int, str]] = []
    previous_key = None
    for position, k in enumerate(nonempty):
        key = head_key(k)
        if key is None:
            continue
        following = nonempty[position + 1] if position + 1 < len(nonempty) else None
        if following is not None:
            next_key = head_key(following)
            if next_key is not None and next_key != key:
                continue          # 手打目录的一行:紧跟着另一条**不同名**的讲标题
            # next_key == key:后一条是重复行,留当前这条
        if key == previous_key:
            continue
        previous_key = key
        match = LESSON_RE.match(rows[k][1])
        out.append((rows[k][0], int(match.group(1)), match.group(2).strip()))
    return out


def check_monotonic(headings) -> list[str]:
    """讲号应严格递增。返回问题清单(空 = 无问题)。

    只报不改:源里真出现乱序时,让人看见,不要让引擎替它决定。
    """
    problems = []
    numbers = [n for _, n, _ in headings]
    for previous, current in zip(numbers, numbers[1:]):
        if current <= previous:
            problems.append(f'讲号未递增:第{previous:02d}讲 之后出现 第{current:02d}讲')
    return problems


def select_scope(headings, wanted):
    """按册的范围筛讲。wanted 为 None 表示源里有几讲就做几讲。

    返回 (选中的 headings, 缺失的讲号)。缺讲不静默:调用方须处理。
    """
    if wanted is None:
        return list(headings), []
    want = set(wanted)
    chosen = [h for h in headings if h[1] in want]
    missing = sorted(want - {h[1] for h in chosen})
    return chosen, missing
