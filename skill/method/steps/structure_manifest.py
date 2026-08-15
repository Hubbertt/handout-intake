#!/usr/bin/env python3
"""付印前置·结构清单(工序 s5i):为当前 Word 建结构清单并按判据接受。

**为什么它是一步。** 付印导出要求「已复核的结构清单」且其 sha256 等于当前 Word——
这道门今天拦对了两次(Word 一变清单即过期)。它原先在生产线里由人手工跑,
所以从没进过工序表;今天我手工跑了四次,它就四次没暴露。

接受判据由 vendor 的 refresh_one 给出:目录条目全部匹配 + 必需块锚点齐全。
不满足则清单留 draft,本步失败——**不把 draft 改成 reviewed**,那是伪造复核。

用法:
  structure_manifest.py --workspace X [--volume V]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from _bootstrap import chain_from_argv  # noqa: E402
import _printline as P  # noqa: E402

CHAIN = chain_from_argv(__doc__)


def main() -> int:
    key = str(CHAIN.bindings.get("pdfKey") or "").strip()
    if not key:
        print(json.dumps({"step": "s5i", "status": "unbound", "missing": ["pdfKey"]}, ensure_ascii=False))
        return 1
    P._guard_env()
    import build_summer_structure_manifests as B
    import refresh_summer_structure_manifest_review as R
    out = CHAIN.workspace / "output" / "print" / "structure-manifest"
    out.mkdir(parents=True, exist_ok=True)
    B.MANIFEST_DIR = out
    R.MANIFEST_DIR = out if hasattr(R, "MANIFEST_DIR") else None
    docx = CHAIN.only("word")
    result = R.refresh_one(key, docx, datetime.now().astimezone().isoformat(timespec="seconds"))
    target = out / f"{key}.structure.json"
    status = (result.get("status") or result.get("manifestStatus") or "").lower()
    ok = target.exists() and json.loads(target.read_text(encoding="utf-8")).get("status") == "reviewed"
    print(json.dumps({"step": "s5i", "status": "reviewed" if ok else "draft",
                      "manifest": str(target), "detail": {k: v for k, v in result.items()
                                                          if k in ("status", "reason", "unmatchedTocEntries", "missingAnchors")}},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
