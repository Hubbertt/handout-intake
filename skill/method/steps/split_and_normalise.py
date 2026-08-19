#!/usr/bin/env python3
"""按讲拆档 + 栏目横幅正规化。

两件事一起做,理由是同一个:让物理源与化学册同构。
  1) 化学是「一个课题一个 docx」,物理是 19 讲挤在一个文件里。拆开后 document=讲,
     引擎的 section/subsection/node 三级才对得上。
  2) 栏目横幅 概｜念｜构｜建 / 深｜研｜精｜炼 装在绘图对象里,而且 Word 把它在
     mc:Choice 与 mc:Fallback 两个分支各写一份。承载段的「自身文本」是被打散的残文
     (｜念｜构｜建概｜念｜｜),既进不了 banner_of(那条按图片哈希查表),也匹配不上
     subModules 的精确文本。正规化成普通段落后,probe 才等于横幅原文。

不改引擎一行:引擎是共享代码且租约在别人手上。修在数据侧。

门:每讲必须恰好 2 个横幅(概念构建、深研精练),数目不对就报错,不猜。
"""
import json
import re
import shutil
import zipfile
from pathlib import Path

from lxml import etree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'

from _bootstrap import chain_from_argv  # noqa: E402
from _lessons import check_monotonic, pick_headings  # noqa: E402

CHAIN = chain_from_argv(__doc__)
SRC = CHAIN.path_for('source.stripped')
OUTDIR = CHAIN.dir_for('lessons')
REGISTRY = CHAIN.path_for('registry')
VOLUME_THEME = str(CHAIN.bindings.get('theme') or '').strip()
# theme 的来由与 theme 本身同等重要:量出来的值必须带着它的量法一起走,
# 否则下一个人只看到一个凭空的字符串。P6 对账时它正是 registry 唯一的残余差异。
VOLUME_THEME_EVIDENCE = str(CHAIN.bindings.get('themeEvidence') or '').strip()
REPORT = CHAIN.path_for('gate.split-banner')
# 解析版(教师版)。册里没有就是 None——有的册天然没有,那不是错误。
# 它是「带答案解析」的唯一入口:carve_engine 的答案从 annotated_word 伙伴档取,
# 没有伙伴档时每道题都记 MISSING_ANSWER(2026-08-20 教师版首跑:158 题全中、withAnswer=0)。
_ANNOTATED = CHAIN.resolve('source.annotated.stripped')
ANNOTATED_SRC = _ANNOTATED[0] if _ANNOTATED else None
ANNOTATED_OUTDIR = CHAIN.dir_for('lessons.annotated')
# 范围由 bindings.scope.lessons 给,None = 源里有几讲做几讲。首版写死 [10..14]。
WANT = CHAIN.scope_lessons()
# 横幅清单的真源是**模板表的 subModules**,不是这里。
# 首版这里写死 ['概｜念｜构｜建', '深｜研｜精｜炼'],与模板表里同名字段并存——
# 同一事实两处,而且代码那一处赢。2026 新版教材加了第三个栏目「情｜境｜启｜思」
# (实测教师版与学生版各 40 处 = 20 讲 × 2 份副本;旧母本 0 处),模板表可以改,
# 代码里那份不改就永远拦着。现在只留模板表一处。
_SCHEMA = json.loads(Path(str(CHAIN.only('schema'))).read_text(encoding='utf-8'))
EXPECTED_BANNERS = list(_SCHEMA.get('subModules') or [])
if not EXPECTED_BANNERS:
    raise SystemExit('模板表没有声明 subModules(栏目横幅)。'
                     '拒绝用一个空清单去判——那会把每一个横幅都判成未知。')


def banner_text(paragraph):
    """横幅原文:取 mc:Choice 那一份,跳过 Fallback 兼容副本。"""
    for container in paragraph.iter(W + 'txbxContent'):
        node = container
        fallback = False
        while node is not None:
            if node.tag == MC + 'Fallback':
                fallback = True
                break
            node = node.getparent()
        if fallback:
            continue
        text = ''.join(t.text or '' for t in container.iter(W + 't'))
        text = re.sub(r'[\s\xa0]+', ' ', text).strip()
        if text:
            return text
    return None


def plain_paragraph(text, template):
    """造一个只含这段文字的普通段落,沿用承载段的 pPr(保住对齐与间距)。"""
    p = etree.Element(W + 'p', nsmap={'w': W[1:-1]})
    ppr = template.find(W + 'pPr')
    if ppr is not None:
        keep = etree.SubElement(p, W + 'pPr')
        for child in ppr:
            if child.tag in (W + 'rPr',):
                continue
            keep.append(etree.fromstring(etree.tostring(child)))
    run = etree.SubElement(p, W + 'r')
    node = etree.SubElement(run, W + 't')
    node.text = text
    node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    return p


