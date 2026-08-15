#!/usr/bin/env python3
"""付印线·qa(工序 s6c-binding-qa):把 vendor 里的能力接到工作区,原地跑一遍。

用法:
  print_qa.py --workspace X [--volume V]
退出码 0=本步 vendor 报告 ready/pass 1=否则
"""

from __future__ import annotations

import json
import sys

from _bootstrap import chain_from_argv  # noqa: E402
import _printline as P  # noqa: E402

CHAIN = chain_from_argv(__doc__)


def main() -> int:
    cfg = P.volume_print_config(CHAIN)
    if cfg["missing"]:
        print(json.dumps({"step": "s6c-binding-qa", "status": "unbound", "missing": cfg["missing"],
                          "why": "付印所需的册级绑定缺项。封面/封底/键名是使用方的事实,不猜。"},
                         ensure_ascii=False, indent=1))
        return 1
    m = P.bind_qa(CHAIN, cfg["key"], cfg["pdfName"])
    (CHAIN.workspace / "output" / "print" / "standard-pdf-qa").mkdir(parents=True, exist_ok=True)
    try:
        m.main()
    except SystemExit as exc:
        # vendor 用 raise SystemExit("说明文字") 报错——code 是字符串。
        # 首版把非 int 一律记成 1 并丢掉文字,于是失败只剩一个数字。
        # **吞掉原因的失败,比失败本身更贵**:要重跑一次才知道错在哪。
        if isinstance(exc.code, int) and exc.code == 0:
            pass
        else:
            print(json.dumps({"step": "s6c-binding-qa", "status": "failed",
                              "reason": str(exc.code)}, ensure_ascii=False, indent=1))
            return 1
    report_path = getattr(m, "REPORT", None) or getattr(m, "STANDARD_REPORT", None) \
        or getattr(m, "REPORT_PATH", None)
    status = "unknown"
    if report_path and report_path.exists():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        # 四步的报告口径不统一:有的顶层 status=ready/pass,有的只有 summary.ready。
        # 只认其一会把成功读成 unknown——首版就因此没把成品拷到 release/,而屏幕上还打着 pass。
        summ = rep.get("summary") or {}
        status = (rep.get("status") or ("pass" if summ.get("ready") or summ.get("passed") == summ.get("pdfCount") else "unknown"))
    ok = status in ("ready", "pass", "ok")
    print(json.dumps({"step": "s6c-binding-qa", "status": status, "report": str(report_path)},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
