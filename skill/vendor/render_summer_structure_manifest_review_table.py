#!/usr/bin/env python3
"""Render structure manifests as Chinese human-review tables.

This script does not identify structure and does not modify Word files. It
only turns the layer 1 / layer 1.5 English machine manifest into a table view
for human review: one row per module, one meaning per column.
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
import csv
import html as html_module
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
DEFAULT_MANIFEST_DIR = RUN_DIR / "structure-manifest"
DEFAULT_PARAMS = ROOT / "templates/summer-class-layout/summer_class_module_parameters.current.json"
DEFAULT_OUT_DIR = RUN_DIR / "structure-manifest-human-review"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_table_config(params_path: Path) -> dict[str, Any]:
    params = load_json(params_path)
    requirements = params.get("structureManifestRequirements") or {}
    config = requirements.get("humanReviewTableView") or {}
    if config.get("status") != "required":
        raise SystemExit("structureManifestRequirements.humanReviewTableView.status must be required")
    if config.get("rendererScript") != "scripts/formal/render_summer_structure_manifest_review_table.py":
        raise SystemExit("humanReviewTableView.rendererScript is not the current formal renderer")
    columns = config.get("columns") or []
    if not columns:
        raise SystemExit("humanReviewTableView.columns is empty")
    return config


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "；".join(normalize_cell(item) for item in value if normalize_cell(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def label(value: Any, labels: dict[str, str]) -> str:
    if isinstance(value, list):
        return "；".join(label(item, labels) for item in value)
    raw = normalize_cell(value)
    return labels.get(raw, raw)


def block_title(block: dict[str, Any]) -> str:
    for key in ("bodyDisplayTitle", "formalDisplayTitle", "sourceTitleNormalized", "title"):
        value = normalize_cell(block.get(key)).strip()
        if value:
            return value
    return normalize_cell(block.get("blockId"))


def target_structure_status(block: dict[str, Any], labels: dict[str, str]) -> str:
    if block.get("targetStructureStatus"):
        return label(block.get("targetStructureStatus"), labels)
    role = normalize_cell(block.get("role"))
    level = block.get("hierarchyLevel")
    if role in {"chapter"}:
        return labels.get("target_heading_level_1", "一级标题")
    if role in {"topic", "lesson", "knowledge-point"}:
        return labels.get("target_heading_level_2_or_3", "二/三级标题")
    if role in {"toc-title", "toc-entry"}:
        return labels.get("target_toc_structure", "目录结构")
    if role == "table":
        return labels.get("target_table_classification_required", "表格需分类")
    if role == "image":
        return labels.get("target_image_binding_required", "图片需绑定语义归属")
    if isinstance(level, int) and level <= 8:
        return f"目标大纲等级 {level}"
    return labels.get("target_body_or_module_content", "正文或模块内容")


def source_structure_status(block: dict[str, Any], labels: dict[str, str]) -> str:
    if block.get("sourceStructureStatus"):
        return label(block.get("sourceStructureStatus"), labels)
    role = normalize_cell(block.get("role"))
    anchor = block.get("startAnchor") or {}
    style_id = normalize_cell(anchor.get("styleId"))
    if role in {"toc-title", "toc-entry"}:
        return labels.get("source_toc_evidence", "来源目录结构证据")
    if role in {"chapter", "topic", "lesson", "knowledge-point"}:
        if style_id:
            return f"{labels.get('source_heading_or_module_evidence', '来源标题/模块证据')}：{style_id}"
        return labels.get("source_heading_or_module_evidence", "来源标题/模块证据")
    if role == "table":
        return labels.get("source_table_object", "来源为表格对象")
    if role == "image":
        return labels.get("source_image_object", "来源为图片对象")
    return labels.get("source_pending_classification", "来源结构待判定")


def fallback_classification(block: dict[str, Any]) -> list[str]:
    explicit = block.get("classification")
    if explicit:
        return explicit if isinstance(explicit, list) else [str(explicit)]
    role = normalize_cell(block.get("role"))
    if role in {"table", "image"} or block.get("titleSource") == "generated-from-content":
        return ["needs_human_decision"]
    return ["needs_structural_review"]


def field_value(
    manifest: dict[str, Any],
    block: dict[str, Any],
    field: str,
    row_index: int,
    config: dict[str, Any],
) -> str:
    classification_labels = config.get("classificationLabelsZh") or {}
    source_labels = config.get("sourceStructureStatusLabelsZh") or {}
    target_labels = config.get("targetStructureStatusLabelsZh") or {}
    source = manifest.get("sourceDocx") or {}
    anchor = block.get("startAnchor") or {}
    mapping = {
        "fileKey": manifest.get("key"),
        "sourceFile": Path(str(source.get("path") or "")).name,
        "moduleId": block.get("moduleId") or block.get("blockId"),
        "sourceOrder": block.get("sourceOrder") or row_index,
        "targetOrder": block.get("targetOrder") or row_index,
        "moduleTitle": block_title(block),
        "sourceStructureStatus": source_structure_status(block, source_labels),
        "targetStructureStatus": target_structure_status(block, target_labels),
        "classification": label(fallback_classification(block), classification_labels),
        "outlineLevel": block.get("targetOutlineLevel", block.get("hierarchyLevel")),
        "numberingDecision": block.get("numberingDecision") or block.get("numberingMode") or "",
        "columnDecision": block.get("columnDecision") or block.get("columnMode") or "",
        "tableDecision": block.get("tableDecision") or block.get("tableClassification") or "",
        "exerciseSemanticType": block.get("exerciseSemanticType") or "",
        "tableSemanticType": " / ".join(
            str(value)
            for value in [block.get("tableSemanticType"), block.get("shadingPolicy")]
            if value
        ),
        "imageDecision": block.get("imageDecision") or block.get("imageBinding") or "",
        "actions": block.get("actions") or block.get("actionPlan") or "",
        "decisionOwner": block.get("decisionOwner") or "",
        "reviewStatus": block.get("reviewStatus") or manifest.get("status") or "",
        "anchorText": anchor.get("textSample") or "",
    }
    return normalize_cell(mapping.get(field, block.get(field, "")))


def manifest_rows(manifest: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    columns = config.get("columns") or []
    blocks = manifest.get("moduleReviewRows") or manifest.get("blocks") or []
    rows: list[dict[str, str]] = []
    for row_index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            continue
        rows.append({
            str(column["field"]): field_value(manifest, block, str(column["field"]), row_index, config)
            for column in columns
        })
    return rows


def manifest_paths(args: argparse.Namespace) -> list[Path]:
    explicit = [path for path in args.manifests if path.exists()]
    if explicit:
        return explicit
    manifest_dir = args.manifest_dir
    return sorted(manifest_dir.glob("*.structure.json"))


def write_csv(path: Path, columns: list[dict[str, Any]], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [str(column["field"]) for column in columns]
    labels = [str(column.get("labelZh") or column["field"]) for column in columns]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(labels)
        for row in rows:
            writer.writerow([row.get(field, "") for field in fields])


def write_html(path: Path, columns: list[dict[str, Any]], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [str(column["field"]) for column in columns]
    labels = [str(column.get("labelZh") or column["field"]) for column in columns]
    head = "".join(f"<th>{html_module.escape(label_text)}</th>" for label_text in labels)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html_module.escape(row.get(field, ''))}</td>" for field in fields)
        body_rows.append(f"<tr>{cells}</tr>")
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>结构模块人工复核表</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
th, td {{ border: 1px solid #c8c8c8; padding: 6px 8px; vertical-align: top; word-break: break-word; }}
th {{ background: #f2f2f2; text-align: left; }}
caption {{ text-align: left; font-weight: 600; margin-bottom: 12px; }}
</style>
</head>
<body>
<table>
<caption>结构模块人工复核表</caption>
<thead><tr>{head}</tr></thead>
<tbody>
{''.join(body_rows)}
</tbody>
</table>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--out-csv", type=Path)
    parser.add_argument("--out-html", type=Path)
    parser.add_argument("--out-json", type=Path)
    args = parser.parse_args()

    config = load_table_config(args.params)
    columns = config.get("columns") or []
    paths = manifest_paths(args)
    all_rows: list[dict[str, str]] = []
    source_records: list[dict[str, Any]] = []
    for path in paths:
        manifest = load_json(path)
        rows = manifest_rows(manifest, config)
        all_rows.extend(rows)
        source_records.append({"path": str(path), "rows": len(rows), "status": manifest.get("status")})

    out_csv = args.out_csv or args.out_dir / "structure_module_review_table.zh-CN.csv"
    out_html = args.out_html or args.out_dir / "structure_module_review_table.zh-CN.html"
    out_json = args.out_json or args.out_dir / "structure_module_review_table_report.json"
    write_csv(out_csv, columns, all_rows)
    write_html(out_html, columns, all_rows)
    report = {
        "schemaVersion": "chengziclass.structure-manifest-human-table-render.v1",
        "generatedAt": now_iso(),
        "renderer": "scripts/formal/render_summer_structure_manifest_review_table.py",
        "params": str(args.params),
        "manifestDir": str(args.manifest_dir),
        "outputs": {"csv": str(out_csv), "html": str(out_html)},
        "sources": source_records,
        "summary": {
            "manifestCount": len(paths),
            "rowCount": len(all_rows),
            "displayLanguage": config.get("displayLanguage"),
            "rowGrain": config.get("rowGrain"),
        },
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if not paths:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
