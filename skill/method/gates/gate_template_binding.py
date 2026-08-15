#!/usr/bin/env python3
"""GATE_TEMPLATE_BINDING:版本号必须绑定「根 + 偏离包」这个组合。

**使用方定的数据模型**(2026-08-15):
  每个参数模板拥有一条覆盖全局的默认样式,和任意数量的局部偏离包。
  一个全局样式 + 一个局部偏离包 = 唯一的、任意设备都一样的参数模板。

**现状不满足它。** 参数表里有两个互不相干的版本号:
  docDefaults1.version        = "1-inert"   根
  styleRegistryRelease.version = "1"        偏离包
两者不绑定彼此,也都不绑内容。而 formal-entry 钉的是整份文件的 sha256——
它钉的是文件不是组合:文件里任何无关部分一动它就变(今天已因无关改动变了三次),
而根或偏离包被人改了值、版本号照旧不动,它同样发现不了。
**一个既会误报又会漏报的钉子,不构成绑定。**

本门给出 templateId = 根@版本+包@版本,并各自绑各自内容的 sha256。
换根或换包 → templateId 变;改内容不改版本号 → 哈希对不上,门报出。

计算与校验是同一段代码(--write 写入,默认校验)。
两处各写一张同样的映射,是登记册 P8 反复抓到的形状。

用法:
  gate_template_binding.py --params <参数表> [--write]
退出码 0=绑定成立 1=对不上或未声明
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def canon(node) -> str:
    """确定性序列化。跨机器要同哈希,就不能依赖插入顺序。"""
    return json.dumps(node, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha(node) -> str:
    return hashlib.sha256(canon(node).encode("utf-8")).hexdigest()


def compute(params: dict) -> dict:
    root = params.get("docDefaults1") or {}
    registry = params.get("wordStyleRegistry") or {}
    release = params.get("styleRegistryRelease") or {}
    # 偏离包 = 样式声明本身。注册表里的标准/口径/度量等描述性键不入包:
    # 它们改了不改变任何一个样式的取值,算进去会让包哈希因无关编辑而变,
    # 重蹈「钉文件不钉组合」的覆辙。
    pack = {"paragraphStyles": registry.get("paragraphStyles") or {},
            "characterStyles": registry.get("characterStyles") or {}}
    root_ver = str(root.get("version") or "?")
    pack_ver = str(release.get("version") or "?")
    root_sha, pack_sha = sha(root), sha(pack)
    return {
        "schemaVersion": "chengziclass.parameter-template-binding.v1",
        "model": "一个全局默认样式 + 一个局部偏离包 = 唯一的、任意设备都一样的参数模板。"
                 "使用方 2026-08-15 定。",
        "templateId": f"docDefaults1@{root_ver}+styleRegistry@{pack_ver}",
        "root": {"key": "docDefaults1", "version": root_ver, "sha256": root_sha},
        "pack": {"key": "wordStyleRegistry.{paragraphStyles,characterStyles}",
                 "version": pack_ver, "sha256": pack_sha,
                 "styles": len(pack["paragraphStyles"]) + len(pack["characterStyles"])},
        "combinedSha256": hashlib.sha256(
            f"{root_sha}:{pack_sha}".encode("utf-8")).hexdigest(),
        "why": "换根或换包 → templateId 变;改内容不改版本号 → 哈希对不上。"
               "整份文件的 sha256 两件事都做不到:无关编辑会让它变(误报),"
               "而版本号不动的内容改动它同样发现不了(漏报)。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--write", action="store_true", help="写入/刷新绑定声明")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    text = args.params.read_text(encoding="utf-8")
    params = json.loads(text)
    fresh = compute(params)
    declared = params.get("parameterTemplate")

    if args.write:
        params["parameterTemplate"] = fresh
        args.params.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps({"wrote": fresh["templateId"],
                          "combinedSha256": fresh["combinedSha256"]},
                         ensure_ascii=False, indent=1))
        return 0

    findings = []
    if not declared:
        findings.append({"kind": "not-declared",
                         "why": "参数表没有 parameterTemplate 绑定声明。"
                                "没有声明就没有绑定——本门不代为生成,"
                                "先跑 --write 是一次有意的动作,不该由校验顺手做掉。"})
    else:
        for field in ("templateId", "combinedSha256"):
            if declared.get(field) != fresh[field]:
                findings.append({"kind": "mismatch", "field": field,
                                 "declared": declared.get(field),
                                 "actual": fresh[field]})
        for part in ("root", "pack"):
            d, f = declared.get(part) or {}, fresh[part]
            if d.get("sha256") != f["sha256"]:
                findings.append({"kind": "content-changed", "part": part,
                                 "declaredVersion": d.get("version"),
                                 "actualVersion": f["version"],
                                 "why": f"{part} 的内容与声明的 sha256 对不上。"
                                        "若这次改动是有意的,应升版本号后重跑 --write;"
                                        "版本号不动而内容变,正是本门要抓的事。"})

    report = {"gate": "GATE_TEMPLATE_BINDING", "params": str(args.params),
              "declared": declared, "actual": fresh, "findings": findings,
              "status": "pass" if not findings else "fail"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({"templateId": fresh["templateId"],
                      "findings": findings, "status": report["status"]},
                     ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
