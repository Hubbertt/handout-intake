#!/usr/bin/env python3
"""新增样式:新增一个根,或新增一个包。两者独立,新增后任意组合。三者都可自定义命名。

**使用方定的模型**(2026-08-15):
  样式整套选。1 个全局默认模板 + 1 个局部修正 = 一组样式模板。
  全局默认和局部修正是独立的,可以任意组合。做好框架就可以了。
  全局默认、局部修正、以及组合之后的,都要能够自定义命名。

三层三处真源:
  roots/<rootId>.json    全局默认根。新增根 = 从既有根派生一份、改值。
  packs/<packId>.json    局部偏离包。新增包 = 从既有包派生一份、改值。
  base/                  底座(版面/页面/流程/渲染场景),随 skill 走,不在这里新增。

命名:每一层都有 id(机器标识,目录/文件名,不改)与 name(人读的名字,随时可改)。
  根   roots/<id>.json  的 name
  包   packs/<id>.json  的 name
  组合 compositions/<root>+<pack>/template.json 的 name(默认「包名 × 根名」,compose.py --name 可自定义)
改名只改 name 不改 id——id 被绑定与清单引用,改了全断。

新增只碰自己那一层——新增根不动任何包,新增包不动任何根。
组合是投影,由 compose.py 从根×包合成,改完根或包重合成即可。

为什么从既有的派生而不是从空白造:
  根必须完整——会影响渲染的属性一项都不能缺,缺一项就是留一个由目标机器决定的值。
  从空白造根一定漏;从完整的根改,只会改错不会漏。包同理。

新增后自动:① 合成它参与的全部组合并过门(门不过就撤回,不留半成品)
          ② 在本机真渲那些组合的预览——**每次改样式都渲染一次**
          ③ 清单更新

用法:
  new_template.py root --from docDefaults1 --id docDefaults3 --name "C校基线"
  new_template.py pack --from chengzi-summer-v1 --id chengzi-summer-green --name "绿色标题变体"
  new_template.py rename root|pack|composition <id> --name "新名字"
  new_template.py rerender [--root X] [--pack Y]      改完根/包后:重合成受影响的组合并重渲
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOTS, PACKS, COMPS = HERE / "roots", HERE / "packs", HERE / "compositions"


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)


def dump(o, p: Path):
    p.write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compose_and_render(root: str | None, pack: str | None) -> int:
    roots = [root] if root else sorted(p.stem for p in ROOTS.glob("*.json"))
    packs = [pack] if pack else sorted(p.stem for p in PACKS.glob("*.json"))
    failed = []
    for r in roots:
        for k in packs:
            res = subprocess.run([sys.executable, str(HERE / "compose.py"), "--root", r, "--pack", k],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                failed.append({"composition": f"{r}+{k}", "tail": (res.stdout or res.stderr)[-300:]})
    if failed:
        print(json.dumps({"status": "refused", "why": "有组合没过门,不渲染——渲一个过不了门的模板,预览会骗人。",
                          "failed": failed}, ensure_ascii=False, indent=1))
        return 1
    rc = 0
    for r in roots:
        for k in packs:
            rc |= subprocess.run([sys.executable, str(HERE / "render_catalog.py"), "--template", f"{r}+{k}", "--force"]).returncode
    return rc


def new_root(src: str, new_id: str, name: str) -> int:
    s, d = ROOTS / f"{src}.json", ROOTS / f"{new_id}.json"
    if not s.exists():
        print(json.dumps({"status": "refused", "why": f"来源根不存在:{src}"}, ensure_ascii=False)); return 1
    if d.exists():
        print(json.dumps({"status": "refused", "why": f"根已存在:{new_id}。不覆盖。"}, ensure_ascii=False)); return 1
    root = load(s)
    root["rootId"] = new_id; root.move_to_end("rootId", last=False)
    root["name"] = name or new_id; root.move_to_end("name", last=False); root.move_to_end("rootId", last=False)
    n = "".join(ch for ch in new_id if ch.isdigit()) or "x"
    root["version"] = f"{n}-inert"
    root["what"] = f"由 {src} 派生的全局默认根。派生时逐值相同(-inert),之后每改一项都是单独一次可测的改动。"
    root["derivedFrom"] = {"root": src, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    dump(root, d)
    if compose_and_render(new_id, None):
        d.unlink(missing_ok=True)
        print(json.dumps({"status": "refused", "why": "新根参与的组合没过门,已撤回该根。"}, ensure_ascii=False)); return 1
    print(json.dumps({"status": "ok", "root": new_id, "name": root["name"], "derivedFrom": src,
                      "next": f"编辑 roots/{new_id}.json 后跑 new_template.py rerender --root {new_id}"}, ensure_ascii=False, indent=1))
    return 0


def new_pack(src: str, new_id: str, name: str) -> int:
    s, d = PACKS / f"{src}.json", PACKS / f"{new_id}.json"
    if not s.exists():
        print(json.dumps({"status": "refused", "why": f"来源包不存在:{src}"}, ensure_ascii=False)); return 1
    if d.exists():
        print(json.dumps({"status": "refused", "why": f"包已存在:{new_id}。不覆盖。"}, ensure_ascii=False)); return 1
    pack = load(s)
    pack["packId"] = new_id; pack["name"] = name or new_id
    pack.move_to_end("name", last=False); pack.move_to_end("packId", last=False)
    rel = pack.setdefault("release", OrderedDict())
    rel["version"] = f"{rel.get('version', '1')}-{new_id}"
    rel["derivedFrom"] = {"pack": src, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    dump(pack, d)
    if compose_and_render(None, new_id):
        d.unlink(missing_ok=True)
        print(json.dumps({"status": "refused", "why": "新包参与的组合没过门,已撤回该包。"}, ensure_ascii=False)); return 1
    print(json.dumps({"status": "ok", "pack": new_id, "name": pack["name"], "derivedFrom": src,
                      "next": f"编辑 packs/{new_id}.json 后跑 new_template.py rerender --pack {new_id}"}, ensure_ascii=False, indent=1))
    return 0


def rename(kind: str, ident: str, name: str) -> int:
    """只改 name 不改 id。组合的名字写在它的 template.json;根/包写在各自文件。"""
    if kind == "root":
        p = ROOTS / f"{ident}.json"
    elif kind == "pack":
        p = PACKS / f"{ident}.json"
    else:
        p = COMPS / ident / "template.json"
    if not p.exists():
        print(json.dumps({"status": "refused", "why": f"{kind} 不存在:{ident}"}, ensure_ascii=False)); return 1
    d = load(p); old = d.get("name"); d["name"] = name
    if kind in ("root", "pack"):
        d.move_to_end("name", last=False); d.move_to_end("rootId" if kind == "root" else "packId", last=False)
    else:
        d["nameCustom"] = True   # 自定义过的组合名,重合成时保留,不被默认名覆盖
    dump(d, p) if kind != "composition" else p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # 根/包改名会影响组合的默认名;重合成一次让清单跟上(不重渲,参数没变)。
    if kind in ("root", "pack"):
        subprocess.run([sys.executable, str(HERE / "compose.py"), "--all"], capture_output=True, text=True)
    subprocess.run([sys.executable, str(HERE / "render_catalog.py")], capture_output=True, text=True)
    print(json.dumps({"status": "ok", "kind": kind, "id": ident, "name": {"was": old, "now": name}}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("root"); a.add_argument("--from", dest="src", required=True); a.add_argument("--id", required=True); a.add_argument("--name", default="")
    b = sub.add_parser("pack"); b.add_argument("--from", dest="src", required=True); b.add_argument("--id", required=True); b.add_argument("--name", default="")
    c = sub.add_parser("rerender"); c.add_argument("--root"); c.add_argument("--pack")
    r = sub.add_parser("rename"); r.add_argument("kind", choices=["root", "pack", "composition"]); r.add_argument("id"); r.add_argument("--name", required=True)
    args = ap.parse_args()
    if args.cmd == "root": return new_root(args.src, args.id, args.name)
    if args.cmd == "pack": return new_pack(args.src, args.id, args.name)
    if args.cmd == "rename": return rename(args.kind, args.id, args.name)
    return compose_and_render(args.root, args.pack)


if __name__ == "__main__":
    sys.exit(main())
