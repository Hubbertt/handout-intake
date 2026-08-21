#!/usr/bin/env python3
"""GATE_SOURCE_BATCH_CONSUMED:一批源里的每个文件都必须有去向。

**为什么这道门必须在批这一层,而不是册这一层。**

2026-08-20 实测:2026-08-19 那批交付了 27 个文件,其中 5 份单元自测**从头到尾
没有进过流水线**。而当时:

  · 讲义册的链跑到 34/36 步,
  · 覆盖门报 4556/4556 全绿,
  · 拆分门报 20/20 讲一一配对。

一道也没响。原因不是门写错了,是门问错了问题——它们问的都是
「**进了流水线的东西**有没有全部落地」,而不是「**该进流水线的东西**有没有全部进来」。
册级的门永远看不见这个洞:讲义册自己期望 20 讲、拆出 20 讲,它是对的;
错的是「这批还有 5 份文件,没有任何一册要它们」。

分母只能来自批清单。册不知道自己不知道什么。

判据(每个文件三选一,缺一即 fail):
  consumed      被某册的 bindings 直接绑定(按 sha256 匹配,不按文件名——
                册的 inputs/ 是拷贝,改名不改内容)
  containedIn   内容已含于另一个**被消费的**文件;须在批清单里显式指名,
                并附证据(如「合并本 143,940 字 = 20 份单讲之和 + 318 字目录」)
  excluded      本批不做,须写明理由

「显式声明」是这道门的全部重量。不声明就是 fail——**沉默不算交代**。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bound_files(volumes_root: Path) -> dict[str, list[str]]:
    """扫所有册的 bindings,返回 sha256 -> [册id·哪个绑定]。

    只认 source 与 source.annotated:那是「这一册读了哪些源」的唯一入口。
    产物与工作副本不算——工作副本是派生物,拿它交差等于自证。
    """
    found: dict[str, list[str]] = {}
    pattern = str(volumes_root / "*" / ".handout-intake" / "volumes" / "*" / "bindings.json")
    for binding_path in sorted(glob.glob(pattern)):
        binding = json.loads(Path(binding_path).read_text(encoding="utf-8"))
        volume = binding.get("volume") or Path(binding_path).parent.name
        # parents: [0]=<册id> [1]=volumes [2]=.handout-intake [3]=工作区
        workspace = Path(binding_path).parents[3]
        for key in ("source", "source.annotated"):
            spec = (binding.get("paths") or {}).get(key)
            if not spec:
                continue
            spec = spec if os.path.isabs(spec) else str(workspace / spec)
            for one in sorted(glob.glob(spec)):
                if not os.path.isfile(one):
                    continue
                found.setdefault(sha256(Path(one)), []).append(f"{volume}·{key}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path, help="批清单 manifest")
    parser.add_argument("--volumes", required=True, type=Path, help="册的根目录")
    parser.add_argument("--report", type=Path, help="门报告写到哪")
    args = parser.parse_args()

    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    files = batch.get("files") or []
    if not files:
        print("批清单里没有 files。拒绝在空清单上判——空分母判什么都是绿的。",
              file=sys.stderr)
        return 1

    bound = bound_files(args.volumes)
    by_hash = {f["sha256"]: f for f in files}

    rows = []
    orphans = []
    for entry in files:
        digest = entry["sha256"]
        disposition = entry.get("disposition") or {}
        kind = disposition.get("kind")
        row = {"path": entry["path"], "sha256": digest[:12], "declared": kind}
        if digest in bound:
            row["verdict"] = "consumed"
            row["by"] = bound[digest]
            if kind not in (None, "consumed"):
                row["verdict"] = "declared-mismatch"
                row["why"] = f"清单声明 {kind},实测被 {bound[digest]} 直接消费"
                orphans.append(row)
        elif kind == "containedIn":
            holder = disposition.get("containedIn")
            held = by_hash.get(holder) or next(
                (f for f in files if f["path"] == holder), None)
            if held is None:
                row["verdict"] = "bad-reference"
                row["why"] = f"containedIn 指向 {holder!r},批清单里没有这个文件"
                orphans.append(row)
            elif held["sha256"] not in bound:
                row["verdict"] = "holder-not-consumed"
                row["why"] = (f"内容据称含于 {held['path']},但那个文件自己也没被任何册消费——"
                              "指向一个同样没进来的文件,等于没有交代")
                orphans.append(row)
            elif not disposition.get("evidence"):
                row["verdict"] = "no-evidence"
                row["why"] = "containedIn 没有附证据。包含关系是断言,不是事实,除非量过"
                orphans.append(row)
            else:
                row["verdict"] = "containedIn"
                row["holder"] = held["path"]
        elif kind == "excluded":
            if not disposition.get("why"):
                row["verdict"] = "no-reason"
                row["why"] = "excluded 没写理由"
                orphans.append(row)
            else:
                row["verdict"] = "excluded"
                row["reason"] = disposition["why"]
        else:
            row["verdict"] = "ORPHAN"
            row["why"] = ("没有任何册消费它,批清单里也没有 disposition。"
                          "**这就是 5 份单元自测当初消失的形状**:没人要它,也没人说不要它。")
            orphans.append(row)
        rows.append(row)

    status = "pass" if not orphans else "fail"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1

    report = {
        "schemaVersion": "chengziclass.gate-source-batch-consumed.v1",
        "gate": "GATE_SOURCE_BATCH_CONSUMED",
        "rule": ("批清单里的每个文件必须三选一:被某册直接消费 / 内容含于另一个被消费的文件"
                 "(须指名并附证据) / 显式排除(须写理由)。沉默不算交代。"),
        "batch": str(args.batch),
        "volumesRoot": str(args.volumes),
        "status": status,
        "totals": {"files": len(files), **counts},
        "orphans": orphans,
        "files": rows,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    print(f"GATE_SOURCE_BATCH_CONSUMED: {status}  ({len(files)} 个文件)")
    for verdict, count in sorted(counts.items()):
        print(f"   {verdict:20} {count}")
    if orphans:
        print("\n没有交代的文件:")
        for row in orphans:
            print(f"   [{row['verdict']}] {row['path']}")
            print(f"        {row.get('why', '')}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
