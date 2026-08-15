#!/usr/bin/env python3
"""Build reviewed content-structure manifests from formal summer Word masters.

This script reads DOCX packages only. It does not modify Word masters. The
manifest records the semantic skeleton that module scripts must consume before
they touch styles, headers, footers, columns, or PDF exports.
"""

from __future__ import annotations

# ---- 环境定位(handout-intake vendor 化时加入)------------------------------------
# 本文件拷自生产线 scripts/formal,那里写死本机路径是合理的——它只在这一台机器跑。
# 进包后不行:「智能体拿到就能用」的前提是不把一台机器的布局编进方法。
# 规则:环境变量优先,其次 runtime/paths.json,最后才是可移植的默认(Path.home)。
# 找不到时如实报缺,不猜。
import os as _os
from pathlib import Path as _P
def _hi_env(name, default=None):
    v = _os.environ.get(name)
    if v: return _P(v)
    cfg = _P(__file__).resolve().parents[2] / "runtime" / "paths.json"
    if cfg.exists():
        try:
            import json as _j
            v = _j.loads(cfg.read_text(encoding="utf-8")).get(name)
            if v: return _P(_os.path.expanduser(v))
        except Exception:
            pass
    return _P(default) if default is not None else None
# ----------------------------------------------------------------------------

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from summer_scope_filter import active_scope, filter_doc_map, merge_extra
from summer_word_contract import STRUCTURE_MANIFEST_SCHEMA


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
FORMAL_ROOT = ROOT / "library/教辅资料/上海"
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
MANIFEST_DIR = RUN_DIR / "structure-manifest"
REPORT = RUN_DIR / "structure_manifest_build_report.json"

DOCS = filter_doc_map(merge_extra({
    "g07_en": FORMAL_ROOT / "初中/七年级/上册/英语/word/2026-暑假班-七年级-上册-英语-学生版-讲义.docx",
    "g07_cn": FORMAL_ROOT / "初中/七年级/上册/语文/word/2026-暑假班-七年级-上册-语文-学生版-讲义.docx",
    "g08_ph": FORMAL_ROOT / "初中/八年级/上册/物理/word/2026-暑假班-八年级-上册-物理-学生版-讲义.docx",
    "g08_en": FORMAL_ROOT / "初中/八年级/上册/英语/word/2026-暑假班-八年级-上册-英语-学生版-讲义.docx",
    "g08_cn": FORMAL_ROOT / "初中/八年级/上册/语文/word/2026-暑假班-八年级-上册-语文-学生版-讲义.docx",
    "g08_ch": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-习题册-化学的魅力.docx",
    "g08_ch_t34": FORMAL_ROOT / "初中/八年级/全一册/化学/word/2026-暑假班-八年级-全一册-化学-学生版-讲义-第二册.docx",
}, lambda key, entry: Path(entry["docx"])))

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
NS = {"w": W_NS, "r": R_NS, "wp": WP_NS, "a": A_NS, "pic": PIC_NS}
W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"


ROLE_BY_TOC_TEXT = [
    (re.compile(r"^Unit\s+\d+\b", re.I), "chapter"),
    (re.compile(r"^第\s*A?\d+\s*讲"), "chapter"),
    (re.compile(r"^专题\s*\d+|^主题\s*\d+"), "chapter"),
    (re.compile(r"^课题\s*\d+"), "topic"),
    (re.compile(r"^第\s*\d+\s*课时"), "lesson"),
    (re.compile(r"^重难点\s*\d+"), "knowledge-point"),
    (re.compile(r"^跨学科实践"), "topic"),
    (re.compile(r"^专题复习"), "topic"),
]

