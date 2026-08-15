#!/usr/bin/env python3
"""Build a deterministic inventory of visible logical objects in source Word files.

The inventory is intentionally semantic-neutral.  It records what exists and
where it belongs; the reviewed blueprint decides what each object means.  Word
AlternateContent Choice/Fallback serialisations are merged into one logical
object so compatibility branches cannot create false duplicates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

from lxml import etree


CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
SCHEMA_VERSION = "chengziclass.word-source-object-manifest.v1"
BLUEPRINT_SCHEMA_VERSION = "chengziclass.semantic-handout-blueprint.v1"

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}
W = f"{{{NS['w']}}}"
R_EMBED = f"{{{NS['r']}}}embed"
R_ID = f"{{{NS['r']}}}id"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceObjectManifestError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def text_without_textboxes(node: etree._Element) -> str:
    pieces: list[str] = []
    for item in node.iter():
        if any(ancestor.tag == W + "txbxContent" for ancestor in item.iterancestors()):
            continue
        if item.tag in {W + "t", f"{{{NS['m']}}}t"}:
            pieces.append(item.text or "")
        elif item.tag == W + "tab":
            pieces.append("\t")
        elif item.tag in {W + "br", W + "cr"}:
            pieces.append("\n")
    return normalize_text("".join(pieces))


def draws_a_blank(node: etree._Element) -> bool:
    """Whether the paragraph draws an answer line.

    An underlined run of spaces prints a line and is where a student writes,
    but it holds no text — so a paragraph made only of one counted as invisible
    and never became a source object. Nothing then required it to be owned, and
    three of them were dropped as layout spacers without a word.
    """
    for run in node.iter(W + "r"):
        if any(a.tag == W + "txbxContent" for a in run.iterancestors()):
            continue
        properties = run.find(W + "rPr")
        underline = properties.find(W + "u") if properties is not None else None
        if underline is None or (underline.get(W + "val") or "single") == "none":
            continue
        text = "".join(item.text or "" for item in run.findall(W + "t"))
        if text and not text.strip():
            return True
    return False


def full_text(node: etree._Element) -> str:
    pieces: list[str] = []
    for item in node.iter():
        if item.tag in {W + "t", f"{{{NS['m']}}}t"}:
            pieces.append(item.text or "")
        elif item.tag == W + "tab":
            pieces.append("\t")
        elif item.tag in {W + "br", W + "cr"}:
            pieces.append("\n")
    return normalize_text("".join(pieces))


def relative_content_position(
    container: etree._Element,
    target: etree._Element,
) -> dict[str, Any]:
    """Describe an object's stable position relative to visible container text."""

    before: list[str] = []
    after: list[str] = []
    found = False
    for item in container.iter():
        if item is target or item == target:
            found = True
            continue
        if any(ancestor.tag == W + "txbxContent" for ancestor in item.iterancestors()):
            continue
        value = ""
        if item.tag in {W + "t", f"{{{NS['m']}}}t"}:
            value = item.text or ""
        elif item.tag == W + "tab":
            value = "\t"
        elif item.tag in {W + "br", W + "cr"}:
            value = "\n"
        if not value:
            continue
        (after if found else before).append(value)
    before_text = normalize_text("".join(before))
    after_text = normalize_text("".join(after))
    return {
        "precedingVisibleCharacterCount": len("".join(before)),
        "followingVisibleCharacterCount": len("".join(after)),
        "precedingText": before_text[-120:],
        "followingText": after_text[:120],
    }


def enclosing_table_cell(
    container: etree._Element,
    target: etree._Element,
) -> tuple[etree._Element, dict[str, int]] | None:
    if container.tag != W + "tbl":
        return None
    cell = next(
        (ancestor for ancestor in target.iterancestors() if ancestor.tag == W + "tc"),
        None,
    )
    row = next(
        (ancestor for ancestor in target.iterancestors() if ancestor.tag == W + "tr"),
        None,
    )
    if cell is None or row is None:
        return None
    rows = container.findall("./" + W + "tr")
    if row not in rows:
        return None
    cells = row.findall("./" + W + "tc")
    if cell not in cells:
        return None
    return cell, {
        "rowIndex": rows.index(row),
        "columnIndex": cells.index(cell),
    }


