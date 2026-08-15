#!/usr/bin/env python3
"""Shared formal-material scope filters for summer handout workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable


LEGACY_SCOPE_ENV = "CHENGZI_SUMMER_WORD_SCOPE"
EXTRA_ENV = "CHENGZI_SUMMER_EXTRA_MATERIALS"
KEYS_ENV = "CHENGZI_SUMMER_FILTER_KEYS"
SUBJECTS_ENV = "CHENGZI_SUMMER_FILTER_SUBJECTS"
GRADES_ENV = "CHENGZI_SUMMER_FILTER_GRADES"
TERMS_ENV = "CHENGZI_SUMMER_FILTER_TERMS"
EDITIONS_ENV = "CHENGZI_SUMMER_FILTER_EDITIONS"

SUBJECT_ALIASES = {
    "en": "英语",
    "eng": "英语",
    "english": "英语",
    "英语": "英语",
    "cn": "语文",
    "chinese": "语文",
    "语文": "语文",
    "ph": "物理",
    "physics": "物理",
    "物理": "物理",
    "ch": "化学",
    "chemistry": "化学",
    "化学": "化学",
}
GRADE_ALIASES = {
    "7": "七年级",
    "07": "七年级",
    "g7": "七年级",
    "g07": "七年级",
    "七": "七年级",
    "七年级": "七年级",
    "8": "八年级",
    "08": "八年级",
    "g8": "八年级",
    "g08": "八年级",
    "八": "八年级",
    "八年级": "八年级",
}
TERM_ALIASES = {
    "s1": "上册",
    "1": "上册",
    "上": "上册",
    "上册": "上册",
    "s2": "下册",
    "2": "下册",
    "下": "下册",
    "下册": "下册",
    "all": "全一册",
    "full": "全一册",
    "全": "全一册",
    "全一册": "全一册",
}
EDITION_ALIASES = {
    "student": "学生版",
    "students": "学生版",
    "学生": "学生版",
    "学生版": "学生版",
    "teacher": "教师版",
    "teachers": "教师版",
    "老师": "教师版",
    "教师": "教师版",
    "教师版": "教师版",
}
KEY_SUBJECTS = {
    "cn": "语文",
    "ch": "化学",
    "en": "英语",
    "math": "数学",
    "ph": "物理",
}
GRADE_KEYS = {
    "七年级": "g07",
    "八年级": "g08",
}
SUBJECT_KEYS = {value: key for key, value in KEY_SUBJECTS.items()}
SPECIAL_FILE_KEYS = {
    "下册-专题3至专题6": "g08_ch_topics3_6",
}


def _split_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def _normalize(values: Iterable[str], aliases: dict[str, str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        key = value.strip().lower()
        normalized.add(aliases.get(key, aliases.get(value.strip(), value.strip())))
    return normalized


def _active_scope_sets() -> dict[str, Any]:
    editions = _normalize(_split_env(EDITIONS_ENV), EDITION_ALIASES)
    legacy = os.environ.get(LEGACY_SCOPE_ENV, "").strip().lower()
    if not editions:
        if legacy == "student":
            editions = {"学生版"}
        elif legacy in {"teacher", "teachers"}:
            editions = {"教师版"}
    return {
        "keys": {value.strip() for value in _split_env(KEYS_ENV)},
        "subjects": _normalize(_split_env(SUBJECTS_ENV), SUBJECT_ALIASES),
        "grades": _normalize(_split_env(GRADES_ENV), GRADE_ALIASES),
        "terms": _normalize(_split_env(TERMS_ENV), TERM_ALIASES),
        "editions": editions,
        "legacyWordScope": os.environ.get(LEGACY_SCOPE_ENV),
    }


def active_scope() -> dict[str, Any]:
    scope = _active_scope_sets()
    return {
        "keys": sorted(scope["keys"]),
        "subjects": sorted(scope["subjects"]),
        "grades": sorted(scope["grades"]),
        "terms": sorted(scope["terms"]),
        "editions": sorted(scope["editions"]),
        "legacyWordScope": scope["legacyWordScope"],
    }


def _first_match(value: str, candidates: set[str]) -> str | None:
    return next((candidate for candidate in candidates if candidate in value), None)


def _metadata_for_key(key: str | None) -> dict[str, str | None]:
    if not key:
        return {"subject": None, "grade": None, "term": None, "edition": None}
    parts = key.split("_")
    grade = None
    subject = None
    if parts:
        grade = GRADE_ALIASES.get(parts[0].lower())
    if len(parts) >= 2:
        subject = KEY_SUBJECTS.get(parts[1].lower())
    return {"subject": subject, "grade": grade, "term": None, "edition": None}


def metadata_for_path(path: Path, key: str | None = None) -> dict[str, str | None]:
    parts = list(path.parts)
    name = path.name
    subjects = set(SUBJECT_ALIASES.values())
    grades = set(GRADE_ALIASES.values())
    terms = set(TERM_ALIASES.values())
    subject = next((part for part in parts if part in subjects), None)
    grade = next((part for part in parts if part in grades), None)
    term = next((part for part in parts if part in terms), None)
    subject = subject or _first_match(name, subjects)
    grade = grade or _first_match(name, grades)
    term = term or _first_match(name, terms)
    edition = "教师版" if "教师版" in name else ("学生版" if "学生版" in name else None)
    key_meta = _metadata_for_key(key)
    subject = subject or key_meta["subject"]
    grade = grade or key_meta["grade"]
    term = term or key_meta["term"]
    edition = edition or key_meta["edition"]
    return {"subject": subject, "grade": grade, "term": term, "edition": edition}


def path_in_scope(path: Path, key: str | None = None) -> bool:
    scope = _active_scope_sets()
    if scope["keys"] and (key or "") not in scope["keys"]:
        return False
    meta = metadata_for_path(path, key)
    for field, scope_name in (
        ("subject", "subjects"),
        ("grade", "grades"),
        ("term", "terms"),
        ("edition", "editions"),
    ):
        wanted = scope[scope_name]
        if wanted and meta.get(field) not in wanted:
            return False
    return True


def key_for_path(path: Path) -> str | None:
    for marker, special_key in SPECIAL_FILE_KEYS.items():
        if marker in path.name:
            return special_key
    meta = metadata_for_path(path)
    grade_key = GRADE_KEYS.get(meta.get("grade") or "")
    subject_key = SUBJECT_KEYS.get(meta.get("subject") or "")
    if not grade_key or not subject_key:
        return None
    return f"{grade_key}_{subject_key}"


def key_in_scope(key: str | None) -> bool:
    scope = _active_scope_sets()
    if scope["keys"] and (key or "") not in scope["keys"]:
        return False
    meta = _metadata_for_key(key)
    for field, scope_name in (("subject", "subjects"), ("grade", "grades")):
        wanted = scope[scope_name]
        if wanted and meta.get(field) not in wanted:
            return False
    if scope["terms"] or scope["editions"]:
        return False
    return True


def filter_paths(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path_in_scope(path, key_for_path(path))]


def filter_doc_map(docs: dict[str, Path]) -> dict[str, Path]:
    return {key: path for key, path in docs.items() if path_in_scope(path, key)}


def extra_materials() -> dict[str, dict[str, Any]]:
    """册级临时注册:试制件、复现件、一次性实验件。

    为什么不直接往三处 DOCS/MATERIALS 里加键:那三张表不设 scope 环境变量时
    **全部通过**,加一个试制键会让今后每一次不带 scope 的生产跑都捎上它。
    要跑一份试制件,不该以改动别人生产线的默认集合为代价。

    **不设 CHENGZI_SUMMER_EXTRA_MATERIALS 时返回空字典**,三处注册表与今天
    逐字节相同——这是本次改动对既有生产线零影响的依据。

    JSON 形状:
      {"materials": {"<key>": {"docx": ..., "assetDir": ..., "cover": ...,
                               "back": ..., "pdfName": ...}}}
    """
    raw = os.environ.get(EXTRA_ENV, "").strip()
    if not raw:
        return {}
    path = Path(raw)
    if not path.is_file():
        raise RuntimeError(f"EXTRA_MATERIALS_NOT_FOUND: {EXTRA_ENV} 指向 {path},但那里没有文件")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("materials")
    if not isinstance(entries, dict):
        raise RuntimeError(f"EXTRA_MATERIALS_MALFORMED: {path} 里缺 materials 字典")
    return entries


def merge_extra(builtin: dict[str, Any],
                adapt: Callable[[str, dict[str, Any]], Any]) -> dict[str, Any]:
    """把外部注册的册并进内置表。**键冲突即报错,绝不覆盖。**

    悄悄覆盖一个内置键,是把生产件换成试制件的最短路径,而且不会有任何东西响。
    宁可拒绝,不可猜。
    """
    merged = dict(builtin)
    for key, value in extra_materials().items():
        if key in merged:
            raise RuntimeError(
                f"EXTRA_KEY_COLLISION: 「{key}」已是内置注册键,拒绝用外部注册覆盖它。"
                f"换一个不冲突的 key(例如加册次后缀)。")
        adapted = adapt(key, value)
        if adapted is not None:
            merged[key] = adapted
    return merged


def _candidate_path(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value
    if isinstance(value, dict):
        for field in (
            "docx",
            "word",
            "path",
            "dst",
            "formal_pdf",
            "formal",
            "src",
            "pdf",
            "standard_pdf",
            "outlined_pdf",
            "output_pdf",
        ):
            item = value.get(field)
            if isinstance(item, Path):
                return item
    for field in ("docx", "word", "path", "dst", "formal_pdf", "formal", "src", "pdf", "standard_pdf", "outlined_pdf", "output_pdf"):
        item = getattr(value, field, None)
        if isinstance(item, Path):
            return item
    return None


def item_in_scope(value: Any, key: str | None = None) -> bool:
    path = _candidate_path(value)
    return key_in_scope(key) if path is None else path_in_scope(path, key)


def filter_item_map(items: dict[str, Any]) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in items.items():
        path = _candidate_path(value)
        if (path is None and key_in_scope(key)) or (path is not None and path_in_scope(path, key)):
            filtered[key] = value
    return filtered
