#!/usr/bin/env python3
"""Full Word-first compliance audit for formal summer teaching materials.

This script is intentionally conservative: every check reports a separate
status instead of collapsing the document into a single broad pass/fail.
"""

from __future__ import annotations

# ---- 环境定位(handout-intake vendor 化时加入)------------------------------------
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
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from summer_scope_filter import active_scope, filter_paths
from summer_word_contract import MIN_COMPATIBILITY_MODE, sha256


CHENGZI_ROOT = _hi_env("HANDOUT_INTAKE_HOME", str(_P.home()))
# 资料根:环境变量优先(包里由执行器注入为工作区),否则沿用生产线的固定布局。
ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT",
               str(CHENGZI_ROOT / "projects/shared-assets/CZClassRoom/data/teaching-materials"))
FORMAL_ROOT = ROOT / "library/教辅资料/上海"
# 参数表与规范由**样式模板**给出,不由本脚本猜位置。
# 使用方 2026-08-15 定「技能包与样式模板解耦」:审计读哪套参数,是册级绑定/环境变量说了算。
# 生产线原写死 templates/summer-class-layout/…,那是「只有一套模板」时代的假设。
SPEC_PATH = _hi_env("HANDOUT_INTAKE_SPEC_PATH",
                    str(ROOT / "templates/summer-class-layout/橙子教室暑假班资料统一规范.md"))
MODULE_SPEC_PATH = _hi_env("HANDOUT_INTAKE_PARAMS_PATH",
                           str(ROOT / "templates/summer-class-layout/summer_class_module_parameters.current.json"))
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
OUT_PATH = RUN_DIR / "word_full_compliance_audit.json"
PROBE_REPORT_PATH = RUN_DIR / "word_native_open_clean_probe_report.json"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
FRONT_PRINT_MARKER = "CZ_BINDING_FRONT_COVER"
BACK_PRINT_MARKER = "CZ_BINDING_BACK_COVER"

MODULE_SPEC = json.loads(MODULE_SPEC_PATH.read_text(encoding="utf-8"))
SPEC_VERSION = MODULE_SPEC["version"]
MIN_WORD_COMPATIBILITY_MODE = MIN_COMPATIBILITY_MODE
PROHIBITED_FORMAL_NAME_TOKENS = [
    "整合版",
    "正式版",
    "临时版本",
    "final",
    "最终",
    "定稿",
    "最新版",
    "文字转曲版",
]
SENSITIVE_STUDENT_TOKENS = [
    "source_id",
    "source id",
    "OCR",
    "复核",
    "内部标签",
    "教师提示",
    "参考答案",
    "答案解析",
    "【答案】",
    "【解析】",
]
HEADER_BODY_CLEARANCE = MODULE_SPEC["page"]["headerBodyClearanceDxa"]
MIN_HEADER_BODY_GAP_DXA = int(HEADER_BODY_CLEARANCE["minimumGapDxa"])
MIN_TOP_MARGIN_DXA = int(HEADER_BODY_CLEARANCE["minimumTopMarginDxa"])
MIN_HEADER_DISTANCE_DXA = int(HEADER_BODY_CLEARANCE["minimumHeaderDistanceDxa"])
TOC_ENTRY_TAB = MODULE_SPEC["modules"]["toc"]["entryTab"]
TOC_PAGE_TAB_ALIGNMENT = str(TOC_ENTRY_TAB["alignment"])
TOC_PAGE_TAB_POS_DXA = str(TOC_ENTRY_TAB["positionDxa"])
TOC_PAGE_TAB_WORD_NATIVE_POS_DXA = str(
    TOC_ENTRY_TAB.get("wordNativeSerializedPositionDxa")
    or TOC_ENTRY_TAB["positionDxa"]
)
TOC_PAGE_TAB_TOLERANCE_DXA = int(
    TOC_ENTRY_TAB.get("acceptedSerializedPositionToleranceDxa") or 0
)
TOC_PAGE_TAB_LEADER = str(TOC_ENTRY_TAB["leader"])
TOC_PAGE_NUMBER_MUST_BE_LAST_FIELD = bool(TOC_ENTRY_TAB.get("pageNumberMustBeLastField", True))
TOC_TITLES = {"目录", "Contents"}
ALLOWED_MARGIN_ROLES = {
    tuple(str(role["marginsDxa"][key]) for key in ("top", "bottom", "left", "right", "header", "footer")): role["role"]
    for role in MODULE_SPEC["page"]["allowedSectionMarginRoles"]
}


def tab_leader_matches(value: str | None) -> bool:
    if TOC_PAGE_TAB_LEADER == "none":
        return value in {None, "none"}
    return value == TOC_PAGE_TAB_LEADER


def toc_tab_position_matches(value: str | None) -> bool:
    if value is None or not value.lstrip("-").isdigit():
        return False
    return abs(int(value) - int(TOC_PAGE_TAB_WORD_NATIVE_POS_DXA)) <= TOC_PAGE_TAB_TOLERANCE_DXA


def w_attr(el: ET.Element | None, name: str) -> str | None:
    return None if el is None else el.get(W + name)


def text_of(el: ET.Element) -> str:
    parts = []
    runs = [el] if el.tag == W + "r" else el.findall(".//" + W + "r")
    for run in runs:
        for node in run.iter():
            if node.tag == W + "t":
                parts.append(node.text or "")
            elif node.tag == W + "tab":
                parts.append("\t")
    return "".join(parts)


def page_field_instructions(root: ET.Element) -> list[str]:
    """Return PAGE field instructions in both OOXML field encodings."""
    instructions: list[str] = []
    for node in root.iter():
        if node.tag == W + "instrText" and node.text:
            instructions.append(node.text)
        elif node.tag == W + "fldSimple":
            value = node.get(W + "instr")
            if value:
                instructions.append(value)
    return instructions


def normalize_color(value: str | None) -> str | None:
    if not value or value.lower() == "auto":
        return value
    return value.upper()


def half_points(value: str | None) -> float | None:
    if not value or not value.isdigit():
        return None
    return int(value) / 2


@dataclass
class Check:
    id: str
    status: str
    message: str
    evidence: dict[str, Any]
    severity: str = "info"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


