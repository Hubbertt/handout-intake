#!/usr/bin/env python3
"""Update TOC fields and pagination in a Microsoft Word sandbox copy.

This is an internal stage of the unique summer handout workflow.  It never
opens the candidate from its project path in Word: the candidate is copied to
Word's container sandbox, updated and saved there, verified, and only then
copied back to the isolated candidate path.
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
import os
import re
import shutil
import subprocess
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree


CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
DEFAULT_SANDBOX_ROOT = Path(
    str(_P.home() / "Library/Containers/com.microsoft.Word/Data/Documents") + "/"
    "ChengziClassWordProbe/native-toc-update"
)
PLACEHOLDER_TEXT = "在 Microsoft Word 中更新目录字段"
DEFAULT_PARAMS = (
    Path(__file__).resolve().parents[2]
    / "templates/summer-class-layout/summer_class_module_parameters.current.json"
)
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"


class NativeTocError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def inspect_docx(path: Path) -> dict[str, Any]:
    try:
        with ZipFile(path) as package:
            document_xml = package.read("word/document.xml")
            settings_xml = package.read("word/settings.xml")
    except Exception as exc:
        raise NativeTocError(f"invalid-docx-package: {exc}") from exc

    document = etree.fromstring(document_xml)
    settings = etree.fromstring(settings_xml)
    frames: list[dict[str, Any]] = []
    toc_field_count = 0
    result_pieces: list[str] = []
    for node in document.iter():
        if node.tag == W + "fldChar":
            field_type = node.get(W + "fldCharType", "")
            if field_type == "begin":
                frames.append({"isToc": False, "inResult": False})
            elif field_type == "separate" and frames:
                frames[-1]["inResult"] = True
                if frames[-1]["isToc"]:
                    toc_field_count += 1
            elif field_type == "end" and frames:
                frames.pop()
        elif node.tag == W + "instrText" and frames:
            if re.search(r"(^|\s)TOC(\s|$)", node.text or "", flags=re.IGNORECASE):
                frames[-1]["isToc"] = True
        elif node.tag == W + "t" and any(
            frame["isToc"] and frame["inResult"] for frame in frames
        ):
            result_pieces.append(node.text or "")

    compatibility_values = settings.xpath(
        ".//w:compatSetting[@w:name='compatibilityMode']/@w:val",
        namespaces=NS,
    )
    result_text = "".join(result_pieces)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "tocFieldCount": toc_field_count,
        "tocResultVisibleCharacters": len(re.sub(r"\s+", "", result_text)),
        "tocResultPreview": re.sub(r"\s+", " ", result_text).strip()[:240],
        "placeholderCount": document_xml.decode("utf-8").count(PLACEHOLDER_TEXT),
        "compatibilityModeValues": compatibility_values,
    }


def run_word_update(stage: Path, timeout: int) -> dict[str, Any]:
    script = f'''
set docPath to {applescript_string(str(stage))}
set docRef to missing value
try
    tell application "Microsoft Word"
        launch
        set docRef to open file name docPath add to recent files false
        delay 1
        set tocCount to count of tables of contents of docRef
        if tocCount < 1 then error "missing Word table of contents"
        try
            repaginate docRef
        end try
        repeat with tocIndex from 1 to tocCount
            update (table of contents tocIndex of docRef)
        end repeat
        try
            repaginate docRef
        end try
        repeat with tocIndex from 1 to tocCount
            update (table of contents tocIndex of docRef)
        end repeat
        try
            repaginate docRef
        end try
        save docRef
        set pageCount to compute statistics docRef statistic statistic pages
        close docRef saving no
    end tell
on error errText number errNumber
    try
        tell application "Microsoft Word"
            if docRef is not missing value then close docRef saving no
        end tell
    end try
    error errText number errNumber
end try
return "TOCCOUNT=" & (tocCount as text) & linefeed & "PAGECOUNT=" & (pageCount as text)
'''
    try:
        proc = subprocess.run(
            ["osascript", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NativeTocError(f"word-native-toc-update-timeout-after-{timeout}s") from exc
    if proc.returncode != 0:
        raise NativeTocError(
            "word-native-toc-update-failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or f"exit-{proc.returncode}")
        )
    values: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if raw.strip().isdigit():
            values[key.strip()] = int(raw.strip())
    if values.get("TOCCOUNT", 0) < 1 or values.get("PAGECOUNT", 0) < 1:
        raise NativeTocError(f"invalid-word-native-readback: {values}")
    return {
        "tocCount": values["TOCCOUNT"],
        "pageCount": values["PAGECOUNT"],
        "stdout": proc.stdout.strip(),
    }


def run_word_clean_save(stage: Path, timeout: int) -> dict[str, Any]:
    """Reopen and save the normalized package through Microsoft Word.

    The deterministic TOC-format closure below is an OOXML construction step.
    This second Word pass makes Word the final save and acceptance authority.
    """

    script = f'''
set docPath to {applescript_string(str(stage))}
set docRef to missing value
try
    tell application "Microsoft Word"
        launch
        set docRef to open file name docPath add to recent files false
        delay 1
        try
            repaginate docRef
        end try
        save docRef
        set pageCount to compute statistics docRef statistic statistic pages
        close docRef saving no
    end tell
on error errText number errNumber
    try
        tell application "Microsoft Word"
            if docRef is not missing value then close docRef saving no
        end tell
    end try
    error errText number errNumber
end try
return "PAGECOUNT=" & (pageCount as text)
'''
    try:
        proc = subprocess.run(
            ["osascript", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NativeTocError(f"word-native-clean-save-timeout-after-{timeout}s") from exc
    if proc.returncode != 0:
        raise NativeTocError(
            "word-native-clean-save-failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or f"exit-{proc.returncode}")
        )
    page_count = 0
    for line in proc.stdout.splitlines():
        if line.startswith("PAGECOUNT=") and line.split("=", 1)[1].strip().isdigit():
            page_count = int(line.split("=", 1)[1].strip())
    if page_count < 1:
        raise NativeTocError(f"invalid-word-native-clean-save-readback: {proc.stdout!r}")
    return {"pageCount": page_count, "stdout": proc.stdout.strip()}


CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def pad_blank_final_section(stage: Path) -> None:
    """Append one truly blank final page as its own Word section.

    The new section carries explicit empty header/footer parts so the pad page
    has no header, footer, or page number while all previous pages keep their
    registered headers, footers, and visible PAGE fields. Used to make the
    Word content master an even number of native pages before PDF export.
    """

    with ZipFile(stage) as package:
        files = {name: package.read(name) for name in package.namelist()}
    document = etree.fromstring(files["word/document.xml"])
    body = document.find(W + "body")
    final_sect = body.find(W + "sectPr")
    if final_sect is None:
        raise NativeTocError("missing-final-section-properties-for-parity-pad")

    rels = etree.fromstring(files["word/_rels/document.xml.rels"])
    content_types = etree.fromstring(files["[Content_Types].xml"])
    used_ids = {rel.get("Id") for rel in rels}

    def fresh_id(base: str) -> str:
        candidate = base
        index = 1
        while candidate in used_ids:
            index += 1
            candidate = f"{base}{index}"
        used_ids.add(candidate)
        return candidate

    def add_part(part_name: str, root_tag: str, content_type: str, rel_type: str) -> str:
        files[f"word/{part_name}"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:{root_tag} xmlns:w="{W_NS}"><w:p/></w:{root_tag}>'
        ).encode("utf-8")
        override = etree.SubElement(content_types, f"{{{CT_NS}}}Override")
        override.set("PartName", f"/word/{part_name}")
        override.set("ContentType", content_type)
        rel = etree.SubElement(rels, f"{{{PKG_REL_NS}}}Relationship")
        rel_id = fresh_id("rIdParityBlank")
        rel.set("Id", rel_id)
        rel.set("Type", rel_type)
        rel.set("Target", part_name)
        return rel_id

    header_id = add_part(
        "parity-blank-header.xml",
        "hdr",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
    )
    footer_id = add_part(
        "parity-blank-footer.xml",
        "ftr",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
    )

    new_sect = deepcopy(final_sect)
    for tag in ("headerReference", "footerReference", "titlePg", "pgNumType"):
        for node in new_sect.findall(W + tag):
            new_sect.remove(node)
    for position, (tag, rel_id) in enumerate(
        [
            ("headerReference", header_id),
            ("headerReference", header_id),
            ("footerReference", footer_id),
            ("footerReference", footer_id),
        ]
    ):
        ref = etree.Element(W + tag)
        ref.set(W + "type", "default" if position % 2 == 0 else "even")
        ref.set(R + "id", rel_id)
        new_sect.insert(position, ref)

    closing = etree.SubElement(body, W + "p")
    closing_pr = etree.SubElement(closing, W + "pPr")
    body.remove(final_sect)
    closing_pr.append(final_sect)
    etree.SubElement(body, W + "p")
    body.append(new_sect)

    files["word/document.xml"] = etree.tostring(
        document, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    files["word/_rels/document.xml.rels"] = etree.tostring(
        rels, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    files["[Content_Types].xml"] = etree.tostring(
        content_types, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    temp = stage.with_name(f".{stage.name}.parity-pad-{time.time_ns()}.tmp")
    with ZipFile(temp, "w", ZIP_DEFLATED) as package:
        for name, payload in files.items():
            package.writestr(name, payload)
    os.replace(temp, stage)


def _set_val(parent: etree._Element, tag: str, value: str) -> etree._Element:
    child = parent.find(W + tag)
    if child is None:
        child = etree.SubElement(parent, W + tag)
    child.set(W + "val", value)
    return child


def _set_on_off(parent: etree._Element, tag: str, enabled: bool) -> None:
    child = parent.find(W + tag)
    if child is None:
        child = etree.SubElement(parent, W + tag)
    child.set(W + "val", "1" if enabled else "0")


def _toc_result_paragraphs(document: etree._Element) -> list[etree._Element]:
    """Return paragraphs containing visible text inside the outer TOC result.

    A Word TOC field spans multiple paragraphs. Walking ``document.iter()`` and
    testing a ``w:p`` node before its children are visited misses the first
    result paragraph and every later paragraph whose field state is established
    by a previous sibling. Process one paragraph at a time while preserving the
    field stack across paragraph boundaries instead.
    """

    frames: list[dict[str, bool]] = []
    paragraphs: list[etree._Element] = []
    for paragraph in document.iter(W + "p"):
        has_toc_result_text = False
        for node in paragraph.iter():
            if node.tag == W + "fldChar":
                field_type = node.get(W + "fldCharType", "")
                if field_type == "begin":
                    frames.append({"isToc": False, "inResult": False})
                elif field_type == "separate" and frames:
                    frames[-1]["inResult"] = True
                elif field_type == "end" and frames:
                    frames.pop()
            elif node.tag == W + "instrText" and frames:
                if re.search(
                    r"(^|\s)TOC(\s|$)",
                    node.text or "",
                    flags=re.IGNORECASE,
                ):
                    frames[-1]["isToc"] = True
            elif (
                node.tag == W + "t"
                and (node.text or "").strip()
                and any(
                    frame["isToc"] and frame["inResult"]
                    for frame in frames
                )
            ):
                has_toc_result_text = True
        if has_toc_result_text:
            paragraphs.append(paragraph)
    return paragraphs


def _toc_levels(specs: dict[int, dict[str, Any]]) -> tuple[int, ...]:
    return tuple(sorted(specs))


def _style_rpr_defaults(styles: etree._Element) -> dict[str, dict[str, Any]]:
    """Resolve run-property defaults for each style, following basedOn chains.

    Word clean-save may collapse run-level properties that duplicate the
    paragraph style. Auditing therefore needs the effective value: run first,
    then the owning style chain, then OOXML fallbacks (cs<-hAnsi, szCs<-sz,
    bCs<-b, missing bold <- false).
    """

    raw: dict[str, dict[str, Any]] = {}
    based_on: dict[str, Any] = {}
    for style in styles.findall("w:style", NS):
        style_id = style.get(W + "styleId", "")
        r_pr = style.find(W + "rPr")
        fonts = r_pr.find(W + "rFonts") if r_pr is not None else None

        def _val(tag: str) -> Any:
            node = r_pr.find(W + tag) if r_pr is not None else None
            if node is None:
                return None
            value = node.get(W + "val")
            if value is None and tag in ("b", "bCs"):
                return "1"
            return value

        raw[style_id] = {
            "eastAsia": fonts.get(W + "eastAsia") if fonts is not None else None,
            "ascii": fonts.get(W + "ascii") if fonts is not None else None,
            "hAnsi": fonts.get(W + "hAnsi") if fonts is not None else None,
            "cs": fonts.get(W + "cs") if fonts is not None else None,
            "size": _val("sz"),
            "sizeCs": _val("szCs"),
            "color": _val("color"),
            "bold": _val("b"),
            "boldCs": _val("bCs"),
        }
        parent = style.find(W + "basedOn")
        based_on[style_id] = parent.get(W + "val") if parent is not None else None
    resolved: dict[str, dict[str, Any]] = {}

    def _resolve(style_id: str, trail: tuple[str, ...] = ()) -> dict[str, Any]:
        if style_id in resolved:
            return resolved[style_id]
        current = dict(raw.get(style_id) or {})
        parent_id = based_on.get(style_id)
        if parent_id and parent_id not in trail and parent_id in raw:
            parent = _resolve(parent_id, trail + (style_id,))
            for key, value in current.items():
                if value is None:
                    current[key] = parent.get(key)
        resolved[style_id] = current
        return current

    for style_id in raw:
        _resolve(style_id)
    return resolved


def _resolve_toc_style_ids(
    styles: etree._Element,
    specs: dict[int, dict[str, Any]],
) -> dict[int, str]:
    """Resolve registered TOC roles after Word renumbers custom style IDs."""

    levels = _toc_levels(specs)
    candidates: dict[int, list[str]] = {level: [] for level in levels}
    expected_names = {
        level: str(specs[level]["name"]).strip().lower()
        for level in levels
    }
    for style in styles.findall("w:style", NS):
        if style.get(W + "type") != "paragraph":
            continue
        style_id = style.get(W + "styleId", "")
        name = style.find("w:name", NS)
        style_name = (name.get(W + "val", "") if name is not None else "").strip().lower()
        for level in levels:
            if style_id == f"CZ_Toc{level}" or style_name == expected_names[level]:
                candidates[level].append(style_id)
    failures = {
        level: values
        for level, values in candidates.items()
        if len(values) != 1
    }
    if failures:
        raise NativeTocError(
            f"registered-TOC-style-resolution-not-unique: {failures}"
        )
    return {level: candidates[level][0] for level in levels}


def normalize_toc_result_format(stage: Path, params_path: Path) -> dict[str, Any]:
    """Apply the registered TOC role styles and close run-level typography.

    Word may copy source fonts such as 微软雅黑 into individual TOC result runs.
    The field and nested PAGEREF/hyperlink structure are preserved; only
    paragraph style ownership and typography properties are normalized.
    """

    params = json.loads(params_path.read_text(encoding="utf-8"))
    registry = params["wordStyleRegistry"]["paragraphStyles"]
    # Every level the registry defines, not a hard-coded three. When 课时
    # entered the table of contents its entries fell back to Word's built-in
    # 「toc 4」, which is larger and looser than our third level — the depth
    # read backwards on the page.
    specs = {
        level: registry[f"CZ_Toc{level}"]
        for level in range(1, 10)
        if f"CZ_Toc{level}" in registry
    }
    with ZipFile(stage) as package:
        files = {name: package.read(name) for name in package.namelist()}
    document = etree.fromstring(files["word/document.xml"])
    styles = etree.fromstring(files["word/styles.xml"])
    target_style_ids = _resolve_toc_style_ids(styles, specs)
    style_names: dict[str, str] = {}
    for style in styles.findall("w:style", NS):
        style_id = style.get(W + "styleId", "")
        name = style.find("w:name", NS)
        style_names[style_id] = (name.get(W + "val", "") if name is not None else "").lower()

    paragraphs = _toc_result_paragraphs(document)
    normalized = 0
    level_counts = {level: 0 for level in specs}
    for paragraph in paragraphs:
        p_style = paragraph.find("w:pPr/w:pStyle", NS)
        style_id = p_style.get(W + "val", "") if p_style is not None else ""
        style_name = style_names.get(style_id, "")
        level = None
        for candidate_level, spec_entry in specs.items():
            registered_name = str(spec_entry["name"]).strip().lower()
            if (
                style_id in {f"TOC{candidate_level}", f"CZ_Toc{candidate_level}"}
                or style_id == target_style_ids.get(candidate_level)
                or style_name in {f"toc {candidate_level}", f"目录 {candidate_level}", registered_name}
            ):
                level = candidate_level
                break
        if level is None:
            continue
        spec = specs[level]
        p_pr = paragraph.find(W + "pPr")
        if p_pr is None:
            p_pr = etree.Element(W + "pPr")
            paragraph.insert(0, p_pr)
        _set_val(p_pr, "pStyle", target_style_ids[level])
        for run in paragraph.findall(".//w:r", NS):
            r_pr = run.find(W + "rPr")
            if r_pr is None:
                r_pr = etree.Element(W + "rPr")
                run.insert(0, r_pr)
            fonts = r_pr.find(W + "rFonts")
            if fonts is None:
                fonts = etree.SubElement(r_pr, W + "rFonts")
            fonts.set(W + "eastAsia", str(spec["fontCn"]))
            fonts.set(W + "ascii", str(spec["fontAscii"]))
            fonts.set(W + "hAnsi", str(spec["fontAscii"]))
            fonts.set(W + "cs", str(spec.get("fontCs") or spec["fontAscii"]))
            half_points = str(int(round(float(spec["sizePt"]) * 2)))
            _set_val(r_pr, "sz", half_points)
            _set_val(r_pr, "szCs", half_points)
            _set_val(r_pr, "color", str(spec.get("color") or "000000").lstrip("#"))
            _set_on_off(r_pr, "b", bool(spec.get("bold", False)))
            _set_on_off(r_pr, "bCs", bool(spec.get("bold", False)))
        normalized += 1
        level_counts[level] += 1
    if normalized < 1:
        raise NativeTocError("no-TOC1-or-TOC2-result-paragraphs-to-normalize")

    files["word/document.xml"] = etree.tostring(
        document, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    temp = stage.with_name(f".{stage.name}.toc-format-{time.time_ns()}.tmp")
    with ZipFile(temp, "w", ZIP_DEFLATED) as package:
        for name, payload in files.items():
            package.writestr(name, payload)
    os.replace(temp, stage)
    return {
        "status": "pass",
        "normalizedParagraphCount": normalized,
        "levelCounts": {str(key): value for key, value in level_counts.items()},
        "fontEastAsia": {str(key): specs[key]["fontCn"] for key in specs},
        "fontSizePt": {str(key): specs[key]["sizePt"] for key in specs},
        "bold": {str(key): bool(specs[key].get("bold", False)) for key in specs},
        "resolvedStyleIds": {
            str(key): value for key, value in target_style_ids.items()
        },
        "preservedFieldsAndHyperlinks": True,
    }


def audit_toc_result_format(stage: Path, params_path: Path) -> dict[str, Any]:
    params = json.loads(params_path.read_text(encoding="utf-8"))
    registry = params["wordStyleRegistry"]["paragraphStyles"]
    # Every level the registry defines, not a hard-coded three. When 课时
    # entered the table of contents its entries fell back to Word's built-in
    # 「toc 4」, which is larger and looser than our third level — the depth
    # read backwards on the page.
    specs = {
        level: registry[f"CZ_Toc{level}"]
        for level in range(1, 10)
        if f"CZ_Toc{level}" in registry
    }
    with ZipFile(stage) as package:
        document = etree.fromstring(package.read("word/document.xml"))
        styles = etree.fromstring(package.read("word/styles.xml"))
    target_style_ids = _resolve_toc_style_ids(styles, specs)
    style_defaults = _style_rpr_defaults(styles)
    style_levels: dict[str, int] = {}
    accepted_names = {
        level: {str(spec["name"]).lower(), f"toc {level}", f"目录 {level}"}
        for level, spec in specs.items()
    }
    for style in styles.findall("w:style", NS):
        style_id = style.get(W + "styleId", "")
        name = style.find("w:name", NS)
        style_name = (name.get(W + "val", "") if name is not None else "").lower()
        for level in specs:
            if style_id in {f"TOC{level}", f"CZ_Toc{level}"} or style_name in accepted_names[level]:
                style_levels[style_id] = level
    for level, style_id in target_style_ids.items():
        style_levels[style_id] = level
    issues: list[dict[str, Any]] = []
    counts = {level: 0 for level in specs}
    for paragraph_index, paragraph in enumerate(_toc_result_paragraphs(document), start=1):
        p_style = paragraph.find("w:pPr/w:pStyle", NS)
        style_id = p_style.get(W + "val", "") if p_style is not None else ""
        level = style_levels.get(style_id)
        if level is None:
            continue
        counts[level] += 1
        spec = specs[level]
        expected_size = str(int(round(float(spec["sizePt"]) * 2)))
        expected_bold = "1" if bool(spec.get("bold", False)) else "0"
        for run_index, run in enumerate(paragraph.findall(".//w:r", NS), start=1):
            r_pr = run.find(W + "rPr")
            fonts = r_pr.find(W + "rFonts") if r_pr is not None else None
            actual = {
                "eastAsia": fonts.get(W + "eastAsia") if fonts is not None else None,
                "ascii": fonts.get(W + "ascii") if fonts is not None else None,
                "hAnsi": fonts.get(W + "hAnsi") if fonts is not None else None,
                "cs": fonts.get(W + "cs") if fonts is not None else None,
                "size": (
                    r_pr.find(W + "sz").get(W + "val")
                    if r_pr is not None and r_pr.find(W + "sz") is not None
                    else None
                ),
                "sizeCs": (
                    r_pr.find(W + "szCs").get(W + "val")
                    if r_pr is not None and r_pr.find(W + "szCs") is not None
                    else None
                ),
                "color": (
                    r_pr.find(W + "color").get(W + "val")
                    if r_pr is not None and r_pr.find(W + "color") is not None
                    else None
                ),
                "bold": (
                    r_pr.find(W + "b").get(W + "val", "1")
                    if r_pr is not None and r_pr.find(W + "b") is not None
                    else None
                ),
                "boldCs": (
                    r_pr.find(W + "bCs").get(W + "val", "1")
                    if r_pr is not None and r_pr.find(W + "bCs") is not None
                    else None
                ),
            }
            defaults = style_defaults.get(style_id) or {}

            def _pick(key: str, *fallbacks: Any) -> Any:
                if actual[key] is not None:
                    return actual[key]
                for fallback in fallbacks:
                    if fallback is not None:
                        return fallback
                return None

            def _on_off(value: Any) -> Any:
                if value is None:
                    return None
                return "0" if str(value).strip().lower() in ("0", "false", "off") else "1"

            # Word may collapse run properties that duplicate the owning TOC
            # style during clean-save; audit the effective typography instead.
            effective = {
                "eastAsia": _pick("eastAsia", defaults.get("eastAsia")),
                "ascii": _pick("ascii", defaults.get("ascii")),
                "hAnsi": _pick("hAnsi", defaults.get("hAnsi")),
                "cs": _pick("cs", defaults.get("cs"), defaults.get("hAnsi")),
                "size": _pick("size", defaults.get("size")),
                "sizeCs": _pick(
                    "sizeCs",
                    defaults.get("sizeCs"),
                    actual["size"],
                    defaults.get("size"),
                ),
                "color": _pick("color", defaults.get("color")),
                "bold": _on_off(_pick("bold", defaults.get("bold"), "0")),
                "boldCs": _on_off(
                    _pick("boldCs", defaults.get("boldCs"), actual["bold"], defaults.get("bold"), "0")
                ),
            }
            expected = {
                "eastAsia": str(spec["fontCn"]),
                "ascii": str(spec["fontAscii"]),
                "hAnsi": str(spec["fontAscii"]),
                "cs": str(spec.get("fontCs") or spec["fontAscii"]),
                "size": expected_size,
                "sizeCs": expected_size,
                "color": str(spec.get("color") or "000000").lstrip("#"),
                "bold": expected_bold,
                "boldCs": expected_bold,
            }
            if effective != expected:
                issues.append(
                    {
                        "paragraphIndex": paragraph_index,
                        "runIndex": run_index,
                        "level": level,
                        "actual": actual,
                        "effective": effective,
                        "expected": expected,
                    }
                )
    return {
        "status": "pass" if not issues and sum(counts.values()) > 0 else "fail",
        "levelCounts": {str(key): value for key, value in counts.items()},
        "issueCount": len(issues),
        "issues": issues[:50],
        "resolvedStyleIds": {
            str(key): value for key, value in target_style_ids.items()
        },
    }


def load_bound_json(path: Path, expected_hash: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    registered = str(value.get("outputSha256") or "")
    if registered != expected_hash:
        raise NativeTocError(
            f"HOLD_INPUT_DRIFT: {path} outputSha256={registered!r}, "
            f"candidate={expected_hash!r}"
        )
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def update_bound_manifests(
    *,
    semantic_manifest_path: Path,
    build_report_path: Path,
    before_hash: str,
    after_hash: str,
    native_report_path: Path,
    word_result: dict[str, Any],
) -> None:
    values = [
        (semantic_manifest_path, load_bound_json(semantic_manifest_path, before_hash)),
        (build_report_path, load_bound_json(build_report_path, before_hash)),
    ]
    receipt = {
        "status": "pass",
        "parentSha256": before_hash,
        "outputSha256": after_hash,
        "reportPath": str(native_report_path),
        "pageCount": word_result["pageCount"],
        "tocCount": word_result["tocCount"],
    }
    for path, value in values:
        value["builderOutputSha256"] = before_hash
        value["outputSha256"] = after_hash
        value["wordNativeTocUpdate"] = receipt
        write_json_atomic(path, value)


def process(
    *,
    candidate: Path,
    sandbox_root: Path,
    report_path: Path,
    semantic_manifest_path: Path,
    build_report_path: Path,
    params_path: Path,
    timeout: int,
) -> dict[str, Any]:
    if not candidate.is_file():
        raise NativeTocError(f"missing-candidate: {candidate}")
    before = inspect_docx(candidate)
    if before["tocFieldCount"] < 1:
        raise NativeTocError("missing-true-toc-field-before-word-update")
    if before["compatibilityModeValues"] != ["15"]:
        raise NativeTocError(
            f"invalid-compatibility-mode-before-word-update: "
            f"{before['compatibilityModeValues']}"
        )

    sandbox_root.mkdir(parents=True, exist_ok=True)
    stage = sandbox_root / (
        f"native-toc-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{before['sha256'][:12]}.docx"
    )
    if stage.exists():
        raise NativeTocError(f"sandbox-stage-already-exists: {stage}")
    shutil.copy2(candidate, stage)

    try:
        word_result = run_word_update(stage, timeout)
        toc_format_closure = normalize_toc_result_format(stage, params_path)
        clean_save_result = run_word_clean_save(stage, timeout)
        after = inspect_docx(stage)
        toc_format_audit = audit_toc_result_format(stage, params_path)
        if toc_format_audit["status"] != "pass":
            # Word may regenerate TOC result typography while saving; close the
            # format once more and let Word re-save as the final authority.
            normalize_toc_result_format(stage, params_path)
            clean_save_result = run_word_clean_save(stage, timeout)
            after = inspect_docx(stage)
            toc_format_audit = audit_toc_result_format(stage, params_path)
        if after["tocFieldCount"] < 1:
            raise NativeTocError("true-toc-field-lost-after-word-update")
        if after["tocResultVisibleCharacters"] < 1:
            raise NativeTocError("empty-toc-result-after-word-update")
        if after["placeholderCount"] != 0:
            raise NativeTocError("visible-toc-placeholder-survived-word-update")
        if after["compatibilityModeValues"] != ["15"]:
            raise NativeTocError(
                f"invalid-compatibility-mode-after-word-update: "
                f"{after['compatibilityModeValues']}"
            )
        if toc_format_audit["status"] != "pass":
            raise NativeTocError(
                "TOC result format drift survived Word clean-save: "
                f"{toc_format_audit}"
            )
        parity_pad: dict[str, Any] = {"status": "not-required"}
        current_pages = int(clean_save_result.get("pageCount") or 0)
        if current_pages % 2 == 1:
            pad_blank_final_section(stage)
            clean_save_result = run_word_clean_save(stage, timeout)
            if int(clean_save_result.get("pageCount") or 0) % 2 != 0:
                raise NativeTocError(
                    "word-even-page-parity-pad-failed: "
                    f"{clean_save_result.get('pageCount')}"
                )
            after = inspect_docx(stage)
            parity_pad = {
                "status": "added",
                "padPage": int(clean_save_result["pageCount"]),
                "pageCountAfterPad": int(clean_save_result["pageCount"]),
                "padPageProperties": "own-section-empty-header-footer-no-page-number",
            }
        shutil.copy2(stage, candidate)
        copied_hash = sha256_file(candidate)
        if copied_hash != after["sha256"]:
            raise NativeTocError(
                f"HOLD_INPUT_DRIFT: sandbox={after['sha256']}, candidate={copied_hash}"
            )
        update_bound_manifests(
            semantic_manifest_path=semantic_manifest_path,
            build_report_path=build_report_path,
            before_hash=before["sha256"],
            after_hash=after["sha256"],
            native_report_path=report_path,
            word_result=word_result,
        )
        build_report_value = load_bound_json(build_report_path, after["sha256"])
        build_report_value["evenPageParityPad"] = parity_pad
        write_json_atomic(build_report_path, build_report_value)
        report = {
            "schemaVersion": "chengziclass.word-native-toc-update.v1",
            "generatedAt": now_iso(),
            "status": "pass",
            "candidatePath": str(candidate),
            "sandboxBoundary": {
                "root": str(sandbox_root),
                "wordOpenedPath": str(stage),
                "projectPathOpenedDirectlyInWord": False,
            },
            "before": before,
            "wordReadback": word_result,
            "tocFormatClosure": toc_format_closure,
            "wordCleanSaveReadback": clean_save_result,
            "tocFormatAudit": toc_format_audit,
            "evenPageParityPad": parity_pad,
            "after": after,
            "hashChain": {
                "parentSha256": before["sha256"],
                "outputSha256": after["sha256"],
                "candidateReadbackSha256": copied_hash,
            },
            "manifestUpdates": [
                str(semantic_manifest_path),
                str(build_report_path),
            ],
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(report_path, report)
        stage.unlink()
        return report
    except Exception:
        # Preserve a failed Word-sandbox stage for bounded diagnosis.  The
        # isolated project candidate remains unaccepted and the workflow fails.
        raise


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This Word-native TOC updater is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py."
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--sandbox-root", type=Path, default=DEFAULT_SANDBOX_ROOT)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--semantic-manifest", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    report = process(
        candidate=args.candidate.resolve(),
        sandbox_root=args.sandbox_root.resolve(),
        report_path=args.report.resolve(),
        semantic_manifest_path=args.semantic_manifest.resolve(),
        build_report_path=args.build_report.resolve(),
        params_path=args.params.resolve(),
        timeout=args.timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
