#!/usr/bin/env python3
"""GATE_VOLUME_METADATA:册级元数据必须自洽。

有的文档在正文之外还写着一层**关于这份文档自己**的数字:试卷的时长、满分、
各节分值。它们不是题目内容,却是内容的一部分——而且彼此之间有恒等式:

    各节分值之和 == 满分

源自己把这个恒等式写了两遍(卷首「满分 70分」、节标题「（共38分）」「（共32分）」),
两遍就有对不上的可能。对不上时,不该由下游去挑哪个数对。

**为什么要有这道门。** 2026-08-20 建单元卷册时,这两个数是我逐份读出来核对的,
5/5 自洽——但那是**人核的**,没有留下判据。人核过一次的东西,下一批换个人就没了。
真源图里当时写的是「待加的门」;写着待加而不加,和没发现是一回事。

判据由模板表 volumeMetadata 给,不写死在这里:讲义册根本没有这一层
(它没有满分、没有分值),对它这道门是 not-applicable ——
**显式报「本册无此层」,不是静默跳过**。两者在报告里长得不一样,这是重点。
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
STRUCTURE = CHAIN.path_for('structure')
SCHEMA = json.loads(Path(str(CHAIN.only('schema'))).read_text(encoding='utf-8'))
REPORT = CHAIN.path_for('gate.volume-metadata')


def main():
    spec = SCHEMA.get('volumeMetadata')
    blocks = json.loads(STRUCTURE.read_text(encoding='utf-8'))

    if not spec:
        REPORT.write_text(json.dumps({
            'schemaVersion': 'chengziclass.gate-volume-metadata.v1',
            'gate': 'GATE_VOLUME_METADATA',
            'status': 'not-applicable',
            'why': ('模板表没有声明 volumeMetadata。本册的文档不带册级数字'
                    '(时长/满分/节分值)——讲义就是这样。'),
            '★notSilent': ('not-applicable 与 pass 是两种结果,报告里分开写。'
                           '一道门长期报 pass 而其实从未判过任何东西,那是判据恒假;'
                           '报 not-applicable 才能让人看出「这里本来就没有可判的」。'),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print('GATE_VOLUME_METADATA: not-applicable(模板表未声明 volumeMetadata)')
        return 0

    total_spec = spec.get('totalMarks') or {}
    section_spec = spec.get('sectionMarks') or {}
    if not total_spec or not section_spec:
        raise SystemExit('volumeMetadata 声明了却缺 totalMarks 或 sectionMarks。'
                         '拒绝只判一半——半个恒等式判不出不等。')

    total_re = re.compile(total_spec['pattern'])
    section_re = re.compile(section_spec['pattern'])
    duration_re = (re.compile(spec['duration']['pattern'])
                   if spec.get('duration') else None)

    totals: dict[str, int] = {}
    durations: dict[str, int] = {}
    sections: dict[str, list] = defaultdict(list)
    for block in blocks:
        doc = block.get('document')
        text = block.get('text') or ''
        role = block.get('role')
        if role == total_spec.get('role'):
            found = total_re.search(text)
            if found:
                totals[doc] = int(found.group(1))
            if duration_re:
                hit = duration_re.search(text)
                if hit:
                    durations[doc] = int(hit.group(1))
        if role == section_spec.get('role'):
            found = section_re.search(text)
            if found:
                sections[doc].append({'title': text.strip(),
                                      'marks': int(found.group(1)),
                                      'locator': block.get('locator')})

    documents = sorted(set(list(totals) + list(sections)))
    rows, failures = [], []
    for doc in documents:
        total = totals.get(doc)
        parts = sections.get(doc, [])
        summed = sum(p['marks'] for p in parts)
        row = {'document': doc, 'totalMarks': total,
               'durationMinutes': durations.get(doc),
               'sectionMarks': [(p['title'], p['marks']) for p in parts],
               'sum': summed}
        if total is None:
            row['verdict'] = 'no-total'
            failures.append({'document': doc,
                             'why': f'找不到满分(判据 {total_spec["pattern"]!r} 在 '
                                    f'role={total_spec.get("role")!r} 上零命中)'})
        elif not parts:
            row['verdict'] = 'no-sections'
            failures.append({'document': doc, 'why': '找不到任何带分值的节标题'})
        elif summed != total:
            row['verdict'] = 'MISMATCH'
            failures.append({'document': doc,
                             'why': f'各节分值之和 {summed} ≠ 满分 {total};'
                                    f'差 {summed - total:+d}。源里这两个数对不上,'
                                    '不猜哪个对。',
                             'sections': row['sectionMarks']})
        else:
            row['verdict'] = 'ok'
        rows.append(row)

    if not documents:
        failures.append({'why': ('模板表声明了 volumeMetadata,但一份文档都没量到——'
                                 '判据恒假。声明了就必须命中,否则这道门等于不存在。')})

    status = 'pass' if not failures else 'fail'
    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-volume-metadata.v1',
        'gate': 'GATE_VOLUME_METADATA',
        'status': status,
        'rule': spec.get('invariant', '各节分值之和 == 满分'),
        'spec': spec,
        'documents': rows,
        'failures': failures,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'GATE_VOLUME_METADATA: {status}  ({len(documents)} 份文档)')
    for row in rows:
        marks = ' + '.join(str(m) for _, m in row['sectionMarks']) or '—'
        print(f"   {row['document']}  {marks} = {row['sum']}  "
              f"vs 满分 {row['totalMarks']}  [{row['verdict']}]")
    if failures:
        print('  失败:', json.dumps(failures, ensure_ascii=False)[:400])
        raise SystemExit(1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
