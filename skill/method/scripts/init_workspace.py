#!/usr/bin/env python3
"""Create the growth directory for a workspace, and bind it to this skill version.

The skill ships read-only: method and seeds travel with the version, and a package
manager may replace the whole directory on upgrade. Anything the user grows —
their template tables, their volume adjudications, the findings they rejected —
therefore lives beside their work, not inside the skill. That split is not a
compromise; it is what makes an upgrade safe.

The manifest records which engine and which seeds the growth was grown against,
so an import can refuse a version it cannot honour rather than silently applying
rules that were written for something else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
GROWTH_DIRNAME = ".handout-intake"
SUBDIRS = ("templates", "volumes", "transient")


def seed_inventory() -> list[dict[str, Any]]:
    found = []
    for path in sorted((SKILL_ROOT / "seeds").rglob("*.json")):
        found.append({
            "path": str(path.relative_to(SKILL_ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return found


def version() -> str:
    marker = SKILL_ROOT / "VERSION"
    return marker.read_text(encoding="utf-8").strip() if marker.exists() else "0.0.0-unversioned"


def initialise(workspace: Path, force: bool) -> dict[str, Any]:
    root = workspace / GROWTH_DIRNAME
    manifest_path = root / "MANIFEST.json"
    if manifest_path.exists() and not force:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"status": "already-initialised", "root": str(root),
                "boundTo": existing.get("skillVersion"),
                "note": "已初始化过;要重新绑定到当前 skill 版本请加 --force。"
                        "不自动覆盖:成长物是使用者的劳动,不是可以顺手重置的缓存。"}
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    hits = root / "hits.json"
    if not hits.exists():
        hits.write_text(json.dumps({
            "schemaVersion": "handout-intake.hits.v1",
            "rule": "每条长期规则上线以来命中几次。长期为 0 先怀疑判据恒假,"
                    "不是「确实没有」——恒假的判据在报告里和一切正常长得一模一样。",
            "rules": {},
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest = {
        "schemaVersion": "handout-intake.manifest.v1",
        "initialisedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "skillVersion": version(),
        "skillRoot": str(SKILL_ROOT),
        "seeds": seed_inventory(),
        "admissionRule": "写进本目录的每条规律必须带:现象/判据/处置/本质、全类扫描命中数、"
                         "一道门、破坏性自证、认可状态。配不出门的只能记为观察。"
                         "定稿前一律 provisional——在使用方认可之前归纳,会把错的选择变成带门的规则。",
        "exportDefault": "默认导出方法、模板表、规律与门;册级裁决含源文原文片段,"
                         "须显式勾选并提示版权风险。",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
    return {"status": "initialised", "root": str(root),
            "skillVersion": manifest["skillVersion"], "seeds": len(manifest["seeds"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path,
                        help="项目根目录;成长物写在它下面的 .handout-intake/")
    parser.add_argument("--force", action="store_true",
                        help="重新绑定到当前 skill 版本（不删除已有成长物）")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        raise SystemExit(f"工作区不存在:{args.workspace}")
    report = initialise(args.workspace.resolve(), args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