class DocxAudit:
    def __init__(self, path: Path, clean_probe_index: dict[str, dict[str, Any]]) -> None:
        self.path = path
        try:
            self.relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError(
                f"Word compliance audit target is outside the teaching-materials root: {path}"
            ) from exc
        self.checks: list[Check] = []
        self.clean_probe_index = clean_probe_index
        self.front_print_marker_count = 0
        self.back_print_marker_count = 0
        self.complete_print_master = False

    def check(self, cid: str, status: str, message: str, evidence: dict[str, Any], severity: str = "info") -> None:
        self.checks.append(Check(cid, status, message, evidence, severity))

    def rpr_props(self, rp: ET.Element | None, *, bold_missing: bool | None = False) -> dict[str, Any]:
        fonts = rp.find(W + "rFonts") if rp is not None else None
        sz = rp.find(W + "sz") if rp is not None else None
        color = rp.find(W + "color") if rp is not None else None
        bold_el = rp.find(W + "b") if rp is not None else None
        bold_specified = bold_el is not None
        if bold_specified:
            bold = w_attr(bold_el, "val") not in {"0", "false", "False"}
        else:
            bold = bold_missing
        return {
            "fontEastAsia": w_attr(fonts, "eastAsia"),
            "fontAscii": w_attr(fonts, "ascii") or w_attr(fonts, "hAnsi"),
            "fontHAnsi": w_attr(fonts, "hAnsi"),
            "pt": half_points(w_attr(sz, "val")),
            "bold": bold,
            "boldSpecified": bold_specified,
            "color": normalize_color(w_attr(color, "val")),
            "rStyle": w_attr(rp.find(W + "rStyle"), "val") if rp is not None else None,
        }

    def run_props(self, r: ET.Element) -> dict[str, Any]:
        return self.rpr_props(r.find(W + "rPr"), bold_missing=False)

    def paragraph_style_run_props(self, zf: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
        if "word/styles.xml" not in zf.namelist():
            return {}
        root = ET.fromstring(zf.read("word/styles.xml"))
        raw: dict[str, dict[str, Any]] = {}
        based_on: dict[str, str | None] = {}
        for style in root.findall(W + "style"):
            if w_attr(style, "type") != "paragraph":
                continue
            style_id = w_attr(style, "styleId")
            if not style_id:
                continue
            raw[style_id] = self.rpr_props(style.find(W + "rPr"), bold_missing=None)
            based_on[style_id] = w_attr(style.find(W + "basedOn"), "val")

        resolved: dict[str, dict[str, Any]] = {}

        def resolve(style_id: str, seen: set[str] | None = None) -> dict[str, Any]:
            if style_id in resolved:
                return resolved[style_id]
            seen = set() if seen is None else seen
            if style_id in seen:
                return {}
            seen.add(style_id)
            props = dict(resolve(based_on[style_id], seen)) if based_on.get(style_id) in raw else {}
            for key, value in raw.get(style_id, {}).items():
                if key == "boldSpecified":
                    continue
                if value is not None:
                    props[key] = value
            resolved[style_id] = props
            return props

        return {style_id: resolve(style_id) for style_id in raw}

    def registered_paragraph_style_ids(
        self,
        zf: zipfile.ZipFile,
        canonical_style_id: str,
    ) -> set[str]:
        """Resolve a registered CZ role after Microsoft Word renumbers style IDs."""

        if "word/styles.xml" not in zf.namelist():
            return set()
        registry = (
            MODULE_SPEC.get("wordStyleRegistry", {}).get("paragraphStyles", {})
            or {}
        )
        expected = registry.get(canonical_style_id) or {}
        expected_name = str(expected.get("name") or "").strip().lower()
        root = ET.fromstring(zf.read("word/styles.xml"))
        matches: set[str] = set()
        for style in root.findall(W + "style"):
            if w_attr(style, "type") != "paragraph":
                continue
            style_id = w_attr(style, "styleId") or ""
            name = style.find(W + "name")
            style_name = (w_attr(name, "val") or "").strip().lower()
            if style_id == canonical_style_id or (
                expected_name and style_name == expected_name
            ):
                matches.add(style_id)
        return matches

    def effective_runs(
        self,
        runs: list[dict[str, Any]],
        paragraph_style: str | None,
        style_run_props: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        inherited = style_run_props.get(paragraph_style or "", {})
        effective = []
        for run in runs:
            merged = dict(run)
            for key in ["fontEastAsia", "fontAscii", "fontHAnsi", "pt", "color"]:
                if merged.get(key) is None and inherited.get(key) is not None:
                    merged[key] = inherited[key]
            if not merged.get("boldSpecified") and inherited.get("bold") is not None:
                merged["bold"] = inherited["bold"]
            effective.append(merged)
        return effective

    def para_props(self, p: ET.Element) -> dict[str, Any]:
        pp = p.find(W + "pPr")
        spacing = pp.find(W + "spacing") if pp is not None else None
        jc = pp.find(W + "jc") if pp is not None else None
        style = pp.find(W + "pStyle") if pp is not None else None
        ind = pp.find(W + "ind") if pp is not None else None
        tab = pp.find(W + "tabs/" + W + "tab") if pp is not None else None
        return {
            "pStyle": w_attr(style, "val"),
            "jc": w_attr(jc, "val"),
            "before": w_attr(spacing, "before"),
            "after": w_attr(spacing, "after"),
            "line": w_attr(spacing, "line"),
            "lineRule": w_attr(spacing, "lineRule"),
            "firstLine": w_attr(ind, "firstLine"),
            "tabVal": w_attr(tab, "val"),
            "tabPos": w_attr(tab, "pos"),
            "tabLeader": w_attr(tab, "leader"),
        }

    def looks_like_toc_line(self, value: str) -> bool:
        if "\t" in value:
            return True
        if re.fullmatch(r"\d+", value):
            return True
        return bool(
            re.match(
                r"^(第\s*A\d{1,2}\s*讲|Unit\s*\d+|专题\s*([一二三四五六七八九十]|\d+)|课题\s*\d+|第\s*\d+\s*课时|跨学科实践|重难点\s*\d+|专题复习)",
                value,
                re.I,
            )
        )

    def find_toc_block(self, paragraphs: list[ET.Element]) -> tuple[int | None, int | None]:
        toc_idx = None
        for idx, p in enumerate(paragraphs):
            if text_of(p).strip() in TOC_TITLES:
                toc_idx = idx
                break
        if toc_idx is None:
            return None, None
        seen = 0
        for idx in range(toc_idx + 1, len(paragraphs)):
            value = text_of(paragraphs[idx]).strip()
            if not value:
                if seen:
                    return toc_idx, idx
                continue
            if seen and not self.looks_like_toc_line(value):
                return toc_idx, idx
            seen += 1
        return toc_idx, min(len(paragraphs), toc_idx + 1)

    def visible_runs(self, p: ET.Element) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for r in p.findall(W + "r"):
            txt = text_of(r)
            if txt.strip():
                props = self.run_props(r)
                props["text"] = txt[:80]
                runs.append(props)
        return runs

    def style_only_runs(self, runs: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
        return [
            {
                k: r.get(k)
                for k in ["fontEastAsia", "fontAscii", "fontHAnsi", "pt", "bold", "color"]
            }
            for r in runs[:limit]
        ]

    def body_paragraphs(self, zf: zipfile.ZipFile) -> list[ET.Element]:
        root = ET.fromstring(zf.read("word/document.xml"))
        return root.findall(".//" + W + "p")

    def audit_path_and_name(self) -> None:
        expected = re.compile(
            r"^2026-暑假班-(七年级|八年级)-(上册|全一册)-(语文|英语|物理|化学)-(学生版|教师版)-(讲义|习题册)(-[^-]+)?\.docx$"
        )
        name = self.path.name
        problems = [token for token in PROHIBITED_FORMAL_NAME_TOKENS if token in name]
        if not expected.match(name):
            self.check(
                "formal.filename.shape",
                "fail",
                "正式 Word 文件名未完全匹配规范字段顺序。",
                {"filename": name},
                "must-fix",
            )
        elif problems:
            self.check(
                "formal.filename.prohibited-token",
                "fail",
                "正式 Word 文件名含规范禁止的制作状态或技术标识。",
                {"filename": name, "tokens": problems},
                "must-fix",
            )
        else:
            self.check("formal.filename", "pass", "正式 Word 文件名符合字段顺序且无禁止词。", {"filename": name})

        if "/缓存/" in self.relative or "/正式版/" in self.relative or "/整合版/" in self.relative:
            self.check(
                "formal.path.structure",
                "fail",
                "正式 Word 路径仍含缓存、正式版或整合版等旧结构层。",
                {"relativePath": self.relative},
                "must-fix",
            )
        else:
            self.check("formal.path.structure", "pass", "正式 Word 路径符合扁平正式库结构。", {"relativePath": self.relative})

    def audit_sections(self, zf: zipfile.ZipFile) -> None:
        root = ET.fromstring(zf.read("word/document.xml"))
        sects = root.findall(".//" + W + "sectPr")
        sizes = Counter()
        margins = Counter()
        section_details = []
        for idx, sect in enumerate(sects, 1):
            pg_sz = sect.find(W + "pgSz")
            pg_mar = sect.find(W + "pgMar")
            size = (w_attr(pg_sz, "w"), w_attr(pg_sz, "h"), w_attr(pg_sz, "orient"))
            margin = (
                w_attr(pg_mar, "top"),
                w_attr(pg_mar, "bottom"),
                w_attr(pg_mar, "left"),
                w_attr(pg_mar, "right"),
                w_attr(pg_mar, "header"),
                w_attr(pg_mar, "footer"),
            )
            sizes[size] += 1
            margins[margin] += 1
            section_details.append({"index": idx, "size": size, "margin": margin})

        bad_sizes = [
            item for item in section_details
            if item["size"][0:2] not in [("11906", "16838"), ("11907", "16839"), ("11906", "16839")]
        ]
        if bad_sizes:
            self.check(
                "word.page.a4",
                "fail",
                "存在非 A4 纵向或未明示 A4 的节设置。",
                {"badSections": bad_sizes[:10], "sectionCount": len(sects)},
                "must-fix",
            )
        else:
            self.check(
                "word.page.a4",
                "pass",
                "所有可见节尺寸为 A4 纵向兼容值。",
                {"sectionCount": len(sects), "sizes": {str(k): v for k, v in sizes.items()}},
            )

        zero_margin = ("0", "0", "0", "0", "0", "0")
        zero_margin_count = margins.get(zero_margin, 0)
        if zero_margin_count:
            self.check(
                "word.page.binding-zero-margin-sections",
                "fail",
                "正式 Word 只允许目录与正文，发现疑似封皮或装订空白页使用的零边距节。",
                {"zeroMarginSections": zero_margin_count},
                "must-fix",
            )
        else:
            self.check(
                "word.page.binding-zero-margin-sections",
                "pass",
                "Word 中没有封皮或装订空白页使用的零边距节。",
                {},
            )

        unregistered_margins = {
            margin: count
            for margin, count in margins.items()
            if margin not in ALLOWED_MARGIN_ROLES
        }
        if unregistered_margins:
            self.check(
                "word.page.margins.consistency",
                "needs-review",
                "存在未登记角色的节边距，需要确认是否为封面、补白、正文或特殊页。",
                {
                    "uniqueMarginCount": len(margins),
                    "unregisteredMargins": {str(k): v for k, v in unregistered_margins.items()},
                    "topMargins": {str(k): v for k, v in margins.most_common(8)},
                },
                "review",
            )
        else:
            self.check(
                "word.page.margins.consistency",
                "pass",
                "节边距均可归入已登记的页面角色。",
                {
                    "uniqueMarginCount": len(margins),
                    "margins": {
                        str(k): {"count": v, "role": ALLOWED_MARGIN_ROLES.get(k)}
                        for k, v in margins.items()
                    },
                },
            )

        clearance_failures = []
        upward_shift_failures = []
        for item in section_details:
            top, _bottom, _left, _right, header, _footer = item["margin"]
            if (top, header) == ("0", "0"):
                continue
            if not (top and header and top.isdigit() and header.isdigit()):
                continue
            gap = int(top) - int(header)
            evidence = {
                "section": item["index"],
                "topDxa": int(top),
                "headerDxa": int(header),
                "headerBodyGapDxa": gap,
                "role": ALLOWED_MARGIN_ROLES.get(item["margin"]),
            }
            if gap < MIN_HEADER_BODY_GAP_DXA:
                clearance_failures.append(evidence)
            if int(top) < MIN_TOP_MARGIN_DXA or int(header) < MIN_HEADER_DISTANCE_DXA:
                upward_shift_failures.append(evidence)

        if clearance_failures or upward_shift_failures:
            self.check(
                "word.page.header-body-clearance",
                "fail",
                "页眉与正文结构距离不足，或存在通过降低上边距/页眉距把页面内容整体上移的节设置。",
                {
                    "moduleSpecPath": str(MODULE_SPEC_PATH),
                    "moduleSpecVersion": SPEC_VERSION,
                    "targetGapDxa": HEADER_BODY_CLEARANCE.get("targetGapDxa"),
                    "minimumGapDxa": MIN_HEADER_BODY_GAP_DXA,
                    "minimumTopMarginDxa": MIN_TOP_MARGIN_DXA,
                    "minimumHeaderDistanceDxa": MIN_HEADER_DISTANCE_DXA,
                    "standardHeaderDistanceDxa": HEADER_BODY_CLEARANCE.get("standardHeaderDistanceDxa"),
                    "clearanceFailures": clearance_failures[:20],
                    "upwardShiftFailures": upward_shift_failures[:20],
                    "repairDirection": HEADER_BODY_CLEARANCE.get("repairDirection"),
                    "acceptedRepairParameters": HEADER_BODY_CLEARANCE.get("acceptedRepairParameters"),
                    "rejectedRepairParameters": HEADER_BODY_CLEARANCE.get("rejectedRepairParameters"),
                    "repairPolicy": HEADER_BODY_CLEARANCE.get("repairPolicy"),
                },
                "must-fix",
            )
        else:
            self.check(
                "word.page.header-body-clearance",
                "pass",
                "节参数未发现页眉正文距离不足或整体上移修复风险。",
                {
                    "moduleSpecPath": str(MODULE_SPEC_PATH),
                    "moduleSpecVersion": SPEC_VERSION,
                    "targetGapDxa": HEADER_BODY_CLEARANCE.get("targetGapDxa"),
                    "minimumGapDxa": MIN_HEADER_BODY_GAP_DXA,
                    "minimumTopMarginDxa": MIN_TOP_MARGIN_DXA,
                    "minimumHeaderDistanceDxa": MIN_HEADER_DISTANCE_DXA,
                    "standardHeaderDistanceDxa": HEADER_BODY_CLEARANCE.get("standardHeaderDistanceDxa"),
                },
            )

    def audit_word_native_format(self, zf: zipfile.ZipFile) -> None:
        try:
            settings = ET.fromstring(zf.read("word/settings.xml"))
        except KeyError:
            self.check(
                "word.format.native-docx",
                "pass",
                "未发现兼容模式标记；按当前 Word 格式候选处理。",
                {"compatibilityMode": None, "minimumAccepted": MIN_WORD_COMPATIBILITY_MODE},
            )
            return

        mode: int | None = None
        settings_found = []
        for el in settings.findall(".//" + W + "compatSetting"):
            attrs = {key.split("}")[-1]: value for key, value in el.attrib.items()}
            settings_found.append(attrs)
            if attrs.get("name") == "compatibilityMode":
                raw = attrs.get("val")
                mode = int(raw) if raw and raw.isdigit() else None

        if mode is not None and mode < MIN_WORD_COMPATIBILITY_MODE:
            self.check(
                "word.format.native-docx",
                "fail",
                "正式 Word 母版仍处于旧兼容模式，入库前必须用 Microsoft Word 原生“转换文档”升级。",
                {"compatibilityMode": mode, "minimumAccepted": MIN_WORD_COMPATIBILITY_MODE},
                "must-fix",
            )
        else:
            self.check(
                "word.format.native-docx",
                "pass",
                "正式 Word 母版未处于旧兼容模式。",
                {
                    "compatibilityMode": mode,
                    "minimumAccepted": MIN_WORD_COMPATIBILITY_MODE,
                    "compatSettings": settings_found[:8],
                },
            )

    def audit_word_clean_open(self) -> None:
        current_hash = sha256(self.path)
        probe = self.clean_probe_index.get(str(self.path))
        if not probe:
            self.check(
                "word.native-clean-open",
                "fail",
                "缺少当前 Word 母版的 Microsoft Word 原生干净打开验收记录。",
                {"requiredReport": str(PROBE_REPORT_PATH), "sha256": current_hash},
                "must-fix",
            )
            return
        if probe.get("sha256") != current_hash:
            self.check(
                "word.native-clean-open",
                "fail",
                "Word 原生打开验收记录不是当前文件版本。",
                {"requiredSha256": current_hash, "probeSha256": probe.get("sha256"), "probeStatus": probe.get("status")},
                "must-fix",
            )
            return
        if probe.get("status") != "pass":
            self.check(
                "word.native-clean-open",
                "fail",
                "Microsoft Word 打开该母版时未通过干净打开验收。",
                {"probeStatus": probe.get("status"), "windows": probe.get("windows"), "staticTexts": probe.get("staticTexts")},
                "must-fix",
            )
            return
        self.check(
            "word.native-clean-open",
            "pass",
            "Microsoft Word 原生打开当前母版未出现修复、损坏或未命名恢复状态。",
            {"sha256": current_hash, "compatibilityMode": probe.get("compatibilityMode"), "documents": probe.get("documents")},
        )

    def audit_headers(self, zf: zipfile.ZipFile) -> None:
        header_parts = sorted(n for n in zf.namelist() if re.match(r"word/header\d+\.xml$", n))
        if not header_parts:
            self.check("word.header.exists", "fail", "未发现 Word 页眉部件。", {}, "must-fix")
            return

        try:
            rels_root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
            document_root = ET.fromstring(zf.read("word/document.xml"))
        except KeyError as exc:
            self.check("word.header.relationships", "fail", "缺少 Word 页眉关系或正文 XML。", {"missing": str(exc)}, "must-fix")
            return

        rid_to_target = {
            rel.get("Id"): rel.get("Target")
            for rel in rels_root.findall(REL + "Relationship")
            if rel.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
        }
        referenced_targets = set()
        referenced_content_targets = set()
        missing_effective_section_refs = []
        inherited_section_refs = []
        unresolved_refs = []
        zero_margin_sections = 0
        last_refs_by_type: dict[str, str] = {}
        for idx, sect in enumerate(document_root.findall(".//" + W + "sectPr"), 1):
            pg_mar = sect.find(W + "pgMar")
            zero_margin = bool(
                pg_mar is not None
                and all(w_attr(pg_mar, key) == "0" for key in ["top", "right", "bottom", "left", "header", "footer"])
            )
            if zero_margin:
                zero_margin_sections += 1
            refs = sect.findall(W + "headerReference")
            for ref in refs:
                ref_type = w_attr(ref, "type") or "default"
                rid = ref.get(R + "id")
                target = rid_to_target.get(rid)
                if not target:
                    unresolved_refs.append({"section": idx, "rid": rid})
                    continue
                normalized_target = "word/" + target.lstrip("/")
                referenced_targets.add(normalized_target)
                last_refs_by_type[ref_type] = normalized_target

            if zero_margin:
                continue

            required_types = ["even", "default"]
            if sect.find(W + "titlePg") is not None:
                required_types.append("first")
            missing_types = [ref_type for ref_type in required_types if not last_refs_by_type.get(ref_type)]
            if missing_types:
                missing_effective_section_refs.append({"section": idx, "missingTypes": missing_types})
            if not refs:
                inherited_section_refs.append(idx)
            for target in last_refs_by_type.values():
                referenced_content_targets.add(target)

        if missing_effective_section_refs or unresolved_refs:
            self.check(
                "word.header.section-references",
                "fail",
                "正文节缺少有效页眉引用或页眉关系无法解析。",
                {
                    "missingNonBlankSectionRefs": missing_effective_section_refs[:30],
                    "unresolvedRefs": unresolved_refs[:30],
                    "zeroMarginSections": zero_margin_sections,
                },
                "must-fix",
            )
        else:
            self.check(
                "word.header.section-references",
                "pass",
                "非空白正文节均有可解析的有效页眉引用。",
                {
                    "referencedHeaderParts": sorted(referenced_content_targets)[:20],
                    "zeroMarginSections": zero_margin_sections,
                    "inheritedSectionRefsSample": inherited_section_refs[:20],
                },
            )

        mismatches = []
        long_right = []
        empty_right = []
        empty_referenced = []
        source_headers = []
        unstructured_referenced = []
        sample = []
        for part in header_parts:
            root = ET.fromstring(zf.read(part))
            visible_text = " ".join(text_of(p).strip() for p in root.findall(".//" + W + "p") if text_of(p).strip())
            is_referenced = part in referenced_content_targets
            is_any_referenced = part in referenced_targets
            if re.search(r"学科网|ZXXK|xueke", visible_text, re.I):
                source_headers.append({"part": part, "referenced": is_any_referenced, "text": visible_text[:120]})
            if is_referenced and not visible_text:
                empty_referenced.append(part)
            tbl = root.find(".//" + W + "tbl")
            if tbl is None:
                if is_referenced and visible_text:
                    unstructured_referenced.append({"part": part, "text": visible_text[:120]})
                continue
            tr = tbl.find(W + "tr")
            if tr is None:
                continue
            cells = tr.findall(W + "tc")[:2]
            if len(cells) < 2:
                continue
            cell_infos = []
            for tc in cells:
                paragraphs = [p for p in tc.findall(".//" + W + "p") if text_of(p).strip()]
                runs = []
                para_props = []
                for p in paragraphs:
                    para_props.append(self.para_props(p))
                    runs.extend(self.visible_runs(p))
                cell_infos.append(
                    {
                        "text": " ".join(text_of(p).strip() for p in paragraphs),
                        "runs": runs,
                        "paragraphProps": para_props,
                    }
                )
            left = cell_infos[0]["runs"][0] if cell_infos[0]["runs"] else {}
            right = cell_infos[1]["runs"][0] if cell_infos[1]["runs"] else {}
            style_fields = ["fontEastAsia", "fontAscii", "pt", "bold", "color"]
            diffs = {k: (left.get(k), right.get(k)) for k in style_fields if left.get(k) != right.get(k)}
            if diffs and cell_infos[1]["text"]:
                mismatches.append({"part": part, "leftText": cell_infos[0]["text"], "rightText": cell_infos[1]["text"], "diffs": diffs})
            if not cell_infos[1]["text"]:
                empty_right.append(part)
            if re.search(r"^第\s*A\d{2}\s*讲|^第A\d{2}讲", cell_infos[1]["text"]):
                long_right.append({"part": part, "rightText": cell_infos[1]["text"]})
            if len(sample) < 6:
                sample.append({"part": part, "left": cell_infos[0], "right": cell_infos[1]})

        if source_headers:
            self.check("word.header.source-residue", "fail", "页眉仍含旧来源或平台标识。", {"examples": source_headers[:20]}, "must-fix")
        else:
            self.check("word.header.source-residue", "pass", "页眉未发现旧来源或平台标识。", {"referencedHeaderCount": len(referenced_content_targets)})

        if empty_referenced or unstructured_referenced:
            self.check(
                "word.header.visible-structure",
                "fail",
                "正文节引用了空页眉或非规范双区域页眉。",
                {"emptyReferencedHeaders": empty_referenced[:20], "unstructuredReferencedHeaders": unstructured_referenced[:20]},
                "must-fix",
            )
        else:
            self.check("word.header.visible-structure", "pass", "正文节引用的页眉均有规范双区域可见结构。", {"sample": sample})

        if mismatches:
            self.check("word.header.left-right-style", "fail", "页眉左右字体、字号、字重或颜色不一致。", {"mismatches": mismatches[:10]}, "must-fix")
        else:
            self.check("word.header.left-right-style", "pass", "有内容的页眉左右字体、字号、字重和颜色一致。", {"sample": sample})

        if long_right:
            self.check("word.header.right.short-navigation", "fail", "页眉右侧仍使用完整章节主标题，未压缩为短导航。", {"examples": long_right[:10]}, "must-fix")
        else:
            self.check("word.header.right.short-navigation", "pass", "页眉右侧未发现完整“第Axx讲”标题。", {"emptyRightParts": empty_right[:10]})

    def audit_footers(self, zf: zipfile.ZipFile) -> None:
        footer_parts = sorted(n for n in zf.namelist() if re.match(r"word/footer\d+\.xml$", n))
        if not footer_parts:
            self.check(
                "word.footer.visible-page-number",
                "fail",
                "正式 Word 内容母版缺少可见 PAGE 页码页脚。",
                {"owner": "Word"},
                "must-fix",
            )
            return
        try:
            rels_root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
            document_root = ET.fromstring(zf.read("word/document.xml"))
        except KeyError as exc:
            self.check(
                "word.footer.visible-page-number",
                "fail",
                "缺少 Word 页脚关系或正文 XML。",
                {"missing": str(exc)},
                "must-fix",
            )
            return
        rid_to_target = {
            rel.get("Id"): rel.get("Target")
            for rel in rels_root.findall(REL + "Relationship")
            if rel.get("Type")
            == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
        }
        last_refs_by_type: dict[str, str] = {}
        referenced_targets: set[str] = set()
        missing_effective_section_refs = []
        unresolved_refs = []
        for idx, section in enumerate(document_root.findall(".//" + W + "sectPr"), 1):
            refs = section.findall(W + "footerReference")
            for ref in refs:
                ref_type = w_attr(ref, "type") or "default"
                rid = ref.get(R + "id")
                target = rid_to_target.get(rid)
                if not target:
                    unresolved_refs.append({"section": idx, "rid": rid})
                    continue
                normalized = "word/" + target.lstrip("/")
                last_refs_by_type[ref_type] = normalized
            required_types = ["default", "even"]
            if section.find(W + "titlePg") is not None:
                required_types.append("first")
            missing = [
                ref_type
                for ref_type in required_types
                if not last_refs_by_type.get(ref_type)
            ]
            if missing:
                missing_effective_section_refs.append(
                    {"section": idx, "missingTypes": missing}
                )
            for target in last_refs_by_type.values():
                referenced_targets.add(target)

        invalid_parts = []
        samples = []
        for part in sorted(referenced_targets):
            if part not in zf.namelist():
                invalid_parts.append({"part": part, "reason": "referenced-part-missing"})
                continue
            root = ET.fromstring(zf.read(part))
            visible = " ".join(
                text_of(paragraph).strip()
                for paragraph in root.findall(".//" + W + "p")
                if text_of(paragraph).strip()
            )
            page_instructions = [
                instruction
                for instruction in page_field_instructions(root)
                if re.search(r"\bPAGE\b", instruction, flags=re.IGNORECASE)
                and not re.search(r"\bNUMPAGES\b", instruction, flags=re.IGNORECASE)
            ]
            if part.endswith("parity-blank-footer.xml"):
                # Declared Word-side even-page parity pad footer: must stay
                # empty and carry no PAGE field; it is the only footer part
                # exempt from the one-PAGE-field rule.
                if visible or page_instructions:
                    invalid_parts.append(
                        {
                            "part": part,
                            "reason": "parity-blank-footer-must-be-empty-without-page-field",
                            "pageFieldCount": len(page_instructions),
                            "text": visible[:120],
                        }
                    )
                continue
            if len(page_instructions) != 1:
                invalid_parts.append(
                    {
                        "part": part,
                        "reason": "expected-one-PAGE-field",
                        "pageFieldCount": len(page_instructions),
                    }
                )
            if visible and not re.fullmatch(r"[\d\s]+", visible):
                invalid_parts.append(
                    {"part": part, "reason": "unexpected-visible-footer-text", "text": visible}
                )
            if len(samples) < 8:
                samples.append(
                    {
                        "part": part,
                        "text": visible,
                        "pageInstructions": page_instructions,
                    }
                )
        if (
            referenced_targets
            and not missing_effective_section_refs
            and not unresolved_refs
            and not invalid_parts
        ):
            self.check(
                "word.footer.visible-page-number",
                "pass",
                "每个有效奇偶页页脚均含一个受控 PAGE 字段；PDF 不得盖印或修改页码。",
                {
                    "referencedFooterParts": sorted(referenced_targets),
                    "samples": samples,
                    "owner": "Word",
                },
            )
        else:
            self.check(
                "word.footer.visible-page-number",
                "fail",
                "正式 Word 内容母版缺少有效奇偶页 PAGE 页脚，或页脚内容不符合受控规则。",
                {
                    "referencedFooterParts": sorted(referenced_targets),
                    "missingEffectiveSectionRefs": missing_effective_section_refs[:20],
                    "unresolvedRefs": unresolved_refs[:20],
                    "invalidParts": invalid_parts[:20],
                    "samples": samples,
                },
                "must-fix",
            )

    def audit_toc_entries(self, zf: zipfile.ZipFile) -> None:
        paragraphs = self.body_paragraphs(zf)
        toc_idx, toc_end = self.find_toc_block(paragraphs)
        if toc_idx is None or toc_end is None:
            self.check("word.toc.entries.right-page-column", "fail", "未找到可审计的目录模块。", {}, "must-fix")
            return

        failures = []
        entries = []
        for idx in range(toc_idx + 1, toc_end):
            p = paragraphs[idx]
            value = text_of(p).strip()
            if not value:
                continue
            if re.fullmatch(r"\d+", value):
                failures.append({"paragraphIndex": idx + 1, "text": value, "reason": "page-number-as-own-paragraph"})
                continue
            entries.append({"paragraphIndex": idx + 1, "text": value[:140]})
            props = self.para_props(p)
            if "\t" not in value:
                reason = "page-number-attached-to-title-text" if re.search(r"\d{1,3}$", value) else "missing-right-tab"
                failures.append({"paragraphIndex": idx + 1, "text": value[:160], "reason": reason})
                continue
            label, page = value.rsplit("\t", 1)
            if not label.strip():
                failures.append({"paragraphIndex": idx + 1, "text": value[:160], "reason": "missing-label-before-page"})
            if not page.strip().isdigit():
                failures.append({"paragraphIndex": idx + 1, "text": value[:160], "reason": "page-not-final-numeric-field"})
            if re.search(r"\d{1,3}\s*$", label):
                failures.append({"paragraphIndex": idx + 1, "text": value[:160], "reason": "page-number-appears-before-final-tab"})
            if (
                props.get("tabVal") != TOC_PAGE_TAB_ALIGNMENT
                or not toc_tab_position_matches(props.get("tabPos"))
                or not tab_leader_matches(props.get("tabLeader"))
            ):
                failures.append(
                    {
                        "paragraphIndex": idx + 1,
                        "text": value[:160],
                        "reason": "right-tab-parameter-mismatch",
                        "actual": {
                            "tabVal": props.get("tabVal"),
                            "tabPos": props.get("tabPos"),
                            "tabLeader": props.get("tabLeader"),
                        },
                        "expected": {
                            "tabVal": TOC_PAGE_TAB_ALIGNMENT,
                            "designTabPos": TOC_PAGE_TAB_POS_DXA,
                            "wordNativeSerializedTabPos": TOC_PAGE_TAB_WORD_NATIVE_POS_DXA,
                            "acceptedSerializedPositionToleranceDxa": TOC_PAGE_TAB_TOLERANCE_DXA,
                            "tabLeader": TOC_PAGE_TAB_LEADER,
                            "minimumTitleToPageNumberGapDxa": TOC_ENTRY_TAB.get("minimumTitleToPageNumberGapDxa"),
                        },
                    }
                )

        if failures or not entries:
            self.check(
                "word.toc.entries.right-page-column",
                "fail",
                "目录页码未全部位于最后的右对齐页码列。",
                {
                    "moduleSpecPath": str(MODULE_SPEC_PATH),
                    "moduleSpecVersion": SPEC_VERSION,
                    "entryCount": len(entries),
                    "expectedTab": TOC_ENTRY_TAB,
                    "failures": failures[:30],
                    "rule": "每条目录必须为：条目文字 + 右对齐制表位 + 最后一字段数字页码。",
                },
                "must-fix",
            )
        else:
            self.check(
                "word.toc.entries.right-page-column",
                "pass",
                "目录条目均使用最后字段右对齐页码列。",
                {
                    "moduleSpecPath": str(MODULE_SPEC_PATH),
                    "moduleSpecVersion": SPEC_VERSION,
                    "entryCount": len(entries),
                    "samples": entries[:8],
                    "expectedTab": TOC_ENTRY_TAB,
                },
            )

    def is_probable_toc(self, txt: str, props: dict[str, Any]) -> bool:
        if props.get("pStyle") and "TOC" in str(props.get("pStyle")).upper():
            return True
        if "\t" in txt and re.search(r"\t\s*\d+\s*$", txt):
            return True
        return bool(re.match(r"^第\s*A\d{2}\s*讲.*\d+$", txt) or re.match(r"^第A\d{2}讲.*\d+$", txt))

    def is_chemistry_main_title(self, txt: str) -> bool:
        if "\t" in txt:
            return False
        compact = re.sub(r"\s+", "", txt)
        return bool(re.match(r"^专题([一二三四五六七八九十]|\d)", compact)) and len(compact) <= 24

    def is_english_main_title(self, txt: str) -> bool:
        if "\t" in txt:
            return False
        return bool(re.match(r"^Unit\s*\d+\s+.+", txt, re.I))

    def audit_titles_and_body(self, zf: zipfile.ZipFile) -> None:
        paragraphs = self.body_paragraphs(zf)
        paragraph_style_runs = self.paragraph_style_run_props(zf)
        chapter_style_ids = self.registered_paragraph_style_ids(
            zf, "CZ_ChapterTitle"
        )
        chapter_titles = []
        toc_titles = []
        title_style_groups: dict[str, dict[str, Any]] = {}
        body_style_counter: Counter[str] = Counter()
        body_samples = []
        forbidden_hits = []
        answer_symbol_hits = []
        for idx, p in enumerate(paragraphs, 1):
            txt = text_of(p).strip()
            if not txt:
                continue
            props = self.para_props(p)
            runs = self.visible_runs(p)
            effective_runs = self.effective_runs(runs, props.get("pStyle"), paragraph_style_runs)
            if any(token.lower() in txt.lower() for token in SENSITIVE_STUDENT_TOKENS):
                forbidden_hits.append({"paragraphIndex": idx, "text": txt[:160]})
            if re.fullmatch(r"[√×✓✗\s]+", txt):
                answer_symbol_hits.append({"paragraphIndex": idx, "text": txt[:160]})

            looks_like_chapter = bool(
                re.match(r"^第\s*A\d{2}\s*讲|^第A\d{2}讲", txt)
                or self.is_chemistry_main_title(txt)
                or self.is_english_main_title(txt)
            )
            is_chapter = props.get("pStyle") in chapter_style_ids
            if looks_like_chapter and self.is_probable_toc(txt, props):
                toc_titles.append({"index": idx, "text": txt[:120], "paragraphProps": props, "runs": effective_runs[:3], "directRuns": runs[:3]})
                continue
            if is_chapter:
                entry = {"index": idx, "text": txt[:120], "paragraphProps": props, "runs": effective_runs[:3], "directRuns": runs[:3]}
                chapter_titles.append(entry)
                style_runs = self.style_only_runs(effective_runs)
                style_para = {k: props.get(k) for k in ["jc", "before", "after", "line", "lineRule", "firstLine"]}
                key = json.dumps({"paragraphProps": style_para, "runs": style_runs}, ensure_ascii=False, sort_keys=True)
                title_style_groups.setdefault(key, {"count": 0, "samples": [], "style": {"paragraphProps": style_para, "runs": style_runs}})
                title_style_groups[key]["count"] += 1
                if len(title_style_groups[key]["samples"]) < 5:
                    title_style_groups[key]["samples"].append(txt[:120])
                continue

            if len(txt) > 8 and len(body_samples) < 600:
                # Skip obvious TOC or header-ish rows.
                if (
                    txt in TOC_TITLES
                    or re.match(r"^(专题|课题|第\d+课时)", txt)
                    or props.get("pStyle") in {"CZ_TocTitle", "CZ_Toc1", "CZ_Toc2", "CZ_Toc3"}
                    or ("\t" in txt and re.search(r"\t\s*\d+\s*$", txt))
                ):
                    continue
                sig = {
                    "paragraphProps": {k: props.get(k) for k in ["before", "after", "line", "lineRule", "pStyle"]},
                    "runs": [
                        {k: r.get(k) for k in ["fontEastAsia", "fontAscii", "pt", "bold", "color"]}
                        for r in effective_runs[:1]
                    ],
                }
                body_style_counter[json.dumps(sig, ensure_ascii=False, sort_keys=True)] += 1
                body_samples.append({"index": idx, "text": txt[:80], "style": sig})

        if forbidden_hits:
            self.check(
                "word.content.student-safety-markers",
                "needs-review",
                "学生版 Word 出现答案/解析/OCR/复核/内部来源等风险标记；按本轮要求只报告，不自动处理内容。",
                {"hits": forbidden_hits[:30], "handling": "report-only"},
                "content-report-only",
            )
        else:
            self.check("word.content.student-safety-markers", "pass", "未发现学生版禁用的显式答案/解析/OCR/复核/内部来源标记。", {})

        if answer_symbol_hits:
            self.check(
                "word.content.answer-symbols",
                "needs-review",
                "学生版 Word 出现疑似答案用 √/× 符号；按本轮要求只报告，不自动处理内容。",
                {"hits": answer_symbol_hits[:30], "handling": "report-only"},
                "content-report-only",
            )
        else:
            self.check("word.content.answer-symbols", "pass", "未发现独立疑似答案 √/× 符号。", {})

        title_groups = sorted(title_style_groups.values(), key=lambda x: x["count"], reverse=True)
        noncompliant_titles = []
        for item in chapter_titles:
            runs = item["runs"]
            first = runs[0] if runs else {}
            pt = first.get("pt")
            color = normalize_color(first.get("color"))
            if not (pt is not None and 15 <= pt <= 18 and first.get("bold") and color in {"1F2933", "000000", None}):
                noncompliant_titles.append(item)
        if not chapter_titles:
            self.check("word.title.chapter-detected", "fail", "未在 Word 正文检测到章节首页主标题。", {"tocTitleCount": len(toc_titles)}, "must-fix")
        elif noncompliant_titles:
            self.check(
                "word.title.chapter-style",
                "fail",
                "章节首页主标题未统一到规范允许的字号、字重和颜色范围。",
                {"chapterTitleCount": len(chapter_titles), "styleGroupCount": len(title_groups), "examples": noncompliant_titles[:12], "styleGroups": title_groups[:8]},
                "must-fix",
            )
        elif len(title_groups) > 2:
            self.check(
                "word.title.chapter-style",
                "needs-review",
                "章节首页主标题参数基本在允许范围，但样式组过多，需要确认是否为分层标题例外。",
                {"chapterTitleCount": len(chapter_titles), "styleGroupCount": len(title_groups), "styleGroups": title_groups[:8]},
                "review",
            )
        else:
            self.check(
                "word.title.chapter-style",
                "pass",
                "章节首页主标题字号、字重和颜色处于规范允许范围。",
                {"chapterTitleCount": len(chapter_titles), "styleGroupCount": len(title_groups), "styleGroups": title_groups[:4]},
            )

        dominant = body_style_counter.most_common(8)
        bad_body_examples = []
        for sample in body_samples[:300]:
            runs = sample["style"]["runs"]
            first = runs[0] if runs else {}
            para = sample["style"]["paragraphProps"]
            p_style = para.get("pStyle")
            if first.get("pt") and first.get("pt") < 9:
                bad_body_examples.append(sample)
            elif (
                para.get("line")
                and p_style in {"CZ_ImageBlock", "CZ_TableText", "CZ_TableHeader", "CZ_Caption"}
                and para.get("line") not in {"240", "300", "420"}
            ):
                bad_body_examples.append(sample)
            elif (
                para.get("line")
                and p_style not in {"CZ_ImageBlock", "CZ_TableText", "CZ_TableHeader", "CZ_Caption"}
                and para.get("line") not in {"240", "264", "270", "276", "280", "288", "300", "320", "420"}
            ):
                bad_body_examples.append(sample)
            if len(bad_body_examples) >= 15:
                break
        if bad_body_examples:
            self.check(
                "word.body.parameters",
                "fail",
                "正文抽样存在明显偏离字号或行距参数的段落。",
                {"dominantStyles": dominant, "examples": bad_body_examples},
                "must-fix",
            )
        else:
            self.check(
                "word.body.parameters",
                "pass",
                "正文抽样未发现过小字号或极端行距，结构参数符合正文模块要求。",
                {"dominantStyles": dominant, "sampleCount": len(body_samples)},
            )

    def audit(self) -> dict[str, Any]:
        self.audit_path_and_name()
        with zipfile.ZipFile(self.path) as zf:
            document_root = ET.fromstring(zf.read("word/document.xml"))
            self.front_print_marker_count = len(
                document_root.findall(
                    f".//{WP}docPr[@name='{FRONT_PRINT_MARKER}']"
                )
            )
            self.back_print_marker_count = len(
                document_root.findall(
                    f".//{WP}docPr[@name='{BACK_PRINT_MARKER}']"
                )
            )
            self.complete_print_master = False
            marker_status = (
                "pass"
                if self.front_print_marker_count == 0
                and self.back_print_marker_count == 0
                else "fail"
            )
            self.check(
                "word.content-source.binding-page-markers",
                marker_status,
                (
                    "正式 Word 中封皮和封底标记均为 0。"
                    if marker_status == "pass"
                    else "正式 Word 发现封皮或封底标记；装订页只能由 PDF assembly 添加。"
                ),
                {
                    "frontMarkerCount": self.front_print_marker_count,
                    "backMarkerCount": self.back_print_marker_count,
                    "wordContentMasterOnly": True,
                },
                "must-fix" if marker_status == "fail" else "info",
            )
            self.audit_word_native_format(zf)
            self.audit_word_clean_open()
            self.audit_sections(zf)
            self.audit_headers(zf)
            self.audit_footers(zf)
            self.audit_toc_entries(zf)
            self.audit_titles_and_body(zf)
        status_counts = Counter(check.status for check in self.checks)
        severity_counts = Counter(check.severity for check in self.checks)
        return {
            "path": str(self.path),
            "relativePath": self.relative,
            "sha256": sha256(self.path),
            "bytes": self.path.stat().st_size,
            "statusCounts": dict(status_counts),
            "severityCounts": dict(severity_counts),
            "checks": [c.as_dict() for c in self.checks],
        }


def discover_formal_word_docs() -> list[Path]:
    return filter_paths(sorted(
        p
        for p in FORMAL_ROOT.rglob("word/*.docx")
        if "/缓存/" not in p.as_posix()
        and not p.name.startswith("~$")
    ))


def load_clean_probe_index(
    report_path: Path = PROBE_REPORT_PATH,
) -> dict[str, dict[str, Any]]:
    if not report_path.exists():
        return {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    index: dict[str, dict[str, Any]] = {}
    for item in report.get("results", []):
        raw = item.get("path")
        if not raw:
            continue
        path = Path(str(raw))
        keys = {str(raw)}
        if path.is_absolute():
            keys.add(str(path.resolve()))
        else:
            keys.add(str((CHENGZI_ROOT / path).resolve()))
            keys.add(str((ROOT / path).resolve()))
        for key in keys:
            index[key] = item
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", nargs="*", type=Path)
    parser.add_argument("--probe-report", type=Path, default=PROBE_REPORT_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    out_path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    docs = (
        [path.resolve() for path in args.documents]
        if args.documents
        else discover_formal_word_docs()
    )
    missing = [str(path) for path in docs if not path.is_file()]
    if missing:
        raise SystemExit(f"missing Word audit documents: {missing}")
    clean_probe_index = load_clean_probe_index(args.probe_report.resolve())
    results = [DocxAudit(path, clean_probe_index).audit() for path in docs]
    aggregate = {
        "schemaVersion": "chengziclass.summer-word-full-compliance-audit.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "specVersion": SPEC_VERSION,
        "specPath": str(SPEC_PATH),
        "moduleSpecPath": str(MODULE_SPEC_PATH),
        "scope": {
            "formalRoot": str(FORMAL_ROOT),
            "formalWordCount": len(docs),
            "activeScope": active_scope(),
            "includedRelativePaths": [
                p.resolve().relative_to(ROOT.resolve()).as_posix()
                for p in docs
            ],
        },
        "gate": {
            "wordPassRequiredBeforePdf": True,
            "passDefinition": "Formal Word masters must be current .docx, pass Microsoft Word native clean-open for the current hash, and pass module audits before PDF export. Content issues are report-only unless the user later authorizes content handling.",
        },
        "results": results,
    }
    aggregate["summary"] = {
        "documentsWithFail": sum(1 for r in results if r["statusCounts"].get("fail")),
        "documentsWithNeedsReview": sum(1 for r in results if r["statusCounts"].get("needs-review")),
        "totalFailChecks": sum(r["statusCounts"].get("fail", 0) for r in results),
        "totalNeedsReviewChecks": sum(r["statusCounts"].get("needs-review", 0) for r in results),
    }
    out_path.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(out_path)
    print(json.dumps(aggregate["summary"], ensure_ascii=False, indent=2))
    if aggregate["summary"]["totalFailChecks"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
