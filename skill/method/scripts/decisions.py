#!/usr/bin/env python3
"""特例裁决队列:链把「不敢猜的」挂进来,人裁完链再往下。

**为什么要一个队列。**
使用方 2026-08-15 定的用法:「后面真正使用时,只需要给文件、拆分、人工裁决特例即可。」
现有的人裁点散在三处(truth-map / 图裁决表 / 渲染复核),格式各异,
人得知道去哪改哪个 JSON——那是流水线的用法,不是产品的用法。

队列的规矩:
  挂  链里任何一步遇到「不猜」的情形,调 open() 挂一条:哪一步、什么对象、
      几个选项、为什么不能自己定。挂完这一步报 awaiting-decision,不往下走。
  裁  人(或智能体代人问过之后)调 decide():选哪个、谁裁的、一句理由。
      **理由是必填**:没有理由的裁决进不了经验层,下一册无法据此归纳。
  用  下一次跑到同一步,先查队列:已裁的直接用,不再问。
  留  队列文件就在册目录 decisions/ 下,与源文同寿命,导出时随册走(含源文片段,走版权双开关)。

用法(命令行):
  decisions.py list   --volume-dir <册目录>
  decisions.py decide --volume-dir <册目录> --id <裁决id> --choice <选项> --by <谁> --why <理由>
库用法(链内):
  from decisions import Queue; q = Queue(volume_dir); q.open(...); q.get(...)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


class Queue:
    def __init__(self, volume_dir: Path):
        self.dir = Path(volume_dir) / "decisions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "queue.json"
        self.data = (json.loads(self.path.read_text(encoding="utf-8"))
                     if self.path.exists() else
                     {"schemaVersion": "handout-intake.decisions.v1", "items": {}})

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")

    def open(self, decision_id: str, *, step: str, subject: str, question: str,
             options: list[str], why_not_auto: str, evidence: dict | None = None) -> dict:
        """挂一条待裁。已存在且已裁的不覆盖——裁决是人的劳动,链不许抹。"""
        item = self.data["items"].get(decision_id)
        if item and item.get("status") == "decided":
            return item
        item = {"id": decision_id, "step": step, "subject": subject, "question": question,
                "options": options, "whyNotAuto": why_not_auto, "evidence": evidence or {},
                "status": "pending", "openedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
        self.data["items"][decision_id] = item
        self._save()
        return item

    def get(self, decision_id: str):
        item = self.data["items"].get(decision_id)
        return item if item and item.get("status") == "decided" else None

    def decide(self, decision_id: str, *, choice: str, by: str, why: str) -> dict:
        item = self.data["items"].get(decision_id)
        if not item:
            raise KeyError(f"队列里没有 {decision_id}")
        if choice not in item["options"]:
            raise ValueError(f"选项 {choice!r} 不在 {item['options']} 里。"
                             f"要加新选项是改问题,不是裁决——先改问题再裁。")
        if not why.strip():
            raise ValueError("理由是必填。没有理由的裁决进不了经验层,下一册无法据此归纳。")
        item.update(status="decided", choice=choice, decidedBy=by, why=why,
                    decidedAt=datetime.now().astimezone().isoformat(timespec="seconds"))
        self._save()
        return item

    def pending(self) -> list[dict]:
        return [v for v in self.data["items"].values() if v.get("status") == "pending"]

    def decided(self) -> list[dict]:
        return [v for v in self.data["items"].values() if v.get("status") == "decided"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list"); l.add_argument("--volume-dir", required=True, type=Path)
    d = sub.add_parser("decide"); d.add_argument("--volume-dir", required=True, type=Path)
    d.add_argument("--id", required=True); d.add_argument("--choice", required=True)
    d.add_argument("--by", required=True); d.add_argument("--why", required=True)
    args = ap.parse_args()
    q = Queue(args.volume_dir)
    if args.cmd == "list":
        print(json.dumps({"pending": q.pending(), "decided": len(q.decided())},
                         ensure_ascii=False, indent=1))
        return 0 if not q.pending() else 2
    item = q.decide(args.id, choice=args.choice, by=args.by, why=args.why)
    print(json.dumps(item, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
