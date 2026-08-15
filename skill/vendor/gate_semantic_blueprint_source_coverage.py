#!/usr/bin/env python3
"""Fail closed unless every visible source Word object has one reviewed owner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from semantic_title_visual_text_plugin_contract import (  # noqa: E402
    TitleVisualTextEvidenceError,
    validate_evidence_records,
)

CANONICAL_PROCESS_ID = "chengziclass.summer-handout-word-production.v1"
INTERNAL_INVOCATION_ENV = "CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL"
MANIFEST_SCHEMA = "chengziclass.word-source-object-manifest.v1"
REPORT_SCHEMA = "chengziclass.semantic-blueprint-source-coverage.v1"
APPROVED_REVIEW_DISPOSITIONS = {"opaque-preserve"}


class SourceCoverageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_key(source: dict[str, Any]) -> tuple[str, str]:
    locator = source.get("locator") or {}
    return str(source.get("path") or ""), str(locator.get("value") or "")


def inline_image_segments(block: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for index, segment in enumerate(block.get("segments") or [], start=1):
        if isinstance(segment, dict) and segment.get("kind") == "inline_image":
            result.append((f"segment-{index}", segment))
    if block.get("type") == "table":
        for row_index, row in enumerate(block.get("rows") or [], start=1):
            if not isinstance(row, list):
                continue
            for column_index, cell in enumerate(row, start=1):
                if not isinstance(cell, dict):
                    continue
                for segment_index, segment in enumerate(
                    cell.get("segments") or [],
                    start=1,
                ):
                    if (
                        isinstance(segment, dict)
                        and segment.get("kind") == "inline_image"
                    ):
                        result.append(
                            (
                                f"cell-{row_index}-{column_index}-segment-{segment_index}",
                                segment,
                            )
                        )
    return result


def visible_text(block: dict[str, Any]) -> str:
    if isinstance(block.get("segments"), list):
        return "".join(
            str(item.get("text") or "")
            for item in block["segments"]
            if isinstance(item, dict)
        )
    if block.get("type") == "table":
        return " ".join(
            (
                "".join(
                    str(item.get("text") or "")
                    for item in cell.get("segments") or []
                    if isinstance(item, dict)
                )
                if isinstance(cell, dict)
                else str(cell)
            )
            for row in (block.get("rows") or [])
            for cell in (row if isinstance(row, list) else [])
        )
    return str(block.get("text") or "")


def build_coverage_report(
    blueprint_path: Path,
    manifest_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise SourceCoverageError("Unsupported source-object manifest schemaVersion")
    current_blueprint_hash = sha256_file(blueprint_path)
    if manifest.get("blueprintSha256") != current_blueprint_hash:
        raise SourceCoverageError(
            "HOLD_INPUT_DRIFT: source-object manifest is not bound to the current blueprint"
        )

    objects: dict[str, dict[str, Any]] = {}
    locator_index: dict[tuple[str, str], str] = {}
    manifest_source_paths: set[str] = set()
    for source in manifest.get("sourceDocuments") or []:
        path = str(source.get("path") or "")
        manifest_source_paths.add(path)
        for item in source.get("objects") or []:
            object_id = str(item.get("objectId") or "")
            if not object_id or object_id in objects:
                raise SourceCoverageError(f"Missing or duplicate source object id: {object_id}")
            objects[object_id] = item
            for locator in {
                str((item.get("locator") or {}).get("value") or ""),
                *(str(value) for value in item.get("serializedLocators") or []),
            }:
                if not locator:
                    continue
                key = (path, locator)
                previous = locator_index.get(key)
                if previous is not None and previous != object_id:
                    raise SourceCoverageError(
                        f"Source locator resolves to more than one logical object: {key}"
                    )
                locator_index[key] = object_id

    failures: list[dict[str, Any]] = []
    ownership: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for collection_name in (
        "blocks",
        "sourceTitleParagraphs",
        "sourceObjectExclusions",
        "sourceObjectReviewQueue",
        "sourceObjectSubstitutions",
    ):
        identifiers = [
            str(record.get("id") or "")
            for record in blueprint.get(collection_name) or []
            if isinstance(record, dict)
        ]
        missing_count = sum(not identifier for identifier in identifiers)
        duplicate_ids = sorted(
            identifier
            for identifier, count in Counter(identifiers).items()
            if identifier and count > 1
        )
        if missing_count:
            failures.append(
                {
                    "code": "missing-blueprint-record-id",
                    "collection": collection_name,
                    "count": missing_count,
                }
            )
        if duplicate_ids:
            failures.append(
                {
                    "code": "duplicate-blueprint-record-id",
                    "collection": collection_name,
                    "ids": duplicate_ids,
                }
            )

    def resolve(source: dict[str, Any], owner: dict[str, Any]) -> str | None:
        object_id = str(source.get("objectId") or "")
        if object_id:
            if object_id not in objects:
                failures.append(
                    {
                        "code": "unknown-source-object-id",
                        "objectId": object_id,
                        "owner": owner,
                    }
                )
                return None
            return object_id
        key = source_key(source)
        resolved = locator_index.get(key)
        if resolved is None:
            failures.append(
                {
                    "code": "source-locator-not-in-manifest",
                    "sourcePath": key[0],
                    "sourceLocator": key[1],
                    "owner": owner,
                }
            )
        return resolved

    title_by_object: dict[str, dict[str, Any]] = {}
    for record in blueprint.get("sourceTitleParagraphs") or []:
        owner = {
            "kind": "source-title-alias",
            "id": record.get("id"),
            "canonicalBlockId": record.get("canonicalBlockId"),
        }
        object_id = resolve(record.get("source") or {}, owner)
        if object_id:
            title_by_object[object_id] = record
            ownership[object_id].append(owner)

    exclusion_by_object: dict[str, dict[str, Any]] = {}
    for record in blueprint.get("sourceObjectExclusions") or []:
        owner = {
            "kind": "explicit-exclusion",
            "id": record.get("id"),
            "classification": record.get("classification"),
        }
        object_id = resolve(record.get("source") or {}, owner)
        if object_id:
            exclusion_by_object[object_id] = record
            ownership[object_id].append(owner)

    unresolved_review_objects: list[dict[str, Any]] = []
    for record in blueprint.get("sourceObjectReviewQueue") or []:
        disposition = str(record.get("disposition") or "needs-review")
        owner = {
            "kind": "review-queue",
            "id": record.get("id"),
            "disposition": disposition,
        }
        object_id = resolve(record.get("source") or {}, owner)
        if object_id:
            ownership[object_id].append(owner)
            if disposition not in APPROVED_REVIEW_DISPOSITIONS:
                unresolved_review_objects.append(
                    {
                        "objectId": object_id,
                        "disposition": disposition,
                        "recordId": record.get("id"),
                    }
                )
            elif not (
                objects[object_id].get("kind") == "shape"
                and (
                    (objects[object_id].get("shape") or {}).get("hasTextbox")
                    or (objects[object_id].get("shape") or {}).get("hasImage")
                )
            ):
                unresolved_review_objects.append(
                    {
                        "objectId": object_id,
                        "disposition": disposition,
                        "recordId": record.get("id"),
                        "reason": (
                            "opaque-preserve is limited to compatibility carriers "
                            "whose textbox/image payload is independently owned"
                        ),
                    }
                )

    canonical_title_blocks: dict[str, set[str]] = defaultdict(set)
    for object_id, record in title_by_object.items():
        canonical_title_blocks[str(record.get("canonicalBlockId") or "")].add(
            object_id
        )
    for block in blueprint.get("blocks") or []:
        source = block.get("source") or {}
        if source.get("status") == "layout":
            continue
        owner = {"kind": "blueprint-block", "id": block.get("id"), "type": block.get("type")}
        if source.get("objectPart") not in (None, ""):
            owner["part"] = str(source["objectPart"])
        locator = source.get("locator") or {}
        if locator.get("kind") == "range":
            source_path = str(source.get("path") or "")
            if source_path not in manifest_source_paths:
                failures.append(
                    {
                        "code": "aggregate-range-source-not-in-manifest",
                        "sourcePath": source_path,
                        "owner": owner,
                    }
                )
            continue
        # A block may draw on more than one source paragraph — a row of
        # options laid out per question can take two of its four from one
        # source row and two from the next. Each extra source names the part
        # it contributes, so the union stays disjoint and every object is
        # still covered exactly once.
        for extra_source in block.get("additionalSources") or []:
            extra_owner = dict(owner)
            if extra_source.get("objectPart") not in (None, ""):
                extra_owner["part"] = str(extra_source["objectPart"])
            extra_id = resolve(extra_source, extra_owner)
            if extra_id is not None:
                ownership[extra_id].append(extra_owner)
        object_id = resolve(source, owner)
        if object_id is None:
            continue
        if object_id in canonical_title_blocks.get(
            str(block.get("id") or ""),
            set(),
        ):
            continue
        ownership[object_id].append(owner)
        for segment_label, segment in inline_image_segments(block):
            segment_owner = {
                "kind": "blueprint-inline-image-segment",
                "id": block.get("id"),
                "segment": segment_label,
            }
            segment_object_id = resolve(segment.get("source") or {}, segment_owner)
            if segment_object_id:
                ownership[segment_object_id].append(segment_owner)

    for record in blueprint.get("sourceObjectSubstitutions") or []:
        owner = {
            "kind": "source-object-substitution",
            "id": record.get("id"),
            "targetBlockId": record.get("targetBlockId"),
            "replacementText": record.get("replacementText"),
        }
        object_id = resolve(record.get("source") or {}, owner)
        if object_id:
            ownership[object_id].append(owner)

    block_by_id = {
        str(block.get("id") or ""): block
        for block in blueprint.get("blocks") or []
        if isinstance(block, dict)
    }
    try:
        title_visual_evidence = validate_evidence_records(
            blueprint.get("sourceTitleVisualTextEvidence"),
            canonical_block_text={
                block_id: visible_text(block)
                for block_id, block in block_by_id.items()
                if block.get("type") in {"chapter", "heading1", "heading2", "heading3"}
            },
        )
    except TitleVisualTextEvidenceError as exc:
        failures.append(
            {
                "code": "invalid-title-visual-text-evidence",
                "detail": str(exc),
            }
        )
        title_visual_evidence = {}

    title_visual_objects: dict[str, str] = {}
    for title_object_id, title_record in title_by_object.items():
        title_item = objects[title_object_id]
        title_path = str(title_item.get("sourcePath") or "")
        title_locator = str((title_item.get("locator") or {}).get("value") or "")
        canonical_id = str(title_record.get("canonicalBlockId") or "")
        for object_id, item in objects.items():
            if item.get("kind") != "image" or item.get("sourcePath") != title_path:
                continue
            locators = {
                str((item.get("locator") or {}).get("value") or ""),
                *(str(value) for value in item.get("serializedLocators") or []),
            }
            if any(
                locator.startswith(
                    (
                        f"{title_locator}/drawing[",
                        f"{title_locator}/vml-image[",
                        f"{title_locator}/alternateContent[",
                    )
                )
                for locator in locators
            ):
                previous = title_visual_objects.setdefault(object_id, canonical_id)
                if previous != canonical_id:
                    failures.append(
                        {
                            "code": "title-visual-canonical-binding-collision",
                            "objectId": object_id,
                        }
                    )

    evidence_by_object: dict[str, dict[str, Any]] = {}
    for evidence_id, evidence in title_visual_evidence.items():
        owner = {"kind": "title-visual-text-evidence", "id": evidence_id}
        object_id = resolve(evidence.get("source") or {}, owner)
        if object_id is None:
            continue
        item = objects[object_id]
        if item.get("kind") != "image":
            failures.append(
                {
                    "code": "title-visual-evidence-not-image",
                    "evidenceId": evidence_id,
                    "objectId": object_id,
                }
            )
            continue
        if object_id in evidence_by_object:
            failures.append(
                {
                    "code": "duplicate-title-visual-evidence",
                    "objectId": object_id,
                }
            )
            continue
        evidence_by_object[object_id] = evidence
        actual_media_sha256 = str(
            (item.get("image") or {}).get("mediaSha256") or ""
        ).lower()
        if actual_media_sha256 != str(evidence.get("mediaSha256") or "").lower():
            failures.append(
                {
                    "code": "HOLD_VISUAL_EVIDENCE_STALE",
                    "evidenceId": evidence_id,
                    "objectId": object_id,
                }
            )

    for object_id, canonical_id in title_visual_objects.items():
        evidence = evidence_by_object.get(object_id)
        if evidence is None:
            failures.append(
                {
                    "code": "HOLD_TITLE_VISUAL_UNINSPECTED",
                    "objectId": object_id,
                }
            )
            continue
        if evidence.get("canonicalBlockId") != canonical_id:
            failures.append(
                {
                    "code": "HOLD_TITLE_BINDING_COLLISION",
                    "objectId": object_id,
                    "evidenceId": evidence.get("id"),
                }
            )
        exclusion = exclusion_by_object.get(object_id)
        decision = evidence.get("decision")
        if decision == "mixed_content":
            if exclusion is not None:
                failures.append(
                    {
                        "code": "HOLD_EXCLUDED_CONTENT_OBJECT",
                        "objectId": object_id,
                        "evidenceId": evidence.get("id"),
                    }
                )
            elif not ownership.get(object_id):
                failures.append(
                    {
                        "code": "HOLD_TITLE_VISUAL_UNMATERIALIZED",
                        "objectId": object_id,
                    }
                )
        else:
            if exclusion is None:
                failures.append(
                    {
                        "code": "missing-title-visual-exclusion",
                        "objectId": object_id,
                        "evidenceId": evidence.get("id"),
                    }
                )
            elif exclusion.get("titleVisualTextEvidenceId") != evidence.get("id"):
                failures.append(
                    {
                        "code": "title-visual-exclusion-evidence-mismatch",
                        "objectId": object_id,
                        "evidenceId": evidence.get("id"),
                    }
                )
    for object_id, evidence in evidence_by_object.items():
        if object_id not in title_visual_objects:
            failures.append(
                {
                    "code": "title-visual-evidence-outside-registered-title",
                    "objectId": object_id,
                    "evidenceId": evidence.get("id"),
                }
            )

    # Empty visual-container paragraphs have no independent visible payload.
    # Once every child image/shape/textbox has its own reviewed owner, the
    # paragraph wrapper is a deterministic compatibility carrier rather than a
    # second content object.  This keeps the source scan label-independent
    # without forcing hundreds of meaningless model labels.
    children_by_owner: dict[str, list[str]] = defaultdict(list)
    for object_id, item in objects.items():
        owner_object_id = str(item.get("ownerObjectId") or "")
        if owner_object_id:
            children_by_owner[owner_object_id].append(object_id)
    derived_opaque_carriers: list[str] = []
    changed = True
    while changed:
        changed = False
        for object_id, item in objects.items():
            if ownership.get(object_id):
                continue
            if item.get("kind") != "paragraph" or str(item.get("text") or ""):
                continue
            child_ids = children_by_owner.get(object_id) or []
            if child_ids and all(ownership.get(child_id) for child_id in child_ids):
                ownership[object_id].append(
                    {
                        "kind": "opaque-compatibility-carrier",
                        "derived": True,
                        "childObjectIds": child_ids,
                    }
                )
                derived_opaque_carriers.append(object_id)
                changed = True

    unowned = [
        {
            "objectId": object_id,
            "kind": item.get("kind"),
            "text": str(item.get("text") or "")[:240],
            "sourcePath": item.get("sourcePath"),
            "sourceLocator": (item.get("locator") or {}).get("value"),
            "purposeCandidate": item.get("purposeCandidate"),
            "purposeStatus": item.get("purposeStatus"),
        }
        for object_id, item in objects.items()
        if not ownership.get(object_id)
    ]
    # One source object may legitimately become several blueprint blocks: a
    # 「A．… B．… C．… D．…」 paragraph is one Word object but four options, and
    # the registry's own CZ_Num_ChoiceAlpha demands each option be its own
    # block with fixed label geometry. Such blocks declare which part they take;
    # the object is still covered exactly once, as the disjoint union of its
    # parts. Blocks that claim the whole object cannot share it, and two blocks
    # claiming the same part is still double ownership.
    duplicate = []
    for object_id, owners in ownership.items():
        parts = [str(owner.get("part") or "") for owner in owners]
        if len(owners) == 1 and not parts[0]:
            continue
        if all(parts) and len(set(parts)) == len(parts):
            continue
        duplicate.append({"objectId": object_id, "owners": owners,
                          "parts": parts})
    if unowned:
        failures.append({"code": "unowned-visible-source-objects", "count": len(unowned)})
    if duplicate:
        failures.append({"code": "duplicate-source-object-ownership", "count": len(duplicate)})
    if unresolved_review_objects:
        failures.append(
            {
                "code": "unresolved-source-object-review",
                "count": len(unresolved_review_objects),
            }
        )
    branch_payload_mismatches = manifest.get("branchPayloadMismatches") or []
    if branch_payload_mismatches:
        failures.append(
            {
                "code": "alternate-content-branch-payload-mismatch",
                "count": len(branch_payload_mismatches),
            }
        )

    status = "pass" if not failures else "fail"
    result = {
        "schemaVersion": REPORT_SCHEMA,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "blueprintPath": str(blueprint_path),
        "blueprintSha256": current_blueprint_hash,
        "sourceObjectManifestPath": str(manifest_path),
        "sourceObjectManifestSha256": sha256_file(manifest_path),
        "summary": {
            "sourceObjectCount": len(objects),
            "ownedObjectCount": sum(bool(ownership.get(object_id)) for object_id in objects),
            "unownedObjectCount": len(unowned),
            "duplicateOwnershipCount": len(duplicate),
            "unresolvedReviewCount": len(unresolved_review_objects),
            "branchPayloadMismatchCount": len(branch_payload_mismatches),
            "failureCount": len(failures),
            "titleVisualObjectCount": len(title_visual_objects),
            "titleVisualEvidenceCount": len(evidence_by_object),
            "derivedOpaqueCarrierCount": len(derived_opaque_carriers),
        },
        "failures": failures,
        "unownedObjects": unowned,
        "duplicateOwnership": duplicate,
        "unresolvedReviewObjects": unresolved_review_objects,
        "branchPayloadMismatches": branch_payload_mismatches,
        "derivedOpaqueCompatibilityCarriers": derived_opaque_carriers,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    if os.environ.get(INTERNAL_INVOCATION_ENV) != CANONICAL_PROCESS_ID:
        raise SystemExit(
            "This coverage gate is an internal registered step. "
            "Start from run_summer_word_prepress_workflow.py."
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--source-object-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = build_coverage_report(
        args.blueprint,
        args.source_object_manifest,
        args.report,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if result["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
