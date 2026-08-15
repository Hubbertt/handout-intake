#!/usr/bin/env python3
"""Shared Word-master gates for summer teaching materials.

Global Microsoft Word automation rule:
Microsoft Word automation must never open a formal Word master or export a PDF
from the project path directly. Word rewrites DOCX package timestamps on
open/close and may trigger macOS file access prompts when automation opens
project paths. Copy the DOCX into Word's own container sandbox, open and export
only that temporary copy, then transfer the accepted output back by ordinary
filesystem copy. Formal masters are edited only by explicit module repair
steps.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MIN_COMPATIBILITY_MODE = 15
STRUCTURE_MANIFEST_SCHEMA = "chengziclass.summer.structure-manifest.v1"
STRUCTURE_MANIFEST_ACCEPTED_STATUSES = {"reviewed", "accepted", "approved"}
ALLOW_LEGACY_SEMANTIC_WRITE_ENV = "CHENGZI_ALLOW_LEGACY_SEMANTIC_WRITE"
WORD_PROBE_ROOT = Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents/ChengziClassWordProbe"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class WordGateError(RuntimeError):
    """Raised when a Word master is not eligible for production export."""


class StructureManifestError(RuntimeError):
    """Raised when the content-structure manifest is missing or stale."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compatibility_mode(path: Path) -> int | None:
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise WordGateError(f"Invalid DOCX package entry: {bad}")
        try:
            settings = ET.fromstring(zf.read("word/settings.xml"))
        except KeyError:
            return None
    for el in settings.findall(".//" + W + "compatSetting"):
        attrs = {key.split("}")[-1]: value for key, value in el.attrib.items()}
        if attrs.get("name") == "compatibilityMode":
            raw = attrs.get("val")
            return int(raw) if raw and raw.isdigit() else None
    return None


def require_current_docx(path: Path) -> dict[str, object]:
    mode = compatibility_mode(path)
    if mode is not None and mode < MIN_COMPATIBILITY_MODE:
        raise WordGateError(
            f"{path} is compatibilityMode={mode}; production Word masters require "
            f"compatibilityMode>={MIN_COMPATIBILITY_MODE}."
        )
    return {
        "path": str(path),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "compatibilityMode": mode,
        "minimumCompatibilityMode": MIN_COMPATIBILITY_MODE,
    }


def require_accepted_structure_manifest(key: str, docx: Path, manifest_dir: Path) -> dict[str, object]:
    """Require a reviewed semantic structure manifest before module work or PDF export."""
    manifest = manifest_dir / f"{key}.structure.json"
    if not manifest.exists():
        raise StructureManifestError(
            f"Missing structure manifest for {key}: {manifest}. "
            "Build and review the content structure before module normalization or PDF export."
        )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != STRUCTURE_MANIFEST_SCHEMA:
        raise StructureManifestError(
            f"Unsupported structure manifest schema for {key}: {data.get('schemaVersion')}"
        )
    status = str(data.get("status") or "")
    if status not in STRUCTURE_MANIFEST_ACCEPTED_STATUSES:
        raise StructureManifestError(
            f"Structure manifest for {key} is not reviewed: status={status!r}."
        )
    source = data.get("sourceDocx") or {}
    current_hash = sha256(docx)
    if source.get("sha256") != current_hash:
        raise StructureManifestError(
            f"Structure manifest for {key} is stale: manifest sha256={source.get('sha256')}, "
            f"current sha256={current_hash}."
        )
    declared_path = source.get("path")
    if declared_path and Path(str(declared_path)).resolve() != docx.resolve():
        raise StructureManifestError(
            f"Structure manifest for {key} points to a different Word master: {declared_path}"
        )
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise StructureManifestError(f"Structure manifest for {key} has no content blocks.")
    missing = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            missing.append({"index": index, "field": "block-object"})
            continue
        for field in ("blockId", "role", "hierarchyLevel", "lockPolicy"):
            if field not in block:
                missing.append({"index": index, "field": field})
    if missing:
        raise StructureManifestError(
            f"Structure manifest for {key} has incomplete blocks: {missing[:10]}"
        )
    return {
        "status": "pass",
        "manifest": str(manifest),
        "schemaVersion": data.get("schemaVersion"),
        "reviewStatus": status,
        "sourceSha256": current_hash,
        "blockCount": len(blocks),
        "reviewedAt": data.get("reviewedAt"),
        "lowestNavigationRule": data.get("navigation", {}).get("rightHeaderRule"),
    }


def block_legacy_semantic_write(script_name: str) -> None:
    """Stop write scripts that still infer semantic structure outside manifests."""
    if os.environ.get(ALLOW_LEGACY_SEMANTIC_WRITE_ENV) == "1":
        return
    raise SystemExit(
        f"{script_name} is blocked because it is a legacy semantic write script. "
        "The v4.5.13 workflow requires write modules to read the reviewed "
        "structure-manifest blocks/navigation instead of inferring titles, TOC, "
        "headers, sections, or columns from regex, filenames, or paragraph appearance. "
        f"Set {ALLOW_LEGACY_SEMANTIC_WRITE_ENV}=1 only for a documented rollback, "
        "not for formal production."
    )
