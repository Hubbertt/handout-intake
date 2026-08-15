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
from collections import Counter
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
ATOMS = CHAIN.path_for('atoms')
OUT = CHAIN.path_for('atoms.normalised')
REPORT = CHAIN.path_for('gate.exercise-items')
EXERCISE = '过关检测'


def main():
    atoms = json.loads(ATOMS.read_text(encoding='utf-8'))
    before = Counter((a.get('kind'), a.get('section')) for a in atoms)

    moved = []
    for atom in atoms:
        if atom.get('section') == EXERCISE and atom.get('kind') == '讲解条目':
            atom['kind'] = '过关检测题'
            atom['kindRestoredFrom'] = '讲解条目'
            atom['kindRestoredWhy'] = (
                'carve_engine 把无答案的编号项一律降级为讲解条目(化学册形状:答案来自解析版)。'
                '本册无解析版,该判据在此不成立。按引擎自己算出的 section=过关检测 还原。')
            moved.append(atom['locator'])

    after = Counter((a.get('kind'), a.get('section')) for a in atoms)

    # 门:两侧都要干净
    leftover = [a['locator'] for a in atoms
                if a.get('section') == EXERCISE and a.get('kind') == '讲解条目']
    outside = [a['locator'] for a in atoms
               if a.get('section') != EXERCISE and a.get('kind') == '过关检测题']
    # 破坏性自证:栏目外的讲解条目一条都不能被动过
    expository = [a for a in atoms
                  if a.get('section') != EXERCISE and a.get('kind') == '讲解条目']

    failures = []
    if leftover:
        failures.append({'why': '过关检测内仍有讲解条目', 'locators': leftover[:10]})
    if outside:
        failures.append({'why': '栏目外出现过关检测题', 'locators': outside[:10]})
    if not moved:
        failures.append({'why': '一条都没还原,判据可能恒假'})

    status = 'pass' if not failures else 'fail'
    report = {
        'schemaVersion': 'chengziclass.gate-exercise-items.v1',
        'gate': 'GATE_EXERCISE_ITEMS_ARE_QUESTIONS',
        'status': status,
        'rule': 'section==过关检测 且 kind==讲解条目 → kind=过关检测题;栏目外一条都不动',
        'restoredCount': len(moved),
        'expositoryLeftUntouched': len(expository),
        'expositorySamples': [(a.get('stem') or '')[:24] for a in expository[:4]],
        'questionsWithOptions': sum(1 for a in atoms
                                    if a.get('kind') == '过关检测题' and a.get('options')),
        'questionsWithSubQuestions': sum(1 for a in atoms
                                         if a.get('kind') == '过关检测题' and a.get('subQuestions')),
        'optionFiguresInExercise': sum(
            o.get('images', 0) for a in atoms if a.get('kind') == '过关检测题'
            for o in (a.get('options') or [])),
        'before': {f'{k[0]}|{k[1]}': v for k, v in before.items()},
        'after': {f'{k[0]}|{k[1]}': v for k, v in after.items()},
        'failures': failures,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    OUT.write_text(json.dumps(atoms, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'门 {status}:还原 {len(moved)} 条,栏目外 {len(expository)} 条讲解正文原封不动')
    print(f'  还原后带选项的题 {report["questionsWithOptions"]},带小问的 {report["questionsWithSubQuestions"]}')
    print(f'  过关检测内的选项图 {report["optionFiguresInExercise"]} 张')
    print(f'  栏目外讲解正文样例: {report["expositorySamples"]}')
    if failures:
        print('  失败:', json.dumps(failures, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
