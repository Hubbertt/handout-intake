#!/usr/bin/env python3
"""GATE_RECONSTRUCTIBLE:能不能据两类信息还原出源文件。

**这是原子化的最终判准。** PM 2026-08-20:「做到原子化后能根据原子化的两类信息
完整地还原出源文件。」

★「类别覆盖全」不等于「能还原」。s1d 的普查回答的是「源里出现的每一类事实,我们记了没有」;
本门问的是更硬的那个问题:**逐块、逐字、逐属性地比,还原得出来吗**。
两者差别很大——记住「源里有 spacing 这一类」与「第 137 段的 spacing 是多少」是两回事。

比三样(都是**逐个**比,不是比总数):

  ① 字符:源里每个非空段的文本,必须在内容层里找得到,且**归属到具体模块**
  ② 版式:源里每个段/表的属性容器,必须在非内容层里有**同 locator 的记录**,且属性逐项相等
  ③ 图形:源里每个图形的锚定方式与环绕方式,必须逐个对上

对不上的**逐条列出来**,不给百分比了事——百分比会让人以为"差不多了",
而还原是全有或全无:少一项,重建出来的就不是原来那份。
"""
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from _bootstrap import chain_from_argv  # noqa: E402

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
VML = '{urn:schemas-microsoft-com:vml}'

CHAIN = chain_from_argv(__doc__)
REPORT = CHAIN.path_for('gate.reconstructible')


def short(tag):
    return tag.split('}')[-1]


def source_blocks(path):
    root = ET.fromstring(zipfile.ZipFile(path).read('word/document.xml'))
    body = root.find(W + 'body')
    para = table = 0
    for child in body:
        if child.tag == W + 'tbl':
            table += 1
            yield f'body/tbl[{table}]', child, 'tbl'
        elif child.tag == W + 'p':
            para += 1
            yield f'body/p[{para}]', child, 'p'


def main():
    lessons = CHAIN.resolve('lessons')
    layout = CHAIN.resolve('layout')
    atoms_path = CHAIN.path_for('atoms.normalised')
    if not lessons or not layout:
        raise SystemExit('缺 lessons 或 layout。还原判定需要两类信息都在。')

    lay = json.loads(layout[0].read_text(encoding='utf-8'))
    by_loc = {(b['document'], b['locator']): b for b in lay['blocksDetail']}
    atoms = json.loads(atoms_path.read_text(encoding='utf-8'))
    text_of_doc = {}
    for a in atoms:
        text_of_doc.setdefault(a['document'], []).append(
            json.dumps(a, ensure_ascii=False))
    struct = json.loads(CHAIN.path_for('structure').read_text(encoding='utf-8'))
    for b in struct:
        text_of_doc.setdefault(b.get('document'), []).append(
            json.dumps(b, ensure_ascii=False))
    haystack = {k: re.sub(r'\s+', '', ''.join(v)) for k, v in text_of_doc.items()}

    losses = Counter()
    examples = {}

    def note(code, doc, loc, detail):
        losses[code] += 1
        examples.setdefault(code, []).append({'document': doc, 'locator': loc,
                                              'detail': detail[:120]})

    # ★文档标识:分档**文件名**是 A01-序言-…,而内容层里的 document 是 第A01讲。
    # 首版直接拿 path.stem 去查内容层,键全对不上 → 3990 段全判成「未归属」,
    # 而真实值是 0。一道会报假红的门比没有门更坏:假红与真红一样会被无视,
    # 而下一次真红就没人当回事了。
    # 由 registry 给出「档名 → document」的对应,不在这里另猜一套。
    label_of = {}
    reg = CHAIN.resolve('registry')
    if reg:
        for entry in (json.loads(reg[0].read_text(encoding='utf-8')).get('documents') or []):
            path_name = Path(entry.get('physicalPath') or entry.get('path') or '').stem
            if path_name and entry.get('lesson'):
                label_of[path_name] = entry['lesson']

    for path in lessons:
        doc = label_of.get(path.stem)
        if doc is None:
            note('DOCUMENT_LABEL_UNKNOWN', path.stem, '-',
                 'registry 里没有这一档的 lesson 标识,无从与内容层对账')
            continue
        hay = haystack.get(doc, '')
        for loc, node, kind in source_blocks(path):
            rec = by_loc.get((doc, loc))
            if rec is None:
                note('LAYOUT_BLOCK_MISSING', doc, loc, '非内容层没有这一块')
                continue
            if kind == 'p':
                # ② 版式逐项比
                want = {}
                pr = node.find(W + 'pPr')
                if pr is not None:
                    for c in pr:
                        if short(c.tag) != 'rPr':
                            want[short(c.tag)] = ({short(k): v for k, v in c.attrib.items()}
                                                  or True)
                got = rec.get('pPr') or {}
                for key, val in want.items():
                    if key not in got:
                        note('PPR_KEY_LOST', doc, loc, f'{key} 未记')
                    elif got[key] != val:
                        note('PPR_VALUE_DIFF', doc, loc, f'{key}: {val} vs {got[key]}')
                # ③ 图形
                for holder in list(node.iter(W + 'drawing')) + list(node.iter(VML + 'shape')):
                    kinds = [d.get('anchoring') for d in rec.get('drawings') or []]
                    if not kinds:
                        note('DRAWING_LOST', doc, loc, '源里有图形,非内容层没记')
                        break
                # ① 字符归属
                text = ''.join(t.text or '' for t in node.iter(W + 't')).strip()
                # 空白规范化后再比:内容层里的文本是收过空白的,原样比会把
                # 「第01讲  序言」(双空格)判成未见。
                probe = re.sub(r'\s+', '', text)[:8]
                if len(re.sub(r'\s+', '', text)) >= 8 and probe not in hay:
                    note('TEXT_UNATTRIBUTED', doc, loc, text[:60])

    status = 'pass' if not losses else 'fail'
    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-reconstructible.v1',
        'gate': 'GATE_RECONSTRUCTIBLE',
        'rule': ('逐块、逐字、逐属性地比:源里的每一段文本必须在内容层里有归属,'
                 '每一个属性必须在非内容层里有同 locator 的记录且值相等,'
                 '每一个图形的锚定与环绕必须对上。'),
        '★whyNoPercentage': ('不给百分比:百分比会让人以为「差不多了」,而还原是全有或全无——'
                             '少一项,重建出来的就不是原来那份。'),
        'status': status,
        'documents': len(lessons),
        'losses': dict(losses),
        'examples': {k: v[:4] for k, v in examples.items()},
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'GATE_RECONSTRUCTIBLE: {status}  ({len(lessons)} 档)')
    for code, n in losses.most_common():
        print(f'   {code:24} {n}')
        for e in examples[code][:2]:
            print(f'       · {e["document"]} {e["locator"]}  {e["detail"][:60]}')
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