def _make_zip_deterministic(path) -> None:
    """把 docx 重写为确定性 zip:所有条目 mtime 固定、顺序固定。

    ★P6 两次空手跑对账查出的根因。分讲 docx 内容完全相同,字节却不同——
    zip 条目带 mtime。而对象身份 objectId 的前缀取的是**源文档 sha256 的前 12 位**,
    于是 sha 不同 → objectId 不同 → 整条蓝图链、object-manifest、word、pdf 全跟着不同。

    **流水线拿一个不可复现的哈希当对象身份。** 内容一样而身份不一样,
    「同一个对象」在两次跑里就成了两个对象——这不是比对方法的问题,是产物的问题。

    固定为 1980-01-01(zip 纪元下限),不用「当前时间」也不用源文件 mtime:
    前者每次不同,后者随拷贝变。
    """
    import shutil, zipfile as _zf
    from pathlib import Path as _P
    src = _P(path)
    tmp = src.with_suffix(src.suffix + ".det")
    with _zf.ZipFile(src) as zin:
        names = sorted(zin.namelist())
        with _zf.ZipFile(tmp, "w", _zf.ZIP_DEFLATED) as zout:
            for name in names:
                info = _zf.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = _zf.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zout.writestr(info, zin.read(name))
    shutil.move(str(tmp), str(src))


def split_one(SRC, outdir, label):
    """把一份源按讲切开。原卷与解析版走**同一段**逻辑——
    两份各写一遍必然漂,今天已经在讲边界与横幅清单上各栽过一次。"""
    outdir.mkdir(parents=True, exist_ok=True)
    print(f'切分 {label}: {SRC.name}')
    package = zipfile.ZipFile(SRC)
    root = etree.fromstring(package.read('word/document.xml'))
    body = root.find(W + 'body')
    children = list(body)
    sectpr = body.find(W + 'sectPr')

    # 讲边界(document order)
    # 讲边界与 fingerprint / census 共用 _lessons 的同一条判据。
    # 首版这里独立写了一遍 `pStyle == '3'`——同一条规则三处各写一份,
    # 而它只对某一份源文件成立。
    def _rows():
        for index, child in enumerate(children):
            if child.tag != W + 'p':
                continue
            text = ''.join(t.text or '' for t in child.iter(W + 't')).strip()
            field = any(node.tag in (W + 'instrText', W + 'fldChar')
                        for node in child.iter())
            yield index, text, field

    bounds = pick_headings(_rows())
    for problem in check_monotonic(bounds):
        print(f'  [讲号存疑] {problem}')
    if not bounds:
        raise SystemExit('源里没找到任何讲标题,拒绝切分。')

    spans = {}
    for position, (start, number, title) in enumerate(bounds):
        end = bounds[position + 1][0] if position + 1 < len(bounds) else len(children)
        spans[number] = (start, end, title)

    wanted = [n for _, n, _ in bounds] if WANT is None else list(WANT)
    results = []
    failures = []
    for number in wanted:
        if number not in spans:
            failures.append({'lesson': number, 'why': '未找到该讲的正文标题'})
            continue
        start, end, title = spans[number]
        slice_ = children[start:end]

        seen = []
        replaced = 0
        rebuilt = []
        for node in slice_:
            if node.tag == W + 'p' and node.find('.//' + W + 'txbxContent') is not None:
                text = banner_text(node)
                if text in EXPECTED_BANNERS:
                    seen.append(text)
                    rebuilt.append(plain_paragraph(text, node))
                    replaced += 1
                    continue
                failures.append({'lesson': number,
                                 'why': f'承载文本框的段落内容不是已知横幅: {text!r}'})
            rebuilt.append(node)

        # 门:每讲的横幅集合必须与模板表声明的一致(不是「至少」,是「就是」)
        if sorted(seen) != sorted(EXPECTED_BANNERS):
            failures.append({'lesson': number, 'why': f'横幅数目/内容不符,实测 {seen}'})
            continue

        newroot = etree.fromstring(etree.tostring(root))
        newbody = newroot.find(W + 'body')
        for child in list(newbody):
            newbody.remove(child)
        for node in rebuilt:
            newbody.append(etree.fromstring(etree.tostring(node)))
        if sectpr is not None:
            newbody.append(etree.fromstring(etree.tostring(sectpr)))

        # 讲号补零:册只做 10-14 时看不出问题,做满 20 讲时 A1/A2 会排到 A10 后面。
        # 「只在小范围里试过」的默认值,扩大范围时才露馅。
        target = outdir / f'A{number:02d}-{title.replace(" ", "")}.docx'
        # 每档新开一个读句柄:复用同一个 ZipFile 跨多次全量重读会撞 CRC 校验状态。
        # 且不复用源的 ZipInfo——它带着原条目的 CRC 与长度。
        with zipfile.ZipFile(SRC) as source, \
                zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
            for name in source.namelist():
                if name == 'word/document.xml':
                    continue
                out.writestr(name, source.read(name))
            out.writestr('word/document.xml',
                         etree.tostring(newroot, xml_declaration=True,
                                        encoding='UTF-8', standalone=True))
        results.append({'lesson': f'第A{number:02d}讲', 'title': title,
                        'path': str(target), 'bodyChildren': len(rebuilt),
                        'bannersNormalised': replaced})
        print(f'  第A{number:02d}讲 {title}: {len(rebuilt)} 个 body 子元素, 横幅正规化 {replaced} 个')

    return results, failures


