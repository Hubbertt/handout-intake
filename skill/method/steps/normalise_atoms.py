#!/usr/bin/env python3
"""GATE_EXERCISE_ITEMS_ARE_QUESTIONS:把过关检测块里被降级的编号项还原成题。

为什么需要这一步:
  carve_engine 第 1618 行 `if kind == "编号项" and not answer: kind = "讲解条目"`。
  这是化学册的形状——那边答案来自配套解析版,没答案的编号项确实是讲解正文。
  物理是学生版、无解析版,于是**每一条编号项都被降级**,过关检测里的题也不例外。

  物理的「1.」同时用在两处:概念构建/深研精练里的讲解编号(「1. 光源」),
  和过关检测里的题号(「1．下列物体属于光源的是（ ）」)。形状完全相同,
  唯一的区别是它落在哪个栏目——而栏目引擎已经算对了(按横幅图片哈希)。

不改引擎:引擎是共享代码且租约在别人手上。修在数据侧,用引擎自己算出的 section。
"""
import json
import re
from collections import Counter
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
ATOMS = CHAIN.path_for('atoms')
OUT = CHAIN.path_for('atoms.normalised')
REPORT = CHAIN.path_for('gate.exercise-items')

# 「哪些原子是题」由模板表给,不写死在这里。
# 首版写死 EXERCISE = '过关检测' —— 又一次把某一类文档的事实编进代码。
# 单元卷没有「过关检测」这个栏目,题块靠 node(节标题「一、学科基础（共38分）」)界定;
# 拿写死的判据去跑,选中 0 条,而这一步的门恰好只检查「有没有还原过」,
# 于是它会报「判据可能恒假」——报对了,但报的是自己那条判据。
_SCHEMA = json.loads(Path(str(CHAIN.only('schema'))).read_text(encoding='utf-8'))
_DEFAULT_SCOPE = {'by': 'section', 'equals': '过关检测', 'restoredKind': '过关检测题'}
SCOPE = _SCHEMA.get('questionScope') or _DEFAULT_SCOPE
SCOPE_FIELD = SCOPE.get('by', 'section')
SCOPE_EQUALS = SCOPE.get('equals')
SCOPE_PATTERN = re.compile(SCOPE['pattern']) if SCOPE.get('pattern') else None
RESTORED_KIND = SCOPE.get('restoredKind', '过关检测题')
if SCOPE_EQUALS is None and SCOPE_PATTERN is None:
    raise SystemExit('questionScope 既没给 equals 也没给 pattern。'
                     '拒绝用一个永远选不中的判据跑——那会把整册的题全留在讲解条目里,'
                     '而门只会说「一条都没还原」。')


def in_scope(atom):
    value = atom.get(SCOPE_FIELD)
    if value is None:
        return False
    if SCOPE_EQUALS is not None:
        return value == SCOPE_EQUALS
    return bool(SCOPE_PATTERN.search(value))


def main():
    atoms = json.loads(ATOMS.read_text(encoding='utf-8'))
    before = Counter((a.get('kind'), a.get('section')) for a in atoms)

    moved = []
    for atom in atoms:
        if in_scope(atom) and atom.get('kind') == '讲解条目':
            atom['kind'] = RESTORED_KIND
            atom['kindRestoredFrom'] = '讲解条目'
            atom['kindRestoredWhy'] = (
                'carve_engine 把无答案的编号项一律降级为讲解条目(化学册形状:答案来自解析版)。'
                f'该判据在此不成立(缺答案的原因是源里就没有,不是它不是题)。'
                f'按引擎自己算出的 {SCOPE_FIELD} 落在题块内还原。')
            moved.append(atom['locator'])

    after = Counter((a.get('kind'), a.get('section')) for a in atoms)

    # 门:两侧都要干净
    leftover = [a['locator'] for a in atoms
                if in_scope(a) and a.get('kind') == '讲解条目']
    outside = [a['locator'] for a in atoms
               if not in_scope(a) and a.get('kind') == RESTORED_KIND]
    # 破坏性自证:题块外的讲解条目一条都不能被动过
    expository = [a for a in atoms if not in_scope(a) and a.get('kind') == '讲解条目']
    scope_matched = [a for a in atoms if in_scope(a)]

    failures = []
    if leftover:
        failures.append({'why': '过关检测内仍有讲解条目', 'locators': leftover[:10]})
    if outside:
        failures.append({'why': '栏目外出现过关检测题', 'locators': outside[:10]})
    # 恒假检查看的是**选择器本身有没有选中过东西**,不是「有没有还原过」。
    # 首版检查后者:一册里所有题都配到了答案时(引擎就不会降级),还原数天然为 0,
    # 会被判成恒假——那是把「没有需要修的」当成「判据坏了」。
    if not scope_matched:
        failures.append({'why': f'questionScope 选中 0 条原子(by={SCOPE_FIELD} '
                                f'equals={SCOPE_EQUALS!r} pattern={SCOPE.get("pattern")!r}),'
                                '判据恒假'})

    status = 'pass' if not failures else 'fail'
    report = {
        'schemaVersion': 'chengziclass.gate-exercise-items.v1',
        'gate': 'GATE_EXERCISE_ITEMS_ARE_QUESTIONS',
        'status': status,
        'rule': (f'{SCOPE_FIELD} 落在题块内 且 kind==讲解条目 → kind={RESTORED_KIND};'
                 '题块外一条都不动'),
        'questionScope': SCOPE,
        'scopeMatched': len(scope_matched),
        'restoredCount': len(moved),
        'expositoryLeftUntouched': len(expository),
        'expositorySamples': [(a.get('stem') or '')[:24] for a in expository[:4]],
        'questionsWithOptions': sum(1 for a in atoms
                                    if a.get('kind') == RESTORED_KIND and a.get('options')),
        'questionsWithSubQuestions': sum(1 for a in atoms
                                         if a.get('kind') == RESTORED_KIND and a.get('subQuestions')),
        'optionFiguresInExercise': sum(
            o.get('images', 0) for a in atoms if a.get('kind') == RESTORED_KIND
            for o in (a.get('options') or [])),
        'before': {f'{k[0]}|{k[1]}': v for k, v in before.items()},
        'after': {f'{k[0]}|{k[1]}': v for k, v in after.items()},
        'failures': failures,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    OUT.write_text(json.dumps(atoms, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'门 {status}:题块内 {len(scope_matched)} 条,还原 {len(moved)} 条,'
          f'题块外 {len(expository)} 条讲解正文原封不动')
    print(f'  还原后带选项的题 {report["questionsWithOptions"]},带小问的 {report["questionsWithSubQuestions"]}')
    print(f'  题块内的选项图 {report["optionFiguresInExercise"]} 张')
    print(f'  题块外讲解正文样例: {report["expositorySamples"]}')
    if failures:
        print('  失败:', json.dumps(failures, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
