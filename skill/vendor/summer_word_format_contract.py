#!/usr/bin/env python3
"""Shared DOCX format-contract helpers for summer Word production.

The formal Word workflow needs a package-level view of formatting, because
text can live in ordinary paragraphs, table cells, headers/footers, DrawingML
text boxes, or legacy VML text boxes.  This module deliberately works on the
OOXML package for deterministic inspection; Microsoft Word remains the
authority for opening, saving, pagination, and PDF export.
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

import hashlib
import json
import os
import posixpath
import re
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from lxml import etree


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
DEFAULT_PARAMS = _hi_env("HANDOUT_INTAKE_PARAMS_PATH", str(ROOT / "templates/summer-class-layout/summer_class_module_parameters.current.json"))

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V_NS = "urn:schemas-microsoft-com:vml"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
W = f"{{{W_NS}}}"
MC = f"{{{MC_NS}}}"
A = f"{{{A_NS}}}"
PIC = f"{{{PIC_NS}}}"
OFFICE_REL = f"{{{OFFICE_REL_NS}}}"

NS = {
    "w": W_NS,
    "mc": MC_NS,
    "v": V_NS,
    "a": A_NS,
    "pic": PIC_NS,
    "r": OFFICE_REL_NS,
    "pr": PACKAGE_REL_NS,
}

TEXT_PART_RE = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments\d*)\.xml$"
)
BOOLEAN_PROPERTIES = ("bold", "italic", "underline")
STRING_PROPERTIES = ("highlight", "color", "font", "size")
FORMAT_PROPERTIES = BOOLEAN_PROPERTIES + STRING_PROPERTIES
DEFAULT_COLORS = {None, "", "auto", "000000", "1F2933"}

# A visual-pass-through paragraph style is a semantic tag only.  It exists for
# source text boxes whose inherited Word formatting is already the approved
# layout authority.  Any of these fields would cause the tag to take over
# typography or paragraph geometry and could repaginate the document.
VISUAL_PASSTHROUGH_PARAGRAPH_STYLE_VISUAL_FIELDS = frozenset(
    {
        "fontCn",
        "fontAscii",
        "fontCs",
        "sizePt",
        "color",
        "bold",
        "italic",
        "lineDxa",
        "lineRule",
        "lineMultiple",
        "beforeDxa",
        "afterDxa",
        "alignment",
        "leftIndentDxa",
        "rightIndentDxa",
        "firstLineDxa",
        "hangingDxa",
        "outlineLevel",
        "tocLevel",
        "keepNext",
        "keepLines",
        "pageBreakBefore",
        "widowControl",
        "contextualSpacing",
        "tabStops",
    }
)


class FormatContractError(RuntimeError):
    """Raised when a package cannot satisfy the formal format contract."""


def visual_passthrough_paragraph_style_errors(style_id: str, spec: dict[str, Any]) -> list[str]:
    """Return hard failures for a semantic style that must not change layout.

    The generated OOXML definition must contain only its identity, name and a
    ``Normal`` base.  This deliberately rejects even harmless-looking visual
    defaults: a future edit that adds one would otherwise silently change text
    box height when the style is applied to a legacy or implicit source story.
    """

    errors: list[str] = []
    if spec.get("visualPassThrough") is not True:
        errors.append(f"{style_id}.visualPassThrough must be true")
    if spec.get("basedOnStyleId") != "Normal":
        errors.append(f"{style_id}.basedOnStyleId must be Normal")
    if spec.get("nextStyleId") not in {None, ""}:
        errors.append(f"{style_id}.nextStyleId is not permitted")
    visual_fields = sorted(
        field for field in VISUAL_PASSTHROUGH_PARAGRAPH_STYLE_VISUAL_FIELDS if field in spec
    )
    if visual_fields:
        errors.append(f"{style_id} declares visual fields: {', '.join(visual_fields)}")
    return errors


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_params(path: Path = DEFAULT_PARAMS) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("formatContract"), dict):
        raise FormatContractError("formatContract is missing from current parameters")
    return data


def resolve_root_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _relationship_part_name(part_name: str) -> str:
    """Return the package relationship part for one OOXML part."""

    parent = posixpath.dirname(part_name)
    basename = posixpath.basename(part_name)
    return posixpath.join(parent, "_rels", f"{basename}.rels")


def _resolve_relationship_target(part_name: str, target: str) -> str:
    """Resolve a package-relative relationship target to a package path."""

    normalized = str(target or "").replace("\\", "/")
    if normalized.startswith("/"):
        return normalized.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(part_name), normalized))


def drawingml_image_crop_records(files: dict[str, bytes], part_name: str = "word/document.xml") -> list[dict[str, Any]]:
    """Read DrawingML source rectangles and their bound media targets.

    The returned crop index is stable within the selected OOXML part and is
    used only as an evidence locator. Callers must select by the resolved
    relationship target, never by a guessed relationship ID alone.
    """

    if part_name not in files:
        return []
    relationship_targets: dict[str, dict[str, str]] = {}
    relationship_payload = files.get(_relationship_part_name(part_name))
    if relationship_payload is not None:
        rels_root = etree.fromstring(relationship_payload)
        for relationship in rels_root.findall("pr:Relationship", NS):
            if relationship.get("TargetMode") == "External":
                continue
            relationship_id = relationship.get("Id")
            target = relationship.get("Target")
            if relationship_id and target:
                normalized_target = str(target).replace("\\", "/")
                relationship_targets[relationship_id] = {
                    "relationshipTarget": normalized_target,
                    "packageTarget": _resolve_relationship_target(part_name, normalized_target),
                }

    root = etree.fromstring(files[part_name])
    records: list[dict[str, Any]] = []
    for crop_index, rect in enumerate(root.findall(".//a:srcRect", NS)):
        picture = rect
        while picture is not None and picture.tag != PIC + "pic":
            picture = picture.getparent()
        relationship_id = None
        if picture is not None:
            blip = picture.find(".//a:blip", NS)
            if blip is not None:
                relationship_id = blip.get(OFFICE_REL + "embed")
        records.append(
            {
                "part": part_name,
                "cropIndex": crop_index,
                "relationshipId": relationship_id,
                "relationshipTarget": (relationship_targets.get(str(relationship_id or "")) or {}).get("relationshipTarget"),
                "packageTarget": (relationship_targets.get(str(relationship_id or "")) or {}).get("packageTarget"),
                "attributes": dict(sorted(rect.attrib.items())),
            }
        )
    return records


def drawingml_image_crop_records_by_locator(files: dict[str, bytes]) -> dict[tuple[str, int], dict[str, Any]]:
    """Index DrawingML crop records by their OOXML part and crop index."""

    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for part_name in sorted(name for name in files if TEXT_PART_RE.fullmatch(name)):
        for record in drawingml_image_crop_records(files, part_name):
            indexed[(part_name, int(record["cropIndex"]))] = record
    return indexed


def local_name(node: etree._Element | None) -> str:
    return etree.QName(node).localname if node is not None else ""


def effective_walk(node: etree._Element) -> Iterator[etree._Element]:
    """Yield a single compatible branch for every mc:AlternateContent node.

    Word can store both a Choice and a Fallback branch.  Counting both branches
    doubles text and can manufacture false formatting differences.  The first
    Choice is the deterministic primary branch; Fallback is used only when no
    Choice exists.
    """

    if node.tag == MC + "AlternateContent":
        choices = [child for child in node if child.tag == MC + "Choice"]
        fallback = next((child for child in node if child.tag == MC + "Fallback"), None)
        selected = choices[0] if choices else fallback
        if selected is not None:
            yield from effective_walk(selected)
        return
    yield node
    for child in node:
        yield from effective_walk(child)


def effective_walk_without_nested_paragraphs(paragraph: etree._Element) -> Iterator[etree._Element]:
    """Walk one paragraph without absorbing paragraphs nested in a text box."""

    def visit(node: etree._Element, *, root: bool) -> Iterator[etree._Element]:
        if node.tag == MC + "AlternateContent":
            choices = [child for child in node if child.tag == MC + "Choice"]
            fallback = next((child for child in node if child.tag == MC + "Fallback"), None)
            selected = choices[0] if choices else fallback
            if selected is not None:
                yield from visit(selected, root=False)
            return
        if not root and node.tag == W + "p":
            return
        yield node
        for child in node:
            yield from visit(child, root=False)

    yield from visit(paragraph, root=True)


def nearest_paragraph(node: etree._Element) -> etree._Element | None:
    current = node
    while current is not None:
        if current.tag == W + "p":
            return current
        current = current.getparent()
    return None


def text_from_effective_nodes(nodes: Iterable[etree._Element]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in {W + "br", W + "cr"}:
            parts.append("\n")
    return "".join(parts)


def paragraph_text(paragraph: etree._Element) -> str:
    return text_from_effective_nodes(effective_walk_without_nested_paragraphs(paragraph))


def run_text(run: etree._Element) -> str:
    return text_from_effective_nodes(effective_walk(run))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u3000", " ")).strip()


def paragraph_style_id(paragraph: etree._Element) -> str | None:
    p_style = paragraph.find("w:pPr/w:pStyle", NS)
    return p_style.get(W + "val") if p_style is not None else None


def set_paragraph_style_id(paragraph: etree._Element, style_id: str) -> None:
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is None:
        p_pr = etree.Element(W + "pPr")
        paragraph.insert(0, p_pr)
    p_style = p_pr.find("w:pStyle", NS)
    if p_style is None:
        p_style = etree.Element(W + "pStyle")
        p_pr.insert(0, p_style)
    p_style.set(W + "val", style_id)


def paragraph_container(paragraph: etree._Element, part_name: str) -> str:
    ancestors: list[etree._Element] = []
    current = paragraph.getparent()
    while current is not None:
        ancestors.append(current)
        current = current.getparent()
    if any(node.tag == W + "txbxContent" for node in ancestors):
        if any(node.tag == f"{{{V_NS}}}textbox" for node in ancestors):
            return "vml-textbox"
        return "drawingml-textbox"
    if part_name.startswith("word/header"):
        return "header"
    if part_name.startswith("word/footer"):
        return "footer"
    if part_name == "word/footnotes.xml":
        return "footnote"
    if part_name == "word/endnotes.xml":
        return "endnote"
    if part_name.startswith("word/comments"):
        return "comment"
    return "body"


def paragraph_is_textbox(paragraph: etree._Element) -> bool:
    return paragraph_container(paragraph, "word/document.xml") in {"drawingml-textbox", "vml-textbox"}


def has_on_flag(r_pr: etree._Element | None, tag: str) -> bool:
    if r_pr is None:
        return False
    node = r_pr.find(f"w:{tag}", NS)
    if node is None:
        return False
    return str(node.get(W + "val") or "1").lower() not in {"0", "false", "off", "none", "nil"}


def underline_value(r_pr: etree._Element | None) -> bool:
    if r_pr is None:
        return False
    node = r_pr.find("w:u", NS)
    if node is None:
        return False
    return str(node.get(W + "val") or "single").lower() not in {"0", "false", "off", "none", "nil"}


def run_direct_format(run: etree._Element) -> dict[str, Any]:
    r_pr = run.find("w:rPr", NS)
    fonts = r_pr.find("w:rFonts", NS) if r_pr is not None else None
    font = None
    if fonts is not None:
        font_parts = [
            f"{name}={fonts.get(W + name)}"
            for name in ("ascii", "hAnsi", "eastAsia", "cs")
            if fonts.get(W + name)
        ]
        font = "|".join(font_parts) or None
    size = None
    if r_pr is not None:
        size_node = r_pr.find("w:sz", NS)
        if size_node is None:
            size_node = r_pr.find("w:szCs", NS)
        size = size_node.get(W + "val") if size_node is not None else None
    highlight_node = r_pr.find("w:highlight", NS) if r_pr is not None else None
    color_node = r_pr.find("w:color", NS) if r_pr is not None else None
    return {
        "bold": has_on_flag(r_pr, "b") or has_on_flag(r_pr, "bCs"),
        "italic": has_on_flag(r_pr, "i") or has_on_flag(r_pr, "iCs"),
        "underline": underline_value(r_pr),
        "highlight": highlight_node.get(W + "val") if highlight_node is not None else None,
        "color": color_node.get(W + "val") if color_node is not None else None,
        "font": font,
        "size": size,
    }


def direct_runs(paragraph: etree._Element) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in effective_walk_without_nested_paragraphs(paragraph):
        if node.tag != W + "r" or nearest_paragraph(node) is not paragraph:
            continue
        text = run_text(node)
        if not text:
            continue
        r_style = node.find("w:rPr/w:rStyle", NS)
        result.append(
            {
                "text": text,
                "direct": run_direct_format(node),
                "characterStyleId": r_style.get(W + "val") if r_style is not None else None,
            }
        )
    return result


def paragraph_records(
    root: etree._Element,
    part_name: str,
    *,
    include_empty_paragraphs: bool = False,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    container_counts: Counter[str] = Counter()
    text_counts: Counter[str] = Counter()
    for node in effective_walk(root):
        if node.tag != W + "p":
            continue
        raw_text = paragraph_text(node)
        normalized = normalize_text(raw_text)
        if not normalized and not include_empty_paragraphs:
            continue
        container = paragraph_container(node, part_name)
        container_counts[container] += 1
        text_counts[normalized] += 1
        records.append(
            {
                "part": part_name,
                "container": container,
                "containerIndex": container_counts[container],
                "textOccurrence": text_counts[normalized],
                "rawText": raw_text,
                "normalizedText": normalized,
                "isEmpty": not bool(normalized),
                "styleId": paragraph_style_id(node),
                "runs": direct_runs(node),
            }
        )
    for record in records:
        record["anchorId"] = stable_hash(
            {
                "text": record["normalizedText"],
                "occurrence": record["textOccurrence"],
                "container": record["container"],
            }
        )
    return records


def text_part_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(name for name in zf.namelist() if TEXT_PART_RE.fullmatch(name))


def defined_style_ids(zf: zipfile.ZipFile) -> set[str]:
    defined: set[str] = set()
    for name in ("word/styles.xml", "word/stylesWithEffects.xml"):
        if name not in zf.namelist():
            continue
        root = etree.fromstring(zf.read(name))
        for style in root.findall(".//w:style", NS):
            style_id = style.get(W + "styleId")
            if style_id:
                defined.add(style_id)
    return defined


def scan_docx(path: Path, *, include_empty_paragraphs: bool = False) -> dict[str, Any]:
    if not path.exists():
        raise FormatContractError(f"DOCX does not exist: {path}")
    with zipfile.ZipFile(path) as zf:
        invalid = zf.testzip()
        if invalid:
            raise FormatContractError(f"Invalid DOCX package entry: {invalid}")
        paragraphs: list[dict[str, Any]] = []
        for part_name in text_part_names(zf):
            root = etree.fromstring(zf.read(part_name))
            paragraphs.extend(
                paragraph_records(
                    root,
                    part_name,
                    include_empty_paragraphs=include_empty_paragraphs,
                )
            )
        styles = defined_style_ids(zf)
    summary = {
        "paragraphCount": len(paragraphs),
        "textBoxParagraphCount": sum(
            1 for item in paragraphs if item["container"] in {"drawingml-textbox", "vml-textbox"}
        ),
        "paragraphStyleCounts": dict(Counter(item.get("styleId") or "<implicit>" for item in paragraphs)),
        "emptyParagraphCount": sum(1 for item in paragraphs if item.get("isEmpty")),
        "directEmphasisRuns": {
            name: sum(
                1
                for item in paragraphs
                for run in item["runs"]
                if bool(run["direct"].get(name))
            )
            for name in BOOLEAN_PROPERTIES
        },
    }
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "definedStyleIds": sorted(styles),
        "paragraphs": paragraphs,
        "summary": summary,
    }


def document_format_digest(path: Path) -> dict[str, Any]:
    scan = scan_docx(path)
    paragraph_format_signature = [
        {
            "part": item["part"],
            "container": item["container"],
            "styleId": item["styleId"],
            "text": item["normalizedText"],
            "runs": item["runs"],
        }
        for item in scan["paragraphs"]
    ]
    state = {
        "sha256": scan["sha256"],
        "summary": scan["summary"],
        "styleClosureDigest": stable_hash(
            {
                "definedStyleIds": scan["definedStyleIds"],
                "paragraphStyles": [
                    (item["part"], item["container"], item["styleId"], item["normalizedText"])
                    for item in scan["paragraphs"]
                ],
            }
        ),
        "directFormatDigest": stable_hash(paragraph_format_signature),
    }
    state["formatDigest"] = stable_hash(state)
    return state


def snapshot_format_digests(paths: Iterable[str | Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            result[str(path)] = document_format_digest(path)
    return result


def format_snapshot_delta(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    paths = sorted(set(before) | set(after))
    result: list[dict[str, Any]] = []
    for path in paths:
        old = before.get(path)
        new = after.get(path)
        result.append(
            {
                "path": path,
                "parentSha256": old.get("sha256") if old else None,
                "outputSha256": new.get("sha256") if new else None,
                "parentFormatDigest": old.get("formatDigest") if old else None,
                "outputFormatDigest": new.get("formatDigest") if new else None,
                "changed": old != new,
                "summaryDelta": {
                    "paragraphCount": (new or {}).get("summary", {}).get("paragraphCount", 0)
                    - (old or {}).get("summary", {}).get("paragraphCount", 0),
                    "textBoxParagraphCount": (new or {}).get("summary", {}).get("textBoxParagraphCount", 0)
                    - (old or {}).get("summary", {}).get("textBoxParagraphCount", 0),
                },
            }
        )
    return result


def configured_bindings(params: dict[str, Any]) -> list[dict[str, Any]]:
    config = ((params.get("formatContract") or {}).get("sourceFormatBaseline") or {})
    bindings = config.get("sourceBindings") or []
    if not isinstance(bindings, list):
        raise FormatContractError("formatContract.sourceFormatBaseline.sourceBindings must be a list")
    return [item for item in bindings if isinstance(item, dict)]


def active_bindings(params: dict[str, Any]) -> list[dict[str, Any]]:
    from summer_scope_filter import path_in_scope

    result: list[dict[str, Any]] = []
    for binding in configured_bindings(params):
        target = resolve_root_path(str(binding.get("targetPath") or ""))
        key = str(binding.get("key") or "") or None
        if target and path_in_scope(target, key):
            result.append(binding)
    return result


def baseline_output_path(params: dict[str, Any]) -> Path:
    config = ((params.get("formatContract") or {}).get("sourceFormatBaseline") or {})
    value = config.get("outputPath")
    if not value:
        raise FormatContractError("formatContract.sourceFormatBaseline.outputPath is missing")
    return resolve_root_path(str(value))


def select_source_records(scan: dict[str, Any], selection: dict[str, Any] | None) -> dict[str, Any]:
    """Restrict a source baseline to the reviewed content anchors for one binding.

    A source can contain several independently-authored modules.  A binding is
    only allowed to compare the portion that has been reviewed for that target;
    otherwise an unrelated source-side teaching emphasis would be mislabeled as
    a production formatting regression.
    """

    if not selection:
        return scan
    containers = {str(value) for value in selection.get("containers") or []}
    style_ids = {str(value) for value in selection.get("styleIds") or []}
    exact_texts = {normalize_text(str(value)) for value in selection.get("textExact") or []}
    text_prefixes = [normalize_text(str(value)) for value in selection.get("textPrefixes") or []]
    records: list[dict[str, Any]] = []
    for record in scan.get("paragraphs") or []:
        if containers and str(record.get("container") or "") not in containers:
            continue
        if style_ids and str(record.get("styleId") or "") not in style_ids:
            continue
        text = str(record.get("normalizedText") or "")
        if exact_texts and text not in exact_texts:
            continue
        if text_prefixes and not any(text.startswith(prefix) for prefix in text_prefixes):
            continue
        records.append(record)
    result = dict(scan)
    result["paragraphs"] = records
    result["summary"] = {
        **dict(scan.get("summary") or {}),
        "baselineSelectedParagraphCount": len(records),
        "baselineSelection": selection,
    }
    return result


def build_source_format_baseline(params: dict[str, Any]) -> dict[str, Any]:
    config = ((params.get("formatContract") or {}).get("sourceFormatBaseline") or {})
    if config.get("status") != "required-before-layout-writes":
        raise FormatContractError("source format baseline is not marked required-before-layout-writes")
    results: list[dict[str, Any]] = []
    for binding in active_bindings(params):
        source = resolve_root_path(str(binding.get("sourcePath") or ""))
        target = resolve_root_path(str(binding.get("targetPath") or ""))
        expected_hash = str(binding.get("sourceSha256") or "")
        if not source.exists():
            raise FormatContractError(f"Baseline source is missing: {source}")
        actual_hash = sha256_file(source)
        if expected_hash and expected_hash != actual_hash:
            raise FormatContractError(
                f"HOLD_INPUT_DRIFT: baseline source hash mismatch for {source}; "
                f"expected={expected_hash}, actual={actual_hash}"
            )
        if str(binding.get("reviewStatus") or "") not in {"reviewed", "accepted", "approved"}:
            raise FormatContractError(f"Baseline binding is not reviewed: {binding.get('bindingId')}")
        comparison = binding.get("comparison") or {}
        source_scan = select_source_records(scan_docx(source), comparison.get("sourceSelection"))
        results.append(
            {
                "bindingId": binding.get("bindingId"),
                "key": binding.get("key"),
                "targetFileId": binding.get("targetFileId"),
                "sourcePath": str(source),
                "sourceSha256": actual_hash,
                "targetPath": str(target),
                "comparison": comparison,
                "approvedFormatExceptions": binding.get("approvedFormatExceptions") or [],
                "source": source_scan,
            }
        )
    report = {
        "schemaVersion": "chengziclass.summer-word-source-format-baseline.v1",
        "generatedAt": now_iso(),
        "parameterSource": str(DEFAULT_PARAMS),
        "propertySet": config.get("propertySet") or list(FORMAT_PROPERTIES),
        "documents": results,
    }
    report["baselineSha256"] = stable_hash(report)
    return report


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _char_values(record: dict[str, Any], property_name: str) -> list[Any] | None:
    values: list[Any] = []
    raw = ""
    for run in record.get("runs") or []:
        text = str(run.get("text") or "")
        raw += text
        value = (run.get("direct") or {}).get(property_name)
        if property_name in BOOLEAN_PROPERTIES:
            value = bool(value)
        values.extend([value] * len(text))
    return values if raw == record.get("rawText") else None


def _semantic_value(property_name: str, value: Any) -> Any:
    if property_name == "color":
        normalized = str(value).strip().upper() if value is not None else None
        return None if normalized in DEFAULT_COLORS else normalized
    if property_name == "highlight":
        normalized = str(value).strip().lower() if value is not None else None
        return None if normalized in {None, "", "none"} else normalized
    return value


def _spans_from_positions(raw_text: str, positions: list[int], *, limit: int = 20) -> list[dict[str, Any]]:
    if not positions:
        return []
    spans: list[dict[str, Any]] = []
    start = previous = positions[0]
    for position in positions[1:]:
        if position == previous + 1:
            previous = position
            continue
        spans.append({"start": start, "end": previous + 1, "text": raw_text[start : previous + 1]})
        start = previous = position
    spans.append({"start": start, "end": previous + 1, "text": raw_text[start : previous + 1]})
    return spans[:limit]


def compare_direct_formats(source: dict[str, Any], target: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "sourceFormatLost": [],
        "unapprovedFormatAdded": [],
        "layoutFormatChanged": [],
    }
    if source.get("rawText") != target.get("rawText"):
        return result
    for property_name in FORMAT_PROPERTIES:
        source_values = _char_values(source, property_name)
        target_values = _char_values(target, property_name)
        if source_values is None or target_values is None or len(source_values) != len(target_values):
            continue
        source_semantic = [_semantic_value(property_name, value) for value in source_values]
        target_semantic = [_semantic_value(property_name, value) for value in target_values]
        if property_name in BOOLEAN_PROPERTIES:
            lost = [i for i, (old, new) in enumerate(zip(source_semantic, target_semantic)) if old and not new]
            added = [i for i, (old, new) in enumerate(zip(source_semantic, target_semantic)) if new and not old]
            if lost:
                result["sourceFormatLost"].append(
                    {"property": property_name, "spans": _spans_from_positions(source["rawText"], lost)}
                )
            if added:
                result["unapprovedFormatAdded"].append(
                    {"property": property_name, "spans": _spans_from_positions(source["rawText"], added)}
                )
            continue
        changed = [
            i
            for i, (old, new) in enumerate(zip(source_semantic, target_semantic))
            if old != new and (old is not None or new is not None)
        ]
        if not changed:
            continue
        payload = {"property": property_name, "spans": _spans_from_positions(source["rawText"], changed)}
        if property_name in {"highlight", "color"}:
            result["sourceFormatLost"].append(payload)
            result["unapprovedFormatAdded"].append(payload)
        else:
            result["layoutFormatChanged"].append(payload)
    return result


def exception_allows(
    exceptions: list[dict[str, Any]],
    *,
    source: dict[str, Any],
    issue: dict[str, Any],
    direction: str,
) -> bool:
    for item in exceptions:
        if not isinstance(item, dict):
            continue
        if item.get("property") not in {None, issue.get("property")}:
            continue
        if item.get("direction") not in {None, direction, "both"}:
            continue
        match_text = str(item.get("text") or "")
        if match_text and match_text not in source.get("normalizedText", ""):
            continue
        if not item.get("reason"):
            continue
        return True
    return False


def compare_baseline_document(entry: dict[str, Any]) -> dict[str, Any]:
    source = entry.get("source") or {}
    source_path = Path(str(entry.get("sourcePath") or ""))
    target_path = Path(str(entry.get("targetPath") or ""))
    if not source_path.exists() or not target_path.exists():
        return {
            "bindingId": entry.get("bindingId"),
            "status": "missing",
            "sourceExists": source_path.exists(),
            "targetExists": target_path.exists(),
        }
    expected_source_hash = str(entry.get("sourceSha256") or "")
    actual_source_hash = sha256_file(source_path)
    if expected_source_hash != actual_source_hash:
        return {
            "bindingId": entry.get("bindingId"),
            "status": "input-drift",
            "expectedSourceSha256": expected_source_hash,
            "actualSourceSha256": actual_source_hash,
        }
    target = scan_docx(target_path)
    source_records = source.get("paragraphs") or []
    target_by_text: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in target.get("paragraphs") or []:
        target_by_text[str(record.get("normalizedText") or "")].append(record)
    used_target_indexes: Counter[str] = Counter()
    missing_anchors: list[dict[str, Any]] = []
    source_lost: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    layout_changes: list[dict[str, Any]] = []
    exceptions = entry.get("approvedFormatExceptions") or []
    for source_record in source_records:
        key = str(source_record.get("normalizedText") or "")
        candidates = target_by_text.get(key) or []
        position = used_target_indexes[key]
        if position >= len(candidates):
            missing_anchors.append(
                {
                    "anchorId": source_record.get("anchorId"),
                    "text": key[:180],
                    "container": source_record.get("container"),
                }
            )
            continue
        target_record = candidates[position]
        used_target_indexes[key] += 1
        differences = compare_direct_formats(source_record, target_record)
        for direction, destination in (
            ("source-lost", source_lost),
            ("target-added", added),
            ("layout-changed", layout_changes),
        ):
            source_key = {
                "source-lost": "sourceFormatLost",
                "target-added": "unapprovedFormatAdded",
                "layout-changed": "layoutFormatChanged",
            }[direction]
            for issue in differences[source_key]:
                if exception_allows(exceptions, source=source_record, issue=issue, direction=direction):
                    continue
                destination.append(
                    {
                        "anchorId": source_record.get("anchorId"),
                        "text": source_record.get("normalizedText", "")[:180],
                        **issue,
                    }
                )
    comparison = entry.get("comparison") or {}
    fail_for_unmatched = bool(comparison.get("requireAllSourceAnchorsInTarget", False))
    fail_for_layout = bool(comparison.get("failOnLayoutDirectFormatDrift", False))
    status = "pass"
    if source_lost or added or (fail_for_unmatched and missing_anchors) or (fail_for_layout and layout_changes):
        status = "fail"
    return {
        "bindingId": entry.get("bindingId"),
        "key": entry.get("key"),
        "targetPath": str(target_path),
        "targetSha256": target.get("sha256"),
        "status": status,
        "summary": {
            "sourceAnchors": len(source_records),
            "matchedAnchors": len(source_records) - len(missing_anchors),
            "unmatchedSourceAnchors": len(missing_anchors),
            "sourceFormatLost": len(source_lost),
            "unapprovedFormatAdded": len(added),
            "layoutFormatChanged": len(layout_changes),
        },
        "unmatchedSourceAnchors": missing_anchors[:100],
        "sourceFormatLost": source_lost[:100],
        "unapprovedFormatAdded": added[:100],
        "layoutFormatChanged": layout_changes[:100],
    }


def load_baseline(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "chengziclass.summer-word-source-format-baseline.v1":
        raise FormatContractError(f"Unsupported source format baseline schema: {data.get('schemaVersion')}")
    return data


def evaluate_source_baseline(baseline: dict[str, Any]) -> dict[str, Any]:
    results = [compare_baseline_document(item) for item in baseline.get("documents") or []]
    failed = [item for item in results if item.get("status") != "pass"]
    return {
        "status": "pass" if not failed else "fail",
        "results": results,
        "summary": {
            "documents": len(results),
            "failed": len(failed),
            "sourceFormatLost": sum(item.get("summary", {}).get("sourceFormatLost", 0) for item in results),
            "unapprovedFormatAdded": sum(item.get("summary", {}).get("unapprovedFormatAdded", 0) for item in results),
            "inputDrift": sum(1 for item in results if item.get("status") == "input-drift"),
        },
    }


def formal_word_documents() -> list[Path]:
    from summer_scope_filter import filter_paths

    formal_root = ROOT / "library/教辅资料/上海"
    return filter_paths(
        sorted(
            path
            for path in formal_root.rglob("word/*.docx")
            if "/缓存/" not in path.as_posix() and not path.name.startswith("~$")
        )
    )


def _record_locator(record: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(record.get("part") or ""),
        str(record.get("container") or ""),
        int(record.get("containerIndex") or 0),
        str(record.get("normalizedText") or ""),
    )


def _adapter_evidence_for_document(
    path: Path,
    scan: dict[str, Any],
    config: dict[str, Any],
    *,
    evidence_target_path: Path | None = None,
) -> list[dict[str, Any]]:
    evidence_config = config.get("adapterEvidence") or {}
    if not evidence_config:
        return []
    report_path_value = evidence_config.get("reportPath")
    if not report_path_value:
        return [{"code": "adapter-evidence-report-path-missing"}]
    report_path = resolve_root_path(str(report_path_value))
    if not report_path.exists():
        return [{"code": "adapter-evidence-report-missing", "path": str(report_path)}]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [{"code": "adapter-evidence-report-invalid", "detail": str(exc)}]
    if report.get("schemaVersion") != "chengziclass.summer-word-file-bound-format-adapter-report.v1":
        return [{"code": "adapter-evidence-report-schema-invalid", "detail": str(report.get("schemaVersion"))}]
    # A staging gate validates the staged bytes but must still locate the
    # adapter receipt by its immutable formal target.  The caller supplies
    # that binding explicitly; ordinary formal gates keep the default.
    resolved_path = (evidence_target_path or path).resolve()
    entry = next(
        (
            item
            for item in report.get("adapters") or []
            if isinstance(item, dict)
            and Path(str(item.get("targetPath") or "")).resolve() == resolved_path
        ),
        None,
    )
    if entry is None:
        return [{"code": "adapter-evidence-target-missing", "targetPath": str(path)}]
    issues: list[dict[str, Any]] = []
    if entry.get("status") != "pass":
        issues.append({"code": "adapter-evidence-status-not-pass", "detail": str(entry.get("status"))})
    if evidence_config.get("requireCurrentOutputHash", True) and entry.get("outputSha256") != scan.get("sha256"):
        issues.append(
            {
                "code": "adapter-evidence-output-not-current",
                "expectedOutputSha256": entry.get("outputSha256"),
                "actualOutputSha256": scan.get("sha256"),
            }
        )
    records_by_locator = {_record_locator(record): record for record in scan.get("paragraphs") or []}
    for expected in entry.get("styleAssignments") or []:
        if not isinstance(expected, dict):
            issues.append({"code": "adapter-evidence-style-assignment-invalid"})
            continue
        locator = _record_locator(expected)
        actual = records_by_locator.get(locator)
        if actual is None:
            issues.append({"code": "adapter-evidence-anchor-missing", "locator": list(locator)})
            continue
        wanted_style = expected.get("styleId")
        if actual.get("styleId") != wanted_style:
            issues.append(
                {
                    "code": "adapter-evidence-style-mismatch",
                    "locator": list(locator),
                    "expectedStyleId": wanted_style,
                    "actualStyleId": actual.get("styleId"),
                }
            )
    for expected in entry.get("directFormatRequirements") or []:
        if not isinstance(expected, dict):
            issues.append({"code": "adapter-evidence-direct-format-requirement-invalid"})
            continue
        locator = _record_locator(expected)
        actual = records_by_locator.get(locator)
        if actual is None:
            issues.append({"code": "adapter-evidence-direct-format-anchor-missing", "locator": list(locator)})
            continue
        property_name = str(expected.get("property") or "")
        wanted = expected.get("expected")
        values = [bool((run.get("direct") or {}).get(property_name)) for run in actual.get("runs") or []]
        if wanted is False and any(values):
            issues.append(
                {
                    "code": "adapter-evidence-direct-format-mismatch",
                    "locator": list(locator),
                    "property": property_name,
                    "expected": False,
                    "actual": True,
                }
            )
        elif wanted is True and not values:
            issues.append(
                {
                    "code": "adapter-evidence-direct-format-mismatch",
                    "locator": list(locator),
                    "property": property_name,
                    "expected": True,
                    "actual": False,
                }
            )
    crop_summaries = entry.get("imageCropCorrections") or []
    for summary in crop_summaries:
        if not isinstance(summary, dict):
            issues.append({"code": "adapter-evidence-image-crop-summary-invalid"})
            continue
        expected_matches = summary.get("expectedMatches")
        matched = summary.get("matched")
        changed = summary.get("changed")
        already_fixed = summary.get("alreadyFixed")
        if matched != expected_matches or not isinstance(changed, int) or not isinstance(already_fixed, int) or changed + already_fixed != matched:
            issues.append(
                {
                    "code": "adapter-evidence-image-crop-summary-mismatch",
                    "ruleId": summary.get("ruleId"),
                    "expectedMatches": expected_matches,
                    "matched": matched,
                    "changed": changed,
                    "alreadyFixed": already_fixed,
                }
            )
    crop_requirements = entry.get("imageCropRequirements") or []
    if crop_requirements:
        try:
            _crop_infos, crop_files = load_docx_files(path)
            crop_records = drawingml_image_crop_records_by_locator(crop_files)
        except (OSError, ValueError, etree.XMLSyntaxError) as exc:
            issues.append({"code": "adapter-evidence-image-crop-read-failed", "detail": str(exc)})
            crop_records = {}
        for expected in crop_requirements:
            if not isinstance(expected, dict):
                issues.append({"code": "adapter-evidence-image-crop-requirement-invalid"})
                continue
            locator = (str(expected.get("part") or ""), int(expected.get("cropIndex") or -1))
            actual = crop_records.get(locator)
            if actual is None:
                issues.append({"code": "adapter-evidence-image-crop-anchor-missing", "locator": list(locator)})
                continue
            if actual.get("relationshipTarget") != expected.get("relationshipTarget"):
                issues.append(
                    {
                        "code": "adapter-evidence-image-crop-target-mismatch",
                        "locator": list(locator),
                        "expected": expected.get("relationshipTarget"),
                        "actual": actual.get("relationshipTarget"),
                    }
                )
            if actual.get("attributes") != expected.get("expectedAttributes"):
                issues.append(
                    {
                        "code": "adapter-evidence-image-crop-attributes-mismatch",
                        "locator": list(locator),
                        "expected": expected.get("expectedAttributes"),
                        "actual": actual.get("attributes"),
                    }
                )
    return issues


def _serialized_paragraph_count(path: Path) -> int:
    """Count every serialised paragraph, including Choice/Fallback branches.

    ``scan_docx`` deliberately follows the branch Word renders.  Semantic
    coverage has a stricter responsibility: an AlternateContent fallback can
    be selected by another compatible reader, so its blank paragraphs cannot
    be omitted from the signed inventory.
    """

    count = 0
    with zipfile.ZipFile(path) as zf:
        for part_name in text_part_names(zf):
            root = etree.fromstring(zf.read(part_name))
            count += sum(1 for node in root.iter(W + "p"))
    return count


def _source_preserved_legacy_style_ids_from_manifest(
    manifest_path: Path,
    *,
    document_sha256: str,
    defined_style_ids: set[str],
    serialized_paragraph_count: int,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Read the complete external inventory before allowing a legacy style.

    A source-preserving DOCX intentionally keeps legacy style references in
    place.  They are never a blanket allow-list: every undefined reference
    must be present in the current, byte-bound inventory, whose paragraph
    count must equal every serialised ``w:p`` in every Word story branch.
    """

    errors: list[dict[str, Any]] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return set(), [{"code": "source-preserved-inventory-unreadable", "detail": str(exc)}]
    if not isinstance(manifest, dict):
        return set(), [{"code": "source-preserved-inventory-invalid"}]
    if manifest.get("schemaVersion") != "chengziclass.summer-word-semantic-text-inventory.v4":
        errors.append(
            {
                "code": "source-preserved-inventory-schema-invalid",
                "actual": manifest.get("schemaVersion"),
            }
        )
    if manifest.get("sourceDocumentSha256") != document_sha256:
        errors.append(
            {
                "code": "source-preserved-inventory-document-hash-drift",
                "expected": document_sha256,
                "actual": manifest.get("sourceDocumentSha256"),
            }
        )
    paragraphs = manifest.get("paragraphs")
    if not isinstance(paragraphs, list):
        return set(), [*errors, {"code": "source-preserved-inventory-paragraphs-invalid"}]
    summary = manifest.get("summary") or {}
    declared_count = summary.get("allSerializedParagraphs") if isinstance(summary, dict) else None
    if declared_count != serialized_paragraph_count or len(paragraphs) != serialized_paragraph_count:
        errors.append(
            {
                "code": "source-preserved-inventory-paragraph-count-incomplete",
                "declared": declared_count,
                "inventoried": len(paragraphs),
                "serialized": serialized_paragraph_count,
            }
        )

    legacy_style_ids: set[str] = set()
    for paragraph_index, paragraph in enumerate(paragraphs):
        if not isinstance(paragraph, dict):
            errors.append(
                {"code": "source-preserved-inventory-paragraph-invalid", "paragraphIndex": paragraph_index}
            )
            continue
        styles = [
            ("paragraph", paragraph.get("sourceParagraphStyleId")),
            ("paragraph-default-character", paragraph.get("sourceParagraphDefaultCharacterStyleId")),
        ]
        runs = paragraph.get("runs") or []
        if not isinstance(runs, list):
            errors.append(
                {"code": "source-preserved-inventory-runs-invalid", "paragraphIndex": paragraph_index}
            )
            continue
        for run_index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(
                    {
                        "code": "source-preserved-inventory-run-invalid",
                        "paragraphIndex": paragraph_index,
                        "runIndex": run_index,
                    }
                )
                continue
            styles.extend(
                [
                    ("run-character", run.get("sourceCharacterStyleId")),
                    ("run-effective-character", run.get("effectiveSourceCharacterStyleId")),
                ]
            )
        for kind, raw_style_id in styles:
            if not raw_style_id:
                continue
            style_id = str(raw_style_id)
            if style_id in defined_style_ids:
                continue
            if style_id.startswith("CZ_"):
                errors.append(
                    {
                        "code": "source-preserved-inventory-undefined-canonical-style",
                        "styleId": style_id,
                        "kind": kind,
                        "paragraphIndex": paragraph_index,
                    }
                )
                continue
            legacy_style_ids.add(style_id)
    return legacy_style_ids, errors


