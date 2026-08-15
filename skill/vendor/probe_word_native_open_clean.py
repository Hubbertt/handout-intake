#!/usr/bin/env python3
"""Probe whether a DOCX opens cleanly in Microsoft Word on macOS.

The probe opens the file with Microsoft Word, watches for unreadable-content,
repair, recovered-document, or untitled-document states, and quits without
saving. It does not edit the document.
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
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import json

from summer_word_contract import WORD_PROBE_ROOT, compatibility_mode, sha256


ROOT = _hi_env("HANDOUT_INTAKE_MATERIALS_ROOT", "~/handout-intake-materials")
RUN_DIR = ROOT / "reviews/2026-06-30-v4-5-8-module-workflow"
REPORT_PATH = RUN_DIR / "word_native_open_clean_probe_report.json"
WORD_SAVED_STATE = Path.home() / "Library/Saved Application State/com.microsoft.Word.savedState"
REPAIR_TEXT_MARKERS = [
    "发现无法读取",
    "无法读取的内容",
    "是否恢复",
    "显示修复",
    "已恢复",
    "文件可能已经损坏",
    "可能已经损坏",
]
UNTITLED_PREFIXES = ("文档", "Document")


def run_osascript(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["osascript", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            ["osascript", "-"],
            124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout}s",
        )


def applescript_string(value: Path | str) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def close_word() -> None:
    for command in [
        ["osascript", "-e", 'if application "Microsoft Word" is running then tell application "Microsoft Word" to quit saving no'],
        ["pkill", "-x", "Microsoft Word"],
        ["pkill", "-f", "Microsoft Error Reporting"],
    ]:
        try:
            subprocess.run(command, text=True, capture_output=True, timeout=8)
        except subprocess.TimeoutExpired:
            continue
    deadline = time.time() + 10
    while time.time() < deadline:
        probe = subprocess.run(
            ["pgrep", "-x", "Microsoft Word"],
            text=True,
            capture_output=True,
        )
        if probe.returncode != 0:
            break
        time.sleep(0.25)
    shutil.rmtree(WORD_SAVED_STATE, ignore_errors=True)


def cleanup_probe_files() -> None:
    if not WORD_PROBE_ROOT.exists():
        return
    for path in WORD_PROBE_ROOT.glob("word-clean-open-probe-*"):
        try:
            path.unlink()
        except IsADirectoryError:
            shutil.rmtree(path, ignore_errors=True)
        except FileNotFoundError:
            continue


def ensure_word_ready(timeout: int = 20) -> subprocess.CompletedProcess[str]:
    launch = subprocess.run(
        ["open", "-gj", "-a", "Microsoft Word"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if launch.returncode != 0:
        return launch
    deadline = time.time() + timeout
    last = subprocess.CompletedProcess(["osascript", "-"], 1, stdout="", stderr="Word did not become ready")
    while time.time() < deadline:
        last = run_osascript(
            'tell application "Microsoft Word" to return name',
            timeout=5,
        )
        if last.returncode == 0:
            return last
        time.sleep(0.5)
    return last


def open_word_document(path: Path, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    script = f'''
tell application "Microsoft Word"
    launch
    open file name {applescript_string(path)} read only true add to recent files false
end tell
'''
    ready = ensure_word_ready(timeout=min(timeout, 20))
    if ready.returncode != 0:
        return ready
    opener = run_osascript(script, timeout=timeout)
    for _attempt in range(2):
        error_text = f"{opener.stdout}\n{opener.stderr}"
        if opener.returncode == 0 or ("-609" not in error_text and "连接无效" not in error_text):
            break
        time.sleep(1)
        ready = ensure_word_ready(timeout=min(timeout, 20))
        if ready.returncode != 0:
            return ready
        opener = run_osascript(script, timeout=timeout)
    return opener


def word_windows() -> list[str]:
    script = r'''
tell application "System Events"
    if exists process "Microsoft Word" then
        tell process "Microsoft Word"
            return name of every window
        end tell
    end if
end tell
return ""
'''
    proc = run_osascript(script, timeout=10)
    if proc.returncode != 0:
        return [f"osascript-error:{proc.stderr.strip()}"]
    raw = proc.stdout.strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def static_texts() -> str:
    script = r'''
tell application "System Events"
    if exists process "Microsoft Word" then
        tell process "Microsoft Word"
            set out to ""
            repeat with w in windows
                try
                    set out to out & "WINDOW:" & (name of w as text) & "\n"
                    set out to out & ((value of every static text of w) as text) & "\n"
                end try
            end repeat
            return out
        end tell
    end if
end tell
return ""
'''
    proc = run_osascript(script, timeout=10)
    return proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip()


def word_documents() -> list[dict[str, str]]:
    script = r'''
tell application "Microsoft Word"
    set out to ""
    repeat with d in documents
        try
            set out to out & (name of d as text) & linefeed
        end try
    end repeat
    return out
end tell
'''
    proc = run_osascript(script, timeout=10)
    if proc.returncode != 0:
        return [{"name": "osascript-error", "error": proc.stderr.strip()}]
    docs = []
    for line in proc.stdout.splitlines():
        value = line.strip()
        if value:
            docs.append({"name": value})
    return docs


def has_repair_marker(*values: str) -> bool:
    joined = "\n".join(v or "" for v in values)
    return any(marker in joined for marker in REPAIR_TEXT_MARKERS)


def has_untitled_or_recovered_document(docs: list[dict[str, str]], windows: list[str]) -> bool:
    names = [doc.get("name", "") for doc in docs] + windows
    for name in names:
        if "已恢复" in name:
            return True
        if name.lower().startswith("document ") and not name.lower().endswith((".docx", ".doc")):
            return True
        if any(name.startswith(prefix) for prefix in UNTITLED_PREFIXES) and not name.lower().endswith((".docx", ".doc")):
            return True
    return False


def has_document_window(windows: list[str]) -> bool:
    ignored = {
        "Microsoft Word",
        "打开新的和最近使用的文件",
        "Open New and Recent",
    }
    return any(name and name not in ignored for name in windows)


def probe(path: Path, wait_seconds: float = 20.0, *, open_original: bool = False) -> dict[str, object]:
    close_word()
    temp_probe_created = False
    probe_path = path
    if not open_original:
        WORD_PROBE_ROOT.mkdir(parents=True, exist_ok=True)
        cleanup_probe_files()
        probe_path = WORD_PROBE_ROOT / f"word-clean-open-probe-{os.getpid()}-{int(time.time() * 1000)}-{path.name}"
        shutil.copy2(path, probe_path)
        temp_probe_created = True

    opener = open_word_document(probe_path)
    record: dict[str, object] = {
        "path": str(path),
        "probePath": str(probe_path),
        "openedOriginal": open_original,
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "compatibilityMode": compatibility_mode(path),
        "openedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "openReturnCode": opener.returncode,
        "openStdoutTail": opener.stdout[-1000:],
        "openStderrTail": opener.stderr[-1000:],
        "status": "failed",
    }
    if opener.returncode != 0:
        texts = static_texts()
        if has_repair_marker(opener.stderr, opener.stdout, texts):
            record["status"] = "repair-state"
            record["staticTexts"] = texts
            close_word()
            if temp_probe_created:
                probe_path.unlink(missing_ok=True)
            return record
        close_word()
        if temp_probe_created:
            probe_path.unlink(missing_ok=True)
        return record

    end = time.time() + wait_seconds
    observed: list[str] = []
    observed_docs: list[dict[str, str]] = []
    observed_static = ""
    while time.time() < end:
        windows = word_windows()
        docs = word_documents()
        texts = static_texts()
        observed = windows
        observed_docs = docs
        observed_static = texts
        joined = " | ".join(windows)
        if has_repair_marker(texts, joined) or has_untitled_or_recovered_document(docs, windows):
            record["status"] = "repair-state"
            record["windows"] = windows
            record["documents"] = docs
            record["staticTexts"] = texts
            close_word()
            if temp_probe_created:
                probe_path.unlink(missing_ok=True)
            return record
        if docs and not any(doc.get("name") == "osascript-error" for doc in docs):
            record["status"] = "pass"
            record["windows"] = windows
            record["documents"] = docs
            close_word()
            if temp_probe_created:
                probe_path.unlink(missing_ok=True)
            return record
        if has_document_window(windows):
            record["status"] = "pass"
            record["windows"] = windows
            record["documents"] = docs
            close_word()
            if temp_probe_created:
                probe_path.unlink(missing_ok=True)
            return record
        time.sleep(1)

    record["status"] = "timeout-or-no-document-window"
    record["windows"] = observed
    record["documents"] = observed_docs
    record["staticTexts"] = observed_static or static_texts()
    close_word()
    if temp_probe_created:
        probe_path.unlink(missing_ok=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--wait", type=float, default=20.0)
    parser.add_argument("--open-original", action="store_true", help="Open the source file itself instead of a temporary copy.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    results = [probe(path, args.wait, open_original=args.open_original) for path in args.paths]
    report = {
        "schemaVersion": "chengziclass.word-native-open-clean-probe.v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "results": results,
        "summary": {
            "documents": len(results),
            "passed": sum(1 for r in results if r.get("status") == "pass"),
            "repairState": sum(1 for r in results if r.get("status") == "repair-state"),
            "failed": sum(1 for r in results if r.get("status") not in {"pass", "repair-state"}),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["repairState"] or report["summary"]["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
