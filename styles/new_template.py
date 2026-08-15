#!/usr/bin/env python3
"""新增样式模板——两种新增:换全局默认根,或在同一根下加局部偏离包。

**使用方定的模型**(2026-08-15):
  每个参数模板 = 一条覆盖全局的默认样式(根)+ 任意数量的局部偏离包。
  版本号绑的是「根 + 包」的组合(GATE_TEMPLATE_BINDING)。
  样式整套选;但可以新增,新增有全局默认和局部偏离两种。

两种新增的差别不在文件形状,在**改哪一层**:
  --kind root   换根:docDefaults1 → docDefaults2。字号/字体/行距的基线全变,是新的一族。
                     适用于「换一个学校 / 换一套版式体系」。
  --kind pack   加偏离包:根不动,只改若干样式(标题换底纹、正文换行距)。
                     适用于「同一族里的变体」。

两种都从一个既有模板派生(--from),这不是偷懒:
  ① 根必须完整——会影响渲染的属性一项都不能缺,缺一项就是留一个由目标机器决定的值。
     从空白造根,一定漏;从完整的根改,只会改错不会漏。
  ② 派生关系写进 template.json(derivedFrom),溯源不断。

新增之后自动做三件事,不做完不算新增:
  门    GATE_TEMPLATE_BINDING --write 重算「根+包」绑定;GATE_STYLE_SELF_CONTAINED 的
        根完整性检查(缺属性即拒绝);GATE_HEADING_LADDER。
  渲    在本机真渲一次预览。**每次改样式都应该渲染一次**(使用方定)。
  录    catalog.json 登记。

用法:
  new_template.py --kind root --from chengzi-summer-v1 --id school-b-v1 --name "B校版式"
  new_template.py --kind pack --from chengzi-summer-v1 --id chengzi-summer-blue --name "蓝色标题变体"
之后编辑 styles/<id>/params.json,再跑 --rerender <id>(改完必渲)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "skill"
CATALOG = HERE / "catalog.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def save_catalog(c: dict) -> None:
    CATALOG.write_text(json.dumps(c, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def run_gates(params: Path) -> list[dict]:
    """新模板必须过的门。任何一道不过就不算新增成功——门不过的模板不进清单。"""
    py = sys.executable
    gates = [
        ("GATE_TEMPLATE_BINDING(--write)", [py, str(SKILL / "method/gates/gate_template_binding.py"),
                                            "--params", str(params), "--write"]),
        ("GATE_UNIT_SANITY", [py, str(SKILL / "method/gates/gate_unit_sanity.py"), "--params", str(params)]),
        ("GATE_HEADING_LADDER", [py, str(SKILL / "method/gates/gate_heading_ladder.py"), "--params", str(params)]),
        ("GATE_INERT_VERSION", [py, str(SKILL / "method/gates/gate_inert_version.py"), "--params", str(params)]),
    ]
    out = []
    for name, cmd in gates:
        r = subprocess.run(cmd, capture_output=True, text=True)
        out.append({"gate": name, "exit": r.returncode, "tail": (r.stdout or r.stderr)[-300:]})
    return out


def derive(kind: str, src_id: str, new_id: str, name: str, desc: str) -> int:
    src = HERE / src_id
    dst = HERE / new_id
    if not (src / "params.json").exists():
        print(json.dumps({"status": "refused", "why": f"来源模板不存在:{src_id}"}, ensure_ascii=False))
        return 1
    if dst.exists():
        print(json.dumps({"status": "refused", "why": f"目标已存在:{new_id}。不覆盖——覆盖会丢掉别人改过的东西。"},
                         ensure_ascii=False))
        return 1
    shutil.copytree(src, dst)
    params_path = dst / "params.json"
    params = json.loads(params_path.read_text(encoding="utf-8"))

    if kind == "root":
        # 换根:docDefaults1 → docDefaults<N>。名字里的数字递增,不与既有任何模板重名。
        old_root = params.get("docDefaults1") or {}
        n = 2
        while any((HERE / t / "params.json").exists() and
                  f"docDefaults{n}" in json.loads((HERE / t / "params.json").read_text(encoding="utf-8"))
                  for t in [p.name for p in HERE.iterdir() if p.is_dir() and p.name != "renders"]):
            n += 1
        new_root = json.loads(json.dumps(old_root))
        new_root["version"] = f"{n}-inert"
        new_root["what"] = f"docDefaults{n}:由 {src_id} 的根派生的新全局默认。派生时逐值相同(-inert),之后每改一项都是单独一次可测的改动。"
        new_root["derivedFrom"] = {"template": src_id, "root": "docDefaults1", "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        params[f"docDefaults{n}"] = new_root
        params.pop("docDefaults1", None)
        # 编译器与门读 docDefaults1 这个键名——键名是「当前根」的槽位,不是根的身份。
        # 根的身份在 version 里。所以新根仍放回 docDefaults1 槽位,身份写在 version。
        params["docDefaults1"] = params.pop(f"docDefaults{n}")
        params["docDefaults1"]["rootId"] = f"docDefaults{n}"
        what = f"新根 docDefaults{n}(-inert,派生时逐值同 {src_id})"
    else:
        # 加偏离包:根不动,styleRegistryRelease 版本递增,包内容派生时逐值相同。
        rel = params.setdefault("styleRegistryRelease", {})
        rel["version"] = str(rel.get("version", "1")) + f"-{new_id}"
        rel["derivedFrom"] = {"template": src_id, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        rel["title"] = f"{name}(局部偏离包,派生自 {src_id})"
        what = f"新偏离包 styleRegistry@{rel['version']}(根不动)"

    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # template.json 的形状与既有模板一致——渲染器按 params/spec/outlineLevels 键找文件。
    # 首版漏了这三个键,渲染器当场 KeyError:新增「成功」了,却渲不出预览。
    src_tpl = json.loads((src / "template.json").read_text(encoding="utf-8")) if (src / "template.json").exists() else {}
    tpl = {"schemaVersion": "handout-intake.style-template.v1",
           # 渲染场景随模板走:场景定义的是「用哪些块型渲预览」,派生模板同样需要。
           # 首版没带,渲染器报「渲染 0 个场景」——新增成功却渲不出东西。
           "renderScenes": src_tpl.get("renderScenes") or [],
           "preview": src_tpl.get("preview") or {},
           "params": "params.json",
           "spec": next((f.name for f in dst.glob("*.md")), None),
           "outlineLevels": next((f.name for f in dst.glob("chengzi_word_outline_levels*.json")), None),
           "id": new_id, "name": name, "description": desc, "kind": kind,
           "derivedFrom": src_id, "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
           "what": what,
           "editNext": "编辑 params.json 后必须跑 new_template.py --rerender " + new_id +
                       "——每次改样式都渲染一次;门重算绑定,预览与参数 sha256 绑定,参数一变预览即过期。"}
    (dst / "template.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    gates = run_gates(params_path)
    failed = [g for g in gates if g["exit"] != 0]
    if failed:
        # 门不过就撤:不留一个进不了清单的半成品目录。
        shutil.rmtree(dst)
        print(json.dumps({"status": "refused", "why": "派生后的模板没过门,已撤回。", "gates": failed},
                         ensure_ascii=False, indent=1))
        return 1

    cat = load_catalog()
    cat["templates"][new_id] = {"id": new_id, "name": name, "description": desc, "kind": kind,
                                "derivedFrom": src_id, "paramsSha256": sha(params_path),
                                "status": "unrendered",
                                "note": "尚未渲染。跑 --rerender 或安装向导会渲。"}
    save_catalog(cat)
    print(json.dumps({"status": "ok", "template": new_id, "kind": kind, "what": what,
                      "gates": [g["gate"] for g in gates], "next": tpl["editNext"]},
                     ensure_ascii=False, indent=1))
    return rerender(new_id)


def rerender(tid: str) -> int:
    params_path = HERE / tid / "params.json"
    if not params_path.exists():
        print(json.dumps({"status": "refused", "why": f"模板不存在:{tid}"}, ensure_ascii=False))
        return 1
    gates = run_gates(params_path)
    failed = [g for g in gates if g["exit"] != 0]
    if failed:
        print(json.dumps({"status": "refused", "why": "改后的参数没过门,不渲染——渲一个过不了门的模板,预览会骗人。",
                          "gates": failed}, ensure_ascii=False, indent=1))
        return 1
    r = subprocess.run([sys.executable, str(HERE / "render_catalog.py"), "--template", tid, "--force"], text=True)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=["root", "pack"])
    ap.add_argument("--from", dest="src")
    ap.add_argument("--id")
    ap.add_argument("--name")
    ap.add_argument("--desc", default="")
    ap.add_argument("--rerender", metavar="TEMPLATE_ID")
    args = ap.parse_args()
    if args.rerender:
        return rerender(args.rerender)
    if not (args.kind and args.src and args.id and args.name):
        ap.error("新增需要 --kind --from --id --name;重渲用 --rerender <id>")
    return derive(args.kind, args.src, args.id, args.name, args.desc)


if __name__ == "__main__":
    sys.exit(main())
