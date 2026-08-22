"""OMML (Office Math Markup, ``m:oMath``) -> LaTeX.

Additive capability for the vendored carve engine: the handout-intake engine had no equation
handling and 组卷网 / 学科网 exports carry OMML. Self-contained (lxml only); covers runs, fractions,
sub/superscripts (incl. pre-scripts), radicals, delimiters, n-ary operators, functions, accents,
bars, boxes, group characters, limits, matrices and equation arrays. Unknown constructs degrade
to their concatenated children so no text is lost.
"""
from __future__ import annotations

import re
import unicodedata

from lxml import etree

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
M = f"{{{M_NS}}}"

# Unicode operator / symbol -> LaTeX command
_SYMBOLS = {
    "×": r"\times ", "÷": r"\div ", "±": r"\pm ", "∓": r"\mp ", "·": r"\cdot ", "⋅": r"\cdot ",
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ", "≈": r"\approx ", "≡": r"\equiv ", "∝": r"\propto ",
    "∞": r"\infty ", "∈": r"\in ", "∉": r"\notin ", "⊂": r"\subset ", "⊃": r"\supset ", "⊆": r"\subseteq ",
    "⊇": r"\supseteq ", "∪": r"\cup ", "∩": r"\cap ", "∅": r"\emptyset ", "∀": r"\forall ", "∃": r"\exists ",
    "→": r"\rightarrow ", "←": r"\leftarrow ", "↔": r"\leftrightarrow ", "⇒": r"\Rightarrow ", "⇐": r"\Leftarrow ",
    "⇔": r"\Leftrightarrow ", "∵": r"\because ", "∴": r"\therefore ", "∠": r"\angle ", "⊥": r"\perp ",
    "∥": r"\parallel ", "△": r"\triangle ", "°": r"^{\circ}", "′": "'", "″": "''", "∂": r"\partial ",
    "∇": r"\nabla ", "√": r"\sqrt", "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ", "∮": r"\oint ",
    "−": "-", "–": "-", "—": "-", "…": r"\ldots ", "⋯": r"\cdots ", "α": r"\alpha ", "β": r"\beta ",
    "γ": r"\gamma ", "δ": r"\delta ", "ε": r"\varepsilon ", "ζ": r"\zeta ", "η": r"\eta ", "θ": r"\theta ",
    "ι": r"\iota ", "κ": r"\kappa ", "λ": r"\lambda ", "μ": r"\mu ", "ν": r"\nu ", "ξ": r"\xi ", "π": r"\pi ",
    "ρ": r"\rho ", "σ": r"\sigma ", "τ": r"\tau ", "υ": r"\upsilon ", "φ": r"\varphi ", "ϕ": r"\phi ",
    "χ": r"\chi ", "ψ": r"\psi ", "ω": r"\omega ", "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ",
    "Λ": r"\Lambda ", "Ξ": r"\Xi ", "Π": r"\Pi ", "Σ": r"\Sigma ", "Φ": r"\Phi ", "Ψ": r"\Psi ", "Ω": r"\Omega ",
    "%": r"\%", "&": r"\&", "#": r"\#", "$": r"\$", "{": r"\{", "}": r"\}", "~": r"\sim ", " ": " ",
}

_ACCENTS = {
    "̂": "\\hat{{{0}}}", "^": "\\hat{{{0}}}", "̌": "\\check{{{0}}}", "̃": "\\tilde{{{0}}}",
    "́": "\\acute{{{0}}}", "̀": "\\grave{{{0}}}", "̇": "\\dot{{{0}}}", "̈": "\\ddot{{{0}}}",
    "⃛": "\\dddot{{{0}}}", "̄": "\\bar{{{0}}}", "̅": "\\overline{{{0}}}", "⃗": "\\vec{{{0}}}",
    "⃖": "\\overleftarrow{{{0}}}", "⃡": "\\overleftrightarrow{{{0}}}", "̆": "\\breve{{{0}}}",
    "⃑": "\\overrightarrow{{{0}}}",
}

_NARY = {"∑": "\\sum", "∏": "\\prod", "∐": "\\coprod", "∫": "\\int", "∬": "\\iint", "∭": "\\iiint", "∮": "\\oint",
         "⋃": "\\bigcup", "⋂": "\\bigcap", "⋁": "\\bigvee", "⋀": "\\bigwedge"}

_DELIMS = {"(": "(", ")": ")", "[": "[", "]": "]", "{": "\\{", "}": "\\}", "|": "|", "‖": "\\|", "⟨": "\\langle",
           "⟩": "\\rangle", "〈": "\\langle", "〉": "\\rangle", "⌈": "\\lceil", "⌉": "\\rceil", "⌊": "\\lfloor",
           "⌋": "\\rfloor", "": ".", " ": "."}

_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
          "log", "ln", "lg", "exp", "lim", "max", "min", "det", "gcd", "sup", "inf", "arg", "deg", "dim", "ker"}

