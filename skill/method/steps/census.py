#!/usr/bin/env python3
"""块型普查(第1步):全册 19 讲。模板表是模板级,所以普查覆盖全册,裁决只做 10-14。

产出建表候选:每个候选形态给出全册命中数 + 10-14 讲命中数 + 样本。
命中数是升格判据之一(1->1 不升格),所以必须先量再写规则。
"""
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SRC = str(CHAIN.only('source'))
OUT = str(CHAIN.path_for('census'))
WANT = {10, 11, 12, 13, 14}
T = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)


def clean(s):
    """去掉零宽/控制字符再判形。源里有 115 处 U+200C 会打断行首判据。"""
    return ''.join(c for c in s if unicodedata.category(c) not in ('Cf', 'Cc'))


def main():
    x = zipfile.ZipFile(SRC).read('word/document.xml').decode('utf-8')
    paras = re.findall(r'<w:p[ >].*?</w:p>', x, re.S)

    def text(p):
        return ''.join(T.findall(p))

    def style(p):
        m = re.search(r'w:pStyle w:val="([^"]+)"', p)
        return m.group(1) if m else '-'

    heads = []
    for i, p in enumerate(paras):
        if style(p) != '3':
            continue
        m = re.match(r'第(\d{2})讲\s*(\S.*)', clean(text(p)).strip())
        if m:
            heads.append((i, int(m.group(1)), m.group(2)))

    chapter_of = {}
    for k, (st, num, _) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
        for i in range(st, end):
            chapter_of[i] = num

    # 候选角色:形态 -> 正则。顺序即 sortOrder,先具体后一般。
    candidates = [
        ('讲标题',        r'^第\d{2}讲\s*\S'),
        ('栏目横幅',      r'^[概深过][｜|]?[念研关]'),
        ('知识点标题',    r'^知识点\s*\d+'),
        ('例题题干',      r'^【例\s*\d*】'),
        ('即练题干',      r'^【即练\s*\d*】'),
        ('探究归纳',      r'^【探究归纳】'),
        ('其他方括号标记', r'^【[^】]{1,12}】'),
        ('题型小标题',    r'^[一二三四五六七八九十]+[、．.]\s*(单选题|多选题|填空题|实验题|简答题|计算题|作图题|综合题|选择题)'),
        ('中文编号标题',  r'^[一二三四五六七八九十]+[、．.]\s*\S'),
        ('编号项',        r'^\d{1,2}\s*[．.、]\s*\S'),
        ('小问',          r'^[（(]\s*\d+\s*[)）]'),
        ('圈号项',        r'^[①-⑳]'),
        ('选项行',        r'[ABCDEFGH][．.]\s*\S'),
    ]
    compiled = [(n, re.compile(p)) for n, p in candidates]

    total = Counter()
    scoped = Counter()
    samples = defaultdict(list)
    styles_of = defaultdict(Counter)
    body_total = body_scoped = 0
    body_samples = []

    for i, p in enumerate(paras):
        raw = text(p)
        t = clean(raw).strip()
        if not t:
            continue
        ch = chapter_of.get(i)
        for name, rx in compiled:
            if rx.search(t):
                total[name] += 1
                if ch in WANT:
                    scoped[name] += 1
                if len(samples[name]) < 4:
                    samples[name].append(t[:56])
                styles_of[name][style(p)] += 1
                break
        else:
            body_total += 1
            if ch in WANT:
                body_scoped += 1
                if len(body_samples) < 6:
                    body_samples.append(t[:60])

    print('=' * 72)
    print('块型普查 · 全册 19 讲(模板级) / 第10-14讲(本轮裁决范围)')
    print('=' * 72)
    print(f'{"候选角色":<16}{"全册":>7}{"10-14":>8}   主样式   升格判据2(命中数)')
    for name, _ in compiled:
        if not total[name]:
            continue
        st = styles_of[name].most_common(1)[0][0]
        verdict = 'OK' if total[name] >= 2 else '1->1 不升格'
        print(f'  {name:<14}{total[name]:>7}{scoped[name]:>8}   {st:<8} {verdict}')
    print(f'  {"正文(默认)":<14}{body_total:>7}{body_scoped:>8}')

    print()
    print('样本:')
    for name, _ in compiled:
        if total[name]:
            print(f'  【{name}】')
            for s in samples[name][:2]:
                print(f'      · {s}')
    print('  【正文(默认)】')
    for s in body_samples[:3]:
        print(f'      · {s}')

    payload = {
        'schemaVersion': 'handout-intake.census.v1',
        'source': SRC,
        'scope': {'chapters': sorted(WANT), 'templateCensusCoversWholeVolume': True},
        'chapters': [{'index': n, 'title': t, 'startParagraph': i} for i, n, t in heads],
        'candidates': [
            {
                'role': name,
                'pattern': pat,
                'hitsWholeVolume': total[name],
                'hitsInScope': scoped[name],
                'dominantStyle': styles_of[name].most_common(1)[0][0] if total[name] else None,
                'promotable': total[name] >= 2,
                'samples': samples[name],
            }
            for name, pat in candidates
        ],
        'bodyDefault': {'hitsWholeVolume': body_total, 'hitsInScope': body_scoped},
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f'\n写出: {OUT}')


if __name__ == '__main__':
    main()