def style_closure_for_document(
    path: Path,
    params: dict[str, Any],
    *,
    adapter_evidence_target_path: Path | None = None,
    semantic_manifest_path: Path | None = None,
) -> dict[str, Any]:
    config = (params.get("formatContract") or {}).get("styleClosure") or {}
    registry = (params.get("wordStyleRegistry") or {}).get("paragraphStyles") or {}
    prefix = str(config.get("canonicalStylePrefix") or "CZ_")
    allowed = set(config.get("allowedStyleIds") or []) | set(registry)
    allow_implicit = bool(config.get("allowImplicitParagraphStyle", False))
    containers = {str(value) for value in config.get("containers") or []}
    tracked_style_ids = {str(value) for value in config.get("trackedStyleIds") or []}
    forbidden_style_ids = {str(value) for value in config.get("forbiddenStyleIds") or []}
    preservation = config.get("sourcePreservingSemanticLabels") or {}
    source_preserving = (
        isinstance(preservation, dict)
        and preservation.get("mode") == "external-inventory-source-preserving"
    )
    declared_source_preserved_styles = preservation.get("legacyStyleIds") or []
    source_preserved_style_ids = {
        str(value) for value in declared_source_preserved_styles
    } if source_preserving and isinstance(declared_source_preserved_styles, list) else set()
    configuration_errors: list[dict[str, Any]] = []
    if source_preserving and (
        not isinstance(declared_source_preserved_styles, list)
        or not all(isinstance(value, str) and value for value in declared_source_preserved_styles)
        or len(source_preserved_style_ids) != len(declared_source_preserved_styles)
    ):
        configuration_errors.append(
            {
                "code": "source-preserved-legacy-style-policy-invalid",
                "actual": declared_source_preserved_styles,
            }
        )
    if source_preserving and preservation.get("sourceInventoryAnchored") is not True:
        configuration_errors.append({"code": "source-preserved-inventory-anchor-required"})
    scan = scan_docx(path, include_empty_paragraphs=bool(config.get("includeBlankParagraphs", False)))
    defined = set(scan.get("definedStyleIds") or [])
    inventory_legacy_style_ids: set[str] = set()
    serialized_paragraph_count: int | None = None
    if source_preserving:
        if semantic_manifest_path is None:
            configuration_errors.append({"code": "source-preserved-inventory-required"})
        else:
            serialized_paragraph_count = _serialized_paragraph_count(path)
            inventory_legacy_style_ids, inventory_errors = _source_preserved_legacy_style_ids_from_manifest(
                semantic_manifest_path,
                document_sha256=str(scan.get("sha256") or ""),
                defined_style_ids=defined,
                serialized_paragraph_count=serialized_paragraph_count,
            )
            configuration_errors.extend(inventory_errors)
            if source_preserved_style_ids != inventory_legacy_style_ids:
                configuration_errors.append(
                    {
                        "code": "source-preserved-legacy-style-inventory-mismatch",
                        "declared": sorted(source_preserved_style_ids),
                        "inventoried": sorted(inventory_legacy_style_ids),
                    }
                )
    unresolved: list[dict[str, Any]] = []
    noncanonical: list[dict[str, Any]] = []
    implicit: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    required_definitions = (
        set() if source_preserving else {str(value) for value in config.get("requiredDefinedStyleIds") or []}
    )
    for style_id in sorted(required_definitions - defined):
        unresolved.append({"styleId": style_id, "kind": "required-definition"})
    tracked_paragraphs = 0
    for paragraph in scan.get("paragraphs") or []:
        style_id = paragraph.get("styleId")
        location = {
            "part": paragraph.get("part"),
            "container": paragraph.get("container"),
            "containerIndex": paragraph.get("containerIndex"),
            "text": str(paragraph.get("normalizedText") or "")[:180],
        }
        container_matches = not containers or str(paragraph.get("container") or "") in containers
        style_matches = not tracked_style_ids or str(style_id or "") in tracked_style_ids
        relevant = container_matches and style_matches
        paragraph_source_preserved = bool(style_id) and str(style_id) in source_preserved_style_ids
        if style_id in forbidden_style_ids and not paragraph_source_preserved:
            noncanonical.append({"styleId": style_id, "kind": "forbidden", **location})
            if style_id not in defined:
                unresolved.append({"styleId": style_id, "kind": "forbidden", **location})
        if not relevant:
            continue
        tracked_paragraphs += 1
        if not style_id:
            implicit.append(location)
            if not allow_implicit:
                noncanonical.append({"styleId": "<implicit>", **location})
            continue
        if paragraph_source_preserved:
            quarantined.append({"styleId": style_id, "kind": "paragraph", **location})
        else:
            if style_id not in defined:
                unresolved.append({"styleId": style_id, **location})
            if not (style_id.startswith(prefix) or style_id in allowed):
                noncanonical.append({"styleId": style_id, **location})
        for run in paragraph.get("runs") or []:
            character_style = run.get("characterStyleId")
            if not character_style:
                continue
            if str(character_style) in source_preserved_style_ids:
                quarantined.append({"styleId": character_style, "kind": "character", **location})
                continue
            if character_style not in defined:
                unresolved.append({"styleId": character_style, "kind": "character", **location})
            if not (character_style.startswith(prefix) or character_style in allowed):
                noncanonical.append({"styleId": character_style, "kind": "character", **location})
    adapter_evidence_issues = _adapter_evidence_for_document(
        path,
        scan,
        config,
        evidence_target_path=adapter_evidence_target_path,
    )
    status = "pass" if not configuration_errors and not unresolved and not noncanonical and not adapter_evidence_issues else "fail"
    return {
        "path": str(path),
        "sha256": scan.get("sha256"),
        "status": status,
        "summary": {
            "definedStyles": len(defined),
            "paragraphs": scan.get("summary", {}).get("paragraphCount", 0),
            "textBoxParagraphs": scan.get("summary", {}).get("textBoxParagraphCount", 0),
            "unresolvedStyleReferences": len(unresolved),
            "noncanonicalContentStyles": len(noncanonical),
            "implicitParagraphStyles": len(implicit),
            "sourcePreservedLegacyStyleReferences": len(quarantined),
            "trackedParagraphs": tracked_paragraphs,
            "adapterEvidenceIssues": len(adapter_evidence_issues),
        },
        "unresolvedStyleReferences": unresolved[:150],
        "noncanonicalContentStyles": noncanonical[:150],
        "implicitParagraphStyles": implicit[:150],
        "sourcePreservedLegacyStyleReferences": quarantined[:150],
        "sourcePreservedLegacyStyleIdsFromInventory": sorted(inventory_legacy_style_ids),
        "sourcePreservedInventoryPath": str(semantic_manifest_path) if semantic_manifest_path else None,
        "sourcePreservedSerializedParagraphCount": serialized_paragraph_count,
        "configurationErrors": configuration_errors,
        "adapterEvidenceIssues": adapter_evidence_issues[:150],
    }


