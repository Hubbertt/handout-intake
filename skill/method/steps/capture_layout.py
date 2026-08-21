#!/usr/bin/env python3
"""非内容层:把源的版式事实逐字捕获,与内容层分开存。

**为什么不逐个补字段。** 上一轮普查:源里 47 类版式事实,原子化只记住 11 类。
若照着那 36 类逐个写提取器,下一份源换一批事实,又得重来——**枚举永远不全**,
而且列出来的多半是自己想得到的那几样。

所以这里不枚举:**把 pPr / rPr / 图形锚定与环绕 / 表格属性 / 分节属性原样记下来**,
按 locator 归位。新出现的事实自动被捕获,不必等人想起来。

**与内容层分开存**(独立产物 `layout`,不塞进 atoms):
内容回答「这是什么」,非内容回答「它该怎么呈现」。同一块内容可以进讲义、进单元卷、进错题本,
版式各不相同而内容只有一份——混在一起,就没法各用各的。

判准是**还原性**:能不能据内容层 + 本层重建源文件。本层只管**如实记**,不做取舍;
取舍是编制成册那一步的事。
"""
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from _bootstrap import chain_from_argv  # noqa: E402

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'

CHAIN = chain_from_argv(__doc__)
OUT = CHAIN.path_for('layout')


def short(tag):
    return tag.split('}')[-1]


def props(element):
    """一个属性容器(pPr / rPr / tblPr / tcPr)→ {子元素名: 属性字典}。

    只留**有信息**的:带属性的记属性,不带属性的记 true(它的存在本身就是事实,如 <w:b/>)。
    """
    out = {}
    if element is None:
        return out
    for child in element:
        name = short(child.tag)
        if name == 'rPr':          # pPr 里的 rPr 是段落标记的字符属性,另行归位
            continue
        attrs = {short(k): v for k, v in child.attrib.items()}
        out[name] = attrs or True
    return out


def drawing_facts(paragraph):
    """图形的锚定方式、环绕方式与尺寸。

    ★环绕方式此前只有一个 floating 布尔,而且全册 535 张图**全为 false**——
    源里其实有 65 个 anchor(浮动)+ 65 个 wrapNone。一个从未说过话的字段。
    这里按源实际的标签名记,不再压成布尔。
    """
    facts = []
    VML = '{urn:schemas-microsoft-com:vml}'
    for holder in list(paragraph.iter(W + 'drawing')) + list(paragraph.iter(VML + 'shape')):
        entry = {'kind': short(holder.tag)}
        if short(holder.tag) == 'shape':
            # VML 老式图形:锚定与环绕写在 style 串与 w10:wrap 里,不是 DrawingML 那套标签。
            # 此前它们被记成 anchoring="?" —— 一个"记了等于没记"的值。
            entry['anchoring'] = 'vml'
            if holder.get('style'):
                entry['style'] = holder.get('style')
            wrap = holder.find('{urn:schemas-microsoft-com:office:word}wrap')
            if wrap is not None:
                entry['wrap'] = [f'w10:{wrap.get("type") or "inline"}']
            else:
                entry['wrap'] = ['w10:absent(随文)']
            # ★VML 的尺寸写在 style 的 CSS 串里(width:18.95pt;height:26.75pt),
            #   不在 DrawingML 的 <wp:extent> 标签里。此前只把 style 原样记下,
            #   没解析出尺寸 —— 于是 2026-08-22 首次落库时 342 张 VML 图**一张都算不出相对比例**,
            #   而算不出就写不进库(scale_to_body_font 是 NOT NULL)。
            #   记了 style 不等于记了尺寸:原样留着是对的,但下游要用的是数,得解出来。
            emu = _vml_extent(holder.get('style'))
            if emu:
                entry['extentEmu'] = emu
        for mode in ('inline', 'anchor'):
            node = holder.find(f'{WP}{mode}')
            if node is not None:
                entry['anchoring'] = mode
                entry.update({short(k): v for k, v in node.attrib.items()})
                wraps = [short(c.tag) for c in node if short(c.tag).startswith('wrap')]
                if wraps:
                    entry['wrap'] = wraps
                extent = node.find(f'{WP}extent')
                if extent is not None:
                    entry['extentEmu'] = {'cx': extent.get('cx'), 'cy': extent.get('cy')}
        facts.append(entry)
    return facts


