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
# 解析版(教师版)。册里没有就是 None——有的册天然没有。
_ANN = CHAIN.resolve('source.annotated')
ANNOTATED_SRC = _ANN[0] if _ANN else None
ANNOTATED_WORK = CHAIN.path_for('source.annotated.stripped')
REPORT = CHAIN.path_for('gate.no-zero-width')
REGISTRY = CHAIN.path_for('registry')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def clean_one(SRC, WORK, label):
    """清一份源的零宽/格式控制字符,返回报告片段。

    原卷与解析版走同一段逻辑;两份各写一遍必然漂。
    """
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
        # ★首版这里直接 return,**不生成工作副本**——于是下一步因缺 source.stripped
        # 而阻塞,而本步报的是「门通过」。一条源恰好干净就会卡住整条链,
        # 而报告说一切正常。工作副本是本步的产物,不论清没清都必须落地。
        print(f'{label}:没有零宽/格式控制字符,无需清洗;仍照常产出工作副本')
        shutil.copyfile(SRC, WORK)
        return {
            'source': str(SRC), 'sourceSha256': src_hash,
            'workingCopy': str(WORK), 'workingCopySha256': sha256(WORK),
            'removedByCodepoint': {}, 'removedTotal': 0,
            'destructiveSelfProof': {
                'verdict': 'not-applicable — 源本就没有该类字符,无从自证',
            },
        }

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

    return {
        'source': str(SRC),
        'sourceSha256': src_hash,
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


def main():
    parts = {'原卷': clean_one(SRC, WORK, '原卷')}
    if ANNOTATED_SRC is not None:
        parts['解析版'] = clean_one(Path(str(ANNOTATED_SRC)),
                                   Path(str(ANNOTATED_WORK)), '解析版')
    report = {
        'schemaVersion': 'chengziclass.gate-no-zero-width.v1',
        'gate': 'GATE_NO_ZERO_WIDTH',
        'status': 'cleaned',
        'sources': parts,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    registry = {
        'schemaVersion': 'chengziclass.source-registry.v1',
        'note': ('本步只写「整册一档」的粗粒度 registry;真正按讲一档(含解析版配对的 '
                 'annotated_word)由 s4b-split-lessons 覆写。'
                 '首版这里的 note 写死「物理暑假讲义无解析版」——那是当时某一册的事实,'
                 '不是本步能知道的事。'),
        'documents': [{
            'role': 'original_word',
            'lesson': '整册',
            'period': None,
            'physicalPath': str(WORK),
            'sha256': parts['原卷']['workingCopySha256'],
            'derivedFrom': {'path': str(SRC), 'sha256': parts['原卷']['sourceSha256'],
                            'transform': 'GATE_NO_ZERO_WIDTH 清除 Cf 类字符'},
        }] + ([{
            'role': 'annotated_word',
            'lesson': '整册',
            'period': None,
            'physicalPath': str(ANNOTATED_WORK),
            'sha256': parts['解析版']['workingCopySha256'],
            'derivedFrom': {'path': str(ANNOTATED_SRC),
                            'sha256': parts['解析版']['sourceSha256'],
                            'transform': 'GATE_NO_ZERO_WIDTH 清除 Cf 类字符'},
        }] if '解析版' in parts else []),
    }
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')

    for label, part in parts.items():
        print(f'\n{label}: 清除 {part["removedTotal"]} 个格式控制字符')
        for k, v in sorted(part['removedByCodepoint'].items(), key=lambda kv: -kv[1]):
            print(f'   {k}: {v}')
        proof = part['destructiveSelfProof']
        if 'numberedParagraphsBeforeClean' in proof:
            print(f'   破坏性自证:编号项命中 {proof["numberedParagraphsBeforeClean"]}'
                  f' → {proof["numberedParagraphsAfterClean"]}'
                  f'(恢复 {proof["recovered"]} 段) {proof["verdict"]}')
        else:
            print(f'   破坏性自证:{proof["verdict"]}')
        print(f'   工作副本: {part["workingCopy"]}')


if __name__ == '__main__':
    main()
