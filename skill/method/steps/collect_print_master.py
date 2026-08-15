#!/usr/bin/env python3
"""把 PDF 四步产出的付印件收进本工作区的 print-master。

★P6 抓出:s6-pdf 报成功而 print-master 仍缺——四步流程把成品写在生产复核目录,
而册级绑定指向工作区内的路径。「声明的位置与实际产出不一致」的又一例:
下游 s7 因此永远等不到它,而 s6 自己一直报 ok。

产物归工作区所有,才谈得上每一步独立可跑、独立产出(使用方 2026-08-15 定)。
找不到就如实报缺,**不去别处凑一个同名文件**——凑来的成品与真成品长得一模一样。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
# 转曲成品的位置属**本机既有生产线**,不属本包。写死在包里等于把一台机器的
# 布局编进方法——换台机器就废,而废的方式是「找不到文件」,看起来像成品没生成。
# 故取自册级绑定;缺失时如实报缺,不去猜一个默认位置。


def main() -> int:
    key = str(CHAIN.bindings.get("pdfKey") or "")
    root = (CHAIN.bindings.get("paths") or {}).get("release.outlinedRoot")
    if not root or not key:
        print(json.dumps({"step": "collect-print-master", "status": "unbound",
                          "why": "册级绑定缺 paths['release.outlinedRoot'] 或 pdfKey。"
                                 "转曲成品的位置属本机既有生产线,不属本包——"
                                 "不猜一个默认位置。"},
                         ensure_ascii=False, indent=1))
        return 1
    folder = Path(root) / key
    found = sorted(folder.glob("*.pdf")) if folder.exists() else []
    target = CHAIN.path_for("print-master")
    # ★成品必须对应当前 Word:PDF 四步的导出报告里记着它导自哪份 docx 的 sha256。
    # 不核对就收,收进来的可能是上一轮的旧成品——报 ok 而做错了对象(P6 缺口 13,已重演一次)。
    word = CHAIN.resolve("word")
    word_sha = hashlib.sha256(word[0].read_bytes()).hexdigest() if word else None
    export_report = folder.parent.parent / "content-pdf" / "content_pdf_export_report.json"
    if word_sha and export_report.exists():
        try:
            rep = json.loads(export_report.read_text(encoding="utf-8"))
            recorded = {r.get("docxSha256") for r in rep.get("results") or []}
            if word_sha not in recorded:
                print(json.dumps({"step": "collect-print-master", "status": "stale",
                                  "why": "四步导出报告记录的 docx sha256 与当前 Word 不符——"
                                         "这份 PDF 不是从当前 Word 出的。拒收。",
                                  "currentWord": word_sha[:16],
                                  "exportedFrom": [x[:16] for x in recorded if x]},
                                 ensure_ascii=False, indent=1))
                return 1
        except Exception as exc:
            print(json.dumps({"step": "collect-print-master", "status": "unverifiable",
                              "why": f"读不到导出报告或格式不符({exc}),无法证明 PDF 对应当前 Word。拒收。"},
                             ensure_ascii=False, indent=1))
            return 1
    if not found:
        print(json.dumps({"step": "collect-print-master", "status": "missing",
                          "searched": str(folder),
                          "why": "四步流程的成品不在预期位置。不去别处凑一个同名文件——"
                                 "凑来的成品与真成品长得一模一样。"},
                         ensure_ascii=False, indent=1))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(found[-1], target)
    print(json.dumps({"step": "collect-print-master", "status": "ok",
                      "from": str(found[-1]), "to": str(target),
                      "bytes": target.stat().st_size}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
