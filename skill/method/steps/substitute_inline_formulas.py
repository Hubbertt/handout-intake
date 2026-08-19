#!/usr/bin/env python3
"""GATE_INLINE_FORMULA_SUBSTITUTED:把行内公式位图换成公式 run。

为什么要单写这一步:
  build_blueprint_from_atoms 的 nativeTextSubstitutions 只在 `elif pictures:`
  分支里查表——那条分支只处理「整块就是一张图」的块。带文字的段落走文本分支,
  行内图作为 kind=inline_image 的 segment 原样穿过去,**从不查替换表**。
  化学册那两处替换都是独立成块的,所以从没撞上这个口子。

  物理这 19 张全是行内的(「距离为〖10m〗,则…」),一张都进不了那条分支。
  修根因要改共享代码 build_blueprint_from_atoms.py,而该目录租约在另一会话手上,
  所以本轮在自己这层做投影替换,并把缺口如实登记。

两处自己踩过的坑,都写在断言与遍历里:
  1) 断言必须按「不同对象」判,不按「出现次数」判——一张公式图可在多段复用
     (#82 的 m 就用了两次)。首版拿 24 次出现比 19 条登记,报假失败。
  2) 遍历必须递归到任何带 segments 的容器,不只是块自身——#159(h′)住在
     表格单元格里。上面那个假失败正好把这条真漏网盖住了。
"""
import json
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
BLUEPRINT = CHAIN.path_for('blueprint.pristine')
SUBS = CHAIN.path_for('mapping.substitutions')
OUT = CHAIN.path_for('blueprint.substituted')
REPORT = CHAIN.path_for('gate.inline-formula')

RUN_MAP = {'plain': 'plain', 'unit': 'plain', 'prime': 'plain',
           'subscript': 'chemical_subscript', 'superscript': 'chemical_superscript',
           'italic': 'plain'}
RUN_NOTES = {
    'italic': ('编译器 RUN_STYLE_IDS 里没有「斜体变量」这一档。物理量符号(f、h、AO)按规范'
               '应为斜体,现降级为 plain。这是词表缺口,不是裁决——需要一个 CZ_MathVariable '
               '之类的字符样式,或复用现有斜体样式。**降级已发生,成品里这些符号不会是斜体。**'),
    'subscript': '借用 chemical_subscript。样式名带 chemical 只是历史命名,排版行为就是下标。',
    'superscript': '借用 chemical_superscript,同上。',
}


def holders(node):
    """产出任何带 segments 列表的容器(块自身、表格单元格、以后可能有的嵌套)。"""
    if isinstance(node, dict):
        if isinstance(node.get('segments'), list):
            yield node
        for key, value in node.items():
            if key != 'segments':
                yield from holders(value)
    elif isinstance(node, list):
        for value in node:
            yield from holders(value)


