#!/usr/bin/env python3
"""Shared fail-closed contract for title-image text recognition evidence.

Recognition is a pluggable semantic-tagging concern.  Formal Word construction
never calls a model or OCR engine; it only consumes approved evidence whose
input fingerprint and cache key are bound to the frozen source object and the
resolver version.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = "chengziclass.source-title-visual-text-evidence.v1"
SEMANTIC_STAGE = "semantic-tagging"
APPROVED_REVIEW_STATUS = "approved"
ALLOWED_RESOLUTION_METHODS = {
    "vision-model",
    "ocr",
    "deterministic-script",
    "human-review",
}
ALLOWED_DECISIONS = {
    "decoration_only",
    "title_text_carrier",
    "mixed_content",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class TitleVisualTextEvidenceError(RuntimeError):
    pass


def _digest_payload(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_input_fingerprint(
    *,
    source_sha256: str,
    source_locator: str,
    media_sha256: str,
) -> str:
    """Bind recognition input to the frozen Word, exact image and media bytes."""

    return _digest_payload(
        {
            "sourceSha256": source_sha256.lower(),
            "sourceLocator": source_locator,
            "mediaSha256": media_sha256.lower(),
        }
    )


def build_cache_key(
    *,
    input_fingerprint: str,
    resolver_id: str,
    resolver_version: str,
) -> str:
    """Return the only accepted cache key for a resolver result."""

    return _digest_payload(
        {
            "inputFingerprint": input_fingerprint.lower(),
            "resolverId": resolver_id,
            "resolverVersion": resolver_version,
        }
    )


def normalize_visible_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def validate_evidence_records(
    records: Any,
    *,
    canonical_block_text: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate evidence shape, cache binding and optional title preservation."""

    if not isinstance(records, list):
        raise TitleVisualTextEvidenceError(
            "sourceTitleVisualTextEvidence must be a list"
        )
    by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise TitleVisualTextEvidenceError(
                "Every sourceTitleVisualTextEvidence record must be an object"
            )
        record_id = str(record.get("id") or "")
        if not record_id or record_id in by_id:
            raise TitleVisualTextEvidenceError(
                f"Missing or duplicate title visual evidence id at index {index}"
            )
        if record.get("schemaVersion") != SCHEMA_VERSION:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} has an unsupported schemaVersion"
            )
        if record.get("semanticStage") != SEMANTIC_STAGE:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must belong to {SEMANTIC_STAGE}"
            )
        if record.get("review_status") != APPROVED_REVIEW_STATUS:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must be approved"
            )
        method = str(record.get("resolutionMethod") or "")
        if method not in ALLOWED_RESOLUTION_METHODS:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} has an unsupported resolutionMethod"
            )
        decision = str(record.get("decision") or "")
        if decision not in ALLOWED_DECISIONS:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} has an unsupported decision"
            )
        source = record.get("source") or {}
        locator = source.get("locator") or {}
        source_sha256 = str(source.get("sha256") or "").lower()
        source_locator = str(locator.get("value") or "")
        media_sha256 = str(record.get("mediaSha256") or "").lower()
        if (
            locator.get("kind") != "image"
            or not source_locator
            or not SHA256_PATTERN.fullmatch(source_sha256)
            or not SHA256_PATTERN.fullmatch(media_sha256)
        ):
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must bind an image locator, "
                "source SHA-256 and media SHA-256"
            )
        resolver_id = str(record.get("resolverId") or "")
        resolver_version = str(record.get("resolverVersion") or "")
        if not resolver_id or not resolver_version:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must declare resolverId and resolverVersion"
            )
        expected_fingerprint = build_input_fingerprint(
            source_sha256=source_sha256,
            source_locator=source_locator,
            media_sha256=media_sha256,
        )
        if record.get("inputFingerprint") != expected_fingerprint:
            raise TitleVisualTextEvidenceError(
                f"HOLD_INPUT_DRIFT: title visual evidence {record_id} inputFingerprint mismatch"
            )
        expected_cache_key = build_cache_key(
            input_fingerprint=expected_fingerprint,
            resolver_id=resolver_id,
            resolver_version=resolver_version,
        )
        if record.get("cacheKey") != expected_cache_key:
            raise TitleVisualTextEvidenceError(
                f"HOLD_INPUT_DRIFT: title visual evidence {record_id} resolver cacheKey mismatch"
            )
        contains_title_text = record.get("containsTitleText")
        if not isinstance(contains_title_text, bool):
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} containsTitleText must be boolean"
            )
        expected_contains_title_text = decision != "decoration_only"
        if contains_title_text is not expected_contains_title_text:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} decision and "
                "containsTitleText disagree"
            )
        title_text = str(record.get("titleText") or "")
        canonical_block_id = str(record.get("canonicalBlockId") or "")
        if not canonical_block_id:
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must declare canonicalBlockId"
            )
        if contains_title_text and not normalize_visible_text(title_text):
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must preserve recognized titleText"
            )
        if not contains_title_text and normalize_visible_text(title_text):
            raise TitleVisualTextEvidenceError(
                f"Title visual evidence {record_id} must leave titleText empty when false"
            )
        if canonical_block_text is not None:
            block_text = canonical_block_text.get(canonical_block_id)
            if block_text is None:
                raise TitleVisualTextEvidenceError(
                    f"Title visual evidence {record_id} references a missing canonical block"
                )
            if contains_title_text and (
                normalize_visible_text(title_text)
                not in normalize_visible_text(block_text)
            ):
                raise TitleVisualTextEvidenceError(
                    f"Title visual evidence {record_id} recognized text is not preserved "
                    "in its canonical visible title block"
                )
        by_id[record_id] = record
    return by_id
