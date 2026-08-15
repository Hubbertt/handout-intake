#!/usr/bin/env python3
"""把册级绑定声明的 wheel 解包到 interpreter.pythonpath,让链自带运行时。

**为什么它必须是链里的一步。**
P6 空手复现在新工作区第一次跑就死在 `ModuleNotFoundError: lxml`。
原工作区一直好好的,因为 work/pylib 是当初**手工**解包出来的,
而「手工做过一次」在工序表里没有任何痕迹——
下一个人拿到包,会在同一个地方以同样的方式失败。

工序表的意义就是「不给机会」。一件必须先做的事若不在表里,
它就只活在做过的人的记忆里,而记忆不随包分发。

**不联网。** 只解包册级绑定里已经给出的 wheel 文件;找不到就如实报缺,
不去 pip install——联网装包会让「这次能跑」取决于当时的网络与源,
那正是可复现要消灭的东西。

用法:
  bootstrap_runtime.py --workspace X [--volume V]
退出码 0=就绪(或本来就就绪) 1=缺 wheel 或解包失败
"""

from __future__ import annotations

import json
import sys
import zipfile

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)


def main() -> int:
    interp = CHAIN.bindings.get("interpreter") or {}
    target_rel = interp.get("pythonpath")
    if not target_rel:
        print(json.dumps({"step": "bootstrap-runtime", "status": "not-required",
                          "why": "册级绑定没有声明 interpreter.pythonpath,"
                                 "说明这一册不依赖随工作区分发的 wheel。"},
                         ensure_ascii=False, indent=1))
        return 0

    # pythonpath 可能是多段(随册 wheel + 本机共享运行时)。解包只往第一段写——
    # 后面几段是本机既有的运行时,不属本工作区,往那里写是越界。
    # ★这是我把 pythonpath 改成多段时引入的:整串被当成一个目录名,
    #   bootstrap 报 ok 而 lxml 仍然缺——**报 ok 的空转步骤**,又一次。
    import os as _os
    target = CHAIN.workspace / str(target_rel).split(_os.pathsep)[0]
    wheels = CHAIN.resolve("wheels")
    report = {"step": "bootstrap-runtime", "target": str(target),
              "wheels": [w.name for w in wheels]}

    # 已就绪就不重复解包:解包是幂等的,但重复写会让产物 hash 无谓地变。
    existing = sorted(p.name for p in target.glob("*")) if target.exists() else []
    needed = []
    for wheel in wheels:
        stem = wheel.name.split("-")[0]
        if not any(name == stem or name.startswith(stem + "-") for name in existing):
            needed.append(wheel)
    if not needed:
        report.update(status="already-ready", unpacked=0, present=existing)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    if not wheels:
        report.update(status="missing-wheels",
                      why="册级绑定声明了 pythonpath,却没有可解包的 wheel。"
                          "**不联网 pip install**:那会让『这次能跑』取决于当时的"
                          "网络与源,正是可复现要消灭的东西。请把 wheel 随册提供。")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1

    target.mkdir(parents=True, exist_ok=True)
    for wheel in needed:
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(target)
    report.update(status="ok", unpacked=len(needed),
                  contents=sorted(p.name for p in target.glob("*"))[:8])
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
