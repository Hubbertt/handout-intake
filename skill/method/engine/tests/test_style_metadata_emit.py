#!/usr/bin/env python3
"""证明样式元数据的四个发射器真的通电——**尤其是从未跑过的那两个分支**。

编译器发射 uiPriority/qFormat/semiHidden/unhideWhenUsed 之后,实测:
  w:uiPriority 168→205、w:qFormat 25→62   有变化,发射成立
  w:semiHidden 17→17、w:unhideWhenUsed 36→36  **计数不变**

不变的原因是注册表里 37 个样式全部声明 false,于是只走了「移除」分支。
代码写了,但 true 分支**一次都没跑过**——判据恒假的标准形状:
它和「一切正常」长得一模一样,而且没有任何计数会提示你。

真实数据凑不出这个分支(没有样式需要 semiHidden=true),
所以只能用单测强制走一遍。这不是补充验证,是唯一的验证。

运行:  python3.12 test_style_metadata_emit.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import docx
from docx.enum.style import WD_STYLE_TYPE

# 编译器现住在共享的 scripts/formal 下(尚未并入包)。逐级向上找,找不到就明确报错,
# 不猜路径——猜出来的路径会在别人的机器上静默指向别的文件。
ENGINE = None
for base in Path(__file__).resolve().parents:
    candidate = base / "scripts" / "formal" / "build_semantic_handout_from_blueprint.py"
    if candidate.exists():
        ENGINE = candidate
        break
if ENGINE is None:
    raise SystemExit("找不到 build_semantic_handout_from_blueprint.py。"
                     "本测试依赖共享编译器,它尚未并入包——这是已登记的欠账。")
spec = importlib.util.spec_from_file_location("bsh", ENGINE)
bsh = importlib.util.module_from_spec(spec)
sys.modules["bsh"] = bsh
spec.loader.exec_module(bsh)

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


BASE = {"name": "元数据探针", "sizePt": 12, "bold": False, "color": "000000",
        "fontCn": "宋体", "fontAscii": "Times New Roman", "fontCs": "Times New Roman"}


def emit(**overrides) -> str:
    document = docx.Document()
    spec_ = dict(BASE)
    spec_.update(overrides)
    style = bsh.ensure_style(document, f"CZ_Probe{len(overrides)}{hash(str(overrides))&0xFFFF}",
                             spec_, WD_STYLE_TYPE.PARAGRAPH)
    return style._element.xml


print("=== true 分支:元素必须出现 ===")
for key, tag in (("qFormat", "w:qFormat"),
                 ("semiHidden", "w:semiHidden"),
                 ("unhideWhenUsed", "w:unhideWhenUsed")):
    xml = emit(**{key: True})
    check(f"{key}=True → <{tag}/> 出现", f"<{tag}/>" in xml or f"<{tag} " in xml)

print("\n=== false 分支:元素必须不在 ===")
for key, tag in (("qFormat", "w:qFormat"),
                 ("semiHidden", "w:semiHidden"),
                 ("unhideWhenUsed", "w:unhideWhenUsed")):
    xml = emit(**{key: False})
    check(f"{key}=False → <{tag}/> 不出现", f"<{tag}" not in xml)

print("\n=== uiPriority:值必须原样落地 ===")
xml = emit(uiPriority=37)
check("uiPriority=37 → w:val=\"37\"", 'w:uiPriority w:val="37"' in xml,
      "这一项本就有真实数据覆盖,一并锁住防回归")

print("\n=== 破坏性:改坏发射器,单测必须失败 ===")
print("  (此项由人工验证:把 semiHidden 的 if 分支注释掉重跑,上面第 2 行应变 ✗)")

print()
if FAILED:
    print(f"**{len(FAILED)} 项未通过**: {FAILED}")
    raise SystemExit(1)
print("全部通过:四个元数据发射器的 true 与 false 分支都跑过了。")
