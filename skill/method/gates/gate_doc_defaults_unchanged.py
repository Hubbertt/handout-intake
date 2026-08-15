#!/usr/bin/env python3
"""GATE_DOC_DEFAULTS_UNCHANGED:成品的继承根必须等于我们的声明。

**为什么要有这道门。** 继承根(docDefaults)是整份 docx 的默认值来源:样式没写的
属性都从它取值。它此前一行都不是我们的——rFonts/sz/szCs/lang/pPrDefault spacing
全部来自 python-docx 自带的 default.docx。库一升级、换台机器装了别的版本,这些值
就会变,而成品看起来一切正常。

2026-08-15 由编译器接管发射之后,值是我们的了。但「是我们的」需要每次证明,
不能靠记得——本轮就是手工比了一次「逐字节相同」,那不构成门:下次没人会比。

判据取**逐属性比对**而不是整串比对:
  整串比对会因属性顺序、命名空间前缀、空白而误报;
  逐属性比对只在「值真的不同」时报错,而那正是我们要抓的。

用法:
  gate_doc_defaults_unchanged.py --docx <成品> --params <参数表>
退出码 0=通过 1=不符
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_doc_defaults(docx: Path) -> dict:
    """从 docx 读出继承根的实际值,扁平化成 属性路径 → 值。"""
    xml = zipfile.ZipFile(docx).read("word/styles.xml").decode("utf-8")
    block = re.search(r"<w:docDefaults>.*?</w:docDefaults>", xml, re.S)
    if not block:
        return {}
    text = block.group(0)
    found: dict[str, str] = {}
    for scope, tag in (("rPr", "rPrDefault"), ("pPr", "pPrDefault")):
        part = re.search(rf"<w:{tag}>.*?</w:{tag}>", text, re.S)
        if not part:
            continue
        for el in re.finditer(r"<w:(\w+)((?:\s+w:\w+=\"[^\"]*\")*)\s*/?>", part.group(0)):
            name, attrs = el.group(1), el.group(2)
            if name in (tag, scope):
                continue
            for a in re.finditer(r'w:(\w+)="([^"]*)"', attrs):
                found[f"{scope}/{name}/@{a.group(1)}"] = a.group(2)
            if not attrs.strip():
                found[f"{scope}/{name}"] = "(present)"
    return found


def declared(params: dict) -> dict:
    """从参数表读出我们声明的、**会真正留在文件里**的那些根值。

    只比对与 Word 应用默认不同的项:等于应用默认的写了也会被 Word 清掉
    (实测:autoSpaceDE=1、caps=0 等),拿它们比对必然误报。这些项的自足
    由参数表本身承载,由另一道「生效值比对」门校验,不在本门范围。
    """
    root = params.get("docDefaults1") or {}
    rpr, ppr = root.get("rPrDefault") or {}, root.get("pPrDefault") or {}
    want: dict[str, str] = {}
    fonts = rpr.get("rFonts")
    if isinstance(fonts, dict):
        for k, v in fonts.items():
            if not k.startswith("_"):
                want[f"rPr/rFonts/@{k}"] = str(v)
    for key in ("sz", "szCs", "kern"):
        if rpr.get(key) is not None:
            want[f"rPr/{key}/@val"] = str(int(rpr[key]))
    lang = rpr.get("lang")
    if isinstance(lang, dict):
        for k, v in lang.items():
            if not k.startswith("_"):
                want[f"rPr/lang/@{k}"] = str(v)
    spacing = ppr.get("spacing")
    if isinstance(spacing, dict):
        for k, v in spacing.items():
            if not k.startswith("_"):
                want[f"pPr/spacing/@{k}"] = str(v)
    return want


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    actual = read_doc_defaults(args.docx)
    want = declared(json.loads(args.params.read_text(encoding="utf-8")))
    if not want:
        print(json.dumps({"gate": "GATE_DOC_DEFAULTS_UNCHANGED", "status": "refused",
                          "why": "参数表里没有 docDefaults1 声明。无法证明根是我们的,"
                                 "这不是通过,是不知道。"}, ensure_ascii=False))
        return 1

    missing = sorted(k for k in want if k not in actual)
    drifted = sorted((k, want[k], actual[k]) for k in want
                     if k in actual and actual[k] != want[k])
    report = {
        "gate": "GATE_DOC_DEFAULTS_UNCHANGED",
        "docx": str(args.docx),
        "declaredCount": len(want),
        "missing": [{"key": k, "declared": want[k]} for k in missing],
        "drifted": [{"key": k, "declared": d, "actual": a} for k, d, a in drifted],
        "status": "pass" if not (missing or drifted) else "fail",
    }
    if missing or drifted:
        report["why"] = ("成品的继承根与我们的声明不符。根是整份 docx 的默认值来源,"
                         "样式没写的属性都从它取值——根被改写而无人察觉,"
                         "等于把一批值的决定权交回给了本地环境。")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
