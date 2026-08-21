#!/usr/bin/env python3
"""GATE_SHAPE_PREDICATE_HAS_EVIDENCE:每条形状判据都要有证据。

**为什么。** 2026-08-20 一天里同一类事故出了 7 次:判据写成「长什么样」,而在那个位置
长得一样的东西不止一种。命中之后没有任何报错——产物看起来完整,错误藏在内容里。

  · 选项行判据吃掉了小问里的行内选填,**整条小问被拆成两个假选项而销毁**
  · 合并答案判据把小数当题号,519 条【答案】里走那条分支的 8 条**全是误判**
  · 编号项判据把详解续行「0.04s×3=0.12s故B符合题意」当成新题,**凭空多出一道题,内容是别人的答案**
  · 「Ported as modules」匹配到正文里的「imported as modules」,处置建议整个反了
  · 多选升格把跨小问的答案字母去重成 2,21 道里 13 道误判

**「能认出来」与「认对了」是两件事,而中间没有报错。**

本门要求:每条 roles[].pattern / tags[].pattern 带 `evidence`。分工是——

  门自己量:  命中数(全量扫源),并自动抓「判据恒假」(0 命中)
  必须声明:  同形态**还可能是什么**、**靠什么分辨** —— 这是机器判不了的

写不出「还可能是什么」有两种情况:一是这个形状在此处确实唯一(声明 shapeIsUnique 并给理由),
二是**还没想过** —— 后者正是本门要拦的。

★放宽或收紧一条判据时,必须在 evidence.redGreen 里留下同一份样本上的红→绿。
不要求门去复跑(它没有历史),但要求写下来:改了什么、改前红在哪、改后绿成什么样。
没有这一段的改动,等于没验过。
"""
import json
import re
import sys
import zipfile
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SCHEMA = json.loads(Path(str(CHAIN.only('schema'))).read_text(encoding='utf-8'))
REPORT = CHAIN.path_for('gate.shape-predicates')
T = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)


def paragraphs_of(path):
    xml = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8')
    for chunk in re.findall(r'<w:p[ >].*?</w:p>', xml, re.S):
        text = ''.join(T.findall(chunk)).strip()
        if text:
            yield text


def main():
    sources = CHAIN.resolve('source.stripped') or CHAIN.resolve('source')
    if not sources:
        raise SystemExit('没有可扫的源。拒绝在 0 个样本上判「命中数」——'
                         '那样每条判据都会被判成恒假,而那不是事实。')
    paras = [t for path in sources for t in paragraphs_of(path)]

    option_chars = re.escape((SCHEMA.get('optionMarkers') or {}).get('chars', 'ABCDEFGH'))
    checked, problems = [], []

    def examine(kind, entry, pattern):
        pid = entry.get('id', '?')
        try:
            rx = re.compile(pattern.replace('{optionChars}', option_chars))
        except re.error as exc:
            problems.append({'kind': kind, 'id': pid, 'code': 'BAD_PATTERN',
                             'why': f'正则无法编译:{exc}'})
            return
        hits = sum(1 for t in paras if rx.search(t))
        ev = entry.get('evidence') or {}
        row = {'kind': kind, 'id': pid, 'pattern': pattern, 'measuredHits': hits,
               'declared': bool(ev)}

        if not ev:
            problems.append({'kind': kind, 'id': pid, 'code': 'NO_EVIDENCE', 'hits': hits,
                             'why': ('没有 evidence。必须声明:同形态还可能是什么(alsoMatchesShape)'
                                     '与靠什么分辨(howDistinguished);若此处形状确实唯一,'
                                     '声明 shapeIsUnique:true 并给 why。'
                                     '写不出来通常不是「唯一」,是「还没想过」。')})
        else:
            also = ev.get('alsoMatchesShape') or []
            if also and not ev.get('howDistinguished'):
                problems.append({'kind': kind, 'id': pid, 'code': 'NO_DISCRIMINATOR',
                                 'why': f'声明了同形态 {also!r},却没写靠什么分辨。'})
            if not also and not ev.get('shapeIsUnique'):
                problems.append({'kind': kind, 'id': pid, 'code': 'NO_ALTERNATIVES',
                                 'why': '既没列出同形态的其它可能,也没声明 shapeIsUnique。'})
            if ev.get('shapeIsUnique') and not ev.get('why'):
                problems.append({'kind': kind, 'id': pid, 'code': 'UNIQUE_WITHOUT_REASON',
                                 'why': 'shapeIsUnique 是断言,必须给理由。'})
            if ev.get('tightenedAt') and not ev.get('redGreen'):
                problems.append({'kind': kind, 'id': pid, 'code': 'NO_RED_GREEN',
                                 'why': f'声明在 {ev["tightenedAt"]} 改过判据,却没有 redGreen 记录。'
                                        '改判据不留红→绿,等于没验过。'})
            row['alsoMatchesShape'] = also
            row['shapeIsUnique'] = bool(ev.get('shapeIsUnique'))

        # 命中数由门自己量,顺带抓判据恒假
        if hits == 0 and not ev.get('expectedZero'):
            problems.append({'kind': kind, 'id': pid, 'code': 'NEVER_MATCHES', 'hits': 0,
                             'why': ('本源里 0 命中。判据恒假:它永远不会说话,'
                                     '报告里与「确实没问题」一模一样。'
                                     '确实该为 0 的(如声明了但源里没用到)请声明 '
                                     'expectedZero:true 并给 why。')})
        if ev.get('expectedZero') and hits:
            problems.append({'kind': kind, 'id': pid, 'code': 'EXPECTED_ZERO_BUT_HIT',
                             'hits': hits,
                             'why': '声明了 expectedZero,实测却有命中。声明与事实不符。'})
        checked.append(row)

    for role in SCHEMA.get('roles') or []:
        if role.get('pattern'):
            examine('role', role, role['pattern'])
    for tag in SCHEMA.get('tags') or []:
        if tag.get('pattern'):
            examine('tag', tag, tag['pattern'])

    status = 'pass' if not problems else 'fail'
    codes = {}
    for p in problems:
        codes[p['code']] = codes.get(p['code'], 0) + 1
    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-shape-predicates.v1',
        'gate': 'GATE_SHAPE_PREDICATE_HAS_EVIDENCE',
        'rule': ('每条形状判据必须带 evidence:同形态还可能是什么、靠什么分辨;'
                 '命中数由门自己量并自动抓判据恒假;改过判据的必须留红→绿。'),
        'status': status,
        'sources': [str(p) for p in sources],
        'paragraphsScanned': len(paras),
        'totals': {'predicates': len(checked), 'problems': len(problems), **codes},
        'predicates': checked,
        'problems': problems,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'GATE_SHAPE_PREDICATE_HAS_EVIDENCE: {status}  '
          f'({len(checked)} 条判据 / 扫了 {len(paras)} 段)')
    for code, n in sorted(codes.items()):
        print(f'   {code:24} {n}')
    for p in problems[:14]:
        print(f'     · [{p["code"]}] {p["kind"]} {p["id"]}')
    if problems:
        raise SystemExit(1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
