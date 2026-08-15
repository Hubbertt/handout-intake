#!/usr/bin/env python3
"""两道门:① 根的完整性  ③ 样式生效值与声明一致。

（② 根的不可篡改在 gate_doc_defaults_unchanged.py。）

**③ 最容易做错,先说清楚。**
不能查「XML 里有没有 <w:strike>」——Word 会合法地清掉与根相同的冗余值,
按元素存在去查必然误报。本轮就是先按元素存在去看,才一度误判成「此路不通」。
要查的是**解析后的生效值**:样式自己写了就用它,没写就取根,根也没有就取
OOXML 应用默认。

**① 为什么必须有。**
「根覆盖了所有会影响渲染的属性」不能靠人记得。缺一项就是留一个由目标机器
决定的值——今天补齐,下次有人加个新属性,洞又开了,而且照样没人知道。

用法:
  gate_style_self_contained.py --docx <成品> --params <参数表>
退出码 0=两道门都过 1=任一不过
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

# 会影响渲染、因而根里必须有确定值的属性。少一项就是少一道保障;
# 多一项(如表格属性)则是把不属于根的东西塞进来,会造出永远不过的门。
ROOT_REQUIRED = {
    "rPr": ["rFonts", "sz", "szCs", "lang", "b", "i", "u", "caps", "smallCaps",
            "strike", "vertAlign", "highlight", "color", "position", "spacing", "w"],
    "pPr": ["spacing", "jc", "ind", "keepNext", "keepLines", "widowControl",
            "contextualSpacing", "pageBreakBefore"],
}

# OOXML 应用默认值:根与样式都没写时,Word 用这些。跨版本/语言可能不同,
# 这正是「根必须完整」的理由——写全了就轮不到它们。
APP_DEFAULT = {
    "b": False, "i": False, "u": "none", "caps": False, "smallCaps": False,
    "strike": False, "vertAlign": "baseline", "highlight": "none",
    "position": 0, "spacing": 0, "w": 100,
    "jc": "left", "keepNext": False, "keepLines": False, "widowControl": True,
    "contextualSpacing": False, "pageBreakBefore": False,
}

# 参数表键 → OOXML 属性名
# 参数表键 → 作用域/属性。**必须带作用域。**
#
# w:spacing 在 rPr 里是字符间距(带 w:val),在 pPr 里是段落间距
# (带 w:before/after/line,没有 w:val)。首版把两个作用域拍平进同一命名空间,
# pPr 那个没有 val 被当成布尔真,于是 37 个样式全部误报「声明 0 实得 1」。
# **同名不同义被合并**——正是本方法反复抓到的形状,这次长在门自己身上。
KEY_TO_TAG = {
    "bold": "rPr/b", "italic": "rPr/i", "underline": "rPr/u",
    "allCaps": "rPr/caps", "smallCaps": "rPr/smallCaps", "strike": "rPr/strike",
    "verticalAlign": "rPr/vertAlign", "highlight": "rPr/highlight",
    "positionHalfPt": "rPr/position", "characterSpacingTwips": "rPr/spacing",
    "scalePercent": "rPr/w",
    "alignment": "pPr/jc", "keepNext": "pPr/keepNext", "keepLines": "pPr/keepLines",
    "widowControl": "pPr/widowControl", "contextualSpacing": "pPr/contextualSpacing",
    "pageBreakBefore": "pPr/pageBreakBefore",
}
BOOLEAN_TAGS = {"b", "i", "caps", "smallCaps", "strike", "keepNext", "keepLines",
                "widowControl", "contextualSpacing", "pageBreakBefore"}


def normalise(tag: str, value):
    if value is None:
        return None
    if tag in BOOLEAN_TAGS:
        return bool(value) if isinstance(value, bool) else str(value) not in ("0", "false")
    if tag == "jc":
        return {"justify": "both"}.get(str(value), str(value))
    if tag in ("position", "spacing", "w"):
        return int(value)
    return str(value)


def style_values(xml: str) -> dict[str, dict[str, object]]:
    """样式名 → {属性: 值}(只取样式自己写了的)。

    按**样式名**索引而不是 styleId:Word 存盘会重写 styleId
    (登记册 P8 记过:CZ_Heading2 变成 afff)。
    """
    out: dict[str, dict[str, object]] = {}
    for m in re.finditer(r"<w:style [^>]*?>(?:(?!</w:style>).)*?</w:style>", xml, re.S):
        block = m.group(0)
        name = re.search(r'<w:name w:val="([^"]*)"', block)
        if not name:
            continue
        vals: dict[str, object] = {}
        for scope in ("rPr", "pPr"):
            part = re.search(rf"<w:{scope}>.*?</w:{scope}>", block, re.S)
            if not part:
                continue
            for el in re.finditer(r'<w:(\w+)(?:\s+w:val="([^"]*)")?\s*/?>', part.group(0)):
                tag, val = el.group(1), el.group(2)
                if tag in (scope,):
                    continue
                if val is None and tag not in BOOLEAN_TAGS:
                    continue          # 无 w:val 的复合元素(如 pPr 的 spacing)另行处理
                vals[f"{scope}/{tag}"] = normalise(tag, val if val is not None else True)
        out[name.group(1)] = vals
    return out


def root_values(xml: str) -> dict[str, object]:
    block = re.search(r"<w:docDefaults>.*?</w:docDefaults>", xml, re.S)
    if not block:
        return {}
    vals: dict[str, object] = {}
    for scope, holder in (("rPr", "rPrDefault"), ("pPr", "pPrDefault")):
        part = re.search(rf"<w:{holder}>.*?</w:{holder}>", block.group(0), re.S)
        if not part:
            continue
        for el in re.finditer(r'<w:(\w+)(?:\s+w:val="([^"]*)")?[^>]*/?>', part.group(0)):
            tag, val = el.group(1), el.group(2)
            if tag in (holder, scope):
                continue
            if val is None and tag not in BOOLEAN_TAGS:
                continue
            vals[f"{scope}/{tag}"] = normalise(tag, val if val is not None else True)
    return vals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    xml = zipfile.ZipFile(args.docx).read("word/styles.xml").decode("utf-8")
    root_decl = params.get("docDefaults1") or {}
    styles_decl = ((params.get("wordStyleRegistry") or {}).get("paragraphStyles") or {})

    # ① 根的完整性:声明层是否覆盖了 ROOT_REQUIRED
    gaps = []
    for scope, keys in ROOT_REQUIRED.items():
        holder = root_decl.get(f"{scope}Default") or {}
        for key in keys:
            if key not in holder:
                gaps.append(f"{scope}Default/{key}")

    # ③ 生效值比对:样式自己写了就用它,否则取根,否则取应用默认
    actual_styles = style_values(xml)
    actual_root = root_values(xml)
    mismatches = []
    checked = 0
    for style_id, spec in sorted(styles_decl.items()):
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if not name or name not in actual_styles:
            continue
        own = actual_styles[name]
        for key, tag in KEY_TO_TAG.items():
            if key not in spec:
                continue
            bare = tag.split("/", 1)[1]
            want = normalise(bare, spec[key])
            if tag in own:
                got, src = own[tag], "样式"
            elif tag in actual_root:
                got, src = actual_root[tag], "根"
            else:
                got, src = normalise(bare, APP_DEFAULT.get(bare)), "**应用默认**"
            checked += 1
            if got != want:
                mismatches.append({"style": name, "property": tag, "declared": want,
                                   "effective": got, "resolvedFrom": src})

    report = {
        "gates": ["GATE_ROOT_COMPLETE", "GATE_STYLE_EFFECTIVE_VALUES"],
        "rootCompleteness": {"required": sum(len(v) for v in ROOT_REQUIRED.values()),
                             "missing": gaps,
                             "status": "pass" if not gaps else "fail"},
        "effectiveValues": {"checked": checked,
                            "mismatches": mismatches[:40],
                            "mismatchTotal": len(mismatches),
                            "status": "pass" if not mismatches else "fail"},
        "note": "生效值比对**不查元素是否存在**:Word 会合法清掉与根相同的冗余值,"
                "按元素存在去查必然误报。查的是解析后的结果。",
    }
    bad = bool(gaps or mismatches)
    report["status"] = "fail" if bad else "pass"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rootCompleteness", "effectiveValues",
                                             "status")}, ensure_ascii=False, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
