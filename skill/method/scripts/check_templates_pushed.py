#!/usr/bin/env python3
"""GATE_TEMPLATE_PUSHED:册里的模板表与题库里注册的那份必须一致。

**为什么。** 模板表在册里改,题库按**注册进去的那一份**跑。两者之间靠人记得去推——
2026-08-21 实测:讲义那张表加了 linkQuestionsToConcepts=false,库里那份还没有,
于是「题不挂概念」这条裁决对 20 讲**根本没生效**,而一切看起来都正常
(边是清过的,数字是 0,直到下一次重导才会长回来)。

**改了没推 = 裁决只写在纸上。** 而且它的失败方式是延迟的:清库之后当场是绿的,
下一次导入才翻车,那时候没人会想到是模板没推。

用法(不进链——它要连题库,链是离线的):
    check_templates_pushed.py --api http://127.0.0.1:28000 --token-from-env \\
        --pair <templateId>=<本地 schema 路径> [...]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--api', required=True)
    ap.add_argument('--token', required=True, help='Bearer 令牌(由调用方取得,本脚本不碰凭据存储)')
    ap.add_argument('--pair', action='append', required=True,
                    help='templateId=本地schema路径')
    ap.add_argument('--report', type=Path)
    args = ap.parse_args()

    rows, problems = [], []
    for pair in args.pair:
        tid, _, path = pair.partition('=')
        local = json.loads(Path(path).read_text(encoding='utf-8'))
        req = urllib.request.Request(f'{args.api}/api/carve-templates/{tid}')
        req.add_header('Authorization', 'Bearer ' + args.token)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                remote = json.loads(resp.read().decode())
        except Exception as exc:
            problems.append({'templateId': tid, 'code': 'UNREACHABLE', 'why': str(exc)[:120]})
            continue
        same = canon(remote.get('schema')) == canon(local)
        row = {'templateId': tid, 'localPath': path, 'inSync': same}
        if not same:
            a = (remote.get('schema') or {}).get('quizImport') or {}
            b = local.get('quizImport') or {}
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            row['quizImportDiffKeys'] = diff
            problems.append({'templateId': tid, 'code': 'NOT_PUSHED',
                             'why': '册里改了,题库里注册的那份还是旧的——裁决只写在纸上',
                             'quizImportDiffKeys': diff})
        rows.append(row)

    status = 'pass' if not problems else 'fail'
    report = {'schemaVersion': 'chengziclass.gate-template-pushed.v1',
              'gate': 'GATE_TEMPLATE_PUSHED',
              'rule': '册里的模板表与题库里注册的那份逐字一致。',
              '★whyItMatters': ('失败方式是**延迟的**:清库之后当场是绿的,'
                                '下一次导入才翻车,那时候没人会想到是模板没推。'),
              'status': status, 'templates': rows, 'problems': problems}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding='utf-8')
    print(f'GATE_TEMPLATE_PUSHED: {status}')
    for r in rows:
        mark = '  ' if r['inSync'] else '★ '
        print(f'   {mark}{r["templateId"]:34} {"一致" if r["inSync"] else "未推:" + str(r.get("quizImportDiffKeys"))}')
    for p in problems:
        if p['code'] == 'UNREACHABLE':
            print(f'   ★ {p["templateId"]} 连不上:{p["why"]}')
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
