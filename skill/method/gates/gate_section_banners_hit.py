#!/usr/bin/env python3
"""GATE_SECTION_BANNERS_HIT:模板表声明的每张栏目横幅,必须在本册源里真的命中。

为什么要有这条门:
  sectionBanners 的键是**图片内容哈希**——某一次导出的事实,不是教材的事实。
  同一套教材重新导出一次,图被重新压缩,哈希就全变。

  2026-08-20 实测:v1 声明的 698c6dd8dc10(旧母本「过关检测」,引用 19 次)
  在新版学生本与教师版里**各 0 处**。后果不是报错,是**四个栏目一个都认不出来**,
  全册 796 个原子的 section 全为 None,而链一路跑到 s4c2 才停、停的还是别的原因。
  ★依赖内容哈希的判据,失效方式是静默的。静默失效必须由门来吵。

判据(两条,都要过):
  ① 每张声明的横幅,引用数 > 0
  ② 每张的引用数与讲数同量级(0.5×讲数 ≤ 引用数 ≤ 2×讲数)
     —— 栏目头按定义每讲一次。掉到半数以下说明认错了图;
        高出一倍说明这张图另有他用,不该当栏目头。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


def reference_counts(docx: Path) -> dict[str, int]:
    """每个媒体部件的内容哈希 → 它在正文里被引用几次。"""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(docx) as package:
        rels = package.read("word/_rels/document.xml.rels").decode("utf-8")
        document = package.read("word/document.xml").decode("utf-8")
        for match in re.finditer(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels):
            rid, target = match.group(1), match.group(2)
            if "media/" not in target:
                continue
            try:
                payload = package.read("word/" + target.lstrip("/"))
            except KeyError:
                continue
            if not payload:
                continue
            digest = hashlib.sha256(payload).hexdigest()[:12]
            used = document.count(f'r:embed="{rid}"') + document.count(f'r:id="{rid}"')
            counts[digest] = counts.get(digest, 0) + used
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path, action="append",
                    help="要检查的源 docx;可给多次(原卷与解析版各一次)")
    ap.add_argument("--lessons", required=True, type=int, help="本册讲数")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    banners = schema.get("sectionBanners") or {}
    if not banners:
        print("模板表没有声明 sectionBanners。本门不适用——但请确认这是真的:"
              "栏目头若是图片而没被声明,全册栏目会静默消失。", file=sys.stderr)
        return 0

    low, high = max(1, args.lessons // 2), args.lessons * 2
    rows, failures = [], []
    for source in args.source:
        counts = reference_counts(source)
        for digest, spec in banners.items():
            used = counts.get(digest, 0)
            verdict = "pass"
            if used == 0:
                verdict = "fail:0 命中——该哈希在本源里不存在,栏目会静默消失"
            elif not (low <= used <= high):
                verdict = f"fail:命中 {used} 不在 [{low},{high}] 区间,与讲数 {args.lessons} 不同量级"
            row = {"source": source.name, "sha256_12": digest,
                   "label": spec.get("label"), "role": spec.get("role"),
                   "references": used, "verdict": verdict}
            rows.append(row)
            if verdict != "pass":
                failures.append(row)

    report = {
        "schemaVersion": "chengziclass.gate-section-banners-hit.v1",
        "gate": "GATE_SECTION_BANNERS_HIT",
        "rule": "每张声明的栏目横幅在每份源里引用数 > 0,且与讲数同量级",
        "lessons": args.lessons,
        "acceptedRange": [low, high],
        "status": "pass" if not failures else "fail",
        "rows": rows,
        "failures": failures,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    for row in rows:
        print(f'  {row["source"][:28]:<28} {row["sha256_12"]}  {str(row["label"]):<8}'
              f' 引用 {row["references"]:>3}  {row["verdict"]}')
    print(f'门 {report["status"]}')
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
