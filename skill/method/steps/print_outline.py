#!/usr/bin/env python3
"""付印线·outline(工序 s6d-outline):把 vendor 里的能力接到工作区,原地跑一遍。

用法:
  print_outline.py --workspace X [--volume V]
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
        print(json.dumps({"step": "s6d-outline", "status": "unbound", "missing": cfg["missing"],
                          "why": "付印所需的册级绑定缺项。封面/封底/键名是使用方的事实,不猜。"},
                         ensure_ascii=False, indent=1))
        return 1
    import glob as _glob
    if not (_glob.glob("/Applications/Adobe Acrobat*.app") or _glob.glob("/Applications/*/Adobe Acrobat*.app")):
        # 没有 Acrobat 就转不了曲。**不假装,也不拦着**:装订件仍可自用,
        # 但它含字体、不可付印——把这句话写进报告,而不是留一个空的 print-master 让人猜。
        std = CHAIN.workspace / "output" / "print" / "standard-pdf" / cfg["key"] / cfg["pdfName"]
        print(json.dumps({"step": "s6d-outline", "status": "refused-no-acrobat",
                          "usable": str(std) if std.exists() else None,
                          "why": "本机没有 Adobe Acrobat,无法转曲。装订件可自用(预览/校对),"
                                 "但它含字体、**不可付印**——付印件必须 0 残留字体、0 可提取文本。"},
                         ensure_ascii=False, indent=1))
        return 1
    # ★沙盒实测(2026-08-16):HOME 下没有 Acrobat 的用户级配置时,首次驱动 Acrobat 会弹
    #   「找不到钥匙串」对话框——那是 Acrobat 自己弹的,静默门扫不到(它只扫我们的脚本),
    #   而本步在弹窗背后照样跑完并报 ok。链不知道用户屏幕上多了一个框。
    #   能做的:驱动前探一次;没有就明说「首次可能弹框,请先手动打开一次 Acrobat」,不默默让它弹。
    from pathlib import Path as _Pth
    acro_cfg = _Pth.home() / "Library/Application Support/Adobe/Acrobat"
    if not acro_cfg.exists():
        print(json.dumps({"step": "s6d-outline", "status": "needs-first-launch",
                          "why": "本用户还没有 Acrobat 的用户级配置(~/Library/Application Support/Adobe/Acrobat 不存在)。"
                                 "脚本驱动 Acrobat 时它可能弹「找不到钥匙串」对话框——请先**手动打开一次 Adobe Acrobat**"
                                 "让它完成首次初始化,再重跑本步。不代你点掉那个框:那是你账户的安全设置。"},
                         ensure_ascii=False, indent=1))
        return 1
    m = P.bind_outline(CHAIN, cfg["key"], cfg["pdfName"])
    (CHAIN.workspace / "output" / "print" / "outlined-pdf").mkdir(parents=True, exist_ok=True)
    # vendor 的 main() 自己 parse sys.argv,会撞上本步的 --workspace/--volume。
    # 只留它认识的参数:--keys 本册。**不给它 --force**——强制会跳过它自己的
    # 「源报告是否当前」校验,那正是这一步存在的意义。
    # 资源守卫的阈值可由册级绑定覆盖,**不手敲命令绕过链**。
    #
    # vendor 里 DEFAULT_MIN_FREE_GB = 120.0 是个光秃秃的常量,没有任何理由记录。
    # 2026-08-20 查旧册那次**成功**转曲的资源快照:转曲前空闲 121.86 GB、
    # 转曲后 121.87 GB —— **净消耗 −0.01 GB**,14.32 秒 / 114 页。
    # 也就是说这个门槛与实际消耗没有可证的关系;而那一次只比它多 1.86 GB,
    # 差一点它自己也过不去。
    #
    # 但一个数据点不足以改默认值,所以:默认仍是 120,册可显式覆盖并留下理由。
    # 覆盖是**记录在册里的决定**,不是命令行里一次性的绕过。
    guard = (CHAIN.bindings.get("outlineResourceGuard") or {})
    extra: list[str] = []
    if guard.get("minFreeGB") is not None:
        if not guard.get("why"):
            print(json.dumps({"step": "s6d-outline", "status": "failed",
                              "reason": "outlineResourceGuard.minFreeGB 覆盖了默认阈值,"
                                        "但没有写 why。降安全阈值必须留下理由与依据,"
                                        "否则下一个人只看到一个更小的数字。"},
                             ensure_ascii=False, indent=1))
            return 1
        extra += ["--min-free-gb", str(float(guard["minFreeGB"]))]
    if guard.get("maxAcrobatRssGB") is not None:
        extra += ["--max-acrobat-rss-gb", str(float(guard["maxAcrobatRssGB"]))]
    sys.argv = [sys.argv[0], "--keys", cfg["key"], *extra]
    try:
        m.main()
    except SystemExit as exc:
        # vendor 用 raise SystemExit("说明文字") 报错——code 是字符串。
        # 首版把非 int 一律记成 1 并丢掉文字,于是失败只剩一个数字。
        # **吞掉原因的失败,比失败本身更贵**:要重跑一次才知道错在哪。
        if isinstance(exc.code, int) and exc.code == 0:
            pass
        else:
            print(json.dumps({"step": "s6d-outline", "status": "failed",
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
    if ok:
        # 成品同时落到工件 print-master 的登记位置(release/),下游 s7 与导出按它找。
        # 拷而不是移:outlined-pdf/ 是四步流程自己的报告口径所指,两处都得在。
        import shutil
        src = m.OUTLINED_ROOT / cfg["key"] / cfg["pdfName"]
        dst = CHAIN.path_for("print-master")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)
    print(json.dumps({"step": "s6d-outline", "status": status, "report": str(report_path)},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
