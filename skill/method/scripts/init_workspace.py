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
    # VERSION 在产品根(skill/ 的上一层);skill/ 自己的那份是开发目录的遗留。
    # 分发件里只有产品根那份,首版只找 skill/VERSION,新装报 0.0.0-unversioned——
    # 一个「不知道自己是什么版本」的安装,升级时就无从判断该不该接受成长物。
    for marker in (SKILL_ROOT.parent / "VERSION", SKILL_ROOT / "VERSION"):
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip()
    return "0.0.0-unversioned"


VOLUME_BINDINGS_TEMPLATE = {
    "_what": "新册绑定模板(init 生成)。用户只改三处:paths.source / assets.cover / assets.back 指向 inputs/ 里的文件;选样式改 params。",
    "_learnedFrom": "2026-08-15 全新安装实测:从参考册拷绑定会带来别的机器的事实——写死的解释器、通配的产物名、生产线的文件名。",
    "paths": {
        "source": "inputs/<源.docx>",
        "assets.cover": "inputs/cover.pdf",
        "assets.back": "inputs/back.svg",
        "params": "<产品根>/styles/compositions/<根id>+<包id>/params.json  ← 选一组样式=选一个根+一个包;没有的组合先 styles/compose.py --root X --pack Y",
        "word": "output/<按规范命名>.docx",
        "print-master": "output/print-master.pdf",
    },
    "interpreter": {"pythonpath": "work/pylib",
                    "_why": "不写死 python:由安装向导探测(runtime/probe-report.json)。写死一个别处拷来的路径,是把别的机器的事实带进这一册。"},
    "theme": "<册主题,如 第四章 光>",
    "pdfKey": "<册键,如 g08_ph_a10a14>",
    "_wordNameWhy": "文件名须过 formal.filename.shape(年份-班型-年级-册-科目-版本-讲义-范围)。改了会被门拦——门拦是对的。",
}


def initialise(workspace: Path, force: bool, volume: str | None = None) -> dict[str, Any]:
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
    report = {"status": "initialised", "root": str(root),
              "skillVersion": manifest["skillVersion"], "seeds": len(manifest["seeds"])}
    if volume:
        report["bindings"] = str(write_volume_bindings(root, volume, force))
    return report


def write_volume_bindings(root: Path, volume: str, force: bool) -> Path:
    """写出这一册的 bindings.json——真文件,带 <…> 占位,用户只填占位。

    ★不再让人「从现有册拷一份」:2026-08-15 全新安装实测,拷来的绑定带着别的机器的事实
    (写死的解释器、通配的产物名、生产线的文件名),每一样都让链在别处失败。
    模板里外部输入的公有默认取自工序表(seeds/ 与 skill 内的路径按包根算),用户不必知道它们。
    """
    vdir = root / "volumes" / volume
    vdir.mkdir(parents=True, exist_ok=True)
    target = vdir / "bindings.json"
    if target.exists() and not force:
        return target
    product = SKILL_ROOT.parent if (SKILL_ROOT.parent / "styles").exists() else SKILL_ROOT
    b = json.loads(json.dumps(VOLUME_BINDINGS_TEMPLATE))
    b["schemaVersion"] = "handout-intake.bindings.v1"
    b["volume"] = volume
    b["paths"]["params"] = str(product / "styles" / "compositions" / "<根id>+<包id>" / "params.json")
    b["paths"]["spec"] = str(next((product / "styles" / "base").glob("*.md"), product / "styles/base/<规范>.md"))
    # 目录型外部输入要带 /*:resolve 按文件判定,裸目录会被判「不存在」(release.reviewRoot 那次同形)。
    b["paths"]["seeds.templates"] = str(SKILL_ROOT / "seeds" / "templates" / "*.json")
    b["paths"]["schema.reference"] = str(SKILL_ROOT / "seeds" / "examples" / "handout-carve.chemistry-g08.v1.json")
    # 参照件是种子,随 skill 走。★曾指向生产线目录,每次「全新安装」都靠拷来的绑定悄悄继承,
    # 真正陌生的机器上不存在,链在第 2、4 步就断。
    b["paths"]["mapping.shared"] = str(SKILL_ROOT / "seeds" / "examples" / "private-spec-mapping.chemistry-g08.v1.json")
    b["paths"]["matrix.reference"] = str(SKILL_ROOT / "seeds" / "examples" / "audit-matrix.chemistry-g08.v1.json")
    # 册级文件的约定位置——用户按 README 放进这些目录即可,不必改绑定
    b["paths"].update({
        "truth-map": "quality/step0-truth-map.v1.md",
        "schema": "carve-rules-provisional/schema/<册schema>.json",
        "registry": "carve-rules-provisional/registry.json",
        "mapping.own": "carve-rules-provisional/mapping/<册私有映射>.json",
        "mapping.substitutions": "carve-rules-provisional/mapping/native-text-substitutions.v1.json",
        "census": "census/block-census.v1.json",
        "matrix": "quality/audit-matrix.<册>.json",
        "source.stripped": "work/<源名>-已清零宽.docx",
    })
    b["_fillTheseOnly"] = ["paths.source", "paths.assets.cover", "paths.assets.back",
                           "paths.params(选一组样式)", "paths.word(按规范命名)",
                           "paths.schema / paths.mapping.own(册的切分规则文件名)",
                           "paths.matrix / paths.source.stripped(册名)", "theme", "pdfKey"]
    b["_doNotAdd"] = "interpreter.python——由安装向导探测,写死会把别的机器的事实带进这一册。"
    target.write_text(json.dumps(b, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path,
                        help="项目根目录;成长物写在它下面的 .handout-intake/")
    parser.add_argument("--force", action="store_true",
                        help="重新绑定到当前 skill 版本（不删除已有成长物）")
    parser.add_argument("--volume", help="册 id;给了就同时写出该册的 bindings.json 模板")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        raise SystemExit(f"工作区不存在:{args.workspace}")
    report = initialise(args.workspace.resolve(), args.force, args.volume)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
