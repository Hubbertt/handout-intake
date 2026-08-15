#!/usr/bin/env python3
"""第 2 步:按物理册实例化审计矩阵。

对象类与计数从实际蓝图取,不手抄。保证等级按本轮真正跑过的门填;
没跑过的一律写「空白」,不按化学册的格子想当然照抄。
"""
import json
from collections import Counter
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
# ★ 工序表说 s2-matrix 应当 consumes census——第 2 步在施工之前。本脚本实际读的是
# 蓝图,即施工之后。这不是笔误,是本轮真实发生的顺序错误的化石:第 2 步被跳过,
# 矩阵是事后补做的,只能拿手边已有的蓝图当对象类来源。
# 表描述的是应然,本脚本是实然,两者当前不一致——登记在 steps.v1.json 的
# openQuestions,不靠改表把它抹平。
BLUEPRINT = CHAIN.path_for('blueprint.substituted')
CHEM = CHAIN.only('matrix.reference')
OUT = CHAIN.path_for('matrix')

LENSES = ['真源一致', '语义完整', '原生性', '规范落位', '参数化',
          '版面·分页', '图文环绕', '渲染真值', '静默失败']

# 本轮真正跑过的门(9 步流程 + 我自建的)
RAN = {
    '覆盖门': 'gate_semantic_blueprint_source_coverage(9步流程 step3,PASS)',
    '声明落实门': 'gate_semantic_output_declarations(9步流程 step9,PASS)',
    '合规审计': 'audit_summer_word_full_compliance(9步流程 step8,PASS,20项)',
    '整页内容审计': 'audit_word_native_page_content(9步流程 step7,PASS)',
    '零宽门': 'GATE_NO_ZERO_WIDTH(自建,已破坏性自证)',
    '拆档横幅门': 'GATE_LESSON_SPLIT_AND_BANNER_COUNT(自建,5/5)',
    '过关检测还原门': 'GATE_EXERCISE_ITEMS_ARE_QUESTIONS(自建,60条)',
    '行内公式门': 'GATE_INLINE_FORMULA_SUBSTITUTED(自建,19/19)',
    'JPEG密度门': 'GATE_JPEG_DENSITY_VALID(自建,2张)',
}
NOT_RUN = {
    '源比对': 'verify_against_source.py —— **存在但本轮从未运行**。化学册靠它保「真源一致」。',
    '引文门': 'gate_registry_provenance_citations —— 未运行。声称出自规范的值必须引得出原文。',
    '结构清单门': 'gate_summer_structure_manifests —— 未运行。',
    '编译器测试': 'test_semantic_handout_compiler —— 未运行。化学册靠它保「参数化」「版面·分页」。',
    '分页门': 'audit_summer_pdf_page_break_defects —— 未运行(需 PDF 阶段)。',
}


def cell(assurance, by=None, note=None, why=None):
    out = {'assurance': assurance}
    if by:
        out['by'] = by if isinstance(by, list) else [by]
    if note:
        out['note'] = note
    if why:
        out['whyBlank'] = why
    return out


# 九个视角对「带文字的块型」的通用填法(本轮实测)
def text_block_lenses(kind):
    return {
        '真源一致': cell('空白',
                     why='**源比对 verify_against_source 本轮从未运行。** 覆盖门只保「每个源对象恰好被认领一次」,'
                         '不保字符级内容一致。实测后果:源 254 个斜体 run、93 个 114599 色 run,成品全为 0,'
                         '没有任何东西响。这一格是本册最大的空白。'),
        '语义完整': cell('有门', [RAN['覆盖门'], RAN['声明落实门']],
                     '块有归属、登记的替换必须命中且到场'),
        '原生性': cell('部分有门',
                    [RAN['行内公式门']],
                    '19 张公式位图已按哈希登记并替换、有门;图注烧在位图里的按裁定 R1 保留原图,'
                    '不属缺陷。但「这段文字本该是什么形态」仍无通用判据。'),
        '规范落位': cell('有门', [RAN['合规审计']],
                     '**注意口径**:该门查的是「有没有裸的直接格式」,不查「源的字符语义有没有到场」。'
                     '本册斜体被静默压平为 plain 后,这门照样绿——绿而不真。'),
        '参数化': cell('空白',
                    why='引文门与编译器测试本轮均未运行。物理表里的常数(如页眉右区取「第四章 光」)'
                        '有出处但没有门去验;typography 一节是从化学册投影来的,未对物理复核。'),
        '版面·分页': cell('空白',
                      why='编译器测试与分页门本轮均未运行。106 页成品没有任何分页缺陷检查。'),
        '图文环绕': cell('空白' if kind != 'image' else '空白',
                     why='声明落实门跑了,但本册未登记任何图文环绕声明,因此该门在这一格上无事可做——'
                         '判据恒假的一种。'),
        '渲染真值': cell('部分有门', [RAN['整页内容审计']],
                     'Word 原生整页内容审计 PASS;但 PDF 侧未做(未转曲、未过四步 QA)。'),
        '静默失败': cell('有门', [RAN['声明落实门']],
                     '声明→落实:每个块都要带着自己的书签到成品'),
    }