ENGLISH_MODULE_ALIASES = {
    "vocabulary": ["核心词汇", "词汇速记", "Vocabulary", "Core Vocabulary"],
    "vocabularypreview": ["单词预习检测", "Vocabulary Preview"],
    "phrases": ["核心短语", "重点短语", "Phrases", "Key Phrases"],
    "keysentences": ["重点句型", "核心句型", "Key Sentences", "Key Sentence Patterns"],
    "grammar": ["语法讲解", "语法精讲", "语法聚焦", "语法", "Grammar", "Grammar Focus"],
    "reading": ["阅读精练", "阅读理解", "时文阅读", "Reading Practice", "Current Affairs Reading", "Reading Comprehension", "Task-Based Reading"],
    "writing": ["写作", "Writing"],
    "practice": ["实战演练", "Practice"],
}
CHINESE_SECTION_PREFIXES = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_of(el: etree._Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
    return "".join(parts)


def normalize_text(value: str) -> str:
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def compact_text(value: str) -> str:
    value = normalize_text(value)
    return re.sub(r"[\s·•“”\"'《》（）()、，,。:：；;—_\-]+", "", value)


def english_unit_body_title_aliases(title: str) -> list[str]:
    """Map reviewed English display titles back to source-title aliases."""
    normalized = normalize_text(title)
    m = re.match(r"^Unit\s*(\d+)\s+(.+)$", normalized, flags=re.I)
    if not m:
        return []
    unit = int(m.group(1))
    rest = normalize_text(m.group(2))
    suffix_map = [
        ("Lesson Notes", ["上课讲义"]),
        ("Core Knowledge (Dictation Version)", ["单元核心知识（默写版）", "单元核心知识(默写版)"]),
    ]
    aliases: list[str] = []
    for english_suffix, source_suffixes in suffix_map:
        if not rest.endswith(english_suffix):
            continue
        base = normalize_text(rest[: -len(english_suffix)])
        if not base:
            continue
        for source_suffix in source_suffixes:
            aliases.append(f"Unit {unit} {base} {source_suffix}")
            aliases.append(f"Unit {unit} {base}{source_suffix}")
    return aliases


def english_module_body_title_aliases(title: str) -> list[str]:
    normalized = normalize_text(title)
    if " - " not in normalized and "·" not in normalized:
        return []
    suffix = normalized.rsplit(" - ", 1)[-1] if " - " in normalized else normalized.rsplit("·", 1)[-1]
    suffix_key = re.sub(r"[^a-z]", "", suffix.lower())
    keywords = ENGLISH_MODULE_ALIASES.get(suffix_key)
    if not keywords:
        return []
    aliases: list[str] = []
    for keyword in keywords:
        aliases.append(keyword)
        aliases.append(compact_text(keyword))
        for prefix in CHINESE_SECTION_PREFIXES:
            aliases.append(f"{prefix}{keyword}")
            aliases.append(f"{prefix}、{keyword}")
        if keyword == "单词预习":
            aliases.append("第一部分单词预习")
            aliases.append("📖第一部分单词预习")
        if keyword == "核心词汇":
            aliases.append("一核心词汇速记")
            aliases.append("一、核心词汇速记")
    return aliases


def toc_aliases(title: str) -> list[str]:
    aliases = [normalize_text(title)]
    aliases.extend(english_unit_body_title_aliases(title))
    aliases.extend(english_module_body_title_aliases(title))
    compact = compact_text(title)
    aliases.append(compact)
    for alias in list(aliases):
        alias_compact = compact_text(alias)
        if alias_compact and alias_compact not in aliases:
            aliases.append(alias_compact)
    m = re.search(r"第\s*(\d+)\s*课时\s*(.+)$", title)
    if m:
        aliases.append(f"第{m.group(1)}课时 {normalize_text(m.group(2))}")
        aliases.append(f"第{m.group(1)}课时{compact_text(m.group(2))}")
        aliases.append(normalize_text(m.group(2)))
    for pattern in [
        r"^课题\s*\d+\s*(.+)$",
        r"^跨学科实践(?:活动)?\s*(.+)$",
        r"^专题复习\s*(.+)$",
        r"^重难点\s*\d+\s*(.+)$",
    ]:
        m = re.search(pattern, title)
        if m:
            aliases.append(normalize_text(m.group(1)))
            aliases.append(compact_text(m.group(1)))
    compact_title = compact_text(title)
    if "粗盐" in compact_title and "除杂" in compact_title:
        aliases.extend(
            [
                "第1课时 怎样存放和取用粗盐 如何去除粗盐中的难溶性杂质",
                "怎样存放和取用粗盐",
                "如何去除粗盐中的难溶性杂质",
                "第1课时怎样存放和取用粗盐如何去除粗盐中的难溶性杂质",
            ]
        )
    if "滤液" in compact_title and "废弃物" in compact_title and "洗涤" in compact_title:
        aliases.extend(
            [
                "第2课时 如何从滤液中得到食盐固体",
                "如何从滤液中得到食盐固体",
                "怎样处理实验废弃物和洗涤仪器",
                "第2课时如何从滤液中得到食盐固体",
            ]
        )
    return [a for i, a in enumerate(aliases) if a and a not in aliases[:i]]


def title_language(value: str) -> str:
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", value))
    has_latin = bool(re.search(r"[A-Za-z]", value))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "other"


def display_title_fields(
    formal_title: object,
    *,
    source_title: object | None = None,
    title_source: str = "source-title",
) -> dict[str, object]:
    formal = normalize_text(str(formal_title or ""))
    raw_source = normalize_text(str(source_title if source_title is not None else formal))
    return {
        "sourceTitleRaw": raw_source,
        "sourceTitleNormalized": raw_source,
        "formalDisplayTitle": formal,
        "tocDisplayTitle": formal,
        "bodyDisplayTitle": formal,
        "titleLanguage": title_language(formal),
        "titleSource": title_source,
    }


def paragraph_matches_title(text: str, aliases: list[str]) -> tuple[bool, str | None]:
    normalized = normalize_text(text)
    compact = compact_text(text)
    for alias in aliases:
        alias_has_latin = bool(re.search(r"[A-Za-z]", alias))
        alias_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", alias))
        if " " in alias:
            if normalized == alias or normalized.startswith(alias + " "):
                return True, alias
            continue
        if compact == alias or compact.startswith(alias):
            return True, alias
        if alias_has_latin and not alias_has_cjk:
            continue
        if len(alias) >= 8 and alias in compact:
            return True, alias
    return False, None


def style_id(p: etree._Element) -> str | None:
    style = p.find("w:pPr/w:pStyle", NS)
    return style.get(W + "val") if style is not None else None


def left_indent(p: etree._Element) -> int:
    ind = p.find("w:pPr/w:ind", NS)
    if ind is None:
        return 0
    raw = ind.get(W + "left") or ind.get(W + "start") or "0"
    return int(raw) if raw.isdigit() else 0


def has_section_break(p: etree._Element) -> bool:
    return p.find("w:pPr/w:sectPr", NS) is not None


def has_page_break(p: etree._Element) -> bool:
    return any(br.get(W + "type") == "page" for br in p.findall(".//w:br", NS))


def drawing_count(el: etree._Element) -> int:
    return len(el.findall(".//w:drawing", NS))


def table_shape(tbl: etree._Element) -> dict[str, int]:
    rows = tbl.findall("w:tr", NS)
    cols = 0
    if rows:
        cols = max(len(row.findall("w:tc", NS)) for row in rows)
    return {"rows": len(rows), "columns": cols}


def exercise_semantic_type(title: str) -> str | None:
    normalized = normalize_text(title)
    if re.search(r"\b(Core Vocabulary|Key Phrases)\b", normalized, flags=re.I):
        return "fillBlankQuestionGrid"
    if re.search(r"Complete the Sentences|Correct Form|Initial Letters|Chinese Prompts", normalized, flags=re.I):
        return "oneColumnFillBlank"
    if re.search(r"Grammar Cloze|Passage Completion|Cloze Test", normalized, flags=re.I):
        return "oneColumnFillBlank"
    if re.search(r"Key Sentence Patterns", normalized, flags=re.I):
        return "keepTogetherPair"
    return None


def table_semantic_type(text: str) -> str:
    sample = normalize_text(text)
    if re.search(r"\bA[.．、]\s+.+\bB[.．、]", sample):
        return "choiceLayoutTable"
    if re.search(r"/[A-Za-zɪʊəɔæɑːˈˌ]+/", sample) or re.search(r"/iː/|/ɪ/|/e/|/æ/", sample):
        return "phoneticTable"
    if any(token in sample for token in ["词汇", "汉语翻译", "词性", "发音"]):
        return "vocabularyTable"
    if any(token in sample for token in ["用法", "典例", "类别", "连词", "常用句式", "核心要点", "具体内容", "规则变化"]):
        return "grammarKnowledgeTable"
    return "contentDataTable"


def body_children(root: etree._Element) -> list[etree._Element]:
    body = root.find("w:body", NS)
    if body is None:
        return []
    return [child for child in body if child.tag in {W + "p", W + "tbl"}]


def clean_toc_title(text: str) -> tuple[str, str | None]:
    raw = normalize_text(text.replace("\t", " "))
    page = None
    m = re.match(r"^(.*?)(?:\s+)(\d{1,4})$", raw)
    if m:
        raw = m.group(1).strip()
        page = m.group(2)
    return raw, page


def is_toc_entry(child: etree._Element) -> bool:
    if child.tag != W + "p":
        return False
    text = text_of(child)
    if not normalize_text(text):
        return False
    if "\t" in text:
        return True
    if (style_id(child) or "").lower().startswith("toc"):
        return True
    return False


def toc_level(p: etree._Element) -> int:
    sid = style_id(p) or ""
    m = re.search(r"(\d+)$", sid)
    if m:
        return max(1, int(m.group(1)))
    indent = left_indent(p)
    if indent >= 720:
        return 3
    if indent >= 300:
        return 2
    return 1


def role_for_toc(title: str, level: int) -> str:
    for pattern, role in ROLE_BY_TOC_TEXT:
        if pattern.search(title):
            return role
    return "chapter" if level == 1 else ("topic" if level == 2 else "knowledge-point")


def right_header_title(title: str) -> str:
    normalized = normalize_text(title)
    match = re.match(r"^第\s*A?(\d{2})\s*讲\s*(.+)$", normalized)
    if match:
        return f"A{match.group(1)} {normalize_text(match.group(2))}"
    return normalized


def find_toc(children: list[etree._Element]) -> tuple[int | None, int | None, list[dict[str, object]]]:
    start = None
    for idx, child in enumerate(children):
        if child.tag == W + "p" and normalize_text(text_of(child)) in {"目录", "Contents"}:
            start = idx
            break
    if start is None:
        return None, None, []
    entries = []
    end = start + 1
    seen_entry = False
    for idx in range(start + 1, len(children)):
        child = children[idx]
        if child.tag != W + "p":
            if seen_entry:
                end = idx
                break
            continue
        text = normalize_text(text_of(child))
        if not text:
            if seen_entry:
                end = idx
                break
            continue
        if not is_toc_entry(child):
            end = idx
            break
        seen_entry = True
        title, page = clean_toc_title(text_of(child))
        level = toc_level(child)
        entries.append(
            {
                "title": title,
                "pageLabel": page,
                "tocIndex": idx,
                "level": level,
                "role": role_for_toc(title, level),
                "styleId": style_id(child),
                "leftIndentDxa": left_indent(child),
            }
        )
        end = idx + 1
    return start, end, entries


def paragraph_record(index: int, p: etree._Element) -> dict[str, object]:
    text = normalize_text(text_of(p))
    return {
        "kind": "paragraph",
        "index": index,
        "styleId": style_id(p),
        "text": text,
        "textSample": text[:160],
        "leftIndentDxa": left_indent(p),
        "hasPageBreak": has_page_break(p),
        "hasSectionBreak": has_section_break(p),
        "drawingCount": drawing_count(p),
    }


def unit_records(children: list[etree._Element]) -> list[dict[str, object]]:
    records = []
    for idx, child in enumerate(children):
        if child.tag == W + "p":
            records.append(paragraph_record(idx, child))
        elif child.tag == W + "tbl":
            shape = table_shape(child)
            text = normalize_text(text_of(child))
            records.append(
                {
                    "kind": "table",
                    "index": idx,
                    "textSample": text[:160],
                    "rows": shape["rows"],
                    "columns": shape["columns"],
                    "drawingCount": drawing_count(child),
                }
            )
    return records


def find_body_starts(
    units: list[dict[str, object]],
    toc_entries: list[dict[str, object]],
    toc_end: int | None,
) -> tuple[dict[int, int], dict[int, str]]:
    starts: dict[int, int] = {}
    matched_aliases: dict[int, str] = {}

    def search_units(
        aliases: list[str],
        lower: int,
        upper: int | None = None,
        *,
        preferred_styles: set[str] | None = None,
    ) -> tuple[int | None, str | None]:
        passes = [preferred_styles] if preferred_styles else []
        passes.append(None)
        for style_filter in passes:
            for unit in units:
                if unit.get("kind") not in {"paragraph", "table"}:
                    continue
                idx = int(unit["index"])
                if idx < lower or (upper is not None and idx >= upper):
                    continue
                if unit.get("kind") == "paragraph" and style_filter is not None and unit.get("styleId") not in style_filter:
                    continue
                if unit.get("kind") == "table" and style_filter is not None:
                    continue
                text = normalize_text(str(unit.get("text") or unit.get("textSample") or ""))
                if not text:
                    continue
                matched, alias = paragraph_matches_title(text, aliases)
                if matched:
                    return idx, str(alias)
        return None, None

    level1_indexes = [i for i, entry in enumerate(toc_entries) if int(entry.get("level") or 1) == 1]
    search_start = toc_end or 0
    for toc_i in level1_indexes:
        entry = toc_entries[toc_i]
        aliases = toc_aliases(str(entry["title"]))
        found_idx, found_alias = search_units(aliases, search_start, preferred_styles={"CZ_ChapterTitle"})
        if found_idx is not None:
            starts[toc_i] = found_idx
            matched_aliases[toc_i] = str(found_alias)
            search_start = found_idx + 1

    level1_ranges: dict[int, tuple[int, int | None]] = {}
    for pos, toc_i in enumerate(level1_indexes):
        if toc_i not in starts:
            continue
        next_start = None
        for next_toc_i in level1_indexes[pos + 1 :]:
            if next_toc_i in starts:
                next_start = starts[next_toc_i]
                break
        level1_ranges[toc_i] = (starts[toc_i] + 1, next_start)

    current_parent: int | None = None
    for toc_i, entry in enumerate(toc_entries):
        level = int(entry.get("level") or 1)
        if level == 1:
            current_parent = toc_i
            continue
        aliases = toc_aliases(str(entry["title"]))
        lower = toc_end or 0
        upper = None
        if current_parent is not None and current_parent in level1_ranges:
            lower, upper = level1_ranges[current_parent]
        found_idx, found_alias = search_units(
            aliases,
            lower,
            upper,
            preferred_styles={"CZ_Heading2", "CZ_Heading3", "CZ_PassageLabel"},
        )
        if found_idx is not None:
            starts[toc_i] = found_idx
            matched_aliases[toc_i] = str(found_alias)
    return starts, matched_aliases


def block_anchor(unit: dict[str, object]) -> dict[str, object]:
    return {
        "kind": unit.get("kind"),
        "index": unit.get("index"),
        "styleId": unit.get("styleId"),
        "textSample": unit.get("textSample"),
    }


def make_manifest(key: str, path: Path) -> tuple[dict[str, object], dict[str, object]]:
    with ZipFile(path) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    children = body_children(root)
    units = unit_records(children)
    toc_start, toc_end, toc_entries = find_toc(children)
    starts, matched_aliases = find_body_starts(units, toc_entries, toc_end)
    blocks: list[dict[str, object]] = []

    if toc_start is not None:
        title_unit = units[toc_start]
        blocks.append(
            {
                "blockId": f"{key}-toc-title",
                "role": "toc-title",
                "hierarchyLevel": 0,
                "title": "目录",
                **display_title_fields("目录"),
                "startAnchor": block_anchor(title_unit),
                "endAnchor": block_anchor(units[(toc_end or toc_start + 1) - 1]),
                "module": "toc",
                "lockPolicy": "may-style-only",
            }
        )
    for entry_i, entry in enumerate(toc_entries, start=1):
        unit = units[int(entry["tocIndex"])]
        blocks.append(
            {
                "blockId": f"{key}-toc-entry-{entry_i:03d}",
                "role": "toc-entry",
                "hierarchyLevel": entry["level"],
                "title": entry["title"],
                **display_title_fields(entry["title"]),
                "startAnchor": block_anchor(unit),
                "endAnchor": block_anchor(unit),
                "module": "toc",
                "lockPolicy": "may-style-only",
                "navigationRole": entry["role"],
                "pageLabel": entry["pageLabel"],
            }
        )

    ordered_starts = sorted((idx, toc_i) for toc_i, idx in starts.items())
    for order, (start_idx, toc_i) in enumerate(ordered_starts, start=1):
        entry = toc_entries[toc_i]
        end_idx = ordered_starts[order][0] - 1 if order < len(ordered_starts) else (len(units) - 1)
        start_unit = units[start_idx]
        end_unit = units[end_idx]
        blocks.append(
            {
                "blockId": f"{key}-body-{order:03d}",
                "role": entry["role"],
                "hierarchyLevel": entry["level"],
                "title": entry["title"],
                **display_title_fields(entry["title"], source_title=matched_aliases.get(toc_i) or entry["title"]),
                "startAnchor": block_anchor(start_unit),
                "endAnchor": block_anchor(end_unit),
                "module": "chapterTitle" if entry["level"] == 1 else "body",
                "lockPolicy": "may-reflow-within-block",
                "tocEntryIndex": toc_i,
                "rightHeaderText": right_header_title(str(entry["title"])),
                "matchedAlias": matched_aliases.get(toc_i),
                "exerciseSemanticType": exercise_semantic_type(str(entry["title"])),
            }
        )

    active_body = 0
    for unit in units:
        idx = int(unit["index"])
        if unit.get("kind") == "table":
            table_text = str(unit.get("textSample") or "")
            table_type = table_semantic_type(table_text)
            blocks.append(
                {
                    "blockId": f"{key}-table-{idx:05d}",
                    "role": "table",
                    "hierarchyLevel": 9,
                    "title": f"表格 {idx}",
                    **display_title_fields(f"表格 {idx}", title_source="generated-from-content"),
                    "startAnchor": block_anchor(unit),
                    "endAnchor": block_anchor(unit),
                    "module": "table",
                    "lockPolicy": "locked",
                    "rows": unit.get("rows"),
                    "columns": unit.get("columns"),
                    "parentBodyBlockOrder": active_body,
                    "tableSemanticType": table_type,
                    "shadingPolicy": "remove-fill" if table_type == "grammarKnowledgeTable" else "preserve-structure",
                }
            )
        if int(unit.get("drawingCount") or 0) > 0:
            blocks.append(
                {
                    "blockId": f"{key}-image-{idx:05d}",
                    "role": "image",
                    "hierarchyLevel": 9,
                    "title": f"图片对象 {idx}",
                    **display_title_fields(f"图片对象 {idx}", title_source="generated-from-content"),
                    "startAnchor": block_anchor(unit),
                    "endAnchor": block_anchor(unit),
                    "module": "image",
                    "lockPolicy": "keep-with-next",
                    "drawingCount": unit.get("drawingCount"),
                    "parentBodyBlockOrder": active_body,
                }
            )
        body_start_indexes = {start for start, _toc_i in ordered_starts}
        if idx in body_start_indexes:
            active_body += 1

    unmatched = [
        {"index": i, "title": entry["title"], "level": entry["level"], "role": entry["role"]}
        for i, entry in enumerate(toc_entries)
        if i not in starts
    ]
    manifest = {
        "schemaVersion": STRUCTURE_MANIFEST_SCHEMA,
        "status": "draft",
        "generatedAt": now_iso(),
        "reviewedAt": None,
        "reviewMethod": "draft extraction pending independent model or human review",
        "key": key,
        "sourceDocx": {
            "path": str(path),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        },
        "navigation": {
            "rightHeaderRule": "Use the lowest effective TOC/navigation level for the current page. TOC pages use 目录.",
            "tocTitleIndex": toc_start,
            "tocEndIndex": toc_end,
            "bodyStartMatches": starts,
            "matchedAliases": matched_aliases,
            "unmatchedTocEntries": unmatched,
        },
        "moduleMap": {
            "toc-title": "toc",
            "toc-entry": "toc",
            "chapter": "chapterTitle",
            "topic": "body",
            "lesson": "body",
            "knowledge-point": "body",
            "body": "body",
            "exercise-group": "body",
            "word-list": "body",
            "table": "table",
            "image": "image",
        },
        "firstLayerSemanticPolicies": {
            "fillBlankQuestionGrid": "Core Vocabulary and Key Phrases are dictation question grids; each Chinese prompt and answer line is one unsplittable question item.",
            "oneColumnFillBlank": "Vocabulary Preview and cloze-like exercises are fill-blank questions but not two-column dictation grids.",
            "keepTogetherPair": "Chinese prompt and corresponding English answer line must stay in the same question unit.",
            "readingUnderlineReference": "Underlined words in reading questions are reference text, not answer blanks.",
            "emphasis": "Source emphasis is restored only as vocabulary-key, question-meaning, reading-internal emphasis, or reviewed term emphasis; word-internal fragments are non-semantic residue.",
            "tableShading": "Knowledge/grammar comparison tables keep table structure and remove fill shading; option, phonetic, vocabulary, and content-data tables are not recast as knowledge tables.",
        },
        "blocks": blocks,
        "extractionSummary": {
            "bodyChildCount": len(units),
            "paragraphCount": sum(1 for u in units if u["kind"] == "paragraph"),
            "tableCount": sum(1 for u in units if u["kind"] == "table"),
            "drawingAnchorCount": sum(1 for u in units if int(u.get("drawingCount") or 0) > 0),
            "tocEntryCount": len(toc_entries),
            "matchedTocEntryCount": len(starts),
            "unmatchedTocEntryCount": len(unmatched),
            "blockRoleCounts": dict(Counter(str(b["role"]) for b in blocks)),
            "styleIds": dict(Counter(str(u.get("styleId")) for u in units if u.get("styleId"))),
        },
    }
    report = {
        "key": key,
        "path": str(path),
        "status": "draft",
        "tocEntryCount": len(toc_entries),
        "matchedTocEntryCount": len(starts),
        "unmatchedTocEntryCount": len(unmatched),
        "tableCount": manifest["extractionSummary"]["tableCount"],
        "drawingAnchorCount": manifest["extractionSummary"]["drawingAnchorCount"],
        "blockRoleCounts": manifest["extractionSummary"]["blockRoleCounts"],
        "unmatchedTocEntries": unmatched[:20],
    }
    return manifest, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for key, path in DOCS.items():
        manifest, result = make_manifest(key, path)
        out = MANIFEST_DIR / f"{key}.structure.json"
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["manifest"] = str(out)
        results.append(result)
    report = {
        "schemaVersion": "chengziclass.structure-manifest-build-report.v1",
        "generatedAt": now_iso(),
        "acceptReviewed": False,
        "reviewBoundary": "This script builds draft structure manifests only. Reviewed status must be applied by a separate review decision artifact.",
        "manifestDir": str(MANIFEST_DIR),
        "activeScope": active_scope(),
        "results": results,
        "summary": {
            "documents": len(results),
            "reviewed": sum(1 for r in results if r["status"] == "reviewed"),
            "draft": sum(1 for r in results if r["status"] == "draft"),
            "unmatchedTocEntries": sum(int(r["unmatchedTocEntryCount"]) for r in results),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(REPORT)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["unmatchedTocEntries"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
