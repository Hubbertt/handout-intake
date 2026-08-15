#!/usr/bin/env python3
"""GATE_GLYPH_COVERAGE:声明的字体画不出来的字符,必须登记替换,否则拒绝出成品。

**为什么要有这一步。** 首轮的 ❌(U+274C)是在导出完 106 页内容 PDF 之后,由分页
缺陷审计报 undeclared-font 才发现的——判据挂在链子末端,前面全部工序白跑一遍。
而且它检出的是「成品里有个没声明的字体名」,那是症状;根因是「成品里有字符,
声明的字体画不出来」,于是 Word 悄悄回退到别的字体。

三条设计:

  判据实测,不手维护白名单
      拿声明的字体文件逐字查 has_glyph。手写「允许字符表」迟早与真实字体漂开,
      而漂开的那天它长得和一切正常一模一样(登记册 P8)。

  门前移到蓝图阶段
      在建 Word 之前就拦住。这一步的产物是下一步的输入,所以「跳过」在结构上
      不可能,不需要任何人记得跑它。

  查不到字体文件就拒绝,不默认通过
      「宁可拒绝,不可猜」。字体文件找不到时无法证明覆盖,那就不是「通过」,
      是「不知道」——两者必须分开。

替换表在 mapping.characterPolicy,是数据不是代码:
  {"substitutions": [{"from": "❌", "to": "×", "why": ..., "coveredBy": [...]}]}
"""

from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from _bootstrap import chain_from_argv  # noqa: E402

CHAIN = chain_from_argv(__doc__)
BLUEPRINT = CHAIN.path_for('blueprint.substituted')
PARAMS = CHAIN.only('params')
OUT = CHAIN.path_for('blueprint.glyph-safe')
REPORT = CHAIN.path_for('gate.glyph-coverage')

# **不设「不必查」的快速区间。**
#
# 首版为省事跳过了 ASCII 可打印、CJK 汉字、CJK 标点、全角形式四段,理由是
# 「任何正文字体都覆盖」。代码里当时留了话:「若将来发现某字体连这些都缺,
# 这条捷径要立刻撤掉」。那一天在第一册就到了——成品第 35 页的三个全角句点
# 「．．．」(U+FF0E)由 MS-Mincho 渲染,一份中文物理讲义里出现了日文明朝体。
# U+FF0E 正落在被跳过的 0xFF00-0xFF65 区间里,所以门量都没量。
#
# 教训不是「再补一个例外」,是**假设本身不该存在**:门的价值全在实测,
# 一旦允许「这一段不用查」,它就不再是门,是一份声明。has_glyph 很快,
# 全量查一遍的代价远小于漏掉一次。
def fast_covered(code: int) -> bool:
    return False


def not_a_glyph(ch: str) -> bool:
    """控制字符与换行不画字形,不该进覆盖检查。

    首版把制表符(U+0009)算了进来,一口气报 100 处「字体画不出」——判据把
    「排版指令」当成了「要画的字」。判据本身错了,报出来的数就全是噪音,
    而噪音会把旁边那一条真发现淹掉(本例里是 U+2078)。
    """
    return unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") or ch in "\t\n\r"


def declared_font_names(params: dict) -> set[str]:
    styles = ((params.get("wordStyleRegistry") or {}).get("paragraphStyles") or {})
    names = set()
    for spec in styles.values():
        for key in ("fontCn", "fontAscii", "fontCs"):
            if spec.get(key):
                names.add(str(spec[key]))
    return names


def load_fonts(names: set[str], files: dict[str, str]) -> tuple[dict, list[str]]:
    """字体名 → 可查字形的字体对象。查不到文件的单独返回,由调用方拒绝。"""
    try:
        import fitz
    except ImportError:
        raise SystemExit("GATE_GLYPH_COVERAGE 需要 PyMuPDF(fitz)才能实测字形覆盖。"
                         "装不上就不能声称覆盖已验证——这一步宁可停,不出假绿灯。")
    loaded, missing = {}, []
    for name in sorted(names):
        path = files.get(name)
        if not path or not Path(path).exists():
            missing.append(name)
            continue
        loaded[name] = fitz.Font(fontfile=path)
    return loaded, missing


def walk_text(node, sink):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "text" and isinstance(value, str):
                sink.append((node, value))
            else:
                walk_text(value, sink)
    elif isinstance(node, list):
        for item in node:
            walk_text(item, sink)


