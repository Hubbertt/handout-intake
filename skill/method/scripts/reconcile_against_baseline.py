#!/usr/bin/env python3
"""P6 的验收工具:把一次空手复现与基线逐产物对账。

**比什么,才算「对上」。** 逐字节比会永远对不上,而且对不上的理由与工序无关:

  JSON 的键序不是语义      registry 里 theme 与 path 的前后位置不同,内容逐字相同
  docx/pdf 是 zip          条目 mtime 每次不同,同样的内容压出不同的字节
  工作区根、时间戳         两次跑必然不同
  内嵌 sha256              上游一变全跟着变,不是独立事实

所以按**规范化后的内容**比:JSON 解析后 sort_keys 序列化;zip 比其内部条目名与
各条目内容的 hash(丢弃 mtime);其余按文本抹掉工作区根与时间戳。

**这不是放宽判据,是把判据对准要判的东西。** 放宽是「差不多就算过」;
对准是「不把非语义的差异算成语义差异」——两者的区别在于:
前者会让真差异混进来,后者不会。故本工具对每一类都写明比法,
且任何无法归类的差异一律照原样报为 different,不猜。

用法:
  reconcile_against_baseline.py --baseline <基线工作区> --repro <复现工作区> --volume V
退出码 0=全部对上 1=有产物对不上
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chain import Chain  # noqa: E402

TS = re.compile(r'"(?:generatedAt|ranAt|builtAt|at|timestamp|openedAt|reviewedAt|'
                r'elapsedSec|initialisedAt)"\s*:\s*("[^"]*"|[0-9.]+)')
SHA = re.compile(r'\b[0-9a-f]{64}\b')
# ISO 时间不只出现在 JSON 键上,也出现在**字符串里**——报告会把子进程的输出
# 整段捕获成一个字符串,里面的 generatedAt 就成了普通文本。只按键归一会漏掉它,
# 而漏掉的表现是「两次跑产物不同」,看起来像流水线不可复现。
ISO = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?')
# 每次跑的运行标记:临时沙盒文件名带 -YYYYMMDD-HHMMSS-<随机hex>。
# 它记录的是「这次跑是哪一次」,不是「产物是什么」。报告里留着它,
# 两次跑就永远不相等——而那看起来像流水线不可复现。
RUNSTAMP = re.compile(r'-\d{8}-\d{6}-[0-9a-f]{6,}')


def canon(path: Path, root: Path) -> tuple[str, str]:
    """→ (比法, 规范化后的 hash)。比法一并返回,便于报告里写清楚按什么比的。"""
    raw = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        # Word 导出的 PDF 每次带不同的 CreationDate/ID。比**渲染内容**:
        # 页数、页面尺寸、每页文本、字体集合——这些才是「这份成品是不是同一份」。
        try:
            import fitz
            doc = fitz.open(path)
            sig = [str(doc.page_count)]
            for i in range(doc.page_count):
                pg = doc[i]
                sig.append(f"{pg.rect.width:.1f}x{pg.rect.height:.1f}")
                sig.append(pg.get_text("text"))
                sig.append("|".join(sorted(str(f[3]) for f in pg.get_fonts(full=True))))
            return "pdf:渲染内容", hashlib.sha256("\n".join(sig).encode()).hexdigest()[:16]
        except Exception:
            return "pdf:无法解析", hashlib.sha256(raw).hexdigest()[:16]
    if zipfile.is_zipfile(path):
        # zip 的条目 mtime 每次不同。比条目名与各条目内容,不比压缩包字节。
        with zipfile.ZipFile(path) as zf:
            # ★Word 每次存盘**重新分配 styleId**(CZ_Heading2 这次叫 aa、下次叫 afff)。
            # 样式名是稳定的,styleId 不是——今天反复撞到这件事。
            # 故先建 id→名 映射,把引用解析成名字再比:比的是「用了哪个样式」,
            # 不是「那个样式这次叫什么代号」。
            idmap = {}
            if "word/styles.xml" in zf.namelist():
                sx = zf.read("word/styles.xml").decode("utf-8", "ignore")
                for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)"[^>]*>'
                                     r'((?:(?!</w:style>).)*?)</w:style>', sx, re.S):
                    nm = re.search(r'<w:name w:val="([^"]*)"', m.group(2))
                    if nm:
                        idmap[m.group(1)] = nm.group(1)
            parts = []
            for name in sorted(zf.namelist()):
                body = zf.read(name)
                if name.endswith((".xml", ".rels")):
                    text_body = body.decode("utf-8", "ignore")
                    text_body = SHA.sub("<H64>", text_body)
                    # ★Word 每次存盘都盖上自己的印:修订标识、文档 id、创建/修改时间。
                    # 它们与内容无关,却让同样的输入产出不同的字节。
                    # **这不是流水线不可复现,是 Word 的不确定性**——
                    # 判据要对准渲染内容,不对准文件身份。
                    text_body = re.sub(r'\sw:rsid[A-Za-z]*="[^"]*"', "", text_body)
                    text_body = re.sub(r"<w:rsid[^/>]*/>", "", text_body)
                    text_body = re.sub(r"<w1[45]:docId[^/>]*/>", "", text_body)
                    text_body = re.sub(r"<(dcterms:\w+)[^>]*>[^<]*</\1>", "", text_body)
                    text_body = re.sub(r"<cp:revision>[^<]*</cp:revision>", "", text_body)
                    text_body = re.sub(r'w:id="-?\d+"', "", text_body)
                    # w14:paraId / w14:textId 是 Word 每次存盘**随机生成**的段落标识,
                    # 零内容意义。它是最后一处让「同样的输入」产出不同字节的东西。
                    text_body = re.sub(r'\sw14:(?:paraId|textId)="[^"]*"', "", text_body)
                    text_body = RUNSTAMP.sub("-<RUN>", ISO.sub("<TS>", text_body))
                    # Word 自己发号的标识:目录书签 _TocNNNN 按会话顺序分配、
                    # 编号定义的 nsid/tmpl 是随机值、settings 的 rsid 池随每次编辑增长。
                    # 三者都记录「这次是哪一次」,不记录「内容是什么」。
                    # 留着它们,两次跑就永远不相等——而那看起来像流水线不可复现。
                    text_body = re.sub(r'_Toc\d+', '_Toc<N>', text_body)
                    # 又两类 Word 会话计数(与 rsid 同类,非内容):
                    #   docProps/app.xml 的 Words/Characters/Lines/Paragraphs——存盘时现算,口径随会话变;
                    #   settings.xml 的 spidmax——形状 id 计数器,每开一次文档就长。
                    # ★这两类是在两个空目录互相对账时暴露的:正文归一后逐字节相同,
                    #   只剩它们不同——归一化的洞长得和真差异一模一样,每发现一类补一类。
                    text_body = re.sub(r'<(Words|Characters|CharactersWithSpaces|Lines|Paragraphs|TotalTime)>\d+</\1>',
                                       r'<\1>0</\1>', text_body)
                    text_body = re.sub(r'\sspidmax="\d+"', '', text_body)
                    # settings.xml 里的会话痕迹:hdrShapeDefaults(Word 是否曾打开页眉编辑面板决定写不写)、
                    # docId/GUID、shapelayout 的 idmap data。都不是内容。
                    text_body = re.sub(r'<w:hdrShapeDefaults>.*?</w:hdrShapeDefaults>', '', text_body, flags=re.S)
                    text_body = re.sub(r'<w:shapeDefaults>.*?</w:shapeDefaults>', '', text_body, flags=re.S)
                    text_body = re.sub(r'\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}', '<GUID>', text_body)
                    text_body = re.sub(r'\sw15:val="\{[^}]*\}"', '', text_body)
                    # 每张图、每个内容控件都带一个随机 id(anchorId/editId)。
                    # 与 paraId 同类:Word 用来认对象,不用来表达内容。
                    text_body = re.sub(r'\s(?:wp14|w14|w15):(?:anchorId|editId)="[^"]*"',
                                       '', text_body)
                    text_body = re.sub(r'<w:(?:nsid|tmpl)\s+w:val="[^"]*"\s*/>', '', text_body)
                    text_body = re.sub(r'<w:rsids>.*?</w:rsids>', '', text_body, flags=re.S)
                    # 编号定义(abstractNum)每次分配的次序不同,内容是同一组。
                    # **比集合,不比次序**——次序是 Word 的分配顺序,不是内容。
                    if name.endswith("numbering.xml"):
                        # ★先抹随机 id 再排序。我第一版把排序插在抹除之前,
                        # 于是按随机值排——排序本身成了不确定的。
                        # **归一化的每一步都有先后:抹之前排,等于没排。**
                        text_body = re.sub(r'<w:(?:nsid|tmpl)\s+w:val="[^"]*"\s*/>',
                                           "", text_body)
                        blocks = re.findall(r"<w:abstractNum\b.*?</w:abstractNum>",
                                            text_body, re.S)
                        if blocks:
                            stripped = [re.sub(r'\sw:abstractNumId="[^"]*"', "", x)
                                        for x in blocks]
                            text_body = re.sub(
                                r"<w:abstractNum\b.*?</w:abstractNum>", "",
                                text_body, flags=re.S) + "".join(sorted(stripped))
                        # <w:num> 用 abstractNumId **引用**上面那些定义,而分配号每次不同。
                        # 定义已按内容排序,引用号却还是原始分配号——**只归一了被引的一头,
                        # 没归一引用的那一头**,于是集合相同而文件仍不等。
                        # 把 numId 与 abstractNumId 一并抹掉:它们是分配序号,不是内容。
                        nums = re.findall(r"<w:num\b.*?</w:num>", text_body, re.S)
                        if nums:
                            stripped_nums = [
                                re.sub(r'\sw16cid:durableId="[^"]*"', "",
                                    re.sub(r'\sw:numId="[^"]*"', "",
                                       re.sub(r'<w:abstractNumId\s+w:val="[^"]*"\s*/>',
                                              "", x)))
                                for x in nums]
                            text_body = re.sub(r"<w:num\b.*?</w:num>", "",
                                               text_body, flags=re.S) \
                                + "".join(sorted(stripped_nums))
                    if idmap:
                        text_body = re.sub(
                            r'(<w:(?:pStyle|rStyle|tblStyle|next|basedOn|link)\s+w:val=")([^"]+)(")',
                            lambda m: m.group(1) + idmap.get(m.group(2), m.group(2)) + m.group(3),
                            text_body)
                        text_body = re.sub(r'(w:styleId=")([^"]+)(")',
                            lambda m: m.group(1) + idmap.get(m.group(2), m.group(2)) + m.group(3),
                            text_body)
                    body = text_body.encode()
                parts.append(name.encode() + b"\0" + hashlib.sha256(body).digest())
                PART_DIGESTS.setdefault(str(path), {})[name] = hashlib.sha256(body).hexdigest()[:12]
        return "zip:条目名+内容", hashlib.sha256(b"".join(parts)).hexdigest()[:16]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "bytes", hashlib.sha256(raw).hexdigest()[:16]
    text = text.replace(str(root), "<WS>")
    # 包的安装位置(skill/styles/runtime 所在的产品根)也是环境事实,不是产物内容——
    # 同一个包装在两处,报告里记的路径必然不同。抹成 <PKG>。
    # 产品根 = 工作区的上两层(volumes/<册>/ 之上),或包自己的上一层;两种都试。
    for cand in {root.parent.parent, Path(__file__).resolve().parents[3]}:
        if (cand / "skill").exists() or (cand / "styles").exists():
            text = text.replace(str(cand), "<PKG>")
    # Word 沙盒探针的临时文件名带 pid+时间戳,与产物无关。
    text = re.sub(r'word-clean-open-probe-\d+-\d+-', 'word-clean-open-probe-<RUN>-', text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = TS.sub('"<TS>"', text)
        text = SHA.sub("<H64>", text)
        text = RUNSTAMP.sub("-<RUN>", ISO.sub("<TS>", text))
        return "text:抹根与时间戳", hashlib.sha256(text.encode()).hexdigest()[:16]
    # JSON:键序不是语义。**按结构逐键归一,不在序列化后拿正则抹**——
    # 正则把整个 "key": "value" 一起换掉,键也没了;两边键序一变,替换结果就不等价。
    # ★这正是本工具自己的一个洞:它曾把「只差 generatedAt」报成 different,
    #   让人以为流水线不可复现。归一化器的洞,长得和真差异一模一样。
    VOLATILE = {"generatedAt", "ranAt", "builtAt", "at", "timestamp",
                "openedAt", "reviewedAt", "elapsedSec", "initialisedAt",
                # 计时类:两次跑必然不同,且与「产物是不是同一份」无关。
                "durationSeconds", "finishedAt", "startedAt", "completedAt",
                # 文件字节数:两份内容一致的 docx 可因 Word 会话痕迹(hdrShapeDefaults 等)
                # 差几十字节。bytes 说的是「多大」不是「是什么」;内容由 zip 部件比对保证。
                "bytes"}
    def scrub(node):
        if isinstance(node, dict):
            return {k: ("<TS>" if k in VOLATILE else scrub(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        if isinstance(node, str):
            return RUNSTAMP.sub("-<RUN>", ISO.sub("<TS>", SHA.sub("<H64>", node)))
        return node
    flat = json.dumps(scrub(data), ensure_ascii=False, sort_keys=True)
    return "json:结构归一", hashlib.sha256(flat.encode()).hexdigest()[:16]


PART_DIGESTS: dict = {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--repro", required=True, type=Path)
    ap.add_argument("--volume")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    cb = Chain(args.baseline, args.volume, None)
    cr = Chain(args.repro, args.volume, None)
    same, diff, absent, skipped = [], [], [], []
    for aid in sorted(cb.artifacts):
        if cb.artifacts[aid].get("external"):
            continue
        fb, fr = cb.resolve(aid), cr.resolve(aid)
        if not fb:
            continue
        if not fr:
            absent.append(aid)
            continue
        hb = [canon(p, args.baseline) for p in sorted(fb)]
        hr = [canon(p, args.repro) for p in sorted(fr)]
        if [h for _, h in hb] == [h for _, h in hr]:
            same.append({"artifact": aid, "how": hb[0][0], "files": len(fb)})
        else:
            entry = {"artifact": aid, "how": hb[0][0],
                     "baseline": [h for _, h in hb][:3],
                     "repro": [h for _, h in hr][:3]}
            # zip 产物:点名哪个部件不同——排查时不该再靠外挂脚本。
            if hb[0][0].startswith("zip") and len(fb) == 1 and len(fr) == 1:
                pa = PART_DIGESTS.get(str(sorted(fb)[0]), {})
                pr = PART_DIGESTS.get(str(sorted(fr)[0]), {})
                entry["partsDiffer"] = [n for n in sorted(set(pa) | set(pr)) if pa.get(n) != pr.get(n)][:12]
            diff.append(entry)
    report = {"tool": "reconcile_against_baseline",
              "baseline": str(args.baseline), "repro": str(args.repro),
              "counts": {"same": len(same), "different": len(diff),
                         "absentInRepro": len(absent)},
              "same": same, "different": diff, "absentInRepro": absent,
              "comparisonRule": "JSON 按 sort_keys 规范化(键序不是语义);zip 比条目名与"
                                "各条目内容(丢弃 mtime);文本抹工作区根与时间戳。"
                                "**这不是放宽判据,是把判据对准要判的东西**——"
                                "任何无法归类的差异照原样报为 different,不猜。",
              "status": "pass" if not diff and not absent else "fail"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({"counts": report["counts"],
                      "different": [d["artifact"] for d in diff],
                      "absentInRepro": absent, "status": report["status"]},
                     ensure_ascii=False, indent=1))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
