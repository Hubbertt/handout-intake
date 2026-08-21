#!/usr/bin/env python3
"""非内容事实普查:源里到底存在哪些版式事实,原子化又记住了哪几样。

**为什么要有它。** PM 2026-08-20:「具体的非内容信息有很多,我没办法直接全部告诉你,
但你可以做的时候不断汇总整理,完善规范,并且记录到包内,做到原子化后能根据两类信息
完整地还原出源文件。」

判准是**还原性**:拿原子化的产物,能不能重建出源文件。
这条判准自带一个好处——**不必猜「还有哪些非内容信息」,让还原去告诉你**:
往返一圈丢了什么,什么就是没记。

本步做的是还原性的第一半:**把源里实际出现过的版式事实数出来**,
再逐项标注「原子化记了没有」。没记的进登记册,成为下一轮要补的清单。

不猜、不列理论上可能有的字段——**只登记这一份源里真实出现过的**,并带出现次数。
一个从未出现的字段进了登记册,就是给自己造一条恒假的待办。
"""
import json
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from _bootstrap import chain_from_argv  # noqa: E402

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
CHAIN = chain_from_argv(__doc__)
REPORT = CHAIN.path_for('noncontent-survey')

# 原子化目前记住了什么。键是本普查的事实名,值是「记在哪」——
# 空字符串表示**没记**。这张表是人维护的:工具能数出源里有什么,
# 但「我们记了没有」只能由写实现的人如实填。
# ★不再手维护「记了没有」。手维护的表必然与实现漂移,而且漂了没人知道。
# 改为**读非内容层的实际产物**(s4c5-capture-layout 的 layout.json)自动核:
# 一个事实出现在 layout.factKinds 里,就是记住了;没出现,就是没记。
# 这样"记了没有"变成可验证的事实,不再是一句声明。



def survey(path):
    root = ET.fromstring(zipfile.ZipFile(path).read('word/document.xml'))
    found = Counter()
    for p in root.iter(W + 'p'):
        pr = p.find(W + 'pPr')
        if pr is not None:
            for child in pr:
                name = child.tag.split('}')[-1]
                if name != 'rPr':
                    found[f'pPr/{name}'] += 1
        for run in p.iter(W + 'r'):
            rpr = run.find(W + 'rPr')
            if rpr is not None:
                for child in rpr:
                    found[f'rPr/{child.tag.split("}")[-1]}'] += 1
    for el in root.iter():
        name = el.tag.split('}')[-1]
        if name in ('tbl', 'tcPr', 'gridCol', 'pict', 'br', 'tab', 'oMath'):
            found[name] += 1
        elif name in ('inline', 'anchor'):
            found[f'drawing/{name}'] += 1
        elif name.startswith('wrap'):
            found[name] += 1
        elif name in ('pgSz', 'pgMar', 'cols'):
            found[f'sectPr/{name}'] += 1
    return found


def recorded_kinds():
    """非内容层实际记住了哪些事实。没跑过 s4c5 就返回空——那本身就是要报的事。"""
    found = CHAIN.resolve('layout')
    if not found:
        return None
    return set((json.loads(found[0].read_text(encoding='utf-8')).get('factKinds') or {}))


def main():
    recorded = recorded_kinds()
    # ★扫**本册实际切出的档**,不扫整份源。
    # 教师版一份含 20 讲 + 5 卷,而一册只取其中一类;拿整份源去比本册的版式层,
    # 只在另一类里出现的事实会被判成「未记」——2026-08-20 实测:单元卷册因此报 32/47,
    # 而那 15 类(pBdr / autoSpaceDE / topLinePunct …)在 5 卷里本来就一次都没出现。
    # **口径不一致造出来的假缺口**,和漏记长得一模一样。
    sources = CHAIN.resolve('lessons') or CHAIN.resolve('source.stripped') or CHAIN.resolve('source')
    if not sources:
        raise SystemExit('没有可扫的源。')
    found = Counter()
    for path in sources:
        found.update(survey(path))

    if recorded is None:
        raise SystemExit('还没有非内容层产物(layout)。先跑 s4c5-capture-layout——'
                         '没有它就无从核对「记了没有」,而凭空声明一份「已记清单」'
                         '正是本步要消灭的东西。')
    rows = []
    for fact, hits in found.most_common():
        ok = fact in recorded
        rows.append({'fact': fact, 'hits': hits, 'recorded': ok,
                     'recordedAt': 'layout.factKinds' if ok else None,
                     'status': '已记' if ok else '★未记'})
    missing = [r for r in rows if not r['recorded']]
    unknown = []

    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.noncontent-survey.v1',
        'what': ('源里实际出现过的版式事实,以及原子化记了哪几样。'
                 '判准是**还原性**:能不能据两类信息重建源文件。'),
        'rule': ('只登记这一份源里真实出现过的事实,不列理论上可能有的字段——'
                 '一个从未出现的字段进了登记册,就是给自己造一条恒假的待办。'),
        'sources': [str(p) for p in sources],
        'scopeNote': '扫本册实际切出的档,与非内容层同口径;不扫整份源。',
        'totals': {'facts': len(rows), 'recorded': len(rows) - len(missing),
                   'missing': len(missing), 'unregistered': len(unknown)},
        'facts': rows,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'非内容事实普查: 源里出现 {len(rows)} 类 | 已记 {len(rows)-len(missing)} | '
          f'★未记 {len(missing)} | ★登记表里都没有 {len(unknown)}')
    for r in rows[:24]:
        mark = '  ' if r['recorded'] else '★ '
        print(f'   {mark}{r["fact"]:22} {r["hits"]:6}  {r["recordedAt"] or ""}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