def coverage_for_active_scope(params: dict[str, Any]) -> dict[str, Any]:
    from summer_scope_filter import key_for_path

    config = params.get("formatContract") or {}
    docs = formal_word_documents()
    enforced_keys = {str(value) for value in config.get("enforcedKeys") or []}
    enforced_target_paths = {
        resolve_root_path(str(value)).resolve() for value in config.get("enforcedTargetPaths") or []
    }
    if enforced_target_paths:
        covered_docs = [path for path in docs if path.resolve() in enforced_target_paths]
    else:
        covered_docs = [
            path for path in docs if not enforced_keys or str(key_for_path(path) or "") in enforced_keys
        ]
    bound_paths = {
        resolve_root_path(str(binding.get("targetPath") or "")).resolve()
        for binding in active_bindings(params)
        if binding.get("targetPath")
    }
    unbound = [str(path) for path in covered_docs if path.resolve() not in bound_paths]
    require_coverage = bool(config.get("requireCoverageForFormalScope", True))
    status = "pass" if not require_coverage or not unbound else "fail"
    return {
        "status": status,
        "formalDocuments": [str(path) for path in docs],
        "coveredFormalDocuments": [str(path) for path in covered_docs],
        "enforcedKeys": sorted(enforced_keys),
        "enforcedTargetPaths": sorted(str(path) for path in enforced_target_paths),
        "boundTargetPaths": sorted(str(path) for path in bound_paths),
        "unboundFormalDocuments": unbound,
        "requireCoverageForFormalScope": require_coverage,
    }


