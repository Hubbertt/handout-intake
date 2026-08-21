#!/usr/bin/env python3
"""s4h-repair-media:让 media 目录里的每张图都**放得进 Word**。

两件事,同一个目的:

  GATE_JPEG_DENSITY_VALID —— 修 JFIF 密度元数据(下面详述)
  GATE_WMF_RENDERED       —— WMF/EMF 渲染成同名 PNG(见 render_wmf.py)

★合在一步里,是因为代次约定只有两代:media 与 media'。
  这一步的 id 本来就是 repair-media,产出的就是 media',
  「让图放得进去」是同一件事的两个方面。

--- GATE_JPEG_DENSITY_VALID:修 JFIF 密度元数据,不重编码。

两种不合规,都会让 python-docx 放不下这张图:
  A. 有 APP0 但 Xdensity/Ydensity = 0 —— JFIF 规定密度必须非零。
     python-docx 算 Inches(px_width / horz_dpi) 时除零。
  B. 完全没有 APP0 段(SOI 直接接 DQT)—— python-docx 判不出 DPI,
     直接 UnrecognizedImageError。

两种都只动文件头:A 改 4 个字节,B 插入 18 个字节的标准 APP0。
**熵编码数据一个字节不碰**,像素逐位相同——这不是重编码,是补一段缺失的声明。

密度取 96 dpi:Word 的默认屏幕密度,且成品里图宽由 typography.figureScale 按
「源绝对尺寸 × 规范字号 ÷ 源段落字号」算,不依赖这个值。这里只是让它合法。
"""
import hashlib
import json
import struct
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402
import render_wmf  # noqa: E402

CHAIN = chain_from_argv(__doc__)
MEDIA = CHAIN.dir_for('media')
BACKUP = MEDIA.parent / (MEDIA.name + '-before-density-repair')
REPORT = CHAIN.path_for('gate.jpeg-density')
DPI = 96
APP0 = (b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x01'
        + struct.pack('>HH', DPI, DPI) + b'\x00\x00')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def diagnose(data):
    """返回 ('ok'|'zero-density'|'no-app0', APP0 起始偏移或 None)。"""
    if data[:2] != b'\xff\xd8':
        return 'not-jpeg', None
    if data[2:4] != b'\xff\xe0':
        return 'no-app0', None
    payload = 6
    if data[payload:payload + 5] != b'JFIF\x00':
        return 'no-app0', None
    x, y = struct.unpack('>HH', data[payload + 8:payload + 12])
    return ('ok' if x and y else 'zero-density'), payload


def main():
    BACKUP.mkdir(parents=True, exist_ok=True)
    repaired, checked = [], 0
    for path in sorted(MEDIA.glob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.jpg', '.jpeg'):
            continue
        checked += 1
        data = path.read_bytes()
        kind, payload = diagnose(data)
        if kind == 'ok':
            continue
        before = sha(data)
        (BACKUP / path.name).write_bytes(data)
        if kind == 'zero-density':
            fixed = bytearray(data)
            struct.pack_into('>HH', fixed, payload + 8, DPI, DPI)
            fixed = bytes(fixed)
            pixels_untouched = fixed[payload + 12:] == data[payload + 12:]
            how = f'改 4 字节:Xdensity/Ydensity 0 → {DPI}'
        elif kind == 'no-app0':
            fixed = data[:2] + APP0 + data[2:]
            pixels_untouched = fixed[2 + len(APP0):] == data[2:]
            how = f'插入 18 字节标准 JFIF APP0(units=dpi, {DPI}×{DPI})'
        else:
            repaired.append({'file': path.name, 'kind': kind, 'action': 'skipped-not-jpeg'})
            continue
        path.write_bytes(fixed)
        repaired.append({'file': path.name, 'problem': kind, 'how': how,
                         'shaBefore': before, 'shaAfter': sha(fixed),
                         'bytesBefore': len(data), 'bytesAfter': len(fixed),
                         'entropyDataUntouched': pixels_untouched})

    # 破坏性自证:修完之后 python-docx 必须能读出非零 dpi
    verify = []
    try:
        from docx.image.image import Image as DImage
        for item in repaired:
            image = DImage.from_file(str(MEDIA / item['file']))
            item['horzDpiAfter'] = image.horz_dpi
            item['vertDpiAfter'] = image.vert_dpi
            verify.append(bool(image.horz_dpi and image.vert_dpi))
    except ImportError:
        verify = None

    failures = []
    if any(not item.get('entropyDataUntouched') for item in repaired):
        failures.append('有文件的熵编码数据被改动,不是纯头部修复')
    if verify is not None and not all(verify):
        failures.append('修复后 python-docx 仍读不到非零 dpi')
    status = 'pass' if not failures else 'fail'

    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-jpeg-density.v1',
        'gate': 'GATE_JPEG_DENSITY_VALID',
        'status': status,
        'why': ('JFIF 密度非法会让 python-docx 放不下图:密度为 0 触发除零,'
                '缺 APP0 段直接 UnrecognizedImageError。'),
        'principle': '只补声明,不动像素。熵编码数据逐字节相同,已逐个断言。',
        'dpiWritten': DPI,
        'jpegChecked': checked,
        'repaired': len(repaired),
        'backupDir': str(BACKUP),
        'details': repaired,
        'verifiedWithPythonDocx': verify is not None,
        'failures': failures,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'门 {status}:检查 JPEG {checked} 张,修复 {len(repaired)} 张')
    for item in repaired:
        print(f"   {item['file']}  {item.get('problem')}  →  {item.get('how')}")
        print(f"      像素未动: {item.get('entropyDataUntouched')}  "
              f"修复后 dpi: ({item.get('horzDpiAfter')},{item.get('vertDpiAfter')})")
    if failures:
        print('  失败:', failures)
        raise SystemExit(1)




def _render_metafiles() -> int:
    """WMF/EMF → PNG。放在密度修复之后:两者都写 media,报告分开落盘。"""
    report = render_wmf.run(MEDIA)
    out = REPORT.parent / "gate_wmf_rendered.json"
    out.write_text(json.dumps({"gate": "GATE_WMF_RENDERED", **report},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"gate": "GATE_WMF_RENDERED",
                      **{k: v for k, v in report.items() if k != "samples"}},
                     ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == '__main__':
    # 两件事都要做完:密度修复先跑(它可能改写 JPEG 头),再渲染 metafile。
    # 任一失败即非零退出——渲染失败而静默留着 WMF,
    # 等于把炸点推到 Word 构建,那时报的是「认不出的图片」而不是「这张渲染失败」。
    _code = main() or 0
    raise SystemExit(_code or _render_metafiles())