def table_cell_position(
    container: etree._Element,
    target: etree._Element,
) -> dict[str, int] | None:
    context = enclosing_table_cell(container, target)
    return context[1] if context is not None else None


def table_cell_relative_position(
    container: etree._Element,
    target: etree._Element,
) -> dict[str, Any] | None:
    context = enclosing_table_cell(container, target)
    if context is None:
        return None
    cell, _ = context
    return relative_content_position(cell, target)


def relationship_map(parts: dict[str, bytes]) -> dict[str, str]:
    name = "word/_rels/document.xml.rels"
    if name not in parts:
        return {}
    root = etree.fromstring(parts[name])
    result: dict[str, str] = {}
    for relation in root.xpath("./rel:Relationship", namespaces=NS):
        rid = relation.get("Id")
        target = relation.get("Target")
        if not rid or not target or target.startswith(("http:", "https:")):
            continue
        result[rid] = posixpath.normpath(posixpath.join("word", target))
    return result


def source_document_records(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    records = blueprint.get("sourceDocuments")
    if not isinstance(records, list) or not records:
        raise SourceObjectManifestError(
            "Blueprint sourceDocuments must freeze the complete selected Word source set"
        )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(records, start=1):
        if not isinstance(raw, dict):
            raise SourceObjectManifestError("Every sourceDocuments record must be an object")
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute() or path.suffix.lower() not in {".docx", ".doc"}:
            raise SourceObjectManifestError(
                f"sourceDocuments[{index}] must contain an absolute Word path"
            )
        if not path.is_file():
            raise SourceObjectManifestError(f"Source Word does not exist: {path}")
        declared = str(raw.get("sha256") or "").lower()
        if not SHA256_PATTERN.fullmatch(declared):
            raise SourceObjectManifestError(f"Source Word must declare SHA-256: {path}")
        actual = sha256_file(path)
        if actual != declared:
            raise SourceObjectManifestError(
                f"HOLD_INPUT_DRIFT: source Word SHA-256 mismatch: {path}"
            )
        key = str(path)
        if key in seen:
            raise SourceObjectManifestError(f"Duplicate sourceDocuments path: {path}")
        seen.add(key)
        result.append(
            {
                "path": key,
                "sha256": declared,
                "role": str(raw.get("role") or "original_word"),
                "order": int(raw.get("order") or index),
            }
        )
    return sorted(result, key=lambda item: (int(item["order"]), str(item["path"])))


def _alternate_content_context(
    node: etree._Element,
    container: etree._Element,
) -> tuple[int | None, str | None]:
    alternate = next(
        (
            ancestor
            for ancestor in node.iterancestors()
            if ancestor.tag == f"{{{NS['mc']}}}AlternateContent"
        ),
        None,
    )
    if alternate is None:
        return None, None
    alternates = container.xpath(".//mc:AlternateContent", namespaces=NS)
    alternate_index = alternates.index(alternate) + 1
    branch = next(
        (
            ancestor
            for ancestor in node.iterancestors()
            if ancestor.getparent() is alternate
            and ancestor.tag
            in {
                f"{{{NS['mc']}}}Choice",
                f"{{{NS['mc']}}}Fallback",
            }
        ),
        None,
    )
    if branch is None:
        return alternate_index, None
    return alternate_index, etree.QName(branch).localname


def _stable_object_id(source_hash: str, kind: str, locator: str) -> str:
    suffix = hashlib.sha256(f"{kind}|{locator}".encode("utf-8")).hexdigest()[:16]
    return f"srcobj-{source_hash[:12]}-{kind}-{suffix}"


def _image_variants(
    container: etree._Element,
    *,
    container_locator: str,
    parts: dict[str, bytes],
    relationships: dict[str, str],
) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    drawings = container.xpath(".//w:drawing", namespaces=NS)
    for node in container.xpath(
        ".//a:blip[not(ancestor::w:txbxContent)]"
        " | .//v:imagedata[not(ancestor::w:txbxContent)]",
        namespaces=NS,
    ):
        rid = node.get(R_EMBED) or node.get(R_ID)
        part = relationships.get(rid or "")
        if not rid or not part or part not in parts:
            continue
        alternate_index, branch = _alternate_content_context(node, container)
        drawing = next(
            (
                ancestor
                for ancestor in node.iterancestors()
                if ancestor.tag == W + "drawing"
            ),
            None,
        )
        if drawing is not None and drawing in drawings:
            local_index = drawings.index(drawing) + 1
            locator = f"{container_locator}/drawing[{local_index}]/{rid}"
            placement = (
                "floating"
                if drawing.find(".//" + f"{{{NS['wp']}}}anchor") is not None
                else "inline"
            )
            extent = drawing.find(".//" + f"{{{NS['wp']}}}extent")
            doc_properties = drawing.find(
                ".//" + f"{{{NS['wp']}}}docPr"
            )
        else:
            local_index = len(variants) + 1
            locator = f"{container_locator}/vml-image[{local_index}]/{rid}"
            placement = "floating-vml"
            extent = None
            doc_properties = None
        vml_shape = next(
            (
                ancestor
                for ancestor in node.iterancestors()
                if ancestor.tag
                in {
                    f"{{{NS['v']}}}shape",
                    f"{{{NS['v']}}}rect",
                    f"{{{NS['v']}}}roundrect",
                }
            ),
            None,
        )
        extent_pt = None
        if vml_shape is not None:
            style = str(vml_shape.get("style") or "")
            width_match = re.search(r"(?:^|;)width:([0-9.]+)pt", style)
            height_match = re.search(r"(?:^|;)height:([0-9.]+)pt", style)
            if width_match and height_match:
                extent_pt = {
                    "width": float(width_match.group(1)),
                    "height": float(height_match.group(1)),
                }
        crop = None
        source_rect = next(
            (
                ancestor
                for ancestor in node.iterancestors()
                if etree.QName(ancestor).localname == "blipFill"
            ),
            None,
        )
        if source_rect is not None:
            rect = source_rect.find(f"{{{NS['a']}}}srcRect")
            if rect is not None:
                crop = {
                    key: rect.get(key)
                    for key in ("l", "t", "r", "b")
                    if rect.get(key) is not None
                }
        variants.append(
            {
                "locator": locator,
                "rid": rid,
                "part": part,
                "mediaSha256": sha256_bytes(parts[part]),
                "mediaSizeBytes": len(parts[part]),
                "mediaFormat": Path(part).suffix.lower().lstrip("."),
                "nativeTextMetadata": {
                    key: value
                    for key, value in {
                        "name": (
                            doc_properties.get("name")
                            if doc_properties is not None
                            else vml_shape.get("id")
                            if vml_shape is not None
                            else None
                        ),
                        "description": (
                            doc_properties.get("descr")
                            if doc_properties is not None
                            else node.get("title")
                        ),
                        "title": (
                            doc_properties.get("title")
                            if doc_properties is not None
                            else vml_shape.get("title")
                            if vml_shape is not None
                            else None
                        ),
                    }.items()
                    if value
                },
                "alternateContentIndex": alternate_index,
                "branch": branch,
                "placement": placement,
                "extentEmu": (
                    {
                        "cx": int(extent.get("cx")),
                        "cy": int(extent.get("cy")),
                    }
                    if extent is not None
                    and extent.get("cx")
                    and extent.get("cy")
                    else None
                ),
                "extentPt": extent_pt,
                "crop": crop,
                "relativePosition": relative_content_position(container, node),
                "tableCell": table_cell_position(container, node),
                "tableCellRelativePosition": table_cell_relative_position(
                    container,
                    node,
                ),
            }
        )
    return variants


def _merge_compatibility_images(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(variants):
        alternate_index = item.get("alternateContentIndex")
        if alternate_index is None:
            key = ("independent", index)
        else:
            key = ("alternate", alternate_index, item["mediaSha256"])
        grouped[key].append(item)
    result: list[dict[str, Any]] = []
    for values in grouped.values():
        preferred = next(
            (item for item in values if "/drawing[" in str(item["locator"])),
            values[0],
        )
        result.append(
            {
                **preferred,
                "serializedLocators": [item["locator"] for item in values],
                "compatibilityBranches": sorted(
                    {str(item["branch"]) for item in values if item.get("branch")}
                ),
            }
        )
    return sorted(result, key=lambda item: variants.index(next(v for v in variants if v["locator"] == item["locator"])))


def _textbox_paragraph_variants(
    container: etree._Element,
    *,
    container_locator: str,
) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    textboxes = container.xpath(".//w:txbxContent", namespaces=NS)
    branch_counts: dict[tuple[int | None, str | None], int] = defaultdict(int)
    for textbox in textboxes:
        alternate_index, branch = _alternate_content_context(textbox, container)
        branch_key = (alternate_index, branch)
        branch_counts[branch_key] += 1
        textbox_index = branch_counts[branch_key]
        paragraphs = textbox.xpath(
            "./w:p | ./w:tbl/w:tr/w:tc/w:p",
            namespaces=NS,
        )
        visible_index = 0
        for paragraph in paragraphs:
            text = full_text(paragraph)
            if not text:
                continue
            visible_index += 1
            branch_label = branch or "Direct"
            if alternate_index is None:
                locator = (
                    f"{container_locator}/textbox[{textbox_index}]"
                    f"/p[{visible_index}]"
                )
            else:
                locator = (
                    f"{container_locator}/alternateContent[{alternate_index}]"
                    f"/{branch_label}/textbox[{textbox_index}]/p[{visible_index}]"
                )
            position_target = next(
                (
                    ancestor
                    for ancestor in paragraph.iterancestors()
                    if ancestor.tag == f"{{{NS['mc']}}}AlternateContent"
                ),
                textbox,
            )
            variants.append(
                {
                    "locator": locator,
                    "text": text,
                    "textSha256": sha256_bytes(text.encode("utf-8")),
                    "alternateContentIndex": alternate_index,
                    "branch": branch,
                    "textboxIndex": textbox_index,
                    "paragraphIndex": visible_index,
                    "tableCell": table_cell_position(container, paragraph),
                    "relativePosition": relative_content_position(
                        container,
                        position_target,
                    ),
                    "tableCellRelativePosition": table_cell_relative_position(
                        container,
                        position_target,
                    ),
                }
            )
    return variants


def _alternate_content_payload_mismatches(
    container: etree._Element,
    *,
    container_locator: str,
    parts: dict[str, bytes],
    relationships: dict[str, str],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for alternate_index, alternate in enumerate(
        container.xpath(".//mc:AlternateContent", namespaces=NS),
        start=1,
    ):
        payloads: dict[str, dict[str, Any]] = {}
        for branch in alternate:
            branch_name = etree.QName(branch).localname
            if branch_name not in {"Choice", "Fallback"}:
                continue
            text_values = [
                full_text(paragraph)
                for paragraph in branch.xpath(".//w:txbxContent//w:p", namespaces=NS)
                if full_text(paragraph)
            ]
            media_hashes: list[str] = []
            for node in branch.xpath(".//a:blip | .//v:imagedata", namespaces=NS):
                rid = node.get(R_EMBED) or node.get(R_ID)
                part = relationships.get(rid or "")
                if part and part in parts:
                    media_hashes.append(sha256_bytes(parts[part]))
            payloads[branch_name] = {
                "textSha256": [
                    sha256_bytes(value.encode("utf-8")) for value in text_values
                ],
                "mediaSha256": media_hashes,
            }
        if len(payloads) > 1 and len(
            {
                json.dumps(payload, sort_keys=True, ensure_ascii=False)
                for payload in payloads.values()
            }
        ) > 1:
            mismatches.append(
                {
                    "containerLocator": container_locator,
                    "alternateContentIndex": alternate_index,
                    "branchPayloads": payloads,
                }
            )
    return mismatches


def _logical_shapes(
    container: etree._Element,
    *,
    container_locator: str,
) -> list[dict[str, Any]]:
    """Return one carrier/line object per logical shape compatibility group."""

    result: list[dict[str, Any]] = []
    alternates = container.xpath(".//mc:AlternateContent", namespaces=NS)
    for alternate_index, alternate in enumerate(alternates, start=1):
        branches: list[dict[str, Any]] = []
        for branch in alternate:
            branch_name = etree.QName(branch).localname
            if branch_name not in {"Choice", "Fallback"}:
                continue
            shape_nodes = branch.xpath(
                ".//wps:wsp | .//v:shape | .//v:line | .//v:rect | .//v:roundrect",
                namespaces=NS,
            )
            if not shape_nodes:
                continue
            branches.append(
                {
                    "branch": branch_name,
                    "shapeTypes": [
                        etree.QName(shape).localname for shape in shape_nodes
                    ],
                    "hasTextbox": bool(
                        branch.xpath(".//w:txbxContent", namespaces=NS)
                    ),
                    "hasImage": bool(
                        branch.xpath(".//a:blip | .//v:imagedata", namespaces=NS)
                    ),
                }
            )
        if not branches:
            continue
        result.append(
            {
                "locator": (
                    f"{container_locator}/alternateContent[{alternate_index}]/shape[1]"
                ),
                "serializedLocators": [
                    (
                        f"{container_locator}/alternateContent[{alternate_index}]"
                        f"/{item['branch']}/shape[1]"
                    )
                    for item in branches
                ],
                "compatibilityBranches": [item["branch"] for item in branches],
                "shapeTypes": sorted(
                    {shape_type for item in branches for shape_type in item["shapeTypes"]}
                ),
                "hasTextbox": any(item["hasTextbox"] for item in branches),
                "hasImage": any(item["hasImage"] for item in branches),
                "relativePosition": relative_content_position(container, alternate),
                "tableCell": table_cell_position(container, alternate),
                "tableCellRelativePosition": table_cell_relative_position(
                    container,
                    alternate,
                ),
            }
        )
    direct_shapes = [
        node
        for node in container.xpath(
            ".//wps:wsp | .//v:shape | .//v:line | .//v:rect | .//v:roundrect",
            namespaces=NS,
        )
        if not any(
            ancestor.tag == f"{{{NS['mc']}}}AlternateContent"
            for ancestor in node.iterancestors()
        )
    ]
    for index, shape in enumerate(direct_shapes, start=1):
        locator = f"{container_locator}/shape[{index}]"
        result.append(
            {
                "locator": locator,
                "serializedLocators": [locator],
                "compatibilityBranches": [],
                "shapeTypes": [etree.QName(shape).localname],
                "hasTextbox": bool(shape.xpath(".//w:txbxContent", namespaces=NS)),
                "hasImage": bool(
                    shape.xpath(".//a:blip | .//v:imagedata", namespaces=NS)
                ),
                "relativePosition": relative_content_position(container, shape),
                "tableCell": table_cell_position(container, shape),
                "tableCellRelativePosition": table_cell_relative_position(
                    container,
                    shape,
                ),
            }
        )
    return result


def _merge_compatibility_textboxes(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    independent = [
        item for item in variants if item.get("alternateContentIndex") is None
    ]
    by_alternate: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in variants:
        alternate_index = item.get("alternateContentIndex")
        if alternate_index is not None:
            by_alternate[int(alternate_index)].append(item)
    result: list[dict[str, Any]] = [
        {
            **item,
            "serializedLocators": [item["locator"]],
            "compatibilityBranches": [],
        }
        for item in independent
    ]
    for alternate_index, values in by_alternate.items():
        by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in values:
            by_branch[str(item.get("branch") or "Unknown")].append(item)
        fingerprints: list[str] = []
        for branch_values in by_branch.values():
            for item in branch_values:
                if item["textSha256"] not in fingerprints:
                    fingerprints.append(item["textSha256"])
        for fingerprint in fingerprints:
            occurrences = {
                branch: [item for item in branch_values if item["textSha256"] == fingerprint]
                for branch, branch_values in by_branch.items()
            }
            for occurrence_index in range(max(len(items) for items in occurrences.values())):
                merged = [
                    items[occurrence_index]
                    for items in occurrences.values()
                    if occurrence_index < len(items)
                ]
                preferred = next(
                    (item for item in merged if item.get("branch") == "Choice"),
                    merged[0],
                )
                canonical_locator = (
                    f"{preferred['locator'].split('/alternateContent[', 1)[0]}"
                    f"/alternateContent[{alternate_index}]"
                    f"/textbox-paragraph[{len(result) + 1}]"
                )
                result.append(
                    {
                        **preferred,
                        "locator": canonical_locator,
                        "serializedLocators": [item["locator"] for item in merged],
                        "compatibilityBranches": sorted(
                            {str(item.get("branch")) for item in merged if item.get("branch")}
                        ),
                    }
                )
    order = {item["locator"]: index for index, item in enumerate(variants)}
    return sorted(
        result,
        key=lambda item: min(
            order.get(locator, len(order)) for locator in item["serializedLocators"]
        ),
    )


def _purpose_candidate(
    kind: str,
    text: str,
    *,
    is_title_container: bool,
    has_image_in_container: bool,
) -> tuple[str | None, str]:
    if kind == "image":
        # The object inventory must remain semantic-neutral.  Whether a visual
        # is decoration, a title-text carrier, mixed content, or an
        # instructional illustration belongs to the pluggable semantic stage.
        return "visual-purpose-undetermined", "needs-review"
    if kind == "textbox-paragraph":
        compact = re.sub(r"\s+", "", text)
        if re.match(r"^(特别提醒|易错提醒|解题要点|教材延伸)", compact):
            return "content-evidence", "candidate"
        if has_image_in_container and len(compact) <= 12:
            return "opaque-asset", "needs-review"
        return "content-evidence", "candidate"
    return None, "not-applicable"


def extract_source_objects(
    source: dict[str, Any],
    *,
    title_locators: set[str],
) -> dict[str, Any]:
    path = Path(str(source["path"]))
    if path.suffix.lower() != ".docx":
        raise SourceObjectManifestError(
            f"Current source-object scanner requires .docx input: {path}"
        )
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    if "word/document.xml" not in parts:
        raise SourceObjectManifestError(f"Source Word has no document.xml: {path}")
    document = etree.fromstring(parts["word/document.xml"])
    body = document.find(".//" + W + "body")
    if body is None:
        raise SourceObjectManifestError(f"Source Word has no body: {path}")
    relationships = relationship_map(parts)
    source_hash = str(source["sha256"])
    objects: list[dict[str, Any]] = []
    paragraph_index = 0
    table_index = 0
    sequence_index = 0
    branch_payload_mismatches: list[dict[str, Any]] = []

    def append_object(record: dict[str, Any]) -> dict[str, Any]:
        nonlocal sequence_index
        sequence_index += 1
        locator = str(record["locator"]["value"])
        item = {
            "objectId": _stable_object_id(source_hash, str(record["kind"]), locator),
            "sequenceIndex": sequence_index,
            "sourcePath": str(path),
            "sourceSha256": source_hash,
            **record,
        }
        objects.append(item)
        return item

    for child in body:
        if child.tag == W + "p":
            paragraph_index += 1
            container_locator = f"word/document.xml:body/p[{paragraph_index}]"
            container_kind = "paragraph"
            container_index = paragraph_index
        elif child.tag == W + "tbl":
            table_index += 1
            container_locator = f"word/document.xml:body/tbl[{table_index}]"
            container_kind = "table"
            container_index = table_index
        else:
            continue

        direct_text = text_without_textboxes(child)
        image_variants = _image_variants(
            child,
            container_locator=container_locator,
            parts=parts,
            relationships=relationships,
        )
        images = _merge_compatibility_images(image_variants)
        logical_shapes = _logical_shapes(
            child,
            container_locator=container_locator,
        )
        textbox_variants = _textbox_paragraph_variants(
            child,
            container_locator=container_locator,
        )
        branch_payload_mismatches.extend(
            _alternate_content_payload_mismatches(
                child,
                container_locator=container_locator,
                parts=parts,
                relationships=relationships,
            )
        )
        parent: dict[str, Any] | None = None
        # Every visual-only paragraph is itself a source object.  This keeps the
        # inventory independent from later semantic tags and prevents an
        # image-only title from disappearing before recognition.
        if (
            direct_text
            or container_kind == "table"
            or images
            or logical_shapes
            or textbox_variants
            or draws_a_blank(child)
        ):
            kind = "paragraph" if container_kind == "paragraph" else "table"
            parent = append_object(
                {
                    "kind": kind,
                    "locator": {"kind": kind, "value": container_locator},
                    "serializedLocators": [container_locator],
                    "text": direct_text,
                    "textSha256": sha256_bytes(direct_text.encode("utf-8")),
                    "container": {
                        "part": "word/document.xml",
                        "bodyKind": container_kind,
                        "bodyIndex": container_index,
                    },
                    "purposeCandidate": None,
                    "purposeStatus": "not-applicable",
                }
            )

        image_objects: list[dict[str, Any]] = []
        for image in images:
            extent = image.get("extentEmu")
            aspect_ratio = (
                round(float(extent["cx"]) / float(extent["cy"]), 6)
                if isinstance(extent, dict) and int(extent.get("cy") or 0)
                else None
            )
            purpose, purpose_status = _purpose_candidate(
                "image",
                "",
                is_title_container=container_locator in title_locators,
                has_image_in_container=True,
            )
            image_objects.append(
                append_object(
                    {
                        "kind": "image",
                        "locator": {"kind": "image", "value": image["locator"]},
                        "serializedLocators": image["serializedLocators"],
                        "text": "",
                        "textSha256": None,
                        "container": {
                            "part": "word/document.xml",
                            "bodyKind": container_kind,
                            "bodyIndex": container_index,
                        },
                        "ownerObjectId": parent["objectId"] if parent else None,
                        "purposeCandidate": purpose,
                        "purposeStatus": purpose_status,
                        "image": {
                            "relationshipId": image["rid"],
                            "mediaPart": image["part"],
                            "mediaSha256": image["mediaSha256"],
                            "mediaSizeBytes": image["mediaSizeBytes"],
                            "mediaFormat": image.get("mediaFormat"),
                            "nativeTextMetadata": image.get(
                                "nativeTextMetadata"
                            )
                            or {},
                            "placement": image["placement"],
                            "extentEmu": extent,
                            "aspectRatio": aspect_ratio,
                            "extentPt": image.get("extentPt"),
                            "crop": image.get("crop"),
                            "relativePosition": image.get("relativePosition"),
                            "tableCell": image.get("tableCell"),
                            "tableCellRelativePosition": image.get(
                                "tableCellRelativePosition"
                            ),
                            "compatibilityBranches": image["compatibilityBranches"],
                        },
                    }
                )
            )

        shape_objects: list[dict[str, Any]] = []
        for shape in logical_shapes:
            # The shape carrier around a compiled textbox is a style-purpose
            # object.  A line/arrow without text remains position-critical
            # until the semantic review binds or preserves it.
            if shape["hasTextbox"] and not shape["hasImage"]:
                purpose = "template-decoration"
                purpose_status = "candidate"
            elif not shape["hasTextbox"] and not shape["hasImage"]:
                purpose = "opaque-asset"
                purpose_status = "needs-review"
            else:
                purpose = "content-illustration"
                purpose_status = "candidate"
            shape_objects.append(
                append_object(
                    {
                        "kind": "shape",
                        "locator": {"kind": "shape", "value": shape["locator"]},
                        "serializedLocators": shape["serializedLocators"],
                        "text": "",
                        "textSha256": None,
                        "container": {
                            "part": "word/document.xml",
                            "bodyKind": container_kind,
                            "bodyIndex": container_index,
                        },
                        "ownerObjectId": (
                            image_objects[-1]["objectId"]
                            if image_objects
                            else parent["objectId"]
                            if parent
                            else None
                        ),
                        "purposeCandidate": purpose,
                        "purposeStatus": purpose_status,
                        "shape": {
                            "shapeTypes": shape["shapeTypes"],
                            "hasTextbox": shape["hasTextbox"],
                            "hasImage": shape["hasImage"],
                            "compatibilityBranches": shape[
                                "compatibilityBranches"
                            ],
                            "relativePosition": shape.get("relativePosition"),
                            "tableCell": shape.get("tableCell"),
                            "tableCellRelativePosition": shape.get(
                                "tableCellRelativePosition"
                            ),
                        },
                    }
                )
            )

        for textbox in _merge_compatibility_textboxes(textbox_variants):
            purpose, purpose_status = _purpose_candidate(
                "textbox-paragraph",
                textbox["text"],
                is_title_container=container_locator in title_locators,
                has_image_in_container=bool(image_objects),
            )
            append_object(
                {
                    "kind": "textbox-paragraph",
                    "locator": {
                        "kind": "paragraph",
                        "value": textbox["locator"],
                    },
                    "serializedLocators": textbox["serializedLocators"],
                    "text": textbox["text"],
                    "textSha256": textbox["textSha256"],
                    "container": {
                        "part": "word/document.xml",
                        "bodyKind": container_kind,
                        "bodyIndex": container_index,
                    },
                    "ownerObjectId": (
                        shape_objects[-1]["objectId"]
                        if shape_objects
                        else image_objects[-1]["objectId"]
                        if image_objects
                        else parent["objectId"]
                        if parent
                        else None
                    ),
                    "purposeCandidate": purpose,
                    "purposeStatus": purpose_status,
                    "compatibilityBranches": textbox["compatibilityBranches"],
                    "tableCell": textbox.get("tableCell"),
                    "relativePosition": textbox.get("relativePosition"),
                    "tableCellRelativePosition": textbox.get(
                        "tableCellRelativePosition"
                    ),
                }
            )

    for index, item in enumerate(objects):
        item["previousObjectId"] = objects[index - 1]["objectId"] if index else None
        item["nextObjectId"] = (
            objects[index + 1]["objectId"] if index + 1 < len(objects) else None
        )
    return {
        **source,
        "objectCount": len(objects),
        "branchPayloadMismatches": branch_payload_mismatches,
        "objects": objects,
    }


def build_manifest(blueprint_path: Path, output_path: Path) -> dict[str, Any]:
    blueprint_bytes = blueprint_path.read_bytes()
    blueprint = json.loads(blueprint_bytes)
    if blueprint.get("schemaVersion") != BLUEPRINT_SCHEMA_VERSION:
        raise SourceObjectManifestError("Unsupported semantic blueprint schemaVersion")
    sources = source_document_records(blueprint)
    source_results = [
        extract_source_objects(
            source,
            # Deliberately empty: object enumeration must not depend on labels
            # that are produced after scanning.
            title_locators=set(),
        )
        for source in sources
    ]
    objects = [
        item for source in source_results for item in source.get("objects") or []
    ]
    branch_payload_mismatches = [
        {
            "sourcePath": source["path"],
            **item,
        }
        for source in source_results
        for item in source.get("branchPayloadMismatches") or []
    ]
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "pass",
        "blueprintPath": str(blueprint_path),
        "blueprintSha256": sha256_bytes(blueprint_bytes),
        "sourcePolicy": blueprint.get("sourcePolicy"),
        "sourceDocuments": source_results,
        "summary": {
            "sourceCount": len(source_results),
            "objectCount": len(objects),
            "paragraphCount": sum(item["kind"] == "paragraph" for item in objects),
            "tableCount": sum(item["kind"] == "table" for item in objects),
            "textboxParagraphCount": sum(
                item["kind"] == "textbox-paragraph" for item in objects
            ),
            "imageCount": sum(item["kind"] == "image" for item in objects),
            "shapeCount": sum(item["kind"] == "shape" for item in objects),
            "needsReviewCount": sum(
                item.get("purposeStatus") == "needs-review" for item in objects
            ),
            "alternateContentLogicalObjectCount": sum(
                len(item.get("serializedLocators") or []) > 1 for item in objects
            ),
            "branchPayloadMismatchCount": len(branch_payload_mismatches),
        },
        "branchPayloadMismatches": branch_payload_mismatches,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This source-object scanner is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py."
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_manifest(args.blueprint, args.output)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
