#!/usr/bin/env python3
"""乙:把「根 + 偏离包」投影成完全展开的分享件。

**使用方定的两态**(2026-08-15):
  甲 = 本地状态,版本更新。根 + 偏离,是真源,人在这里改。
  乙 = 分享状态,个性化分享。完全展开、凝固,发给别人的就是它。

投影规则:每个样式的每个受治理键都取到确定值,并如实记下它从哪来——
样式自己写的 / 根给的 / **应用默认兜的**。第三种是缺口,不是结果。

**不猜。** 乙 的全部意义是「任意设备都一样」。凡是投影不出确定值的键,
一律进 unresolved 并使本次投影标记为 not-portable,而不是填一个看着合理的值。
填上去,乙 就变成了「看起来完全展开、实际仍依赖环境」——
比不投影更坏,因为它不再提示你去查。

已知的一类:rFonts 在根里是主题引用(minorHAnsi/minorEastAsia),
解析它需要 theme1.xml,而主题随模板变。样式自己写了字体名的不受影响;
没写的,投影拒绝代填——这正是 pendingImprovements 里「rFonts 去主题化」那条
要解决的问题,在这里以缺口的形式如实暴露出来。

映射表不新造:直接复用自足性门的 KEY_TO_TAG(样式键 → 作用域/标签),
而根子树本就用 OOXML 原名。同一张映射写两份必漂,是登记册 P8 反复抓到的形状。

用法:
  project_shared_template.py --params <甲> --out <乙>
退出码 0=完全可移植 1=有键投影不出确定值(乙 仍会写出,但标记 not-portable)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "gates" / "gate_style_self_contained.py"
_spec = importlib.util.spec_from_file_location("gssc", GATE)
G = importlib.util.module_from_spec(_spec)
sys.modules["gssc"] = G
_spec.loader.exec_module(G)

# KEY_TO_TAG 之外、需要单位换算或结构拆解的键。每条都写明为什么不能走通用通路。
SPECIAL = {
    "sizePt": ("rPr", "sz", "根的 sz 单位是半磅(22 = 11pt),样式的 sizePt 单位是磅。"
                          "同一事实两种单位,必须换算,不能直接搬。"),
    "color": ("rPr", "color", "根里是 {val: auto} 结构,样式里是裸字符串。取 val。"),
    "langVal": ("rPr", "lang", "根的 lang 是一个含 val/eastAsia/bidi 的结构,拆成两个样式键。"),
    "langEastAsia": ("rPr", "lang", "同上"),
}
FONT_KEYS = ("fontAscii", "fontCn", "fontCs")


def from_root(root: dict, scope: str, tag: str):
    holder = root.get(f"{scope}Default") or {}
    return holder.get(tag, None)


def project_style(spec: dict, root: dict) -> tuple[dict, list]:
    out, gaps = {}, []

    def put(key, value, src):
        out[key] = {"value": value, "from": src}

    for key, scoped in G.KEY_TO_TAG.items():
        scope, tag = scoped.split("/", 1)
        if key in spec:
            put(key, spec[key], "style")
            continue
        rv = from_root(root, scope, tag)
        if rv is not None:
            put(key, rv, "root")
            continue
        app = G.APP_DEFAULT.get(tag)
        if app is not None:
            put(key, app, "app-default")
            gaps.append({"key": key, "why": "样式与根都没写,只能落到应用默认。"
                                            "应用默认跨 Word 版本/语言可能不同,"
                                            "故这不是投影结果,是缺口。"})
        else:
            gaps.append({"key": key, "why": "样式没写、根没写、也没有已登记的应用默认。"})

    for key, (scope, tag, why) in SPECIAL.items():
        if key in spec:
            put(key, spec[key], "style")
            continue
        rv = from_root(root, scope, tag)
        if rv is None:
            gaps.append({"key": key, "why": f"根里没有 {scope}Default/{tag}。{why}"})
            continue
        if key == "sizePt":
            put(key, (rv / 2) if isinstance(rv, (int, float)) else rv, "root(半磅→磅)")
        elif key == "color":
            put(key, rv.get("val") if isinstance(rv, dict) else rv, "root")
        elif key == "langVal":
            put(key, rv.get("val") if isinstance(rv, dict) else rv, "root")
        elif key == "langEastAsia":
            put(key, rv.get("eastAsia") if isinstance(rv, dict) else rv, "root")

    for key in FONT_KEYS:
        if key in spec:
            put(key, spec[key], "style")
            continue
        gaps.append({"key": key,
                     "why": "根里的 rFonts 是主题引用(minorHAnsi/minorEastAsia),"
                            "解析需要 theme1.xml 且主题随模板变。"
                            "**投影拒绝代填**:填上去,乙 就变成「看起来完全展开、"
                            "实际仍依赖环境」,比不投影更坏——它不再提示你去查。"
                            "这正是 pendingImprovements「rFonts 去主题化」要解决的问题。"})

    # 结构性视觉键原样带过(底纹/边框):它们本就只在样式上出现,根不参与。
    for key in ("paragraphShading", "paragraphBorders", "name", "outlineLevel",
                "basedOnStyleId", "nextStyleId", "uiPriority", "qFormat"):
        if key in spec:
            put(key, spec[key], "style")
    return out, gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    root = params.get("docDefaults1") or {}
    pack = ((params.get("wordStyleRegistry") or {}).get("paragraphStyles") or {})
    binding = params.get("parameterTemplate") or {}

    styles, all_gaps, passthrough = {}, {}, []
    for sid, spec in sorted(pack.items()):
        if not isinstance(spec, dict):
            continue
        # 按格式契约刻意不带视觉字段的样式不参与投影。
        # ★排除依据取自数据里的 visualPassThrough,不由本脚本硬编码样式名——
        # 硬编码的白名单会在样式增删时悄悄失准,而且把「契约」搬成了「代码里的记忆」。
        # 规则原文:generated definition must contain no w:pPr or w:rPr。
        if spec.get("visualPassThrough"):
            passthrough.append(sid)
            continue
        resolved, gaps = project_style(spec, root)
        styles[sid] = resolved
        if gaps:
            all_gaps[sid] = gaps

    gap_keys = sorted({g["key"] for gs in all_gaps.values() for g in gs})
    portable = not all_gaps
    export = {
        "schemaVersion": "chengziclass.shared-template.v1",
        "state": "乙",
        "what": "完全展开、凝固的分享件。甲=本地状态/版本更新,乙=分享状态/个性化分享。"
                "使用方 2026-08-15 定。",
        "projectedFrom": {"templateId": binding.get("templateId"),
                          "combinedSha256": binding.get("combinedSha256"),
                          "paramsVersion": params.get("version")},
        "portable": portable,
        "portabilityNote": ("完全可移植:每个受治理键都有确定来源。" if portable else
                            "**not-portable**:下列键在部分样式上投影不出确定值,"
                            "已如实列出而非代填。乙 的全部意义是「任意设备都一样」,"
                            "填一个看着合理的值会让它变成「看起来完全展开、实际仍依赖环境」——"
                            "比不投影更坏,因为它不再提示你去查。"),
        "excludedByContract": {"styles": passthrough,
                               "why": "visualPassThrough=true:按格式契约刻意不带视觉字段,"
                                      "投影它们没有意义。排除依据取自数据,非硬编码名单。"},
        "unresolvedKeys": gap_keys,
        "unresolvedByStyle": all_gaps,
        "styleCount": len(styles),
        "styles": styles,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(export, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(json.dumps({"out": str(args.out), "styles": len(styles),
                      "excludedByContract": len(passthrough),
                      "portable": portable, "unresolvedKeys": gap_keys,
                      "stylesWithGaps": len(all_gaps)},
                     ensure_ascii=False, indent=1))
    return 0 if portable else 1


if __name__ == "__main__":
    sys.exit(main())