def main() -> int:
    blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
    params = json.loads(PARAMS.read_text(encoding="utf-8"))
    mapping = json.loads(CHAIN.path_for('mapping').read_text(encoding="utf-8"))
    policy = mapping.get("characterPolicy") or {}
    subs = {e["from"]: e["to"] for e in (policy.get("substitutions") or [])}

    names = declared_font_names(params)
    # 字体文件映射:册级绑定显式给的 > 安装向导探到的(runtime/probe-report.json)。
    # ★2026-08-15 全新安装抓出:新册绑定没有 fontFiles(拷来的是别的机器的),门对四个字体全 refused。
    #   门拒绝是对的;缺的是它该去读向导已经探好的那份——机器事实探一次,不该每册各写。
    font_files = dict(CHAIN.bindings.get("fontFiles") or {})
    if not font_files:
        from pathlib import Path as _P
        for base in (_P(__file__).resolve().parents[3], _P(__file__).resolve().parents[2]):
            rep = base / "runtime" / "probe-report.json"
            if rep.exists():
                try:
                    font_files = dict(json.loads(rep.read_text(encoding="utf-8")).get("fontFiles") or {})
                except Exception:
                    font_files = {}
                if font_files:
                    break
    fonts, missing_files = load_fonts(names, font_files)
    if missing_files:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({
            "gate": "GATE_GLYPH_COVERAGE", "status": "refused",
            "why": "声明的字体找不到文件,无法实测覆盖。这不是通过,是不知道。",
            "unresolvedFonts": missing_files,
            "howToFix": "跑 runtime/install_wizard.py 让它探测字体并写入 probe-report.json;或在册级 bindings.json 的 fontFiles 里显式给出。",
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"status": "refused", "unresolvedFonts": missing_files},
                         ensure_ascii=False))
        return 1

    # 有其它证据表明能正常渲染、但本机字体文件测不出覆盖的字符,可以登记在
    # characterPolicy.acceptedWithEvidence 里放行。**每条必须带 evidence 与
    # queuedFix**——没有证据的例外就是把门关掉,而关掉的门和绿灯长得一模一样。
    accepted = {e["char"]: e for e in (policy.get("acceptedWithEvidence") or [])
                if e.get("evidence") and e.get("queuedFix")}
    ignored = [e for e in (policy.get("acceptedWithEvidence") or [])
               if not (e.get("evidence") and e.get("queuedFix"))]

    def covered(ch: str) -> bool:
        code = ord(ch)
        if fast_covered(code) or ch in accepted:
            return True
        return any(font.has_glyph(code) for font in fonts.values())

    holders: list = []
    walk_text(blueprint, holders)

    applied = Counter()
    uncovered = Counter()
    samples: dict[str, str] = {}
    for holder, text in holders:
        new = text
        for src, dst in subs.items():
            if src in new:
                applied[src] += new.count(src)
                new = new.replace(src, dst)
        for ch in set(new):
            if not_a_glyph(ch):
                continue
            if not covered(ch):
                uncovered[ch] += new.count(ch)
                samples.setdefault(ch, text[:60])
        if new != text:
            holder["text"] = new

    def describe(ch: str) -> str:
        try:
            return unicodedata.name(ch)
        except ValueError:
            return "?"

    report = {
        "gate": "GATE_GLYPH_COVERAGE",
        "schemaVersion": "handout-intake.gate.glyph-coverage.v1",
        "declaredFonts": sorted(names),
        "fontFilesUsed": {n: font_files[n] for n in sorted(fonts)},
        "substitutionsApplied": [
            {"from": src, "to": subs[src], "occurrences": n,
             "fromName": describe(src), "toName": describe(subs[src])}
            for src, n in sorted(applied.items())],
        "uncovered": [
            {"char": ch, "codepoint": f"U+{ord(ch):04X}", "name": describe(ch),
             "occurrences": n, "sample": samples[ch]}
            for ch, n in sorted(uncovered.items(), key=lambda kv: -kv[1])],
        "textNodesScanned": len(holders),
        "acceptedWithEvidence": [
            {"char": ch, "codepoint": f"U+{ord(ch):04X}", "name": describe(ch),
             "evidence": e["evidence"], "queuedFix": e["queuedFix"]}
            for ch, e in sorted(accepted.items())],
        "exceptionsRejected": [
            {"char": e.get("char"),
             "why": "例外必须同时带 evidence 与 queuedFix,缺一不放行"}
            for e in ignored],
    }
    report["status"] = "pass" if not uncovered else "fail"
    if uncovered:
        report["why"] = ("成品里有字符,声明的字体一个都画不出来。Word 会悄悄回退到"
                         "别的字体,到 PDF 阶段才由 undeclared-font 报出来——那时"
                         "前面全部工序已经白跑。在这里拦住。")
        report["howToFix"] = ("在 mapping.characterPolicy.substitutions 里登记替换,"
                              "每条带 why 与 coveredBy(哪个声明字体能画出替换字符)。"
                              "**替换字符必须先实测覆盖**,否则只是把一个失败换成另一个。")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    if not uncovered:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    print(json.dumps({k: report[k] for k in
                      ("status", "substitutionsApplied", "uncovered")},
                     ensure_ascii=False, indent=1))
    return 0 if not uncovered else 1


if __name__ == "__main__":
    sys.exit(main())
