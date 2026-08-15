#!/usr/bin/env python3
"""把「共享私有规范」与「物理模板特有」投影成一份合并 mapping。

不复制:共享部分在构建时从真源读出来,合并件是投影,带两侧 sha256。
真源哪天上提成独立文件,只改物理表里 shared.sourceOfTruth 一行。
"""
import hashlib
import json
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SHARED = CHAIN.only('mapping.shared')
OWN = CHAIN.path_for('mapping.own')
OUT = CHAIN.path_for('mapping')

# 从共享真源整节取用的键
SHARED_SECTIONS = ['target', 'principles', 'typography']
# runs / notPrinted 只取与出版社无关的条目
SHARED_RUN_KEYS = ['显式下标', '显式上标', '显式高亮', '底纹', '下划线空白', '无装饰']
SHARED_NOTPRINTED_KEYS = ['答案', '解析', '页码域', 'Fallback副本', '空白占位图']


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shared = json.loads(SHARED.read_text(encoding='utf-8'))
    own = json.loads(OWN.read_text(encoding='utf-8'))

    merged = {'schemaVersion': own['schemaVersion'], 'id': own['id']}
    taken = []
    for key in SHARED_SECTIONS:
        if key in shared:
            merged[key] = shared[key]
            taken.append(key)

    merged['runs'] = {k: v for k, v in (shared.get('runs') or {}).items()
                      if k in SHARED_RUN_KEYS}
    merged['notPrinted'] = {k: v for k, v in (shared.get('notPrinted') or {}).items()
                            if k in SHARED_NOTPRINTED_KEYS}

    # 模板特有覆盖在上层
    for key, value in own.items():
        if key in ('schemaVersion', 'id', 'layering', 'targetVocabularyDrift'):
            continue
        if key in ('runs', 'notPrinted') and isinstance(value, dict):
            merged[key].update({k: v for k, v in value.items()
                                if k not in ('note', 'status', 'inheritsSharedEntries')})
            continue
        merged[key] = value

    merged['_derived'] = {
        'warning': '这是投影,不是真源。改它无效,改真源。',
        'sharedFrom': str(SHARED), 'sharedSha256': sha256(SHARED),
        'sharedSectionsTaken': taken,
        'sharedRunKeys': SHARED_RUN_KEYS,
        'sharedNotPrintedKeys': SHARED_NOTPRINTED_KEYS,
        'templateFrom': str(OWN), 'templateSha256': sha256(OWN),
    }
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'共享取用整节: {taken}')
    print(f'共享 runs {len(merged["runs"])} 条, notPrinted {len(merged["notPrinted"])} 条')
    print(f'模板 blocks {len(merged.get("blocks") or {})} 个角色')
    print(f'合并件: {OUT}')


if __name__ == '__main__':
    main()
