#!/usr/bin/env python3
"""合成:一个根 × 一个包 → 一份完整参数表(交给编译器)。

**使用方定的模型**(2026-08-15):
  样式整套选。1 个全局默认模板 + 1 个局部修正 = 一组样式模板。
  全局默认和局部修正是独立的,可以任意组合。

三层,各自独立存放:
  roots/<rootId>.json    全局默认根(docDefaults),会影响渲染的属性一项不缺
  packs/<packId>.json    局部偏离包(paragraphStyles + characterStyles + release)
  base/base.json         底座:版面/页面/流程契约——既不是根也不是包,是方法层的东西

编译器不改:它继续读一份完整 params。改的是**谁来合成它**——本脚本。
合成结果是**投影**(可再生),不是真源;真源是 roots/ packs/ base/ 三处。
所以合成件写进 compositions/<root>+<pack>/params.json,并带 provenance 记它由谁合成。

拆分时数据自己证明了模型:三套模板拆开后
  根:2 个(docDefaults1 被 v1/blue 共用,sha 相同;docDefaults2 独立)
  包:school-b-v1 的包与 chengzi-summer-v1 **sha 完全相同**——它只换了根没改包,
     所以它不是一个包,它是「根2 × 包v1」这个组合。已删。
  底座:三份逐字相同 → 一份。

用法:
  compose.py --root docDefaults2 --pack chengzi-summer-v1        合成一组并过门
  compose.py --list                                             列出全部根/包/已合成的组合
  compose.py --all                                              合成全部组合(笛卡尔积)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skill"
ROOTS, PACKS, BASE, OUT = HERE / "roots", HERE / "packs", HERE / "base" / "base.json", HERE / "compositions"


def canon(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(o) -> str:
    return hashlib.sha256(canon(o).encode("utf-8")).hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)


NAME_OVERRIDE: dict = {}


def _comp_name(out_dir: Path, root, pack, root_id: str, pack_id: str) -> str:
    if NAME_OVERRIDE.get(f"{root_id}+{pack_id}"):
        return NAME_OVERRIDE[f"{root_id}+{pack_id}"]
    prev = out_dir / "template.json"
    if prev.exists():
        try:
            d = json.loads(prev.read_text(encoding="utf-8"))
            if d.get("nameCustom") and d.get("name"):
                return d["name"]
        except Exception:
            pass
    return f"{pack.get('name') or (pack.get('release') or {}).get('title') or pack_id} × {root.get('name') or root_id}"


def _was_custom(out_dir: Path) -> bool:
    prev = out_dir / "template.json"
    try:
        return bool(json.loads(prev.read_text(encoding="utf-8")).get("nameCustom")) if prev.exists() else False
    except Exception:
        return False


def compose(root_id: str, pack_id: str) -> Path:
    root_p, pack_p = ROOTS / f"{root_id}.json", PACKS / f"{pack_id}.json"
    for p, what in ((root_p, "根"), (pack_p, "包"), (BASE, "底座")):
        if not p.exists():
            raise SystemExit(json.dumps({"status": "refused", "why": f"{what}不存在:{p.name}"}, ensure_ascii=False))
    root, pack, base = load(root_p), load(pack_p), load(BASE)
    params = OrderedDict(base)
    params["docDefaults1"] = root                     # 编译器读这个槽位;根的身份在 root.rootId(真源里)
    if root.get("rootId") != root_id:
        raise SystemExit(json.dumps({"status": "refused", "why": f"roots/{root_id}.json 的 rootId 是 {root.get('rootId')!r},文件名与身份不一致。"}, ensure_ascii=False))
    reg = OrderedDict(params.get("wordStyleRegistry") or {})
    reg["paragraphStyles"] = pack["paragraphStyles"]
    reg["characterStyles"] = pack["characterStyles"]
    params["wordStyleRegistry"] = reg
    params["styleRegistryRelease"] = pack.get("release") or {}
    params["parameterTemplate"] = OrderedDict([
        ("schemaVersion", "chengziclass.parameter-template-binding.v1"),
        ("model", "1 个全局默认根 + 1 个局部偏离包 = 一组样式模板;两者独立,任意组合。使用方 2026-08-15 定。"),
        ("templateId", f"{root_id}+{pack_id}"),
        ("root", {"id": root_id, "sha256": sha(root)}),
        ("pack", {"id": pack_id, "sha256": sha(pack)}),
        ("base", {"sha256": sha(base)}),
        ("combinedSha256", hashlib.sha256(f"{sha(root)}:{sha(pack)}:{sha(base)}".encode()).hexdigest()),
        ("composedAt", datetime.now().astimezone().isoformat(timespec="seconds")),
        ("isProjection", True),
        ("truthSources", ["styles/roots/", "styles/packs/", "styles/base/base.json"]),
        ("why", "合成件是投影,可再生;不要直接改它——改了下次合成就没了。改根改 roots/,改偏离改 packs/。"),
    ])
    out_dir = OUT / f"{root_id}+{pack_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "params.json"
    out.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 组合目录自带 template.json,渲染器按它工作;场景/规范/大纲表指向底座,不各拷一份。
    scenes = load(HERE / "base" / "render-scenes.json").get("renderScenes") or []
    tpl = OrderedDict([("schemaVersion", "handout-intake.style-template.v2"),
                       ("id", f"{root_id}+{pack_id}"), ("root", root_id), ("pack", pack_id),
                       # 组合名:命令行 --name > 已自定义过的名(nameCustom) > 默认「包名 × 根名」。
                       # 三层都可自定义命名(使用方 2026-08-15 定);id 是机器标识,不改。
                       ("name", _comp_name(out_dir, root, pack, root_id, pack_id)),
                       ("params", "params.json"),
                       ("spec", "../../base/" + next((p.name for p in (HERE / 'base').glob('*.md')), '')),
                       ("outlineLevels", "../../base/" + next((p.name for p in (HERE / 'base').glob('chengzi_word_outline_levels*.json')), '')),
                       ("renderScenes", scenes),
                       ("nameCustom", bool(NAME_OVERRIDE.get(f"{root_id}+{pack_id}")) or _was_custom(out_dir)),
                       ("isProjection", True)])
    (out_dir / "template.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def gate(params: Path) -> list[dict]:
    py = sys.executable
    gates = [("GATE_UNIT_SANITY", "gate_unit_sanity.py"), ("GATE_HEADING_LADDER", "gate_heading_ladder.py"),
             ("GATE_INERT_VERSION", "gate_inert_version.py")]
    out = []
    for name, f in gates:
        r = subprocess.run([py, str(SKILL / "method/gates" / f), "--params", str(params)], capture_output=True, text=True)
        out.append({"gate": name, "exit": r.returncode, "tail": (r.stdout or r.stderr)[-200:]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root"); ap.add_argument("--pack")
    ap.add_argument("--list", action="store_true"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--name", help="本次组合的自定义名字(只对 --root/--pack 单组有效)")
    a = ap.parse_args()
    roots = sorted(p.stem for p in ROOTS.glob("*.json")); packs = sorted(p.stem for p in PACKS.glob("*.json"))
    if a.list:
        comps = sorted(p.name for p in OUT.glob("*+*")) if OUT.exists() else []
        print(json.dumps({"roots": roots, "packs": packs, "compositions": comps,
                          "possible": len(roots) * len(packs)}, ensure_ascii=False, indent=1))
        return 0
    pairs = [(r, p) for r in roots for p in packs] if a.all else [(a.root, a.pack)]
    if a.name and a.root and a.pack:
        NAME_OVERRIDE[f"{a.root}+{a.pack}"] = a.name
    if not a.all and not (a.root and a.pack):
        ap.error("--root 与 --pack 都要给;或 --all;或 --list")
    results, bad = [], False
    for r, p in pairs:
        out = compose(r, p)
        g = gate(out)
        ok = all(x["exit"] == 0 for x in g)
        bad = bad or not ok
        results.append({"composition": f"{r}+{p}", "params": str(out.relative_to(HERE.parent)),
                        "gates": "pass" if ok else [x for x in g if x["exit"]]})
    print(json.dumps({"status": "ok" if not bad else "some-failed", "results": results}, ensure_ascii=False, indent=1))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