def ensure_paragraph_styles(
    files: dict[str, bytes], style_specs: dict[str, dict[str, Any]], style_ids: Iterable[str]
) -> list[str]:
    """Create/update registered paragraph styles from current parameters.

    This is intentionally limited to named style definitions.  It never writes
    font sizes or spacing into individual paragraphs; file-bound adapters only
    select registered styles.
    """

    if "word/styles.xml" in files:
        root = etree.fromstring(files["word/styles.xml"])
    else:
        root = etree.Element(W + "styles", nsmap={"w": W_NS})
    written: list[str] = []
    for style_id in style_ids:
        spec = style_specs.get(style_id)
        if not isinstance(spec, dict):
            raise FormatContractError(f"Missing registered style specification: {style_id}")
        for old in list(root.findall("w:style", NS)):
            if old.get(W + "styleId") == style_id:
                root.remove(old)
        style = etree.Element(W + "style")
        style.set(W + "type", "paragraph")
        style.set(W + "styleId", style_id)
        style.set(W + "customStyle", "1")
        name = etree.SubElement(style, W + "name")
        name.set(W + "val", str(spec.get("name") or style_id))
        based_on = etree.SubElement(style, W + "basedOn")
        based_on.set(W + "val", str(spec.get("basedOnStyleId") or "Normal"))
        if spec.get("visualPassThrough") is True:
            errors = visual_passthrough_paragraph_style_errors(style_id, spec)
            if errors:
                raise FormatContractError("Invalid visual-pass-through paragraph style: " + "; ".join(errors))
            # No w:pPr/w:rPr is intentional.  The source paragraph's direct
            # properties and Normal inheritance remain the sole visual source.
            written.append(style_id)
            root.append(style)
            continue
        next_style = spec.get("nextStyleId")
        if next_style:
            node = etree.SubElement(style, W + "next")
            node.set(W + "val", str(next_style))
        p_pr = etree.SubElement(style, W + "pPr")
        spacing = etree.SubElement(p_pr, W + "spacing")
        for key, fallback in (("before", 0), ("after", 0), ("line", 240)):
            source = {"before": "beforeDxa", "after": "afterDxa", "line": "lineDxa"}[key]
            spacing.set(W + key, str(spec.get(source, fallback)))
        spacing.set(W + "lineRule", str(spec.get("lineRule") or "auto"))
        if spec.get("alignment"):
            alignment = etree.SubElement(p_pr, W + "jc")
            alignment.set(W + "val", str(spec["alignment"]))
        ind_keys = {
            "left": spec.get("leftIndentDxa"),
            "right": spec.get("rightIndentDxa"),
            "firstLine": spec.get("firstLineDxa"),
            "hanging": spec.get("hangingDxa"),
        }
        if any(value is not None for value in ind_keys.values()):
            ind = etree.SubElement(p_pr, W + "ind")
            for key, value in ind_keys.items():
                if value is not None:
                    ind.set(W + key, str(value))
        r_pr = etree.SubElement(style, W + "rPr")
        fonts = etree.SubElement(r_pr, W + "rFonts")
        east_asia = str(spec.get("fontCn") or "宋体")
        ascii_font = str(spec.get("fontAscii") or "Times New Roman")
        fonts.set(W + "eastAsia", east_asia)
        fonts.set(W + "ascii", ascii_font)
        fonts.set(W + "hAnsi", ascii_font)
        fonts.set(W + "cs", str(spec.get("fontCs") or ascii_font))
        size_half_points = str(int(round(float(spec.get("sizePt", 12)) * 2)))
        for tag in ("sz", "szCs"):
            size = etree.SubElement(r_pr, W + tag)
            size.set(W + "val", size_half_points)
        color = etree.SubElement(r_pr, W + "color")
        color.set(W + "val", str(spec.get("color") or "000000"))
        if spec.get("bold") is True:
            etree.SubElement(r_pr, W + "b")
            etree.SubElement(r_pr, W + "bCs")
        if spec.get("italic") is True:
            etree.SubElement(r_pr, W + "i")
            etree.SubElement(r_pr, W + "iCs")
        written.append(style_id)
        root.append(style)
    files["word/styles.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return written


def load_docx_files(path: Path) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        infos = zf.infolist()
        return infos, {info.filename: zf.read(info.filename) for info in infos}


def write_docx_files(path: Path, infos: list[zipfile.ZipInfo], files: dict[str, bytes]) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.format-contract-", suffix=".docx", dir=path.parent)
    os.close(descriptor)
    tmp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
            written: set[str] = set()
            for info in infos:
                if info.filename not in files:
                    continue
                out.writestr(info, files[info.filename])
                written.add(info.filename)
            for name in sorted(set(files) - written):
                out.writestr(name, files[name])
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def remove_direct_bold(paragraph: etree._Element) -> int:
    changed = 0
    for node in effective_walk_without_nested_paragraphs(paragraph):
        if node.tag != W + "r" or nearest_paragraph(node) is not paragraph:
            continue
        r_pr = node.find("w:rPr", NS)
        if r_pr is None:
            continue
        for tag in ("b", "bCs"):
            value = r_pr.find(f"w:{tag}", NS)
            if value is not None:
                r_pr.remove(value)
                changed += 1
    return changed


def character_style_id(run: etree._Element) -> str | None:
    """Return the explicitly selected character-style ID for one run."""

    node = run.find("w:rPr/w:rStyle", NS)
    return node.get(W + "val") if node is not None else None


def rewrite_run_character_format(
    run: etree._Element,
    style_id: str | None,
    *,
    preserve_direct_properties: bool = False,
) -> None:
    """Assign a named character style, optionally retaining audited visual overlays.

    The normal construction path replaces the whole run-property node.  A
    file-bound semantic adapter can instead retain its inventory-recorded
    direct properties while replacing only a legacy character-style reference.
    That is used when the source's approved visual baseline has not yet been
    represented by a paragraph style; the semantic audit then requires an
    exact property-for-property match, so retained formatting is never an
    unclassified escape hatch.
    """

    old = run.find("w:rPr", NS)
    if preserve_direct_properties:
        if old is None:
            if not style_id:
                return
            old = etree.Element(W + "rPr")
            run.insert(0, old)
        for old_style in list(old.findall("w:rStyle", NS)):
            old.remove(old_style)
        if style_id:
            r_style = etree.Element(W + "rStyle")
            r_style.set(W + "val", style_id)
            old.insert(0, r_style)
        elif len(old) == 0:
            run.remove(old)
        return

    if old is not None:
        run.remove(old)
    if not style_id:
        return
    r_pr = etree.Element(W + "rPr")
    r_style = etree.SubElement(r_pr, W + "rStyle")
    r_style.set(W + "val", style_id)
    run.insert(0, r_pr)


def _character_style_run_properties(style: etree._Element, spec: dict[str, Any]) -> None:
    """Materialise the registered character-style properties in ``style``."""

    r_pr = etree.SubElement(style, W + "rPr")
    raw_fragments = spec.get("rawRPrXml")
    if raw_fragments is not None:
        if not isinstance(raw_fragments, list) or not all(isinstance(item, str) for item in raw_fragments):
            raise FormatContractError("rawRPrXml must be a list of XML character-property fragments")
        for fragment in raw_fragments:
            try:
                child = etree.fromstring(fragment.encode("utf-8"))
            except etree.XMLSyntaxError as exc:
                raise FormatContractError(f"Invalid raw character-property fragment: {exc}") from exc
            if child.tag == W + "rStyle":
                raise FormatContractError("rawRPrXml must not contain w:rStyle")
            r_pr.append(child)
        return
    if not spec.get("inheritsParagraphFont", False):
        fonts = etree.SubElement(r_pr, W + "rFonts")
        east_asia = str(spec.get("fontCn") or "宋体")
        ascii_font = str(spec.get("fontAscii") or "Times New Roman")
        fonts.set(W + "eastAsia", east_asia)
        fonts.set(W + "ascii", ascii_font)
        fonts.set(W + "hAnsi", ascii_font)
        fonts.set(W + "cs", str(spec.get("fontCs") or ascii_font))
    if not spec.get("inheritsSize", False) and spec.get("sizePt") is not None:
        half_points = str(int(round(float(spec["sizePt"]) * 2)))
        for tag in ("sz", "szCs"):
            node = etree.SubElement(r_pr, W + tag)
            node.set(W + "val", half_points)
    color = spec.get("color")
    if color:
        node = etree.SubElement(r_pr, W + "color")
        node.set(W + "val", str(color).lstrip("#"))
    # A character style must be able to deliberately turn off a bold
    # paragraph style.  Omitting ``w:b`` here would only inherit the parent
    # weight, which is wrong for a reviewed non-bold table-header cell.  Write
    # both the regular and complex-script variants so the result is stable in
    # Word regardless of the script branch it selects.
    if spec.get("bold") is True:
        etree.SubElement(r_pr, W + "b")
        etree.SubElement(r_pr, W + "bCs")
    elif spec.get("bold") is False:
        for tag in ("b", "bCs"):
            node = etree.SubElement(r_pr, W + tag)
            node.set(W + "val", "0")
    if spec.get("italic") is True:
        etree.SubElement(r_pr, W + "i")
        etree.SubElement(r_pr, W + "iCs")
    underline = spec.get("underline")
    if underline and str(underline).lower() not in {"none", "false", "off"}:
        node = etree.SubElement(r_pr, W + "u")
        node.set(W + "val", str(underline))
    if spec.get("highlight"):
        node = etree.SubElement(r_pr, W + "highlight")
        node.set(W + "val", str(spec["highlight"]))
    if spec.get("verticalAlign"):
        node = etree.SubElement(r_pr, W + "vertAlign")
        node.set(W + "val", str(spec["verticalAlign"]))
    shading = spec.get("shading")
    if isinstance(shading, dict):
        node = etree.SubElement(r_pr, W + "shd")
        for key in ("val", "color", "fill"):
            if shading.get(key) is not None:
                node.set(W + key, str(shading[key]))
    if spec.get("emphasisMark"):
        node = etree.SubElement(r_pr, W + "em")
        node.set(W + "val", str(spec["emphasisMark"]))
    if spec.get("smallCaps") is True:
        etree.SubElement(r_pr, W + "smallCaps")


def ensure_character_styles(
    files: dict[str, bytes], style_specs: dict[str, dict[str, Any]], style_ids: Iterable[str]
) -> list[str]:
    """Create/update registered character styles without touching paragraphs."""

    if "word/styles.xml" in files:
        root = etree.fromstring(files["word/styles.xml"])
    else:
        root = etree.Element(W + "styles", nsmap={"w": W_NS})
    written: list[str] = []
    for style_id in style_ids:
        spec = style_specs.get(style_id)
        if not isinstance(spec, dict):
            raise FormatContractError(f"Missing registered character-style specification: {style_id}")
        for old in list(root.findall("w:style", NS)):
            if old.get(W + "styleId") == style_id:
                root.remove(old)
        style = etree.Element(W + "style")
        style.set(W + "type", "character")
        style.set(W + "styleId", style_id)
        style.set(W + "customStyle", "1")
        name = etree.SubElement(style, W + "name")
        name.set(W + "val", str(spec.get("name") or style_id))
        based_on = spec.get("basedOnStyleId")
        if based_on:
            node = etree.SubElement(style, W + "basedOn")
            node.set(W + "val", str(based_on))
        _character_style_run_properties(style, spec)
        root.append(style)
        written.append(style_id)
    files["word/styles.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return written