def main():
    blueprint = json.loads(BLUEPRINT.read_text(encoding='utf-8'))
    _subs = json.loads(SUBS.read_text(encoding='utf-8'))
    table = _subs['objects']
    # 有意保留为原图的对象:词表接不住的形态(如堆叠分式)。
    # 它们不是「漏了」,是**明写理由的例外**。门仍然对未登记的残留 fail——
    # 放宽门等于把「还没处理」和「决定不处理」混成一件事,而前者会悄悄进成品。
    held = _subs.get('heldAsImage') or {}

    substituted, missed = [], []
    for block in blueprint.get('blocks') or []:
        for holder in holders(block):
            rebuilt, changed = [], False
            for segment in holder['segments']:
                if not (isinstance(segment, dict)
                        and segment.get('kind') == 'inline_image'):
                    rebuilt.append(segment)
                    continue
                stem = Path(str(segment.get('path') or '')).stem
                stem = stem[:-len('.render')] if stem.endswith('.render') else stem
                entry = table.get(stem)
                if not entry:
                    rebuilt.append(segment)
                    missed.append({'block': block['id'], 'stem': stem,
                                   'path': segment.get('path')})
                    continue
                # 替换后仍要认领这个源对象,否则覆盖门会把它算成无主。
                # 化学的块级替换在 build_blueprint 里就把 source 带到新块上
                # (1870-1885,多块时还补 objectPart);行内替换这层要自己带。
                claim = segment.get('source')
                for order, part in enumerate(entry['segments'], start=1):
                    run = {'text': part['text'],
                           'run_type': RUN_MAP[part['run_type']],
                           'substitutedSourceImage': stem,
                           'sourceRunType': part['run_type']}
                    if claim:
                        source = dict(claim)
                        if len(entry['segments']) > 1:
                            source['objectPart'] = f'part{order}'
                        run['source'] = source
                    rebuilt.append(run)
                substituted.append({'block': block['id'], 'stem': stem,
                                    'n': entry['n'], 'reads': entry['reads'],
                                    'claim': claim})
                changed = True
            if changed:
                holder['segments'] = rebuilt

    # 覆盖门只认 kind==inline_image 的 segment 作归属者(gate 第 312 行),
    # 换成文字后这些源对象按构造就无主了。
    #
    # 走过三条路,前两条都不对:
    #   1) sourceObjectExclusions —— 编译器校验分类白名单(第 136 行),五种里没有
    #      「已重建为原生内容」。最接近的 non_instructional_shape_carrier 是假的:
    #      这 19 个恰恰是教学内容(10m/25nm/25cm 就是题目数据)。贴错标签=让门说谎。
    #   2) sourceObjectSubstitutions —— 有专门的 targetBlockId+replacementText 字段,
    #      看着像为此而设,但编译器第 1955 行要求 locator.kind ∈ {shape, paragraph},
    #      而公式是 image。这条是给「文字型形状/文本框段落」用的,不是给图片的。
    #   3) additionalSources(块级) —— 对了。覆盖门第 296 行把它算作归属者(还支持
    #      objectPart),编译器全文不引用它,既不校验也不拒绝。
    #
    # 语义上第 3 条也最正:这不是「排除」,是**归属转移**——承载公式文字的那个块,
    # 认领它替换掉的那个源图片对象。对象仍然有主,审计链不断。
    by_block: dict = {}
    for item in substituted:
        claim = item.get('claim')
        if not (claim or {}).get('objectId'):
            continue
        by_block.setdefault(item['block'], []).append(item)
    block_by_id = {b['id']: b for b in (blueprint.get('blocks') or [])}
    for block_id, items in by_block.items():
        block = block_by_id.get(block_id)
        if block is None:
            continue
        extra = block.setdefault('additionalSources', [])
        seen = {str((s or {}).get('objectId')) for s in extra}
        for order, item in enumerate(items, start=1):
            claim = dict(item['claim'])
            if str(claim.get('objectId')) in seen:
                continue
            seen.add(str(claim.get('objectId')))
            if len(items) > 1:
                claim['objectPart'] = f'formula{order}'
            claim['substitutedTo'] = item['reads']
            claim['substitutionReason'] = (
                '公式被源排成位图,按裁定「公式都用公式做」重建为公式 run;'
                '本块认领该源对象,归属转移而非排除')
            extra.append(claim)

    # 页眉右区:写该内容所在的目录标题,不写「X年级X科目」。
    # 编译器第 3041 行 right_body = blueprint.get("headerRight") or "八年级化学"
    # —— 不给这个键就落到硬编码的化学默认值,物理讲义页眉会印着「八年级化学」。
    # 目录标题取 registry 的 theme(第四章 光),它是按图注编号 图4-1-x..图4-5-x 量出来的。
    # 规范(长期):页眉右区写所在目录的讲级标题,随页变化,用 STYLEREF 域。
    # CZ_Heading1 是讲标题的样式(blocks[讲标题].style=heading1 → BLOCK_STYLE_IDS)。
    # STYLEREF 要样式名不是 styleId。讲标题的样式是 CZ_Heading1;
    # 2026-08-15 起它的显示名由「橙子一级标题」改为「橙子二级标题」——
    # 原先各级样式名比自己的大纲级别小一号(「橙子讲次标题」占第 1 级却不叫一级),
    # 已按 名字里的数字 = w:outlineLvl + 1 统一。
    # ★这一处是按名匹配的,名字一变就得跟着改,否则 STYLEREF 找不到样式、
    #   页眉右区整片落空——而落空的页眉和「这一页恰好没有讲标题」长得一样。
    blueprint['headerRightStyleRef'] = '橙子二级标题'
    # 域的缓存结果,Word 更新域后按页刷新。取本册第一讲的标题——
    # 首版这里写死「第10讲 光的反射」,是**旧册的事实留在共享代码里**;
    # 换一册就带着上一册的讲名进成品,直到 Word 更新域才被盖掉。
    _first = next((str(b.get('text') or '').strip()
                   for b in (blueprint.get('blocks') or [])
                   if str(b.get('style') or '') == blueprint['headerRightStyleRef']), '')
    blueprint['headerRight'] = _first
    blueprint['headerRightCacheWhy'] = (
        '取本册第一个 %s 块的文本作为域缓存值;真值由 Word 更新域后按页产生。'
        '不写死某一册的讲名。' % blueprint['headerRightStyleRef'])
    blueprint['headerRightRule'] = (
        '页眉右区写所在目录的讲级标题(PM 2026-08-15 裁定为长期规定),由 STYLEREF 域'
        '按页取 CZ_Heading1。此前写死「第四章 光」是取了最粗的一级,反了。')

    def _stem(seg):
        return Path(str(seg.get('path') or '')).stem

    residual = [_stem(s) for b in (blueprint.get('blocks') or [])
                for h in holders(b) for s in h['segments']
                if isinstance(s, dict) and s.get('kind') == 'inline_image'
                and str(s.get('path') or '').endswith('.wmf')]
    remaining = len(residual)
    undeclared = sorted({stem for stem in residual if stem not in held})
    undeclared_occurrences = sum(1 for stem in residual if stem not in held)
    declared_occurrences = remaining - undeclared_occurrences

    distinct = {item['stem'] for item in substituted}
    failures = []
    if distinct != set(table):
        failures.append(f'登记 {len(table)} 个对象,替换到 {len(distinct)} 个;'
                        f'漏掉 {sorted(set(table) - distinct)}')
    if undeclared:
        failures.append(f'仍有 {len(undeclared)} 个 .wmf 行内图既未替换、也未在 '
                        f'heldAsImage 里登记理由:{undeclared[:8]}')
    status = 'pass' if not failures else 'fail'

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding='utf-8')
    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-inline-formula.v1',
        'gate': 'GATE_INLINE_FORMULA_SUBSTITUTED',
        'status': status,
        'upstreamGap': {
            'file': 'carve-rules/g08-chemistry-huke-2026-topic3-4/build_blueprint_from_atoms.py',
            'lines': '1806-1829',
            'gap': 'segments 里的 inline_image 不查 nativeTextSubstitutions,只有整块是图的块才查',
            'whyNotFixedAtSource': '共享代码,该目录租约在 claude-e 另一会话手上',
            'proposedFix': '在文本分支里对 segments 逐个查表,与 elif pictures: 分支用同一张表',
        },
        'runTypeMapping': RUN_MAP,
        'runTypeNotes': RUN_NOTES,
        'registeredObjects': len(table),
        'distinctSubstituted': len(distinct),
        'occurrences': len(substituted),
        'occurrenceVsObject': '出现次数多于对象数是正常的:同一张公式图可在多段复用(#82 的 m 用了两次)',
        'remainingWmfInline': remaining,
        'residualDeclaredHeld': declared_occurrences,
        'residualUndeclared': undeclared_occurrences,
        'heldAsImageObjects': {k: v.get('reads') for k, v in held.items()},
        'heldAsImageWhy': '词表接不住的形态,明写理由保持原图;不计入失败,但计数照登。',
        'unregisteredInlineImages': {
            'count': len(missed),
            'verdict': '按裁定 R1「有实际含义且清晰的原图不动」,这些是普通行内插图,不替换',
            'samples': missed[:5],
        },
        'details': substituted,
        'failures': failures,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'门 {status}:登记 {len(table)} 个对象 → 替换到 {len(distinct)} 个,'
          f'共 {len(substituted)} 次;残留 .wmf 行内图 {remaining}')
    print(f'  未登记的行内图 {len(missed)} 个,按 R1 保持原图')
    if failures:
        print('  失败:', failures)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
