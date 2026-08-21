#!/usr/bin/env python3
"""GATE_VENDORED_UPSTREAM_MOVED:被拷走的文件,上游改了要有人知道。

**为什么。** 生产环境读不到本包所在的外置卷,所以题库后端把引擎与种子拷了一份进内置盘,
并在 `PROVENANCE.md` 里逐文件记下了「上游是谁、上游当时的 sha256」。记得非常清楚——
**而没有任何东西去核它**。2026-08-20 实测两处漂移:

  · `carve_engine.py`:声明上游 `c8f540ef…`,当前上游 `ceac1005…`
  · `seeds/physics-g08-summer.v1.json`:声明「identical / 27b50616…」,当前上游 `a0a43069…`
    (这一处在 2026-08-20 之前就漂了)

两处都不会有任何提示。拷贝这件事本身没错——是边界所迫;错在**拷完就没人再看一眼**。

判据:对每个消费方 `PROVENANCE.md` 里声明的每一行,把「上游路径 + 上游 sha256」
拿到当前包里重算。相等 = in-sync;不等 = UPSTREAM_MOVED;上游文件没了 = UPSTREAM_GONE。

★核的是**上游有没有动**,不是消费方是否逐字相同。消费方允许有本地增量
(题库那份带 `[quiz-omml]` 标记,PROVENANCE 已声明为 patched)——那是它的裁决,
本门不干涉;本门只回答「你当初拷的那个版本,还是现在这个版本吗」。

★本门可能长期为红,而且**不该由本包来消红**:重新 vendor 是消费方的事
(题库那边由另一条线持租约)。红是交接信号,不是失败——报告里分开写。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
ROW = re.compile(r"^\|\s*`?([^|`]+)`?\s*\|\s*(.+?)\s*\|\s*`?([0-9a-f]{64})`?\s*\|\s*(.*?)\s*\|\s*$")
UPSTREAM_PATH = re.compile(r"`([^`]+\.(?:py|json))`")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


ADJUDICATION_HEADING = re.compile(r"^##\s*Upstream drift adjudications")
"""裁决表的分界。表内每一行 = 「这个上游版本我判过了,对本处无影响」。

