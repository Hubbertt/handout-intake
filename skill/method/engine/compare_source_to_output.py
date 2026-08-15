#!/usr/bin/env python3
"""What the source carries, and what survives into the compiled document.

Column widths, figure alignment, inline figure order and figure size were each
found by looking at one page and noticing something wrong. They were all the
same shape of defect: the carve recorded the property, the blueprint carried
it, and the last step never consumed it — so the output was uniform where the
source varied. Finding those one at a time does not scale.

This compares the two documents property class by property class rather than
object by object. Objects cannot be aligned reliably across a rebuild, but a
distribution can: if the source has 56 centred figures and the output has none,
the property is not wired, whatever the page looks like. Three verdicts:

* 未建立联系 — the source varies, the output is uniform or empty
* 已接上     — the output carries a comparable spread
* 已裁决     — the spec has decided to collapse it, and says why
* 源未使用   — the source does not use it, so nothing can be concluded

It reports; it does not decide. A property may be deliberately dropped, and
that decision belongs to the spec, not to this script.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def in_fallback(node) -> bool:
    current = node.getparent()
    while current is not None:
        if current.tag == MC + "Fallback":
            return True
        current = current.getparent()
    return False


def attribute(node, name: str) -> str | None:
    return node.get(W + name) if node is not None else None


class Styles:
    """Resolve a property the way Word does: direct, then style, then default.

    Comparing direct formatting alone would call the spec's own achievement a
    defect — it forbids direct paragraph formatting and moves everything into
    styles, so the output is *supposed* to look empty at that level. What has
    to match is the formatting a reader sees.
    """

    def __init__(self, package: zipfile.ZipFile) -> None:
        self.paragraph: dict[str, Any] = {}
        self.character: dict[str, Any] = {}
        self.table: dict[str, Any] = {}
        self.based: dict[str, str] = {}
        try:
            root = etree.fromstring(package.read("word/styles.xml"))
        except KeyError:
            return
        for style in root.iter(W + "style"):
            identifier = style.get(W + "styleId")
            based = style.find(W + "basedOn")
            if based is not None:
                self.based[identifier] = based.get(W + "val")
            if style.get(W + "type") == "paragraph":
                self.paragraph[identifier] = style
            elif style.get(W + "type") == "table":
                self.table[identifier] = style
            else:
                self.character[identifier] = style
        defaults = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        self.defaults = defaults

    def _chain(self, table: dict[str, Any], identifier: str | None):
        seen = set()
        while identifier and identifier not in seen:
            seen.add(identifier)
            style = table.get(identifier)
            if style is not None:
                yield style
            identifier = self.based.get(identifier)

    def run_value(self, run, paragraph, tag: str, key: str = "val"):
        direct = run.find(f"{W}rPr/{W}{tag}")
        if direct is not None:
            return direct.get(W + key) or "on"
        style_id = attribute(run.find(f"{W}rPr/{W}rStyle"), "val")
        for style in self._chain(self.character, style_id):
            node = style.find(f"{W}rPr/{W}{tag}")
            if node is not None:
                return node.get(W + key) or "on"
        paragraph_style = attribute(paragraph.find(f"{W}pPr/{W}pStyle"), "val") \
            if paragraph is not None else None
        for style in self._chain(self.paragraph, paragraph_style):
            node = style.find(f"{W}rPr/{W}{tag}")
            if node is not None:
                return node.get(W + key) or "on"
        node = self.defaults.find(W + tag) if self.defaults is not None else None
        return (node.get(W + key) or "on") if node is not None else None

    def toggle(self, paragraph, tag: str) -> bool:
        """An on/off property, read the way Word reads it.

        A toggle is not a value: 「元素在不在」 and 「元素说了什么」 are two
        different questions and the answer differs at both ends. The source
        writes keepNext 3079 times, every one of them w:val="0" — explicitly
        off. Word writes the same property back as a bare <w:keepNext/>, with
        no attribute at all — that is on. Reading the attribute alone made the
        source look glued together and our own output look unglued, in a
        document where the truth is the exact reverse.
        """
        for holder in ([paragraph.find(f"{W}pPr")] +
                       list(self._chain(self.paragraph,
                                        attribute(paragraph.find(f"{W}pPr/{W}pStyle"),
                                                  "val")))):
            if holder is None:
                continue
            node = holder.find(f"{W}pPr/{W}{tag}") if holder.tag != W + "pPr" \
                else holder.find(W + tag)
            if node is not None:
                return (node.get(W + "val") or "1") not in ("0", "false", "off")
        return False

    def table_borders(self, table):
        """The border declaration in force for a table.

        Counting 「有 tcBorders / 无 tcBorders」 measured where the declaration
        sits, not what gets drawn. Our tables declare nothing per cell and take
        every edge from CZ_Table_Standard, so by that measure a fully ruled
        table read as unruled. What a reader sees is the resolved edge.
        """
        direct = table.find(f"{W}tblPr/{W}tblBorders")
        if direct is not None:
            return direct
        style_id = attribute(table.find(f"{W}tblPr/{W}tblStyle"), "val")
        for style in self._chain(self.table, style_id):
            node = style.find(f"{W}tblPr/{W}tblBorders")
            if node is not None:
                return node
        return None

    def paragraph_value(self, paragraph, path: str, key: str):
        direct = paragraph.find(f"{W}pPr/{path}")
        if direct is not None and direct.get(W + key):
            return direct.get(W + key)
        style_id = attribute(paragraph.find(f"{W}pPr/{W}pStyle"), "val")
        for style in self._chain(self.paragraph, style_id):
            node = style.find(f"{W}pPr/{path}")
            if node is not None and node.get(W + key):
                return node.get(W + key)
        return None


def survey(root, styles: "Styles | None" = None) -> dict[str, Counter]:
    """Every property class we know how to look for, counted."""
    found: dict[str, Counter] = {name: Counter() for name in (
        "段落·对齐", "段落·首行缩进", "段落·左缩进", "段落·悬挂缩进",
        "段落·段前距", "段落·段后距", "段落·行距", "段落·与下段同页",
        "段落·大纲级别", "段落·自动编号",
        "字符·加粗", "字符·斜体", "字符·下划线", "字符·颜色", "字符·字号",
        "字符·上下标", "字符·着重号", "字符·着色", "字符·中文字体",
        "表格·列数", "表格·行高", "表格·单元格垂直对齐", "表格·单元格底纹",
        "表格·单元格边框", "表格·合并",
        "图·行内或浮动", "图·环绕方式", "图·裁剪", "图·显示尺寸档",
        "其他·超链接", "其他·书签", "其他·分页符",
    )}

    for paragraph in root.iter(W + "p"):
        if in_fallback(paragraph):
            continue
        properties = paragraph.find(W + "pPr")
        if properties is None:
            found["段落·对齐"]["(无 pPr)"] += 1
            continue
        get = (styles.paragraph_value if styles
               else lambda p, path, key: attribute(p.find(f"{W}pPr/{path}"), key))
        found["段落·对齐"][get(paragraph, W + "jc", "val") or "(默认)"] += 1
        for key, label in (("firstLine", "段落·首行缩进"),
                           ("left", "段落·左缩进"),
                           ("hanging", "段落·悬挂缩进")):
            found[label][get(paragraph, W + "ind", key) or "(无)"] += 1
        for key, label in (("before", "段落·段前距"), ("after", "段落·段后距"),
                           ("line", "段落·行距")):
            found[label][get(paragraph, W + "spacing", key) or "(无)"] += 1
        found["段落·与下段同页"][
            "有" if (styles.toggle(paragraph, "keepNext") if styles
                    else properties.find(W + "keepNext") is not None)
            else "无"] += 1
        found["段落·大纲级别"][get(paragraph, W + "outlineLvl", "val") or "(无)"] += 1
        found["段落·自动编号"]["有" if properties.find(W + "numPr") is not None
                            else "无"] += 1

    parents = {run: paragraph for paragraph in root.iter(W + "p")
               for run in paragraph.iter(W + "r")}
    for run in root.iter(W + "r"):
        if in_fallback(run) or not any(t.text for t in run.findall(W + "t")):
            continue
        paragraph = parents.get(run)
        for tag, label in (("b", "字符·加粗"), ("i", "字符·斜体"),
                           ("u", "字符·下划线"), ("color", "字符·颜色"),
                           ("sz", "字符·字号"),
                           ("vertAlign", "字符·上下标"), ("em", "字符·着重号")):
            value = (styles.run_value(run, paragraph, tag) if styles
                     else attribute(run.find(f"{W}rPr/{W}{tag}"), "val"))
            if value and value not in ("0", "none", "auto"):
                found[label][value] += 1
        # Word paints text two ways and our spec picks one, so counting
        # 「高亮」 and 「底纹」 as separate classes compared a mechanism rather
        # than a result: the source's yellow highlighter and our yellow fill
        # scored as two unrelated properties, each empty on the other side.
        shading = (styles.run_value(run, paragraph, "shd", "fill") if styles
                   else attribute(run.find(f"{W}rPr/{W}shd"), "fill"))
        painted = (styles.run_value(run, paragraph, "highlight") if styles
                   else attribute(run.find(f"{W}rPr/{W}highlight"), "val"))
        for value in (shading, painted):
            if value and value.upper() not in ("AUTO", "FFFFFF", "NONE", "ON"):
                found["字符·着色"][value] += 1
        font = (styles.run_value(run, paragraph, "rFonts", "eastAsia") if styles
                else None)
        if font:
            found["字符·中文字体"][font] += 1

    for table in root.iter(W + "tbl"):
        if in_fallback(table):
            continue
        found["表格·列数"][str(len(table.findall(f"{W}tblGrid/{W}gridCol")))] += 1
        declared = styles.table_borders(table) if styles else \
            table.find(f"{W}tblPr/{W}tblBorders")
        for row in table.findall(W + "tr"):
            height = row.find(f"{W}trPr/{W}trHeight")
            found["表格·行高"][attribute(height, "val") or "(无)"] += 1
            for cell in row.findall(W + "tc"):
                properties = cell.find(W + "tcPr")
                if properties is None:
                    continue
                # Word reads a missing vAlign as top, so 「(无)」 is a value
                # like any other and comparing without it would score 168
                # top-aligned cells against nothing.
                found["表格·单元格垂直对齐"][
                    attribute(properties.find(W + "vAlign"), "val") or "top"] += 1
                # A 「clear / auto」 shading paints nothing. Counting it as a
                # value made an invisible declaration look like lost colour.
                # White on white paper is not shading, whether it is spelled
                # 「auto」 or 「FFFFFF」. The review handouts spell it the
                # second way, 73 times.
                fill = attribute(properties.find(W + "shd"), "fill")
                found["表格·单元格底纹"][
                    fill if fill and fill.upper() not in ("AUTO", "FFFFFF")
                    else "(无)"] += 1
                edges = properties.find(W + "tcBorders")
                for side in ("top", "left", "bottom", "right"):
                    node = edges.find(W + side) if edges is not None else None
                    inside = "insideH" if side in ("top", "bottom") else "insideV"
                    if node is None and declared is not None:
                        node = declared.find(W + side)
                        if node is None:
                            node = declared.find(W + inside)
                    style = attribute(node, "val") if node is not None else None
                    found["表格·单元格边框"][
                        f"{side}:{style}" if style and style != "nil" else "(无)"] += 1
                for slash in ("tl2br", "tr2bl"):
                    node = edges.find(W + slash) if edges is not None else None
                    if node is not None:
                        found["表格·单元格边框"][f"{slash}:{attribute(node, 'val')}"] += 1
                span = attribute(properties.find(W + "gridSpan"), "val")
                merge = properties.find(W + "vMerge")
                found["表格·合并"][
                    f"横{span}" if span else
                    ("纵" + (attribute(merge, "val") or "continue"))
                    if merge is not None else "无"] += 1

    for drawing in root.iter(W + "drawing"):
        if in_fallback(drawing):
            continue
        anchor = drawing.find(WP + "anchor")
        found["图·行内或浮动"]["浮动" if anchor is not None else "行内"] += 1
        if anchor is not None:
            for child in anchor:
                name = etree.QName(child).localname
                if name.startswith("wrap"):
                    found["图·环绕方式"][name] += 1
        crop = drawing.find(f".//{A}srcRect")
        found["图·裁剪"]["有" if crop is not None and crop.attrib else "无"] += 1
        extent = drawing.find(f".//{WP}extent")
        if extent is not None and extent.get("cx"):
            millimetres = int(extent.get("cx")) / 36000
            found["图·显示尺寸档"][f"{int(millimetres // 20) * 20}-"
                              f"{int(millimetres // 20) * 20 + 20}mm"] += 1

    found["其他·超链接"]["有"] = sum(1 for _ in root.iter(W + "hyperlink"))
    found["其他·书签"]["有"] = sum(1 for _ in root.iter(W + "bookmarkStart"))
    found["其他·分页符"]["有"] = sum(
        1 for node in root.iter(W + "br") if node.get(W + "type") == "page")
    return found


def verdict(source: Counter, output: Counter,
            adjudicated: str | None = None) -> tuple[str, str]:
    used = {key: count for key, count in source.items()
            if key not in ("(无)", "(默认)", "无", "(无 pPr)") and count}
    if not used:
        return "源未使用", "源里没有用到这一项"
    # A property may be deliberately collapsed, and that decision belongs to
    # the spec. Recording it here keeps the reduction visible instead of
    # letting it be re-discovered as a defect every run.
    if adjudicated:
        return "已裁决", adjudicated
    carried = {key: count for key, count in output.items()
               if key not in ("(无)", "(默认)", "无", "(无 pPr)") and count}
    if not carried:
        return "未建立联系", "源有、成品完全没有"
    if len(used) > 1 and len(carried) == 1:
        return "未建立联系", f"源有 {len(used)} 种取值,成品只剩 1 种"
    if len(carried) < len(used) and len(carried) < max(2, len(used) // 3):
        return "疑似削弱", f"源 {len(used)} 种取值,成品 {len(carried)} 种"
    return "已接上", f"源 {len(used)} 种 / 成品 {len(carried)} 种"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--mapping", type=Path,
                        help="private spec mapping, for its adjudications")
    args = parser.parse_args()
    adjudications: dict[str, str] = {}
    if args.mapping and args.mapping.is_file():
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        adjudications = ((mapping.get("adjudication") or {})
                         .get("fidelityProperties") or {})

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    source: dict[str, Counter] = {}
    for document in registry["documents"]:
        if document["role"] != "original_word":
            continue
        package = zipfile.ZipFile(document["physicalPath"])
        root = etree.fromstring(package.read("word/document.xml"))
        for name, counts in survey(root, Styles(package)).items():
            source.setdefault(name, Counter()).update(counts)

    package = zipfile.ZipFile(args.output)
    produced = survey(etree.fromstring(package.read("word/document.xml")),
                      Styles(package))

    rows = []
    for name in source:
        state, detail = verdict(source[name], produced.get(name, Counter()),
                                adjudications.get(name))
        rows.append({
            "property": name, "state": state, "detail": detail,
            "source": dict(source[name].most_common(6)),
            "output": dict(produced.get(name, Counter()).most_common(6))})
    order = {"未建立联系": 0, "疑似削弱": 1, "已接上": 2, "已裁决": 3, "源未使用": 4}
    rows.sort(key=lambda row: (order[row["state"]], row["property"]))

    report = {
        "schemaVersion": "chengziclass.source-output-fidelity.v1",
        "output": str(args.output),
        "summary": dict(Counter(row["state"] for row in rows)),
        "properties": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for row in rows:
        if row["state"] in ("未建立联系", "疑似削弱"):
            print(f'  {row["state"]}  {row["property"]:20} {row["detail"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
