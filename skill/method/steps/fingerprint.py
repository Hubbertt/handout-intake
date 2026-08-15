#!/usr/bin/env python3
"""指纹识别 + 块型普查:物理暑假讲义 第10-14讲 对照沪科版化学模板表。

只读,不写任何工作区文件。输出两件:
  1) 沪科表 20 个角色在物理源上的命中率(指纹)
  2) 没被任何角色接住的块的形态普查(建表候选)
"""
import json
import re
import sys
import zipfile
from collections import Counter, OrderedDict

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SRC = str(CHAIN.only('source'))
# 已知模板表:包内 seeds 全部,逐张比。首版只比了沪科一张,因为当时包里只有一张。
SEEDS = CHAIN.resolve('seeds.templates')
TPL = str(next((p for p in SEEDS if 'huke' in p.name), SEEDS[0]))
CHEM_SCHEMA = str(CHAIN.only('schema.reference'))
# ★ 判定必须落盘。首版这个脚本自称「只读,不写任何工作区文件」,于是「沪科表覆盖
# 62.7% → 不匹配 → 另建物理表」这个决定只存在于当时的终端输出里:没有产物、没有
# hash、事后无从复核。而整张物理模板表都建立在它之上。
# 「知识只活在一个不是真源的地方」正是这套方法要消灭的东西,它却先漏在了自己身上。
OUT = CHAIN.path_for('fingerprint')

T = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)
WANT = {10, 11, 12, 13, 14}


def paragraphs(path):
    x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8')
    return re.findall(r'<w:p[ >].*?</w:p>', x, re.S)


def ptext(p):
    return ''.join(T.findall(p)).strip()


def pstyle(p):
    m = re.search(r'w:pStyle w:val="([^"]+)"', p)
    return m.group(1) if m else '-'


def main():
    paras = paragraphs(SRC)
    heads = []
    for i, p in enumerate(paras):
        if pstyle(p) != '3':
            continue
        m = re.match(r'第(\d{2})讲\s*(\S.*)', ptext(p))
        if m:
            heads.append((i, int(m.group(1)), m.group(2)))

    # slice out chapters 10-14
    scope = []
    for k, (st, num, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
        if num in WANT:
            scope.extend(range(st, end))
    scope = sorted(set(scope))

    tpl = json.load(open(TPL))
    chem = json.load(open(CHEM_SCHEMA))
    optchars = chem['optionMarkers']['chars']

    roles = []
    for r in tpl['roles']:
        pat = r.get('pattern')
        if not pat:
            continue
        pat = pat.replace('{optionChars}', optchars)
        try:
            roles.append((r['id'], r.get('sortOrder', 999), re.compile(pat)))
        except re.error as exc:
            print(f'  [skip bad pattern] {r["id"]}: {exc}', file=sys.stderr)
    roles.sort(key=lambda t: t[1])

    hit = Counter()
    unmatched = []
    empty = 0
    for i in scope:
        txt = ptext(paras[i])
        if not txt:
            empty += 1
            continue
        for rid, _, rx in roles:
            if rx.search(txt):
                hit[rid] += 1
                break
        else:
            unmatched.append((i, txt))

    nonempty = len(scope) - empty
    print('=' * 66)
    print('指纹识别:沪科版化学模板表 → 物理暑假讲义 第10-14讲')
    print('=' * 66)
    print(f'范围内段落 {len(scope)}(空段 {empty},有文字 {nonempty})')
    print()
    print(f'{"沪科角色":<16}{"命中":>6}')
    for rid, _, _ in roles:
        if hit[rid]:
            print(f'  {rid:<14}{hit[rid]:>6}')
    dead = [rid for rid, _, _ in roles if not hit[rid]]
    matched = sum(hit.values())
    print()
    print(f'命中合计 {matched} / {nonempty}  = {matched / nonempty:.1%}')
    print(f'零命中角色 {len(dead)} 个: {"、".join(dead)}')
    print(f'未被接住 {len(unmatched)} 段 = {len(unmatched) / nonempty:.1%}')

    print()
    print('=' * 66)
    print('块型普查:未被接住的段落,按形态归类(建表候选)')
    print('=' * 66)

    shapes = OrderedDict([
        ('讲标题', r'^第\d{2}讲\s'),
        ('栏目横幅(全角竖线)', r'^[^\s]*｜'),
        ('知识点标题(无◆)', r'^知识点\s*\d'),
        ('例题', r'^【例\s*\d*】'),
        ('即练', r'^【即练\s*\d*】'),
        ('探究归纳', r'^【探究归纳】'),
        ('其他【】标记', r'^【[^】]{1,12}】'),
        ('过关检测栏目', r'^过关检测\s*$'),
        ('题型小标题', r'^[一二三四五六七八九十]+[、．.]\s*(单选题|填空题|实验题|简答题|计算题|作图题)'),
        ('中文编号标题', r'^[一二三四五六七八九十]+[、．.]\s*\S'),
        ('阿拉伯编号项', r'^\d{1,2}\s*[．.、]'),
        ('小问', r'^[（(]\s*\d+\s*[)）]'),
        ('圈号项', r'^[①-⑳]'),
        ('选项行', r'[ABCD][．.]\s*\S'),
        ('图注', r'^图\s*\d|^甲\s|^[甲乙丙丁]\s{2}'),
    ])
    compiled = [(k, re.compile(v)) for k, v in shapes.items()]

    bucket = Counter()
    samples = {}
    leftovers = []
    for i, txt in unmatched:
        for name, rx in compiled:
            if rx.search(txt):
                bucket[name] += 1
                samples.setdefault(name, []).append(txt[:52])
                break
        else:
            leftovers.append((i, txt))

    for name, _ in compiled:
        if bucket[name]:
            print(f'\n  【{name}】 {bucket[name]} 段')
            for s in samples[name][:3]:
                print(f'      · {s}')

    print(f'\n  【仍未归类】 {len(leftovers)} 段 = {len(leftovers) / nonempty:.1%} of 有文字段落')
    lens = Counter()
    for i, txt in leftovers:
        lens['长(>40字)' if len(txt) > 40 else '短(<=40字)'] += 1
    print(f'      长短分布: {dict(lens)}')
    for i, txt in leftovers[:8]:
        print(f'      · [{i}] {txt[:60]}')

    print()
    print('=' * 66)
    print('结论')
    print('=' * 66)
    verdict = '匹配' if matched / nonempty > 0.8 else '不匹配'
    print(f'  沪科表覆盖 {matched / nonempty:.1%} → 判定「{verdict}」')
    print('  按 SKILL.md「不像 → 走建表流程,绝不强行套最像的那个」')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'schemaVersion': 'handout-intake.fingerprint.v1',
        'source': SRC,
        'comparedAgainst': TPL,
        'threshold': 0.8,
        'thresholdBasis': '经验阈值,未做敏感性分析。判定落在 62.7% 与 80% 之间时'
                          '差距足够大,不靠阈值取舍;若将来某册落在 75-85% 区间,'
                          '这个数必须先量再用。',
        'coverage': round(matched / nonempty, 4),
        'matchedParagraphs': matched,
        'paragraphsWithText': nonempty,
        'unclassified': len(leftovers),
        'verdict': verdict,
        'consequence': ('沿用该模板表' if verdict == '匹配'
                        else '走建表流程,不强行套最像的那个'),
        'roleHits': {name: bucket[name] for name, _ in compiled},
    }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'\n  判定已落盘:{OUT}')


if __name__ == '__main__':
    main()