def main():
    blueprint = json.loads(BLUEPRINT.read_text(encoding='utf-8'))
    counts = Counter(b.get('type') for b in blueprint['blocks'])
    chem = json.loads(CHEM.read_text(encoding='utf-8'))

    objects = {}
    for kind, n in counts.most_common():
        objects[kind] = {'count': n, 'lenses': text_block_lenses(kind)}

    # image 有几格要单独改
    if 'image' in objects:
        objects['image']['lenses']['原生性'] = cell(
            '只能靠看 + 部分有门', [RAN['行内公式门']],
            '193 张去重图逐张人眼过完(R1 原图 171 / R2 标记 2 / R3 换文本 1 / R4 用公式 19)。'
            '公式类已升为有门;「这张图是不是本该是文字」本身仍是只能靠看。')
        objects['image']['lenses']['渲染真值'] = cell(
            '有门', [RAN['JPEG密度门'], RAN['整页内容审计']],
            'JPEG 密度合法性已配门(2 张修复,像素未动);整页内容审计 PASS。')

    if 'choice' in objects:
        objects['choice']['lenses']['语义完整'] = cell(
            '有门', [RAN['覆盖门']],
            '图片选项归属:修复角色 id 分派后 optionObjects 28→82、7 题带 23 张选项图。'
            '但化学册那条 option-rows-uneven(同题每行选项数必须相同)本册**未接入**。')

    # 本册特有的对象类:页眉页脚、目录
    objects['页眉页脚'] = {'count': 4, 'lenses': {
        '真源一致': cell('不适用'),
        '语义完整': cell('空白',
                     why='**本轮已出事**:蓝图不给 headerRight 就落到编译器硬编码的「八年级化学」,'
                         '物理讲义每个正文页眉都印着化学科目名。已修为「第四章 光」,'
                         '但 GATE_HEADER_NOT_GRADE_SUBJECT 尚未实现。'),
        '原生性': cell('构造保证', note='真 PAGE 域'),
        '规范落位': cell('有门', [RAN['合规审计']]),
        '参数化': cell('空白', why='页眉右区取值无门。来源棘轮未接入。'),
        '版面·分页': cell('不适用'),
        '图文环绕': cell('不适用'),
        '渲染真值': cell('部分有门', [RAN['整页内容审计']]),
        '静默失败': cell('空白',
                     why='页眉印错内容不会让任何门变红——这正是本轮的实证。'),
    }}
    objects['目录'] = {'count': 1, 'lenses': {
        '真源一致': cell('不适用', note='目录由排版重建,不从源携带;源的旧目录已登记为 notPrinted'),
        '语义完整': cell('部分有门', [RAN['覆盖门']]),
        '原生性': cell('构造保证', note='Word 原生 TOC 域,step5 已更新重排'),
        '规范落位': cell('有门', [RAN['合规审计']]),
        '参数化': cell('空白', why='目录字体/层级参数从化学册投影,未对物理复核。'),
        '版面·分页': cell('空白', why='未做分页检查。'),
        '图文环绕': cell('不适用'),
        '渲染真值': cell('部分有门', [RAN['整页内容审计']]),
        '静默失败': cell('空白', why='无。'),
    }}

    tally = Counter()
    for o in objects.values():
        for c in o['lenses'].values():
            tally[c['assurance']] += 1

    payload = {
        'schemaVersion': 'chengziclass.production-audit-matrix.v1',
        'rule': chem['rule'],
        'assuranceLevels': chem['assuranceLevels'],
        'lenses': chem['lenses'],
        'instantiatedFor': 'physics-g08-summer-2026(八年级物理暑假班 第A10-A14讲试制,106 页)',
        'instantiatedAt': '2026-08-15',
        'instantiatedBy': 'claude-e,第 2 步补做——本步此前被跳过,TRIAL_STATUS 首版误标为「未做」之外还曾把第 0 步误标为「完成」',
        'honesty': ('保证等级按本轮真正跑过的门填。化学册对应格子填「有门」而本轮没跑那道门的,'
                    '一律写「空白」,不照抄。空白 = 没有任何保证,所有事故都住在这里。'),
        'gatesRun': RAN,
        'gatesNotRun': NOT_RUN,
        'tally': dict(tally),
        'objects': objects,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    total = sum(len(o['lenses']) for o in objects.values())
    print(f'物理矩阵:{len(objects)} 对象类 × 9 视角 = {total} 格')
    for k, v in tally.most_common():
        print(f'   {k}: {v}')
    print(f'\n写出: {OUT}')
    print('\n空白格(要做的事):')
    for name, o in objects.items():
        for ln, c in o['lenses'].items():
            if c['assurance'] == '空白':
                print(f'   {name} × {ln}')


if __name__ == '__main__':
    main()
