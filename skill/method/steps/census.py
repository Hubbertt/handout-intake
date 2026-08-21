#!/usr/bin/env python3
"""块型普查(第1步):全册。模板表是模板级,所以普查覆盖全册;裁决范围由册的 scope 给。

产出建表候选:每个候选形态给出全册命中数 + 10-14 讲命中数 + 样本。
命中数是升格判据之一(1->1 不升格),所以必须先量再写规则。
"""
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict

from _bootstrap import chain_from_argv  # noqa: E402
from _lessons import DEFAULT_SPECS as _L_DEFAULT  # noqa: E402
from _lessons import is_field_xml, pick_headings  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SOURCES = CHAIN.resolve('source')
if not SOURCES:
    raise SystemExit('source 一个都没解析到,拒绝在 0 个源上做普查。')
SRC = str(SOURCES[0])
# 单档时 SRC 就是它;多档(单元卷:源本来就是一卷一档)时逐档扫,计数累加。
OUT = str(CHAIN.path_for('census'))
# 裁决范围由 bindings.scope.lessons 给,None = 全册。首版这里写死 {10..14}。
_scope = CHAIN.scope_lessons()
WANT = None if _scope is None else set(_scope)
T = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)


def clean(s):
    """去掉零宽/控制字符再判形。源里有 115 处 U+200C 会打断行首判据。"""
    return ''.join(c for c in s if unicodedata.category(c) not in ('Cf', 'Cc'))


def _in_scope(ch):
    """WANT 为 None 时全册都算范围内。

    ★首版写作 `ch is not None and (WANT is None or ch in WANT)`:
    WANT 为 None(全册)时,凡是没归到某一讲的段落仍判为**不在范围**。
    2026-08-20 教师版册实测,讲边界一个都没认出来(见下),于是每一段的 ch 都是 None,
    整张普查表的「范围内」一列全是 0——而报告状态正常、链继续往下跑。
    「全册」的正确含义是不筛,不是「筛一个恒假的条件」。
    """
    if WANT is None:
        return True
    return ch is not None and ch in WANT


def text(p):
    return ''.join(T.findall(p))


def style(p):
    m = re.search(r'w:pStyle w:val="([^"]+)"', p)
    return m.group(1) if m else '-'


def main():

    # 文档边界:与 fingerprint / split 共用 _lessons 的同一条判据,
    # 判据本身由模板表给(模板表尚未建时用 _lessons 的缺省)。
    # ★首版这里独立写了 `style(p) != '3'` 加 `第(\d{2})讲`——
    # 那是**某一份源文件**(2026 物理学生版母本)的事实。同一套教材的新合并本
    # 讲标题根本没有 pStyle,实测命中 0 个,chapters 与 lessonsFound 都是空的。
    # 本文件顶上还写着 `from _lessons import pick_headings`,导入了却没用。
    _schema_files = CHAIN.resolve('schema')
    _schema = (json.loads(_schema_files[0].read_text(encoding='utf-8'))
               if _schema_files else {})
    _specs = (_schema.get('documentBoundary') or {}).get('specs')
    # 探针词表可由模板表追加。首版这一份词表全是讲义的形态,
    # 拿它去普查单元卷,「考试信息」「节标题(带分值)」这两种形态根本不在探针里——
    # 量不到的东西不会报错,只会不出现,而报告看起来完整。
    _extra = [(c['role'], c['pattern']) for c in (_schema.get('censusCandidates') or [])]

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
    # 模板表追加的探针排在**最前**:它们更具体(「一、学科基础（共38分）」若排在
    # 「中文编号标题」之后就永远轮不到)。首次命中即止,顺序就是优先级。
    candidates = _extra + candidates
    compiled = [(n, re.compile(p)) for n, p in candidates]

    total = Counter()
    scoped = Counter()
    samples = defaultdict(list)
    styles_of = defaultdict(Counter)
    body_total = body_scoped = 0
    body_samples = []
    all_heads = []
    per_source = []

    for src in SOURCES:
        x = zipfile.ZipFile(src).read('word/document.xml').decode('utf-8')
        paras = re.findall(r'<w:p[ >].*?</w:p>', x, re.S)
        heads = pick_headings(
            ((i, clean(text(p)).strip(), is_field_xml(p)) for i, p in enumerate(paras)),
            _specs)

        # 门:普查是建表的量具。量不出文档边界就出不了可信的「范围内」一列,
        # 而首版会照常写出一份每格都是 0 的报告。宁可拒绝,不可猜。
        if not heads:
            raise SystemExit(
                f'{src.name}:没认出任何文档边界(判据:模板表 documentBoundary.specs,'
                f'未声明时用缺省 {[d["pattern"] for d in _L_DEFAULT]})。'
                '拒绝出一份「范围内」全为 0 的普查表——那种表看起来正常,'
                '却会让后面每一条升格判断都建在空气上。')

        chapter_of = {}
        for k, head in enumerate(heads):
            st, num = head[0], head[1]
            end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
            for i in range(st, end):
                chapter_of[i] = num

        for i, p in enumerate(paras):
            t = clean(text(p)).strip()
            if not t:
                continue
            ch = chapter_of.get(i)
            for name, rx in compiled:
                if rx.search(t):
                    total[name] += 1
                    if _in_scope(ch):
                        scoped[name] += 1
                    if len(samples[name]) < 4:
                        samples[name].append(t[:56])
                    styles_of[name][style(p)] += 1
                    break
            else:
                body_total += 1
                if _in_scope(ch):
                    body_scoped += 1
                    if len(body_samples) < 6:
                        body_samples.append(t[:60])

        all_heads.extend(heads)
        per_source.append({'source': str(src), 'paragraphs': len(paras),
                           'documents': [{'index': h[1], 'title': h[2],
                                          'startParagraph': h[0], 'class': h[3]}
                                         for h in heads]})

    heads = all_heads
    print('=' * 72)
    _label = '全册' if WANT is None else f'第{min(WANT):02d}-{max(WANT):02d}讲'
    print(f'块型普查 · 全册 {len(heads)} 档(模板级) / {_label}(本轮裁决范围)')
    print('=' * 72)
    print(f'{"候选角色":<16}{"全册":>7}{"范围内":>8}   主样式   升格判据2(命中数)')
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
        'sources': [str(p) for p in SOURCES],
        'perSource': per_source,
        'scope': {'chapters': (None if WANT is None else sorted(WANT)),
                  'scopeSource': 'bindings.scope.lessons(None=全册)',
                  'lessonsFound': [h[1] for h in heads],
                  'templateCensusCoversWholeVolume': True},
        'chapters': [{'index': h[1], 'title': h[2], 'startParagraph': h[0],
                      'class': h[3]} for h in heads],
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