# CSS 长度 → EMU。只认真实出现过的单位;认不出的**不猜**,留给普查报出来。
_CSS_UNIT_EMU = {'pt': 12700, 'in': 914400, 'cm': 360000, 'mm': 36000, 'pc': 152400, 'px': 9525}
_CSS_LEN = re.compile(r'(width|height)\s*:\s*(-?[0-9.]+)\s*([a-z]*)', re.I)


def _vml_extent(style):
    """VML style 串里的 width/height → {'cx','cy'}(EMU)。

    单位认不出(如无单位)就整条不给尺寸——宁可缺一项被普查抓到,
    也不要按某个默认单位猜一个数出来:猜错了没人知道,而图会漂。
    """
    if not style:
        return None
    got = {}
    for name, value, unit in _CSS_LEN.findall(style):
        factor = _CSS_UNIT_EMU.get(unit.lower())
        if not factor:
            continue
        got['cx' if name.lower() == 'width' else 'cy'] = str(int(round(float(value) * factor)))
    return got if {'cx', 'cy'} <= set(got) else None


def font_resolver(zf):
    """段落的**生效**正文字号(半点)解析器:直接 rPr > 段落样式(含 basedOn 链)> docDefaults。

    ★为什么必须解继承链。2026-08-22 首次落库实测:962 张图里 617 张所在的段落
    **没有任何直接 rPr 写字号** —— 字号来自样式。只看直接 rPr 就等于「这段没有正文字号」,
    于是相对比例算不出、图写不进库。而「样式里写着」和「没有」是两回事。

    docDefaults 是整份 docx 的默认值来源:样式没写的属性都从它取值。
    三层的优先级是 OOXML 定死的,不是我们的选择。
    """
    try:
        styles = ET.fromstring(zf.read('word/styles.xml'))
    except KeyError:
        return lambda p: None
    default_sz = None
    dd = styles.find(f'{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz')
    if dd is not None:
        default_sz = dd.get(f'{W}val') or dd.get('val')
    own, based, default_style = {}, {}, None
    for st in styles.iter(W + 'style'):
        if st.get(f'{W}type') != 'paragraph':
            continue
        sid = st.get(f'{W}styleId')
        sz = st.find(f'{W}rPr/{W}sz')
        if sz is not None:
            own[sid] = sz.get(f'{W}val') or sz.get('val')
        b = st.find(W + 'basedOn')
        if b is not None:
            based[sid] = b.get(f'{W}val') or b.get('val')
        if st.get(f'{W}default') in ('1', 'true'):
            default_style = sid

    def of_style(sid, seen=None):
        seen = seen or set()
        while sid and sid not in seen:
            seen.add(sid)
            if sid in own:
                return own[sid]
            sid = based.get(sid)
        return None

    def resolve(paragraph):
        # 一、直接 rPr:取块内出现字符最多的那个 run —— 首个 run 常是编号或空 run
        best, best_chars = None, -1
        for run in paragraph.iter(W + 'r'):
            chars = len(''.join(x.text or '' for x in run.iter(W + 't')))
            sz = run.find(f'{W}rPr/{W}sz')
            val = sz.get(f'{W}val') or sz.get('val') if sz is not None else None
            if val and chars > best_chars:
                best, best_chars = val, chars
        if best:
            return {'halfPoints': int(best), 'from': 'run'}
        # 二、段落样式(含 basedOn 链)
        pstyle = paragraph.find(f'{W}pPr/{W}pStyle')
        sid = (pstyle.get(f'{W}val') or pstyle.get('val')) if pstyle is not None else default_style
        val = of_style(sid)
        if val:
            return {'halfPoints': int(val), 'from': f'style:{sid}'}
        # 三、docDefaults
        if default_sz:
            return {'halfPoints': int(default_sz), 'from': 'docDefaults'}
        return None

    return resolve


