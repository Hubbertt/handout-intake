#!/usr/bin/env python3
"""GATE_FONT_OWNERSHIP:成品里出现的每一个字体名,都必须是声明过的。

**为什么既有的两道门都漏了它。**
GATE_GLYPH_COVERAGE 问的是「声明的字体画得出这个字符吗」——答案是能。
PDF 阶段的 undeclared-font 问的是「PDF 里的字体名在 fontStandard 里吗」——
它也没报。于是成品第 35 页三个全角句点「．．．」由 MS-Mincho(日文明朝体)
渲染,一份中文物理讲义里出现了日文字体,两道门都没响。

根因是它们问错了问题:**不是「字符画得出吗」,也不是「PDF 里有什么字体」,
是「文档里每个 run 引用的字体,是不是我们声明过的」。** 前两个问的是能力和结果,
这个问的是归属。

判据取**引用**而非渲染结果:
  渲染结果要等 PDF 出来才知道,那时前面全部工序已经跑完;
  引用在 docx 里就能查,而且 Word 的字体回退正是从这里开始的。

用法:
  gate_font_ownership.py --docx <成品> --params <参数表>
退出码 0=通过 1=有未声明的字体
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

FONT_ATTRS = ("ascii", "hAnsi", "eastAsia", "cs")


def declared_fonts(params: dict) -> set[str]:
    """参数表里声明过的字体名全集:样式级 + 根。"""
    names: set[str] = set()
    styles = ((params.get("wordStyleRegistry") or {}).get("paragraphStyles") or {})
    for spec in styles.values():
        if isinstance(spec, dict):
            for key in ("fontCn", "fontAscii", "fontCs"):
                if spec.get(key):
                    names.add(str(spec[key]))
    chars = ((params.get("wordStyleRegistry") or {}).get("characterStyles") or {})
    for spec in chars.values():
        if isinstance(spec, dict):
            for key in ("fontCn", "fontAscii", "fontCs"):
                if spec.get(key):
                    names.add(str(spec[key]))
    root = ((params.get("docDefaults1") or {}).get("rPrDefault") or {}).get("rFonts")
    if isinstance(root, dict):
        for key, value in root.items():
            # 主题引用(asciiTheme/cstheme…)不是字体名,是指向 theme1.xml 的间接。
            # 首版只排除了 endswith("Theme"),漏掉小写的 cstheme,于是 minorBidi
            # 混进了「声明字体」集合——门自己把一个非字体名当成了合法字体。
            if key.startswith("_") or key.lower().endswith("theme"):
                continue
            names.add(str(value))
    return names


def used_style_ids(docx: Path) -> set[str]:
    """正文里真正被引用的样式 id。"""
    z = zipfile.ZipFile(docx)
    used: set[str] = set()
    for part in ("word/document.xml", "word/header1.xml", "word/footer1.xml"):
        try:
            xml = z.read(part).decode("utf-8")
        except KeyError:
            continue
        used |= set(re.findall(r'<w:(?:pStyle|rStyle) w:val="([^"]*)"', xml))
    return used


def style_font_owners(docx: Path, used: set[str]) -> dict[str, list[dict]]:
    """字体名 → 引用它的样式清单,标出该样式是否被正文用过。

    分流,不是放宽:被用过的样式引用未声明字体,是**会渲染出来的缺陷**;
    没被用过的(多为库模板自带的内置样式)是**继承来的包袱**,不影响当前渲染,
    但一旦有人用了那个样式就会渲成没声明过的字体。两者都要报,处置不同。
    """
    xml = zipfile.ZipFile(docx).read("word/styles.xml").decode("utf-8")
    out: dict[str, list[dict]] = {}
    for m in re.finditer(r"<w:style [^>]*?>(?:(?!</w:style>).)*?</w:style>", xml, re.S):
        block = m.group(0)
        sid = re.search(r'w:styleId="([^"]*)"', block)
        name = re.search(r'<w:name w:val="([^"]*)"', block)
        for el in re.finditer(r"<w:rFonts\b([^>]*)/?>", block):
            for attr in FONT_ATTRS:
                mm = re.search(rf'w:{attr}="([^"]*)"', el.group(1))
                if mm:
                    out.setdefault(mm.group(1), []).append({
                        "style": name.group(1) if name else "?",
                        "styleId": sid.group(1) if sid else "?",
                        "usedInBody": bool(sid and sid.group(1) in used)})
    return out


def referenced_fonts(docx: Path) -> dict[str, Counter]:
    """docx 里每个 rFonts 引用的字体名,按来源分组。

    主题引用(asciiTheme 等)不是字体名,单独计数:它要经 theme1.xml 再解析一次,
    是另一层间接,归属由主题文件决定,不在本门范围(已列为待改进:去主题化)。
    """
    z = zipfile.ZipFile(docx)
    found: dict[str, Counter] = {}
    for part in ("word/styles.xml", "word/document.xml",
                 "word/header1.xml", "word/footer1.xml"):
        try:
            xml = z.read(part).decode("utf-8")
        except KeyError:
            continue
        counter: Counter = Counter()
        for el in re.finditer(r"<w:rFonts\b([^>]*)/?>", xml):
            attrs = el.group(1)
            for name in FONT_ATTRS:
                m = re.search(rf'w:{name}="([^"]*)"', attrs)
                if m:
                    counter[m.group(1)] += 1
        if counter:
            found[part] = counter
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    allowed = declared_fonts(params)
    if not allowed:
        print(json.dumps({"gate": "GATE_FONT_OWNERSHIP", "status": "refused",
                          "why": "参数表里读不到任何声明字体。无法判定归属,"
                                 "这不是通过,是不知道。"}, ensure_ascii=False))
        return 1

    referenced = referenced_fonts(args.docx)
    used = used_style_ids(args.docx)
    owners = style_font_owners(args.docx, used)
    undeclared: dict[str, dict[str, int]] = {}
    total = 0
    for part, counter in referenced.items():
        for name, count in counter.items():
            total += count
            if name not in allowed:
                undeclared.setdefault(name, {})[part] = count

    report = {
        "gate": "GATE_FONT_OWNERSHIP",
        "docx": str(args.docx),
        "declaredFonts": sorted(allowed),
        "referencedParts": {k: dict(v) for k, v in referenced.items()},
        "referenceTotal": total,
        "undeclared": undeclared,
        "undeclaredOwners": {name: owners.get(name, []) for name in undeclared},
        "undeclaredInUsedStyles": sorted(
            name for name in undeclared
            if any(o["usedInBody"] for o in owners.get(name, []))),
        "undeclaredInUnusedStylesOnly": sorted(
            name for name in undeclared
            if owners.get(name) and not any(o["usedInBody"] for o in owners[name])),
        "status": "pass" if not undeclared else "fail",
    }
    if undeclared:
        report["why"] = ("成品引用了参数表没有声明的字体。字体名是声明,字体存在是环境——"
                         "引用一个没声明过的字体,意味着某处的字体归属不在我们控制里,"
                         "换台机器就可能渲成别的东西。")
        report["howToFix"] = ("要么把该字体补进声明(并确认目标机器有它),"
                              "要么找出引用它的那个 run 并改回声明过的字体。"
                              "**不要为了让门变绿而把它加进声明**——那是把问题登记成规范。")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("declaredFonts", "referenceTotal", "undeclared", "status")},
                     ensure_ascii=False, indent=1))
    return 0 if not undeclared else 1


if __name__ == "__main__":
    sys.exit(main())
