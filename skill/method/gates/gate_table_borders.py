#!/usr/bin/env python3
"""GATE_TABLE_BORDERS:成品里每张语义表必须挂着表样式,且该样式在样式表里带可见边框。

起因(2026-08-16):使用方指出成品表格无线框而源文有 tblBorders 六边 single。
链上三处各自都「对」:切分读到了源边框;编译器插了 tblStyle=CZ_Table_Standard 并把样式写进样式表;
参数表要求六边可见。丢在**清理步**:strip 的引用扫描只看 pStyle/rStyle 不看 tblStyle,
把表样式当「没人用」删了,Word 再把表上的引用剥掉——最终成品无框,而每一步自己都没报错。

判据(读成品 docx):
  ① 每个 <w:tbl> 的 tblPr 里有 tblStyle,或自身 tblBorders 有非 nil 的边
  ② 引用的表样式在 styles.xml 里存在,且 tblBorders 至少 top/bottom/insideH 之一为非 nil
选项排版表(registered ABCD option-layout)按参数表 scope 排除。

用法:  gate_table_borders.py --docx <成品> [--params <参数表>]
"""
from __future__ import annotations
import argparse, json, re, sys, zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docx", required=True, type=Path); ap.add_argument("--params", type=Path)
    a = ap.parse_args()
    z = zipfile.ZipFile(a.docx); doc = z.read("word/document.xml").decode("utf-8"); sty = z.read("word/styles.xml").decode("utf-8")
    styles = {}
    for m in re.finditer(r'<w:style [^>]*w:type="table"[^>]*w:styleId="([^"]+)"[^>]*>((?:(?!</w:style>).)*?)</w:style>', sty, re.S):
        edges = dict(re.findall(r'<w:(top|left|bottom|right|insideH|insideV) [^/]*w:val="([^"]+)"', m.group(2)))
        styles[m.group(1)] = edges
    tables = re.findall(r"<w:tbl>.*?</w:tbl>", doc, re.S)
    findings, checked = [], 0
    for i, t in enumerate(tables):
        tp = (re.search(r"<w:tblPr>.*?</w:tblPr>", t, re.S) or [None])[0] if re.search(r"<w:tblPr>", t) else ""
        tp = tp.group(0) if hasattr(tp, "group") else (tp or "")
        own = dict(re.findall(r'<w:(top|left|bottom|right|insideH|insideV) [^/]*w:val="([^"]+)"', tp))
        sid = (re.findall(r'<w:tblStyle w:val="([^"]+)"', tp) or [None])[0]
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", t, re.S))
        # 选项排版表:只有 A./B./C./D. 标号的短表,不在语义表范围
        if re.fullmatch(r"\s*(?:[A-D][．.]\s*\S+\s*){2,4}\s*", text.replace(" ", "")) and len(text) < 40:
            continue
        checked += 1
        own_visible = any(v not in ("nil", "none") for k, v in own.items() if k in ("top", "bottom", "insideH"))
        style_visible = bool(sid) and sid in styles and any(v not in ("nil", "none") for k, v in styles[sid].items() if k in ("top", "bottom", "insideH"))
        if not (own_visible or style_visible):
            findings.append({"table": i, "textHead": text[:24], "tblStyle": sid, "styleExists": sid in styles if sid else False,
                             "ownEdges": own, "why": "既没挂带边框的表样式,自身边框也全为 nil/缺失——页面上就是无框。"})
    print(json.dumps({"gate": "GATE_TABLE_BORDERS", "tables": len(tables), "checked": checked, "tableStyles": list(styles),
                      "findings": findings, "status": "pass" if not findings else "fail"}, ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