_GROUP_CHR = {"⏟": "\\underbrace{{{0}}}", "⏞": "\\overbrace{{{0}}}", "⏝": "\\underbrace{{{0}}}", "⏜": "\\overbrace{{{0}}}",
              "←": "\\underleftarrow{{{0}}}", "→": "\\underrightarrow{{{0}}}"}


def _plain_letter(ch: str) -> str:
    """Mathematical Alphanumeric Symbols (𝑥, 𝐀 …) -> ASCII letters."""
    code = ord(ch)
    if 0x1D400 <= code <= 0x1D7FF:
        name = unicodedata.name(ch, "")
        m = re.search(r"(?:CAPITAL|SMALL) ([A-Z])$", name)
        if m:
            return m.group(1) if "CAPITAL" in name else m.group(1).lower()
        m = re.search(r"DIGIT (\w+)$", name)
        if m:
            return str(["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"].index(m.group(1)))
    return ch


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "　" <= ch <= "〿" or "＀" <= ch <= "￯"


def escape_text(text: str, *, plain: bool = False) -> str:
    out: list[str] = []
    cjk: list[str] = []

    def flush() -> None:
        if cjk:
            out.append("\\text{" + "".join(cjk) + "}")
            cjk.clear()

    for ch in text:
        ch = _plain_letter(ch)
        if _is_cjk(ch):
            cjk.append(ch)
            continue
        flush()
        if plain and ch.isalpha():
            out.append(ch)
        else:
            out.append(_SYMBOLS.get(ch, ch))
    flush()
    result = "".join(out)
    if plain and result and not result.startswith("\\text"):
        # m:sty="p" (plain/upright): keep as text so units like "km" don't italicise
        if re.fullmatch(r"[A-Za-z]+", result):
            return "\\mathrm{" + result + "}"
    return result


def _val(node, tag: str, default: str | None = None) -> str | None:
    if node is None:
        return default
    found = node.find(M + tag)
    if found is None:
        return default
    return found.get(M + "val", default)