def capture(path, document):
    zf = zipfile.ZipFile(path)
    resolve_font = font_resolver(zf)
    root = ET.fromstring(zf.read('word/document.xml'))
    body = root.find(W + 'body')
    rows = []
    para = table = 0
    for child in body:
        if child.tag == W + 'tbl':
            table += 1
            rows.append({
                'document': document, 'locator': f'body/tbl[{table}]', 'node': 'tbl',
                'tblPr': props(child.find(W + 'tblPr')),
                'gridCols': [{short(k): v for k, v in c.attrib.items()}
                             for c in child.iter(W + 'gridCol')],
                'cellProps': [props(c) for c in child.iter(W + 'tcPr')],
            })
            continue
        if child.tag != W + 'p':
            continue
        para += 1
        runs = []
        for run in child.iter(W + 'r'):
            rpr = props(run.find(W + 'rPr'))
            text = ''.join(t.text or '' for t in run.iter(W + 't'))
            breaks = [short(b.tag) for b in run if short(b.tag) in ('br', 'tab', 'sym')]
            if rpr or breaks:
                runs.append({'chars': len(text), 'rPr': rpr,
                             **({'marks': breaks} if breaks else {})})
        entry = {'document': document, 'locator': f'body/p[{para}]', 'node': 'p',
                 'pPr': props(child.find(W + 'pPr'))}
        if runs:
            entry['runs'] = runs
        figs = drawing_facts(child)
        if figs:
            entry['drawings'] = figs
            # 只在有图的段落上解:相对比例是**图**的属性,别的段落不需要,
            # 全量解会让非内容层凭空多出 5351 条没人用的事实。
            font = resolve_font(child)
            if font:
                entry['bodyFont'] = font
        rows.append(entry)
    sect = body.find(W + 'sectPr')
    section = props(sect) if sect is not None else {}
    return rows, section


def main():
    lessons = CHAIN.resolve('lessons')
    # 文档标识统一用 registry 的 lesson(第A01讲),不用文件名(A01-序言-…)。
    # 两层各用各的键,对账时必然全对不上——2026-08-20 实测:s4c6 因此报了 5351 条假损失。
    label_of = {}
    reg = CHAIN.resolve('registry')
    if reg:
        for entry in (json.loads(reg[0].read_text(encoding='utf-8')).get('documents') or []):
            stem = Path(entry.get('physicalPath') or entry.get('path') or '').stem
            if stem and entry.get('lesson'):
                label_of[stem] = entry['lesson']
    if not lessons:
        raise SystemExit('没有分档产物可捕获版式。先跑 s4b-split-lessons。')
    all_rows = []
    sections = {}
    seen = Counter()
    for path in lessons:
        document = label_of.get(path.stem, path.stem)
        rows, section = capture(path, document)
        all_rows.extend(rows)
        sections[document] = section
        for r in rows:
            for k in r.get('pPr', {}):
                seen[f'pPr/{k}'] += 1
            for run in r.get('runs') or []:
                for k in run.get('rPr', {}):
                    seen[f'rPr/{k}'] += 1
            for d in r.get('drawings') or []:
                seen[f'drawing/{d.get("anchoring", "?")}'] += 1
                for w in d.get('wrap') or []:
                    seen[w] += 1
            if r['node'] == 'tbl':
                seen['tbl'] += 1
                # ★表格的三层属性此前只计了 tbl 一项,于是普查把 tcPr/gridCol 判成「未记」——
                # 其实记了,是**计数口径漏了**。口径漏计会让门报假红,而假红与真红一样会被无视。
                for _ in r.get('gridCols') or []:
                    seen['gridCol'] += 1
                for _ in r.get('cellProps') or []:
                    seen['tcPr'] += 1
            for run in r.get('runs') or []:
                for mark in run.get('marks') or []:
                    seen[mark] += 1
            for d in r.get('drawings') or []:
                if d.get('kind') == 'shape':
                    seen['pict'] += 1
                for w in d.get('wrap') or []:
                    seen['wrap'] += 1

    for section in sections.values():
        for key in section:
            seen[f'sectPr/{key}'] += 1
    # oMath 单独计:它在 run 之外
    for path in lessons:
        import zipfile as _z
        _root = ET.fromstring(_z.ZipFile(path).read('word/document.xml'))
        seen['oMath'] += sum(1 for _ in _root.iter(
            '{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath'))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.layout.v1',
        'what': ('非内容层:源的版式事实,按 locator 归位。与内容层(atoms)分开存——'
                 '内容回答「这是什么」,本层回答「它该怎么呈现」。'),
        'rule': ('逐字捕获,不枚举字段:新出现的事实自动被记下,不必等人想起来。'
                 '本层只管如实记,不做取舍;取舍是编制成册那一步的事。'),
        'documents': len(lessons),
        'blocks': len(all_rows),
        'factKinds': dict(seen.most_common()),
        'sectionProps': sections,
        'blocksDetail': all_rows,
    }, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f'版式层已捕获:{len(lessons)} 档 / {len(all_rows)} 块 / '
          f'{len(seen)} 类版式事实 → {OUT}')
    for k, v in seen.most_common(12):
        print(f'   {k:22} {v}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
