#!/usr/bin/env python3
"""GATE_NO_ZERO_WIDTH + 建 registry。

源原始稿是冻结证据,一个字节都不动。清洗写成工作副本,清除数逐一记账。
"""
import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SRC = CHAIN.only('source')
WORK = CHAIN.path_for('source.stripped')
REPORT = CHAIN.path_for('gate.no-zero-width')
REGISTRY = CHAIN.path_for('registry')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    WORK.parent.mkdir(parents=True, exist_ok=True)
    src_hash = sha256(SRC)

    zin = zipfile.ZipFile(SRC)
    xml = zin.read('word/document.xml').decode('utf-8')

    # 只清 Unicode 类别 Cf(格式控制) 里出现在文本中的字符。
    # 逐字符统计,不做整体 replace——要能说出清了哪些、各几个。
    counts = {}
    for ch in set(xml):
        if unicodedata.category(ch) == 'Cf':
            n = xml.count(ch)
            if n:
                counts[f'U+{ord(ch):04X} {unicodedata.name(ch, "?")}'] = n
    if not counts:
        print('源里没有零宽/格式控制字符,门通过且无需清洗')
        return

    cleaned = ''.join(c for c in xml if unicodedata.category(c) != 'Cf')
    removed = len(xml) - len(cleaned)

    # 破坏性自证:清洗后行首判据应能接住原来接不住的段
    T = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)

    def lead_numbered(doc):
        paras = re.findall(r'<w:p[ >].*?</w:p>', doc, re.S)
        return sum(1 for p in paras
                   if re.match(r'^\d{1,2}\s*[．.、]\s*\S', ''.join(T.findall(p)).strip()))

    before, after = lead_numbered(xml), lead_numbered(cleaned)

    # 重打包:除 document.xml 外逐项原样拷贝
    with zipfile.ZipFile(WORK, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'word/document.xml':
                data = cleaned.encode('utf-8')
            zout.writestr(item, data)

    report = {
        'schemaVersion': 'chengziclass.gate-no-zero-width.v1',
        'gate': 'GATE_NO_ZERO_WIDTH',
        'status': 'cleaned',
        'sourceOriginal': str(SRC),
        'sourceOriginalSha256': src_hash,
        'sourceOriginalUntouched': True,
        'workingCopy': str(WORK),
        'workingCopySha256': sha256(WORK),
        'removedByCodepoint': counts,
        'removedTotal': removed,
        'destructiveSelfProof': {
            'claim': '段首零宽字符会打断 ^\\d{1,2}[．.、] 编号项判据',
            'numberedParagraphsBeforeClean': before,
            'numberedParagraphsAfterClean': after,
            'recovered': after - before,
            'verdict': 'pass' if after > before else 'inconclusive — 清洗未改变命中数,该主张在本册未被证实',
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    registry = {
        'schemaVersion': 'chengziclass.source-registry.v1',
        'note': '物理暑假讲义无解析版,故无 annotated_word 条目。carve_engine 只在 partner 存在时读答案标记。',
        'documents': [{
            'role': 'original_word',
            'lesson': '八上物理暑假',
            'period': None,
            'physicalPath': str(WORK),
            'sha256': report['workingCopySha256'],
            'derivedFrom': {'path': str(SRC), 'sha256': src_hash,
                            'transform': 'GATE_NO_ZERO_WIDTH 清除 Cf 类字符'},
        }],
    }
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'清除 {removed} 个格式控制字符:')
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'   {k}: {v}')
    print(f'\n破坏性自证:编号项命中 {before} → {after}(恢复 {after - before} 段)')
    print(f'工作副本: {WORK}')
    print(f'registry: {REGISTRY}')


if __name__ == '__main__':
    main()