**为什么门要认裁决。** 一道永远红的门,红久了就没人看——它与坏掉没有区别。
但「上游动了」本身也不该被抹掉:抹掉之后,下一次上游做了**有影响**的改动同样不会有人知道。
所以裁决绑定在**具体的上游 sha256** 上:判过的那一版报 adjudicated,上游再动一次就重新报红。
裁决写在消费方自己的 PROVENANCE.md 里,不在本包——判的人是消费方,真源就该在它那儿。
"""

PORTED_HEADING = re.compile(r"^Ported as modules")
"""★必须锚定行首。首版写 re.compile(r"Ported as modules", re.I),而正文第 4 行有一句
"the engine and its seed schemas are copied here and **imported as modules**" ——
"imported as modules" 里就含 "ported as modules",于是分界线提前到了第一张表之前,
逐字拷贝的引擎与种子全被判成移植件,处置建议整个反了。
判据越松,越容易在别处命中——今天在这个包里已经是第三次了(选项行吃小问、
小数当题号、这一处)。"""


def rows_of(provenance: Path):
    """PROVENANCE.md 的两张表都是 | vendored | upstream | upstream sha | … | 形状。

    上游那一格可能带说明文字,所以路径按反引号里的 .py/.json 取第一个。

    ★两张表**不是一回事**,处置也不同:
      copied  逐字拷贝(引擎与种子)——上游动了可以机械重拷,差异是可计算的。
      ported  重写移植(prepare_input→preprocess 等)——上游动了**不能机械同步**,
              得有人读懂改了什么再决定要不要跟。
    首版把两者混作一类报,等于告诉人「重拷一下就行」,而那对移植件是错的建议。
    分界就是文中「Ported as modules」这一行。
    """
    kind = "copied"
    for line in provenance.read_text(encoding="utf-8").splitlines():
        if ADJUDICATION_HEADING.search(line):
            break          # 裁决表另行解析,不当成 vendoring 声明
        if PORTED_HEADING.search(line):
            kind = "ported"
        m = ROW.match(line.strip())
        if not m:
            continue
        vendored, upstream_cell, claimed, note = m.groups()
        found = UPSTREAM_PATH.search(upstream_cell)
        if not found:
            continue
        yield vendored.strip(), found.group(1), claimed, note, kind


ADJ_ROW = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|\s*([a-z-]+)\s*\|\s*(.+?)\s*\|\s*$")


def adjudications(provenance: Path) -> dict[tuple[str, str], dict]:
    """(上游路径, 上游 sha256) -> 裁决。只认裁决表那一节里的行。"""
    out: dict[tuple[str, str], dict] = {}
    inside = False
    for line in provenance.read_text(encoding="utf-8").splitlines():
        if ADJUDICATION_HEADING.search(line):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if not inside:
            continue
        m = ADJ_ROW.match(line.strip())
        if m:
            upstream, digest, verdict, why = m.groups()
            out[(upstream, digest)] = {"verdict": verdict, "why": why}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path,
                    default=PACKAGE_ROOT / "runtime/vendor-consumers.json")
    ap.add_argument("--package", type=Path, default=PACKAGE_ROOT,
                    help="上游包根(默认本包)")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    if not args.registry.exists():
        print(f"登记表不存在:{args.registry}\n"
              "它是**本机事实**(消费方装在哪),按 .gitignore 不随包分发。\n"
              "拷 runtime/vendor-consumers.example.json 去掉 .example,填上本机路径。\n"
              "不退回空表——空分母判什么都是绿的。", file=sys.stderr)
        return 1
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    consumers = registry.get("consumers") or []
    if not consumers:
        print("登记表里一个消费方都没有。拒绝在空清单上判——空分母判什么都是绿的。",
              file=sys.stderr)
        return 1

    results, moved = [], []
    for consumer in consumers:
        provenance = Path(consumer["provenance"])
        entry = {"consumer": consumer["id"], "provenance": str(provenance), "files": []}
        if not provenance.exists():
            entry["status"] = "PROVENANCE_MISSING"
            entry["why"] = ("消费方没有 PROVENANCE.md。拷了什么、拷自哪个版本,"
                            "无从核对——这比漂移更糟。")
            moved.append(entry)
            results.append(entry)
            continue
        rows = list(rows_of(provenance))
        judged = adjudications(provenance)
        if not rows:
            entry["status"] = "NO_ROWS"
            entry["why"] = "PROVENANCE.md 里没解析到任何带上游 sha256 的行,判据恒假。"
            moved.append(entry)
            results.append(entry)
            continue
        for vendored, upstream_rel, claimed, note, kind in rows:
            upstream = args.package / upstream_rel
            row = {"vendored": vendored, "upstream": upstream_rel, "kind": kind,
                   "claimedUpstreamSha256": claimed, "note": note}
            if not upstream.exists():
                row["status"] = "UPSTREAM_GONE"
                row["why"] = "上游文件已不在此路径。拷贝还在,来源没了。"
            else:
                now = sha256(upstream)
                row["currentUpstreamSha256"] = now
                row["status"] = "in-sync" if now == claimed else "UPSTREAM_MOVED"
                verdict = judged.get((upstream_rel, now))
                if now != claimed and verdict:
                    row["status"] = "adjudicated"
                    row["verdict"] = verdict["verdict"]
                    row["adjudicationWhy"] = verdict["why"][:300]
                elif now != claimed:
                    row["why"] = ("上游在 vendoring 之后改过。消费方那份仍是旧版本——"
                                  "它不会因此报错,只会继续按旧规则跑。")
                    row["action"] = ("重拷即可(逐字拷贝件;若有本地增量,重拷后重新打上)"
                                     if kind == "copied" else
                                     "★不能机械同步:这是**重写移植**件,要有人读懂上游改了什么"
                                     "再决定跟不跟、怎么跟。")
            entry["files"].append(row)
        bad = [r for r in entry["files"] if r["status"] not in ("in-sync", "adjudicated")]
        entry["status"] = "in-sync" if not bad else "UPSTREAM_MOVED"
        entry["counts"] = {"files": len(entry["files"]), "moved": len(bad),
                           "movedCopied": len([r for r in bad if r.get("kind") == "copied"]),
                           "movedPorted": len([r for r in bad if r.get("kind") == "ported"]),
                           "adjudicated": len([r for r in entry["files"]
                                               if r["status"] == "adjudicated"])}
        if bad:
            moved.append(entry)
        results.append(entry)

    status = "in-sync" if not moved else "drift"
    report = {
        "schemaVersion": "chengziclass.gate-vendored-upstream.v1",
        "gate": "GATE_VENDORED_UPSTREAM_MOVED",
        "rule": "消费方 PROVENANCE.md 声明的每个上游 sha256,必须等于当前上游文件的 sha256。",
        "package": str(args.package),
        "status": status,
        "★redIsHandover": ("红不等于本包出错,也不该由本包消红:重新 vendor 是消费方的事。"
                           "红是交接信号——谁持有消费方的租约,谁去同步。"),
        "consumers": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    print(f"GATE_VENDORED_UPSTREAM_MOVED: {status}")
    for entry in results:
        print(f"  [{entry['status']}] {entry['consumer']}  {entry.get('counts', '')}")
        for row in entry.get("files", []):
            mark = "  " if row["status"] in ("in-sync", "adjudicated") else "★ "
            print(f"    {mark}{row['status']:16} [{row.get('kind','?'):6}] {row['vendored']}")
            if row["status"] == "adjudicated":
                print(f"        上游已动,但已判过:{row['verdict']} — "
                      f"{row['adjudicationWhy'][:60]}…")
            if row["status"] == "UPSTREAM_MOVED":
                print(f"        声明 {row['claimedUpstreamSha256'][:16]}"
                      f" → 现在 {row['currentUpstreamSha256'][:16]}   ({row['upstream']})")
                print(f"        处置:{row['action']}")
    return 0 if status == "in-sync" else 1


if __name__ == "__main__":
    raise SystemExit(main())