class OmmlConverter:
    def convert(self, node) -> str:
        return self._children(node)

    # -- dispatch ---------------------------------------------------------------
    def _children(self, node) -> str:
        parts = []
        for child in node:
            if not isinstance(child.tag, str) or not child.tag.startswith(M):
                continue
            parts.append(self._node(child))
        return "".join(parts)

    def _node(self, node) -> str:
        tag = node.tag[len(M):]
        if tag.endswith("Pr") and tag not in ("sPre",):
            return ""
        handler = getattr(self, f"_do_{tag}", None)
        if handler is not None:
            return handler(node)
        return self._children(node)

    def _e(self, node, tag: str = "e") -> str:
        found = node.find(M + tag)
        return self._children(found) if found is not None else ""

    def _e_all(self, node, tag: str = "e") -> list[str]:
        return [self._children(child) for child in node.findall(M + tag)]

    # -- leaves -------------------------------------------------------------------
    def _do_r(self, node) -> str:
        props = node.find(M + "rPr")
        plain = _val(props, "sty") == "p" if props is not None else False
        text = "".join(t.text or "" for t in node.iter(M + "t"))
        return escape_text(text, plain=plain)

    def _do_t(self, node) -> str:
        return escape_text(node.text or "")

    # -- structures ---------------------------------------------------------------
    def _do_oMath(self, node) -> str:
        return self._children(node)

    def _do_oMathPara(self, node) -> str:
        return " \\\\ ".join(self._children(m) for m in node.findall(M + "oMath"))

    def _do_f(self, node) -> str:
        props = node.find(M + "fPr")
        kind = _val(props, "type", "bar")
        num, den = self._e(node, "num"), self._e(node, "den")
        if kind == "lin":
            return f"{{{num}}}/{{{den}}}"
        if kind == "skw":
            return f"{{}}^{{{num}}}/_{{{den}}}"
        if kind == "noBar":
            return f"\\binom{{{num}}}{{{den}}}"
        return f"\\frac{{{num}}}{{{den}}}"

    def _do_sSub(self, node) -> str:
        return f"{self._e(node)}_{{{self._e(node, 'sub')}}}"

    def _do_sSup(self, node) -> str:
        return f"{self._e(node)}^{{{self._e(node, 'sup')}}}"

    def _do_sSubSup(self, node) -> str:
        return f"{self._e(node)}_{{{self._e(node, 'sub')}}}^{{{self._e(node, 'sup')}}}"

    def _do_sPre(self, node) -> str:
        return f"{{}}_{{{self._e(node, 'sub')}}}^{{{self._e(node, 'sup')}}}{self._e(node)}"

    def _do_rad(self, node) -> str:
        props = node.find(M + "radPr")
        deg = self._e(node, "deg")
        hide = _val(props, "degHide") in ("1", "on", "true")
        body = self._e(node)
        if deg and not hide:
            return f"\\sqrt[{deg}]{{{body}}}"
        return f"\\sqrt{{{body}}}"

    def _do_d(self, node) -> str:
        props = node.find(M + "dPr")
        beg = _val(props, "begChr", "(")
        end = _val(props, "endChr", ")")
        sep = _val(props, "sepChr", "|")
        parts = self._e_all(node)
        # equation array inside braces -> cases
        if beg == "{" and end in (" ", "") and len(parts) == 1 and node.find(f"{M}e/{M}eqArr") is not None:
            return parts[0].replace("\\begin{aligned}", "\\begin{cases}").replace("\\end{aligned}", "\\end{cases}")
        left = _DELIMS.get(beg or "", beg or "(")
        right = _DELIMS.get(end or "", end or ")")
        inner = (escape_text(sep) if sep else "|").join(parts)
        return f"\\left{left} {inner} \\right{right}"

    def _do_nary(self, node) -> str:
        props = node.find(M + "naryPr")
        chr_ = _val(props, "chr", "∫")
        op = _NARY.get(chr_ or "∫", _SYMBOLS.get(chr_ or "", chr_ or "\\int").strip())
        sub_hide = _val(props, "subHide") in ("1", "on", "true")
        sup_hide = _val(props, "supHide") in ("1", "on", "true")
        sub, sup = self._e(node, "sub"), self._e(node, "sup")
        out = op
        if sub and not sub_hide:
            out += f"_{{{sub}}}"
        if sup and not sup_hide:
            out += f"^{{{sup}}}"
        return out + " " + self._e(node)

    def _do_func(self, node) -> str:
        name = self._e(node, "fName").strip()
        bare = name.replace("\\mathrm{", "").replace("}", "").strip()
        m = re.match(r"^([a-z]+)(.*)$", bare)
        if m and m.group(1) in _FUNCS:
            name = f"\\{m.group(1)}{m.group(2)}"
        return f"{name} {self._e(node)}"

    def _do_fName(self, node) -> str:
        return self._children(node)

    def _do_acc(self, node) -> str:
        props = node.find(M + "accPr")
        chr_ = _val(props, "chr", "̂")
        template = _ACCENTS.get(chr_ or "̂", "\\hat{{{0}}}")
        return template.format(self._e(node))

    def _do_bar(self, node) -> str:
        props = node.find(M + "barPr")
        pos = _val(props, "pos", "bot")
        body = self._e(node)
        return f"\\overline{{{body}}}" if pos == "top" else f"\\underline{{{body}}}"

    def _do_box(self, node) -> str:
        return self._e(node)

    def _do_borderBox(self, node) -> str:
        return f"\\boxed{{{self._e(node)}}}"

    def _do_groupChr(self, node) -> str:
        props = node.find(M + "groupChrPr")
        chr_ = _val(props, "chr", "⏟")
        template = _GROUP_CHR.get(chr_ or "⏟", "\\underbrace{{{0}}}")
        return template.format(self._e(node))

    def _do_limLow(self, node) -> str:
        return f"{self._e(node)}_{{{self._e(node, 'lim')}}}"

    def _do_limUpp(self, node) -> str:
        return f"{self._e(node)}^{{{self._e(node, 'lim')}}}"

    def _do_lim(self, node) -> str:
        return self._children(node).replace("\\rightarrow ", "\\to ")

    def _do_m(self, node) -> str:
        rows = []
        for row in node.findall(M + "mr"):
            rows.append(" & ".join(self._e_all(row)))
        return "\\begin{matrix} " + " \\\\ ".join(rows) + " \\end{matrix}"

    def _do_eqArr(self, node) -> str:
        rows = []
        for part in self._e_all(node):
            text = part.strip()
            # split "expr, condition" so cases align on the comma
            for sep in (",", "，"):
                idx = text.find(sep)
                if idx > 0 and "&" not in text:
                    text = text[:idx].rstrip() + ", & " + text[idx + 1 :].lstrip()
                    break
            rows.append(text)
        return "\\begin{aligned} " + " \\\\ ".join(rows) + " \\end{aligned}"

    def _do_phant(self, node) -> str:
        return f"\\phantom{{{self._e(node)}}}"


_CONVERTER = OmmlConverter()


def omml_to_latex(node) -> str:
    """Convert an ``m:oMath`` / ``m:oMathPara`` lxml element to LaTeX (compact whitespace)."""

    latex = _CONVERTER.convert(node) if node.tag == M + "oMathPara" or node.tag == M + "oMath" else _CONVERTER._node(node)
    if node.tag == M + "oMathPara":
        latex = _CONVERTER._do_oMathPara(node)
    elif node.tag == M + "oMath":
        latex = _CONVERTER._children(node)
    latex = re.sub(r"[ \t]+", " ", latex).strip()
    latex = re.sub(r"\s+([_^{}])", r"\1", latex)
    return latex


def omml_string_to_latex(xml: str) -> str:
    return omml_to_latex(etree.fromstring(xml))


__all__ = ["M_NS", "omml_string_to_latex", "omml_to_latex"]