def main():
    results, failures = split_one(Path(str(SRC)), OUTDIR, '原卷')
    annotated_results, annotated_failures = [], []
    if ANNOTATED_SRC is not None:
        annotated_results, annotated_failures = split_one(
            Path(str(ANNOTATED_SRC)), Path(str(ANNOTATED_OUTDIR)), '解析版')
        # 配对判据:两侧讲号集合必须相同。差一个就不许静默——
        # 少配到的那一讲会整讲没有答案,而链照样能跑完。
        left = {r['lesson'] for r in results}
        right = {r['lesson'] for r in annotated_results}
        if left != right:
            annotated_failures.append({
                'why': f'原卷与解析版的讲号不一致:只在原卷 {sorted(left - right)}、'
                       f'只在解析版 {sorted(right - left)}'})

    wanted_n = len(results)
    status = ('pass' if not failures and not annotated_failures and wanted_n
              and (ANNOTATED_SRC is None or len(annotated_results) == wanted_n)
              else 'fail')
    REPORT.write_text(json.dumps({
        'schemaVersion': 'chengziclass.gate-split-and-banner.v1',
        'gate': 'GATE_LESSON_SPLIT_AND_BANNER_COUNT',
        'rule': ('每讲的栏目横幅集合必须与模板表 subModules 声明的一致(是「就是」不是「至少」),'
                 '且必须能拆出全部目标讲;册若有解析版,两侧讲号必须一一对应。'),
        'expectedBanners': EXPECTED_BANNERS,
        'status': status,
        'lessons': results,
        'annotatedLessons': annotated_results,
        'failures': failures + annotated_failures,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    if status != 'pass':
        print('门未通过:', json.dumps(failures + annotated_failures, ensure_ascii=False))
        raise SystemExit(1)

    registry = {
        'schemaVersion': 'chengziclass.source-registry.v1',
        'note': ('一讲一档,与化学册「一课题一档」同构。每档由已清零宽的工作副本切出,'
                 '横幅已正规化为普通段落。册若绑定了解析版,同一讲另写一条 annotated_word,'
                 '答案由 carve_engine 从伙伴档取——**这是「带答案解析」的唯一接线处**。'),
        # ★P6 空手复现抓出:本步只写 physicalPath,而下游 build_blueprint_from_atoms
        # 读的是 path 与 theme —— 基线里这两键是**手工补进去的**,空工作区里不存在,
        # 下游直接 KeyError。手工补过一次在工序表里没有痕迹,下一个人必在同处失败。
        #   path  与 physicalPath 逐字相同,是纯别名,补上即可。
        #   theme 是册级常量(本册「第四章 光」),原先按图注编号人工量出;
        #         它不是本步能推出来的事实,故取自册级绑定 theme——
        #         **不猜、也不留空**:绑定里没有就如实报缺,让人去填,
        #         而不是写一个看着合理的值让它悄悄进成品。
        'documents': [{'role': 'original_word', 'lesson': item['lesson'], 'period': None,
                       'physicalPath': item['path'], 'path': item['path'],
                       **({'theme': VOLUME_THEME} if VOLUME_THEME else {})}
                      for item in results]
                     + [{'role': 'annotated_word', 'lesson': item['lesson'], 'period': None,
                         'physicalPath': item['path'], 'path': item['path'],
                         **({'theme': VOLUME_THEME} if VOLUME_THEME else {})}
                        for item in annotated_results],
    }
    if annotated_results:
        registry['annotatedNote'] = (
            f'解析版 {len(annotated_results)} 讲,与原卷按讲号一一配对(门已校验集合相同)。'
            'carve_engine 只在 partner 存在时才读【答案】【详解】;'
            '在此之前物理册的 withAnswer 恒为 0,而链照样跑完——'
            '「有答案的源」与「答案进了成品」是两件事。')
    if VOLUME_THEME_EVIDENCE:
        registry['themeEvidence'] = VOLUME_THEME_EVIDENCE
    if not VOLUME_THEME:
        registry['_missingTheme'] = ('册级绑定没有 volume.theme。下游 build_blueprint 需要它。'
                                     '请在 .handout-intake/volumes/<册>/bindings.json 里补 '
                                     '"theme": "第四章 光"。')
    for item in results:
        _make_zip_deterministic(item['path'])
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n门通过。registry 已改为 {len(results)} 档: {REGISTRY}')


if __name__ == '__main__':
    main()
