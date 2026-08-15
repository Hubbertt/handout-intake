#!/usr/bin/env python3
"""Regenerate and accept structure manifests for current formal student Word files.

This is used after deterministic Word module repairs change DOCX package hashes.
It does not edit Word content. It accepts a regenerated manifest only when all
TOC entries are matched and required block anchors are present.
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

import json
from datetime import datetime
from pathlib import Path

from build_summer_structure_manifests import DOCS, MANIFEST_DIR, REPORT as BUILD_REPORT, make_manifest
from summer_scope_filter import active_scope
from summer_word_contract import STRUCTURE_MANIFEST_SCHEMA


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
REPORT = RUN_DIR / "structure_manifest_refresh_review_report.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def refresh_one(key: str, path: Path, reviewed_at: str) -> dict[str, object]:
    manifest, build_record = make_manifest(key, path)
    summary = manifest.get("extractionSummary") or {}
    block_count = len(manifest.get("blocks") or [])
    errors = []
    if manifest.get("schemaVersion") != STRUCTURE_MANIFEST_SCHEMA:
        errors.append(f"bad schema: {manifest.get('schemaVersion')}")
    if int(summary.get("tocEntryCount") or 0) <= 0:
        errors.append("manifest has no TOC entries")
    if int(summary.get("unmatchedTocEntryCount") or 0) != 0:
        errors.append(f"unmatched TOC entries: {summary.get('unmatchedTocEntryCount')}")
    if block_count <= 0:
        errors.append("manifest has no blocks")

    out = MANIFEST_DIR / f"{key}.structure.json"
    if errors:
        manifest["status"] = "draft"
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            **build_record,
            "key": key,
            "status": "fail",
            "manifest": str(out),
            "errors": errors,
        }

    source = manifest.get("sourceDocx") or {}
    manifest["status"] = "reviewed"
    manifest["reviewedAt"] = reviewed_at
    manifest["reviewMethod"] = "automated structure-manifest refresh after deterministic Word module repair"
    manifest["reviewRecord"] = {
        "schemaVersion": "chengziclass.structure-manifest-review-record.v1",
        "reviewedAt": reviewed_at,
        "reviewer": {
            "type": "workflow",
            "name": "refresh_summer_structure_manifest_review.py",
            "scope": "Current formal student Word files after non-content module repairs.",
            "notScope": "PDF visual acceptance, typography QA, or subject content correctness.",
        },
        "decision": "accept",
        "basis": (
            f"Regenerated from current formal Word; TOC entries={summary.get('tocEntryCount')}, "
            f"matched={summary.get('matchedTocEntryCount')}, unmatched=0, blocks={block_count}, "
            f"tables={summary.get('tableCount')}, drawing anchors={summary.get('drawingAnchorCount')}."
        ),
        "notes": "Refreshes source hashes after deterministic module edits; later Word/PDF audits remain required.",
        "riskNotes": "Content correctness and final printed layout are outside this structural hash refresh.",
    }
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        **build_record,
        "key": key,
        "status": "pass",
        "manifest": str(out),
        "sourceSha256": source.get("sha256"),
        "blockCount": block_count,
    }


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    reviewed_at = now_iso()
    results = [refresh_one(key, path, reviewed_at) for key, path in DOCS.items()]
    build_report = {
        "schemaVersion": "chengziclass.structure-manifest-build-report.v1",
        "generatedAt": reviewed_at,
        "acceptReviewed": True,
        "reviewBoundary": "Regenerated and reviewed by refresh_summer_structure_manifest_review.py after deterministic Word module changes.",
        "manifestDir": str(MANIFEST_DIR),
        "activeScope": active_scope(),
        "results": results,
        "summary": {
            "documents": len(results),
            "reviewed": sum(1 for r in results if r.get("status") == "pass"),
            "draft": sum(1 for r in results if r.get("status") != "pass"),
            "unmatchedTocEntries": sum(int(r.get("unmatchedTocEntryCount") or 0) for r in results),
        },
    }
    BUILD_REPORT.write_text(json.dumps(build_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schemaVersion": "chengziclass.structure-manifest-refresh-review-report.v1",
        "generatedAt": reviewed_at,
        "manifestDir": str(MANIFEST_DIR),
        "activeScope": active_scope(),
        "results": results,
        "summary": {
            "documents": len(results),
            "passed": sum(1 for r in results if r.get("status") == "pass"),
            "failed": sum(1 for r in results if r.get("status") != "pass"),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(REPORT)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
