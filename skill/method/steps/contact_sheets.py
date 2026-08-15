#!/usr/bin/env python3
"""图逐张裁决的载体:带编号的联系表 + 索引。

使用方裁定「逐张看完」。193 张分页排,每张图打上全局编号,编号回指
lesson / locator / 归属块,这样看到可疑的能立刻定位到源。
"""
import json
import os
from collections import OrderedDict

from PIL import Image, ImageDraw

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
T = str(CHAIN.workspace)
OUT = str(CHAIN.dir_for('figures.contact'))
COLS, ROWS = 5, 4
CELL = 300
LABEL = 26


def collect():
    """按 (讲, locator) 顺序收集每张图的归属信息。"""
    atoms = json.load(open(os.path.join(T, 'work/atoms.json')))
    struct = json.load(open(os.path.join(T, 'work/structure.json')))
    seen = OrderedDict()

    def walk(node, doc, owner, locator):
        if isinstance(node, dict):
            doc = node.get('document') or doc
            locator = node.get('locator') or locator
            label = (node.get('stem') or node.get('text') or
                     node.get('label') or owner)
            for key, value in node.items():
                if key == 'figures' and isinstance(value, list):
                    for figure in value:
                        path = figure.get('file')
                        if not path:
                            continue
                        name = os.path.basename(path)
                        if name in seen:
                            continue
                        # atoms 里的 file 是裸文件名,实体在 --media-dir 下
                        width = int(figure.get('widthEmu') or 0) / 360000
                        height = int(figure.get('heightEmu') or 0) / 360000
                        seen[name] = {
                            'file': os.path.join(T, 'work/media', name),
                            'document': doc, 'locator': locator,
                            'owner': (label or '')[:48],
                            'displayCm': f'{width:.1f}x{height:.1f}',
                            'floating': bool(figure.get('floating')),
                        }
                else:
                    walk(value, doc, label, locator)
        elif isinstance(node, list):
            for value in node:
                walk(value, doc, owner, locator)

    walk(struct, None, None, None)
    walk(atoms, None, None, None)
    return seen


def main():
    os.makedirs(OUT, exist_ok=True)
    seen = collect()
    index = []
    for position, (name, meta) in enumerate(seen.items(), start=1):
        meta = dict(meta)
        meta['n'] = position
        meta['name'] = name
        index.append(meta)

    per = COLS * ROWS
    sheets = (len(index) + per - 1) // per
    for sheet in range(sheets):
        chunk = index[sheet * per:(sheet + 1) * per]
        canvas = Image.new('RGB', (COLS * CELL, ROWS * (CELL + LABEL)), 'white')
        draw = ImageDraw.Draw(canvas)
        for slot, item in enumerate(chunk):
            col, row = slot % COLS, slot // COLS
            x, y = col * CELL, row * (CELL + LABEL)
            try:
                with Image.open(item['file']) as im:
                    im = im.convert('RGB')
                    width, height = im.size
                    item['px'] = f'{width}x{height}'
                    im.thumbnail((CELL - 8, CELL - 8))
                    canvas.paste(im, (x + (CELL - im.width) // 2,
                                      y + LABEL + (CELL - LABEL - im.height) // 2))
            except Exception as exc:                      # noqa: BLE001
                draw.text((x + 8, y + LABEL + 8), f'[无法打开] {exc}', fill='red')
                item['px'] = 'unreadable'
            draw.rectangle([x, y, x + CELL - 1, y + CELL + LABEL - 1], outline='#bbb')
            draw.rectangle([x, y, x + CELL - 1, y + LABEL - 1], fill='#222')
            draw.text((x + 6, y + 7),
                      f"#{item['n']} {item['document'] or ''} {item.get('displayCm', '')}cm",
                      fill='white')
        target = os.path.join(OUT, f'contact-{sheet + 1:02d}.png')
        canvas.save(target)
        print(f'{target}  ({len(chunk)} 张)')

    with open(os.path.join(OUT, 'figure-index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'schemaVersion': 'chengziclass.figure-index.v1',
                   'total': len(index), 'sheets': sheets,
                   'perSheet': per, 'figures': index}, fh,
                  ensure_ascii=False, indent=2)
    print(f'\n共 {len(index)} 张,{sheets} 页联系表')


if __name__ == '__main__':
    main()
