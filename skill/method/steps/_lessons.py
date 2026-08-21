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

DEFAULT_SPECS = [{
    'class': '讲',
    'pattern': r'^第(\d{1,2})讲\s*(\S.*)$',
    'numberGroup': 1,
    'titleGroup': 2,
    'labelPrefix': 'A',
}]
"""文档边界的**缺省**判据。缺省值必须显式:不写出来,下一个人就以为「讲」是唯一的
文档类,而事实是同一份教师版里既有 20 个「第NN讲」,也有 5 份「第N章 … 单元自测」。
真源是模板表的 documentBoundary;这里只在模板表没声明时兜底,并保持旧册可跑。"""

_FIELD_TOKENS = ('PAGEREF', 'HYPERLINK', '<w:fldChar', 'w:instrText')


def compile_specs(specs=None):
    """模板表的 documentBoundary → [(class, regex, numberGroup, titleGroup, labelPrefix)]。

    ★空清单一律拒绝,不退回缺省。「模板表声明了但是空的」与「没声明」是两件事:
    前者是写表的人漏了,拿缺省接住会让一册悄悄按别的文档类切开。
    """
    if specs is not None and not specs:
        raise ValueError('documentBoundary 声明为空清单。'
                         '拒绝用缺省判据顶替——那会把一册按别的文档类切开而不报错。')
    out = []
    for spec in (specs or DEFAULT_SPECS):
        out.append((
            spec.get('class') or '文档',
            re.compile(spec['pattern']),
            int(spec.get('numberGroup', 1)),
            int(spec.get('titleGroup', 2)),
            spec.get('labelPrefix', 'A'),
        ))
    return out


def is_field_xml(paragraph_xml: str) -> bool:
    """段落原始 XML 里是否有域代码。目录项就是域。"""
    return any(token in paragraph_xml for token in _FIELD_TOKENS)


def pick_headings(items, specs=None):
    """items: 可迭代的 (index, text, is_field)。返回 [(index, number, title, cls)]。

    title 是**原文**,不做规范化——它会进档名与登记,改了就是悄悄改产物。
    去空白只用于判重(「弹力 重力」与「弹力　重力」是同一条),不写回返回值。

    需要看「下一段」,所以先物化成列表:目录行只有跟它后面那一段比较才认得出来。

    ★2.11.0:边界在**所有已声明文档类的并集**上识别,返回值带类名。
    只认自己那一类会让跨度算错——教师版里第04讲之后紧跟一份单元自测,
    若只认「讲」,第04讲的跨度会一路吞掉整份单元自测。
    """
    compiled = compile_specs(specs)
    rows = [(index, (text or '').strip(), bool(field)) for index, text, field in items]
    nonempty = [k for k, (_, text, _) in enumerate(rows) if text]

    def match_of(k):
        """第 k 行若是任一类的文档标题,返回 (cls, match);否则 None。"""
        _, text, field = rows[k]
        if field:
            return None
        for cls, rx, _ng, _tg, _lp in compiled:
            m = rx.match(text)
            if m:
                return cls, m
        return None

    def head_key(k):
        hit = match_of(k)
        if hit is None:
            return None
        cls, m = hit
        _c, _rx, _ng, tg, _lp = next(x for x in compiled if x[0] == cls)
        return re.sub(r'\s+', '', m.group(tg))

    out: list[tuple[int, int, str, str]] = []
    previous_key = None
    for position, k in enumerate(nonempty):
        key = head_key(k)
        if key is None:
            continue
        following = nonempty[position + 1] if position + 1 < len(nonempty) else None
        if following is not None:
            next_key = head_key(following)
            if next_key is not None and next_key != key:
                continue          # 手打目录的一行:紧跟着另一条**不同名**的文档标题
            # next_key == key:后一条是重复行,留当前这条
        if key == previous_key:
            continue
        previous_key = key
        cls, m = match_of(k)
        _c, _rx, ng, tg, _lp = next(x for x in compiled if x[0] == cls)
        out.append((rows[k][0], int(m.group(ng)), m.group(tg).strip(), cls))
    return out


def spans(bounds, total, only_class=None):
    """边界表 → 跨度表。跨度**止于下一个任意类的边界**,不是下一个同类边界。

    bounds: pick_headings 的返回值(文档顺序)。total: 子节点总数。
    only_class: 要哪几类的跨度。可以是一个类名、一组类名,或 None(全要)。
        过滤发生在算完跨度之后——先过滤再算跨度正是上面那条注释说的错法。

    ★支持多类是因为「一份源里就是多类文档」这件事真实存在:2026 物理教师版一份里
    既有 20 讲,也有 5 份单元自测。想把它当**单一源**做原子化(不再需要学生版原卷
    去凑结构),就必须一次切出两类。只支持单类时,只能建两个册各切一半,
    而两个册就要两份绑定、两次跑、两处可能不同步。

    返回 [(number, title, cls, start, end)]。
    """
    if only_class is None:
        wanted = None
    elif isinstance(only_class, str):
        wanted = {only_class}
    else:
        wanted = set(only_class)
        if not wanted:
            raise ValueError('documentBoundary.select 是空清单。'
                             '拒绝切出 0 档——要全要就写 null,不要写 []。')
    out = []
    for position, (start, number, title, cls) in enumerate(bounds):
        end = bounds[position + 1][0] if position + 1 < len(bounds) else total
        if wanted is None or cls in wanted:
            out.append((number, title, cls, start, end))
    return out


def check_monotonic(headings) -> list[str]:
    """讲号应严格递增。返回问题清单(空 = 无问题)。

    只报不改:源里真出现乱序时,让人看见,不要让引擎替它决定。
    """
    problems = []
    numbers = [h[1] for h in headings]
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
