#!/usr/bin/env python3
"""Schema-driven carve with structured diagnostics and atomised options.

Reworked after studying the question-bank importer's DOCX pipeline. Three of
its habits are worth keeping regardless of document type:

* rules live in a schema file, so adding a role or a tag is a data change and
  the rule set can be diffed and reviewed;
* an option marker counts only on a boundary — start of text, whitespace or an
  ideographic space — and markers are searched in order, so B is looked for
  only after A was found. A bare regex sweep accepts 「B」 inside a word and
  accepts out-of-order labels; this does not;
* problems are diagnostics with a code, a severity and a locator, collected
  and reported, not printed.

What is deliberately not adopted is its document model. That importer handles
question papers, which are flat lists of questions; a 讲义 is 72% question and
28% exposition, banners and callouts, and flattening it would drop that 28%.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree

V_NS = "urn:schemas-microsoft-com:vml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# 数学公式(OMML)→ LaTeX 文本。上游 2026-08-22 并入:
# 题库那份 vendored 副本里带着它(标记 [quiz-omml]),上游没有——于是同一个引擎有两份行为。
# 不并回来,那份副本就永远退不掉。
#
# ★为什么它不该是"可选的锦上添花":公式里的字符也是字符。
#   引擎看不见公式,题干里的 v=s/t 就凭空少一截——角色识别、选项切分、图的定位
#   都按偏移算,少一截就全错位。而「每个字符都有归属」这条判准,公式一样要算。
#
# ★但默认关:开着会改变既有册的原子与偏移,进而动 GATE_RECONSTRUCTIBLE。
#   由模板表 `omathAsText` 选中(算法形状为代码,要不要用它是数据的事)。
from omml import M_NS as _M_NS, omml_to_latex as _omml_to_latex

M_ = f"{{{_M_NS}}}"
OMATH_AS_TEXT = False   # 由 Schema(模板表 omathAsText)打开

W = f"{{{W_NS}}}"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


SUBSCRIPT_OPEN, SUBSCRIPT_SHUT = "\ue000", "\ue001"


def expand_transcription(text: str) -> str:
    """「MnO_2」 → MnO⟪2⟫ with private-use sentinels around the subscript.

    The transcription has to survive as plain text through role detection and
    the coverage checks, yet still tell the compiler which glyphs are chemical
    subscripts. Sentinels do both: invisible to every text rule, and a clean
    split for the run builder.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "_^" and index + 1 < len(text):
            index += 1
            run = ""
            if text[index] == "{":
                shut = text.index("}", index)
                run, index = text[index + 1:shut], shut + 1
            elif text[index].isdigit():
                # A numeric subscript runs to the end of the digits; 「H_2O」
                # must not swallow the O.
                while index < len(text) and (text[index].isdigit()
                                             or text[index] == "."):
                    run += text[index]
                    index += 1
            else:
                run, index = text[index], index + 1
            out.append(SUBSCRIPT_OPEN + run + SUBSCRIPT_SHUT)
            continue
        out.append(char)
        index += 1
    return "".join(out)


class MediaLibrary:
    """Extract the pictures themselves, keyed by content hash.

    An atom that only knows 「there is a figure here」 is not reusable: a
    question bank, a graph or a quiz app all need the asset. Content hashing
    also collapses the same figure reprinted across lessons into one file.
    """

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self.written: dict[str, str] = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def store(self, payload: bytes, target: str) -> dict[str, str]:
        digest = hashlib.sha256(payload).hexdigest()[:16]
        name = digest + Path(target).suffix.lower()
        if self.directory and digest not in self.written:
            (self.directory / name).write_bytes(payload)
            self.written[digest] = name
        return {"hash": digest, "file": name}


class Diagnostics:
    """Codes, severities and locators — collected, never printed."""

    def __init__(self, definitions: dict[str, Any]) -> None:
        self.definitions = definitions
        self.items: list[dict[str, Any]] = []
        # 「body/p[203]」 repeats in all ten documents, so a locator alone does
        # not identify anything. The document being read is stamped on every
        # item instead of threaded through every call site.
        self.document = ""

    def add(self, code: str, locator: str, note: str = "", **extra: Any) -> None:
        definition = self.definitions.get(code, {})
        self.items.append({
            "code": code,
            "severity": definition.get("severity", "error"),
            "detail": definition.get("detail", ""),
            "document": self.document,
            "locator": locator, "note": note, **extra})

    def counts(self) -> dict[str, int]:
        return dict(Counter(item["code"] for item in self.items))

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item["severity"] == "error")


class Schema:
    def __init__(self, data: dict[str, Any]) -> None:
        global OMATH_AS_TEXT
        # 公式转文本要不要开,由模板表说了算——算法形状是代码,选不选是数据。
        OMATH_AS_TEXT = bool(data.get("omathAsText"))
        self.raw = data
        markers = data["optionMarkers"]
        # The role patterns used to spell the marker set out again, as
        # 「[ABCD]」, while optionMarkers said ABCDEFGH. A fifth option on its
        # own line was therefore never an option — it kept the shape of a list
        # item, sat outside its question's option group, and printed alone
        # under a row of two. One set of characters, named once, substituted
        # into whichever patterns ask for it.
        self.roles = [
            {**role,
             "regex": re.compile(role["pattern"].replace(
                 "{optionChars}", re.escape(markers["chars"])))}
            for role in sorted(data["roles"], key=lambda r: r["sortOrder"])]
        self.banners = data["sectionBanners"]
        self.sub_modules = tuple(data["subModules"])
        self.callouts = tuple(data["calloutHeads"])
        self.option_chars = markers["chars"]
        self.option_separators = tuple(markers["separators"])
        self.boundary_chars = tuple(markers["boundaryChars"])
        self.tags = {t["id"]: re.compile(t["pattern"]) for t in data["tags"]}
        # 角色也可以携带标签。有的源不用【答案】/【详解】,而用教科书体例
        # 「分析：…」「解：…」「答：…」——这类**必须按角色认**(角色是逐段匹配,天然锚行首),
        # 不能写进 tags:tags 作用在 joined(段落直接拼接、无分隔符)上,锚不了行首,
        # 而「分析：」在段中另有出现(小问里的「（1）分析：反射光线…」),
        # 「答：」段中还出现在**填空**里(「答：______；原因是______」)。非锚定判据会把它们全吃掉。
        self.role_tags = {r["id"]: r["tag"] for r in data["roles"] if r.get("tag")}
        self.diagnostics = data["diagnostics"]
        arrows = data.get("shapeArrows", {})
        self.arrow_geometries = tuple(arrows.get("geometries", ()))
        self.arrow_render = arrows.get("render", "——{condition}→")
        self.arrow_plain = arrows.get("renderWithoutCondition", "——→")
        self.arrow_carry = int(arrows.get("carryForward", 0))
        self.condition_arrows = (data.get("conditionArrows") or {}).get("objects") or {}
        objects = data.get("embeddedObjects") or {}
        self.embedded = {k: v for k, v in objects.items()}
        self.field_renderings = {k: v["text"]
                                 for k, v in data.get("fieldRenderings", {}).items()}
        self.question_number = re.compile(data["questionNumberRegex"])
        # 合并答案的判据。源里偶有把好几道题的答案写在一个【答案】下的写法
        # (「1．A  2．B  3．C」),引擎据此按题号拆开。
        # ★首版的判据是 `(?:^|\s)(\d{1,2})[．.]\s*`,它**同时**命中小数与量值:
        # 「0.1」「2.3」「5.5」「10.0cm」「3.1」都被读成题号。
        # 2026-08-20 全量实测(讲义册 20 讲 + 单元卷 5 卷,共 519 条【答案】):
        # 走进这条分支的 8 条**全部是误判**,真正的合并答案 0 条。
        # 一条 100% 错的判据不能靠删掉了事(别的册可能真有合并答案),
        # 所以收紧而不是移除:分隔符后面紧跟数字的,不是题号,是小数。
        splitting = data.get("answerSplitting") or {}
        self.answer_split = re.compile(
            splitting.get("pattern", r"(?:^|\s)(\d{1,2})[．.]\s*"))
        self.answer_split_reject_digit = bool(
            splitting.get("rejectWhenFollowedByDigit", True))

    def role_of(self, text: str) -> str | None:
        for role in self.roles:
            if role["regex"].match(text):
                return role["id"]
        return None

    def kind_of(self, role_id: str) -> str | None:
        for role in self.roles:
            if role["id"] == role_id:
                return role["kind"]
        return None


def split_options(text: str, schema: Schema, diagnostics: Diagnostics,
                  locator: str,
                  image_refs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Atomise a paragraph into one object per option.

    Ported from the importer's GatherOptionBoundaries: a marker must sit on a
    boundary, and the search for each marker starts after the previous one, so
    the labels can only come out in order. A gap is a warning; a label found
    out of order is an error rather than a silent reordering.
    """
    boundaries: list[tuple[int, str]] = []
    search_from = 0
    for char in schema.option_chars:
        found = -1
        cursor = search_from
        while cursor < len(text) - 1:
            index = text.find(char, cursor)
            if index < 0 or index + 1 >= len(text):
                break
            previous = text[index - 1] if index else ""
            before = text[index - 2] if index > 1 else ""
            # Reject only a preceding ASCII letter. Requiring whitespace loses
            # every run-together option; also rejecting digits loses every
            # option that follows a formula or a ratio — 「A．N2B．SO2」.
            #
            # One letter standing alone is an option body, not a word. 「A．A
            # B．B C．C D．D」 and 「A．操作 a B．操作 b」 are how an ordering
            # question writes its choices, and rejecting the letter before each
            # marker made the whole row read as a single option A — 4 rows
            # here. A letter that follows another letter or a digit is still
            # part of a word: 「A．CO B．CO2」 run together gives 「…COB．」,
            # and that must stay rejected.
            alone = not (before.isascii() and before.isalnum())
            on_boundary = not (previous.isascii() and previous.isalpha()) or alone
            if on_boundary and text[index + 1] in schema.option_separators:
                found = index
                break
            cursor = index + 1
        if found < 0:
            if boundaries:
                break          # the run of options has ended
            continue           # nothing found yet: this label simply is absent
        boundaries.append((found, char))
        search_from = found + 2

    if not boundaries:
        return []
    options: list[dict[str, Any]] = []
    refs = image_refs or []
    # A figure belongs to the label that precedes it, so the picture window for
    # an option opens *after* its 「A．」 and closes after the next one — not at
    # the marker positions themselves. Keyed on the marker start instead, every
    # figure lands one option late and the last one falls off the row entirely.
    def where(ref: dict[str, Any]) -> int:
        """A figure's position in the same string the boundaries index.

        Its own offset counts the raw runs; the boundaries were found in the
        collapsed text. Where the two disagree the figures land against the
        wrong options — or off the end of the row.
        """
        return int(ref.get("streamOffset", ref.get("offset") or 0))

    stray = [r for r in refs if where(r) < boundaries[0][0] + 2]
    # Text in front of the first marker, when that marker is not the first
    # label, is an option whose label the source left out. One row here reads
    # 「①②  B．②③  C．全部  D．①②④」 — 「①②」 is option A. Dropping it as a
    # lead fragment would lose an answer; leaving it to be judged by its first
    # character made the whole row a circled note.
    head_start, head_label = boundaries[0]
    head = text[:head_start].strip()
    position = schema.option_chars.index(head_label) if head_label in schema.option_chars else 0
    if head and position:
        missing = schema.option_chars[position - 1]
        # What is missing may be only the separator. 「A温度 B．反应物的浓度」
        # has its A; putting the label back in front of it prints 「A．A温度」.
        raw = text[:head_start]
        start = len(raw) - len(raw.lstrip())
        if head[:1] == missing:
            trimmed = head[1:].lstrip("．.、 　")
            start += len(head) - len(trimmed)
            head = trimmed
        diagnostics.add("OPTION_LABEL_MISSING", locator,
                        f"补出 {missing}．,正文「{head[:12]}」")
        options.append({"label": missing, "text": head,
                        "locator": f"{locator}#{missing}",
                        "range": [start, head_start], "labelRecovered": True,
                        "images": len(stray), "figures": stray})
    elif stray:
        options.append({"label": "", "text": "", "locator": f"{locator}#lead",
                        "images": len(stray), "figures": stray,
                        "leadFragment": True})
    for position, (start, label) in enumerate(boundaries):
        last = position + 1 == len(boundaries)
        end = len(text) if last else boundaries[position + 1][0]
        window = len(text) + len(refs) + 1 if last else end + 2
        owned = [r for r in refs if start + 2 <= where(r) < window]
        options.append({
            "label": label, "text": text[start + 2:end].strip(),
            "locator": f"{locator}#{label}",
            # Where this option sits in the row's text. The marks that make a
            # subscript or a fill-in line live on the runs, and a flat slice
            # throws them away — 1367 options came out as plain body text.
            "range": [start + 2, end],
            "images": len(owned), "figures": owned})
    return options


def run_marks(run) -> dict[str, Any]:
    """The style fingerprint of one run.

    Character semantics live here and nowhere else: the underline that makes a
    fill-in line, the vertAlign that makes a chemical subscript, the colour and
    weight that mark an example label. Flattening a paragraph to a string threw
    all of it away — 2143 of the 8938 runs in this batch carry one of these,
    including 1321 fill-in lines in a fill-in handout.
    """
    properties = run.find(W + "rPr")
    if properties is None:
        return {}
    marks: dict[str, Any] = {}
    for tag, key in (("b", "bold"), ("i", "italic"), ("u", "underline"),
                     ("color", "color"), ("sz", "size"),
                     ("highlight", "highlight"), ("vertAlign", "vertAlign"),
                     ("em", "emphasisMark")):
        node = properties.find(W + tag)
        if node is None:
            continue
        value = node.get(W + "val")
        if tag in ("b", "i"):
            if (value or "on") not in ("0", "false", "off"):
                marks[key] = True
            continue
        if value and value not in ("auto", "none"):
            marks[key] = value
    shading = properties.find(W + "shd")
    if shading is not None and (shading.get(W + "fill") or "auto") != "auto":
        marks["shading"] = shading.get(W + "fill")
    return marks


def paragraph_runs(node, renderings: dict[str, str] | None = None
                   ) -> list[dict[str, Any]]:
    """The paragraph's own runs, in order, each with its fingerprint.

    Same traversal as own_text — fields resolved, Fallback and nested
    paragraphs skipped — but the marks travel with the text instead of being
    discarded at the first join.
    """
    runs: list[dict[str, Any]] = []
    table = renderings or {}
    state: list[Any] = [None, [], False]

    def walk(current, root: bool) -> None:
        for child in current:
            if child.tag == f"{{{MC_NS}}}Fallback":
                continue
            if child.tag == W + "p" and not root:
                continue
            if OMATH_AS_TEXT and child.tag in (M_ + "oMath", M_ + "oMathPara"):
                latex = _omml_to_latex(child)
                if latex:
                    runs.append({"text": latex, "marks": {"math": True, "display": child.tag == M_ + "oMathPara"}})
                continue
            if child.tag == W + "r":
                marker = child.find(W + "fldChar")
                if marker is not None:
                    phase = marker.get(W + "fldCharType")
                    if phase == "begin":
                        state[0], state[1], state[2] = "instruction", [], False
                    elif phase == "separate":
                        state[0] = "result"
                    elif phase == "end":
                        if state[0] and not state[2]:
                            rendered = table.get("".join(state[1]).strip(), "")
                            if rendered:
                                runs.append({"text": rendered,
                                             "marks": run_marks(child)})
                        state[0], state[1], state[2] = None, [], False
                    continue
                if state[0] == "instruction":
                    found = child.find(W + "instrText")
                    if found is not None:
                        state[1].append(found.text or "")
                    continue
                body = "".join(t.text or "" for t in child.findall(W + "t"))
                if body and state[0] == "result":
                    state[2] = True
                if body:
                    runs.append({"text": body, "marks": run_marks(child)})
                continue
            walk(child, False)

    walk(node, True)
    return runs


def own_text(node, renderings: dict[str, str] | None = None,
             collapse: bool = True) -> str:
    """The paragraph's own text, with Word fields resolved.

    A field can render text that is stored nowhere: an EQ field keeps only its
    typesetting instruction, so 「氢气 + 氧气 ——点燃→ 水」 extracts as 「氢气 +
    氧气 水」 — the arrow and its reaction condition vanish silently. Fields
    that do carry a result (the ①②③ numbering here) are read from the result
    as usual; the rest come from a governed table, and an unlisted one is
    reported rather than dropped.
    """
    pieces: list[str] = []
    table = renderings or {}
    state: list[Any] = [None, [], False]   # phase, instruction, saw result text

    def walk(current, root: bool) -> None:
        for child in current:
            if child.tag == f"{{{MC_NS}}}Fallback":
                continue
            if child.tag == W + "p" and not root:
                continue
            if OMATH_AS_TEXT and child.tag in (M_ + "oMath", M_ + "oMathPara"):
                pieces.append(_omml_to_latex(child))
                continue
            if child.tag == W + "r":
                marker = child.find(W + "fldChar")
                if marker is not None:
                    phase = marker.get(W + "fldCharType")
                    if phase == "begin":
                        state[0], state[1], state[2] = "instruction", [], False
                    elif phase == "separate":
                        state[0] = "result"
                    elif phase == "end":
                        if state[0] and not state[2]:
                            pieces.append(table.get("".join(state[1]).strip(), ""))
                        state[0], state[1], state[2] = None, [], False
                    continue
                if state[0] == "instruction":
                    found = child.find(W + "instrText")
                    if found is not None:
                        state[1].append(found.text or "")
                    continue
                body = "".join(t.text or "" for t in child.findall(W + "t"))
                if body and state[0] == "result":
                    state[2] = True
                pieces.append(body)
                continue
            walk(child, False)

    walk(node, True)
    joined = "".join(pieces)
    # The blank run *is* the information when an arrow was drawn over it, so
    # whitespace can only be collapsed after the arrow has been put back.
    return re.sub(r"[\s\xa0]+", " ", joined).strip() if collapse else joined


def unrendered_fields(node, renderings: dict[str, str]) -> list[str]:
    """Field instructions that render nothing and are not in the table."""
    missing: list[str] = []
    phase, instruction, saw = None, [], False
    for run in node.iter(W + "r"):
        marker = run.find(W + "fldChar")
        if marker is not None:
            kind = marker.get(W + "fldCharType")
            if kind == "begin":
                phase, instruction, saw = "instruction", [], False
            elif kind == "separate":
                phase = "result"
            elif kind == "end" and phase:
                text = "".join(instruction).strip()
                if not saw and text not in renderings:
                    missing.append(text)
                phase, instruction, saw = None, [], False
            continue
        if phase == "instruction":
            found = run.find(W + "instrText")
            if found is not None:
                instruction.append(found.text or "")
        elif phase == "result" and any((t.text or "") for t in run.findall(W + "t")):
            saw = True
    return missing


def nested_text(node) -> str:
    return re.sub(r"[\s\xa0]+", " ",
                  "".join(t.text or "" for t in node.iter(W + "t"))).strip()


def source_size_pt(paragraph, fallback: float) -> float:
    """The commonest body size inside this paragraph, weighted by characters.

    A figure is scaled against the type it actually sits beside. Taking the
    paragraph's first sized run instead lets one short superscript or a stray
    16pt fragment decide the scale for the whole line; taking a document-wide
    figure ignores that a paragraph can legitimately run at another size.
    """
    weights: Counter = Counter()
    for run in paragraph.iter(W + "r"):
        text = "".join(t.text or "" for t in run.findall(W + "t"))
        if not text.strip():
            continue
        found = run.find(f"{W}rPr/{W}sz")
        size = (int(found.get(W + "val")) / 2
                if found is not None and found.get(W + "val") else None)
        if size is None:
            paragraph_size = paragraph.find(f"{W}pPr/{W}rPr/{W}sz")
            size = (int(paragraph_size.get(W + "val")) / 2
                    if paragraph_size is not None
                    and paragraph_size.get(W + "val") else fallback)
        weights[size] += len(text.strip())
    return weights.most_common(1)[0][0] if weights else fallback


def default_size_pt(package) -> float:
    try:
        root = etree.fromstring(package.read("word/styles.xml"))
    except KeyError:
        return 12.0
    found = root.find(f".//{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
    return int(found.get(W + "val")) / 2 if found is not None else 12.0


def geometry_of(node) -> dict[str, Any]:
    """Display size and crop. The stored bitmap is not what the page shows.

    26 of these figures are cropped with srcRect — restoring one without its
    crop puts the whole plate back where a detail belonged, and the result is
    unreadable at the size the page gives it. Size and crop travel with the
    asset or the atom cannot be laid out again.
    """
    found: dict[str, Any] = {}
    # A VML shape states its size in its CSS style, not in an extent element.
    # Without it 312 of the figures had no size at all and fell back to the
    # compiler's full-width default — a 7mm arrow drawn 150mm wide.
    for shape in node.iter(f"{{{V_NS}}}shape"):
        style = shape.get("style") or ""
        sizes = dict(re.findall(r"(width|height):\s*([\d.]+)pt", style))
        if "width" in sizes:
            found["widthEmu"] = str(int(float(sizes["width"]) * 12700))
            if "height" in sizes:
                found["heightEmu"] = str(int(float(sizes["height"]) * 12700))
            break
    extent = node.find(f".//{{{WP_NS}}}extent")
    if extent is None:
        extent = node.find(f".//{{{A_NS}}}ext")
    if extent is not None and extent.get("cx"):
        found["widthEmu"] = extent.get("cx")
        found["heightEmu"] = extent.get("cy")
    crop = node.find(f".//{{{A_NS}}}srcRect")
    if crop is not None:
        edges = {edge: crop.get(edge) for edge in ("l", "t", "r", "b")
                 if crop.get(edge)}
        if edges:
            found["crop"] = edges
    anchor = node.find(f"{{{WP_NS}}}anchor")
    found["floating"] = anchor is not None
    if anchor is not None:
        # A floating figure is placed, not merely present: its offsets say
        # where, and its wrap says how the text behaves around it. Recording
        # only 「floating: true」 would leave the layout step nothing to rebuild.
        place: dict[str, Any] = {
            key: anchor.get(key) for key in
            ("behindDoc", "allowOverlap", "layoutInCell", "locked",
             "relativeHeight", "distT", "distB", "distL", "distR")
            if anchor.get(key) is not None}
        for axis, tag in (("horizontal", "positionH"), ("vertical", "positionV")):
            node_axis = anchor.find(f"{{{WP_NS}}}{tag}")
            if node_axis is None:
                continue
            offset = node_axis.find(f"{{{WP_NS}}}posOffset")
            align = node_axis.find(f"{{{WP_NS}}}align")
            place[axis] = {
                "relativeFrom": node_axis.get("relativeFrom"),
                "offsetEmu": (offset.text or "").strip() if offset is not None else None,
                "align": (align.text or "").strip() if align is not None else None}
        for child in anchor:
            name = etree.QName(child).localname
            if name.startswith("wrap"):
                place["wrap"] = name
                # wrapTight and wrapThrough carry a polygon; re-emitting the
                # bare element makes a file Word refuses to open. The element
                # travels verbatim instead of being described.
                place["wrapXml"] = etree.tostring(child, encoding="unicode")
                break
        found["anchor"] = place
    return found


def picture_refs(paragraph, resolve, schema: "Schema") -> list[dict[str, Any]]:
    """Each picture in the paragraph: where it sits, and which asset it is.

    The offset is needed to say which option or sub-question owns the figure —
    a count alone only says 「this question has 3 pictures」, which is exactly
    the kind of unexpressed ownership that let figures drift out of their cells.
    """
    refs: list[dict[str, Any]] = []
    cursor = 0

    def relations_all(node) -> list[str]:
        """节点下**全部**位图引用,按文档顺序。

        ★relation() 只返回第一个。一个 w:drawing 里装两张图(组合图形)时,
          第二张连 work/media/ 都进不去——它在 carve 阶段就没了,而后面每一道
          门看到的都是「已经只有一张」的世界,谁也不会报。
          2026-08-20 A16「力」实测:p[56] 一个 drawing 里两个 blip,
          分别是 F₂(书受支持力,向上)与 F₁(桌面受压力,向下)——**讲力的相互性的一对**。
          留下一张单独看会误导。由 s5b 覆盖率门抓出(1 个未归属源对象)。
          旧册 A10-A14 实测 0 个多图 drawing,已定稿成品不受影响。
        """
        out: list[str] = []
        for blip in node.iter(f"{{{A_NS}}}blip"):
            found = blip.get(f"{{{R_NS}}}embed")
            if found and found not in out:
                out.append(found)
        for data in node.iter(f"{{{V_NS}}}imagedata"):
            found = data.get(f"{{{R_NS}}}id")
            if found and found not in out:
                out.append(found)
        return out

    def picture_geometry(node, rid: str) -> dict[str, Any]:
        """这一张图自己的尺寸。组合图形里每张各有各的 extent。

        取不到就退回整个 drawing 的几何——与单图时代的行为一致,不改既有输出。
        """
        for blip in node.iter(f"{{{A_NS}}}blip"):
            if blip.get(f"{{{R_NS}}}embed") != rid:
                continue
            holder = blip
            while holder is not None and not holder.tag.endswith("}pic"):
                holder = holder.getparent()
            if holder is not None:
                own = geometry_of(holder)
                if own.get("widthEmu"):
                    return own
            break
        return geometry_of(node)

    def describe(node) -> dict[str, Any]:
        """A w:drawing is not necessarily a picture: 74 of them are wsp/wgp
        shapes — text boxes and grouped vectors — which have no bitmap to
        export. Calling those 「missing assets」 would be a false alarm; they
        are a different kind of object and their text is already captured."""
        found = relation(node)
        if found:
            return {"kind": "picture", **(resolve(found) or {}),
                    **geometry_of(node)}
        if any(g.get("prst") in schema.arrow_geometries
               for g in node.iter(f"{{{A_NS}}}prstGeom")):
            # A reaction arrow is part of the equation, not decoration.
            return {"kind": "arrow"}
        return {"kind": "shape"}

    def relation(node) -> str | None:
        for blip in node.iter(f"{{{A_NS}}}blip"):
            found = blip.get(f"{{{R_NS}}}embed")
            if found:
                return found
        for data in node.iter(f"{{{V_NS}}}imagedata"):
            found = data.get(f"{{{R_NS}}}id")
            if found:
                return found
        return None

    def walk(current, root: bool) -> None:
        nonlocal cursor
        for child in current:
            if child.tag == f"{{{MC_NS}}}Fallback":
                continue
            if child.tag == W + "p" and not root:
                continue
            if OMATH_AS_TEXT and child.tag in (M_ + "oMath", M_ + "oMathPara"):
                cursor += len(_omml_to_latex(child))
                continue
            if child.tag == W + "r":
                for node in child:
                    if node.tag in (W + "drawing", W + "pict", W + "object"):
                        refs.append({"offset": cursor, **describe(node)})
                        # 第一张沿用 describe(与单图时代逐字节相同,不动既有输出);
                        # 同一 drawing 里余下的图各补一条,否则它们在此静默消失。
                        for extra in relations_all(node)[1:]:
                            refs.append({"offset": cursor, "kind": "picture",
                                         **(resolve(extra) or {}),
                                         **picture_geometry(node, extra),
                                         "sharesDrawingWith": relations_all(node)[0]})
                    elif node.tag == f"{{{MC_NS}}}AlternateContent":
                        # Word wraps 71 of the figures in a Choice/Fallback
                        # pair. Only direct children of the run were inspected,
                        # so every wrapped figure was invisible — take the
                        # Choice branch, which is what Word actually renders.
                        choice = node.find(f"{{{MC_NS}}}Choice")
                        for inner in (choice.iter() if choice is not None else ()):
                            if inner.tag in (W + "drawing", W + "pict"):
                                refs.append({"offset": cursor, **describe(inner)})
                                for extra in relations_all(inner)[1:]:
                                    refs.append({"offset": cursor, "kind": "picture",
                                                 **(resolve(extra) or {}),
                                                 **picture_geometry(inner, extra),
                                                 "sharesDrawingWith": relations_all(inner)[0]})
                    elif node.tag == W + "t":
                        cursor += len(node.text or "")
                continue
            walk(child, False)

    walk(paragraph, True)
    return refs


def picture_refs_deep(node, resolve) -> list[dict[str, Any]]:
    """Every picture anywhere under a node, Fallback excluded.

    A table's pictures sit several levels down inside cells, where the
    paragraph walk never reaches. Offsets are meaningless across cells, so the
    asset handle is all that is recorded — but recorded it must be: 77 of the
    80 table pictures fall inside questions.
    """
    refs: list[dict[str, Any]] = []

    def walk(current) -> None:
        for child in current:
            if child.tag == f"{{{MC_NS}}}Fallback":
                continue
            if child.tag in (W + "drawing", W + "pict", W + "object"):
                found = None
                for blip in child.iter(f"{{{A_NS}}}blip"):
                    found = blip.get(f"{{{R_NS}}}embed") or found
                    if found:
                        break
                if not found:
                    for data in child.iter(f"{{{V_NS}}}imagedata"):
                        found = data.get(f"{{{R_NS}}}id") or found
                        if found:
                            break
                refs.append({"offset": None,
                             **({"kind": "picture", **(resolve(found) or {}),
                                 **geometry_of(child)}
                                if found else {"kind": "shape",
                                               **geometry_of(child)})})
                continue
            walk(child)

    walk(node)
    return refs


def shape_arrows(paragraph, schema: Schema) -> list[str]:
    """Reaction arrows drawn as floating line shapes.

    「碳酸钙 + 盐酸 ——→ 氯化钙」 keeps the arrow as a vector line anchored over
    the line, with the reaction condition as the shape's own text. The text run
    holds only a run of spaces, so the equation extracts as 「碳酸钙 + 盐酸
    氯化钙」 — chemically a different statement.
    """
    found: list[str] = []
    for geometry in paragraph.iter(f"{{{A_NS}}}prstGeom"):
        if geometry.get("prst") not in schema.arrow_geometries:
            continue
        branch = geometry
        while branch is not None:
            if branch.tag == f"{{{MC_NS}}}Fallback":
                break          # the compatibility copy, not a second arrow
            branch = branch.getparent()
        if branch is not None:
            continue
        holder = geometry
        while holder is not None and holder.tag not in (W + "drawing", W + "pict"):
            holder = holder.getparent()
        condition = "".join(t.text or "" for t in holder.iter(W + "t")).strip() \
            if holder is not None else ""
        found.append(schema.arrow_render.format(condition=condition)
                     if condition else schema.arrow_plain)
    return found


def is_blank(piece: dict) -> bool:
    """An underlined run with nothing but spaces in it: an answer blank."""
    text = piece.get("text") or ""
    return bool(text) and not text.strip() and bool(
        (piece.get("marks") or {}).get("underline"))


def place_arrows(text: str, arrows: list[str],
                 limit: int = 1) -> tuple[str, list[str]]:
    """Put the arrows back where the blank runs are; the rest come back."""
    text, stranded, _ = fill_blanks(text, arrows, limit)
    return text, stranded


def fill_blanks(text: str, arrows: list[str], limit: int = 1
                ) -> tuple[str, list[str], list[tuple[int, int, str]]]:
    """Which blanks the arrows take, as offsets on the string handed in.

    A line carries as many arrows as were drawn over it, and no more. Filling
    every blank would eat the next line's arrow — 「水——通电→氢气↑——通电→+
    氧气↑」 — and leave the symbol equation below it bare; filling only ever one
    strands the second arrow of a line that has two, which is how 「文字表达式：
    磷+氧气　　五氧化二磷（符号表达式：P+O2　　P2O5）」 lost both of its 点燃
    arrows. The limit is the number of arrows the source anchored over this
    line, so it is counted rather than guessed.

    The offsets come back so the run stream can be given the same edit. Filling
    the flat text and the stream in two independent passes left them disagreeing
    whenever a blank straddled two runs — 「氧气」「 」「 五氧化二磷」 — because
    no single run then held a blank wide enough to match, and the arrow reached
    the text but never the runs the compiler prints.
    """
    # The widest blanks are the slots the arrows were drawn over. Taking the
    # first ones instead puts an arrow after the label — 「符号表达式：——通电→
    # H2O H2↑」 — because the spacing after 「：」 also reads as a gap.
    widest = sorted(re.finditer(r"\S([\s\u3000]{2,})\S", text),
                    key=lambda m: -len(m.group(1)))
    taking = sorted(widest[:max(0, min(limit, len(arrows)))],
                    key=lambda m: m.start(1))
    if not taking:
        return text, list(arrows), []
    edits = [(m.start(1), m.end(1), arrows[index])
             for index, m in enumerate(taking)]
    out: list[str] = []
    cursor = 0
    for begin, end, arrow in edits:
        out.append(text[cursor:begin])
        out.append(arrow)
        cursor = end
    out.append(text[cursor:])
    return "".join(out), arrows[len(edits):], edits


def splice_stream(stream: list[dict], edits: list[tuple[int, int, str]]) -> None:
    """Give the run stream the same edit the flat text got, by offset."""
    for begin, end, arrow in sorted(edits, reverse=True):
        cursor = 0
        for piece in stream:
            if "text" not in piece:
                continue
            head, tail = cursor, cursor + len(piece["text"])
            cursor = tail
            if head <= begin < tail or (begin <= head and tail <= end):
                inside_start = max(0, begin - head)
                inside_end = min(len(piece["text"]), end - head)
                piece["text"] = (piece["text"][:inside_start] + arrow
                                 + piece["text"][inside_end:])
                arrow = ""      # the rest of the span only loses its blanks
        continue


def picture_arrows(refs: list[dict[str, Any]], schema: Schema) -> list[str]:
    """Reaction arrows drawn as one small picture, condition and all.

    「点燃」 over an arrow is sometimes a floating line shape carrying the
    condition as its own text, which shapeArrows reads. Sometimes the whole
    thing is a 20×14pt bitmap with no text in it at all, and then there is
    nothing to read: the equation extracts as 「磷+氧气　　五氧化二磷」, which
    is a different statement, and the picture prints as a stray stamp floating
    above the line. Those are registered by content hash — read once, by a
    person, and recorded — and become text like any other transcription.
    """
    found: list[str] = []
    for ref in sorted(refs, key=lambda r: (r.get("anchor") or {}).get("x", 0)):
        entry = schema.condition_arrows.get(str(ref.get("hash") or ""))
        # Only the floating ones look for a slot; an inline arrow has already
        # been spliced where it stands.
        if not entry or not ref.get("anchor") or ref.get("kind") == "transcribed":
            continue
        condition = str(entry.get("condition") or "")
        found.append(schema.arrow_render.format(condition=condition)
                     if condition else schema.arrow_plain)
        ref["kind"] = "transcribed"
        ref["text"] = condition
    return found


def attribute(node, name: str) -> str | None:
    return node.get(W + name) if node is not None else None


def borders_of(properties) -> dict[str, dict[str, str]] | None:
    """Border edges as data. A table whose borders are dropped comes back as a
    different object on the page even when every character survives."""
    if properties is None:
        return None
    holder = properties.find(W + "tblBorders")
    if holder is None:
        holder = properties.find(W + "tcBorders")
    if holder is None:
        return None
    edges = {}
    for edge in holder:
        edges[etree.QName(edge).localname] = {
            key: value for key, value in (
                ("style", attribute(edge, "val")),
                ("size", attribute(edge, "sz")),
                ("colour", attribute(edge, "color"))) if value}
    return edges or None


def shading_of(properties) -> dict[str, str] | None:
    if properties is None:
        return None
    found = properties.find(W + "shd")
    if found is None:
        return None
    got = {key: value for key, value in (
        ("pattern", attribute(found, "val")),
        ("colour", attribute(found, "color")),
        ("fill", attribute(found, "fill"))) if value}
    return got or None


def read_table(table, schema: "Schema", diagnostics: "Diagnostics", locator: str,
               resolve, lists: dict) -> dict[str, Any]:
    """The table as a table: properties, grid, rows, cells — not flattened text.

    Flattening loses exactly what makes a table a table. 79 of the cells here
    carry gridSpan or vMerge, so a cell list without them cannot be laid out
    again: 「实验步骤 / 现象 / 结论」 collapses into one column of prose.
    """
    properties = table.find(W + "tblPr")
    grid = [attribute(column, "w")
            for column in table.findall(f"{W}tblGrid/{W}gridCol")]
    rows: list[dict[str, Any]] = []
    # An arrow that its own cell cannot hold belongs to the cell below it in
    # the same column: the source anchors an arrow above the line it serves,
    # and inside a table 「above」 is the row before. 「实验结论」 keeps its
    # arrow on the 「实验现象」 row and points down into the blank the student
    # writes in.
    waiting: dict[int, list[str]] = {}
    for row_index, row in enumerate(table.findall(W + "tr")):
        row_properties = row.find(W + "trPr")
        height = row_properties.find(W + "trHeight") if row_properties is not None else None
        cells: list[dict[str, Any]] = []
        for cell_index, cell in enumerate(row.findall(W + "tc")):
            cell_properties = cell.find(W + "tcPr")
            span = cell_properties.find(W + "gridSpan") if cell_properties is not None else None
            merge = cell_properties.find(W + "vMerge") if cell_properties is not None else None
            width = cell_properties.find(W + "tcW") if cell_properties is not None else None
            align = cell_properties.find(W + "vAlign") if cell_properties is not None else None
            paragraphs = []
            # A reaction arrow inside a cell is still a reaction arrow, and it
            # is anchored the way the body's are: on the paragraph *above* the
            # line it serves. 「过氧化氢 ——二氧化锰→ 水+氧气」 keeps its arrow on
            # the empty paragraph before it, so placing only within the
            # anchoring paragraph found nothing to fill and dropped it. The
            # blanks also have to be looked for before the whitespace is
            # collapsed — the gap the arrow was drawn over is exactly the run
            # of spaces that collapsing removes.
            pending: list[str] = waiting.pop(cell_index, [])
            for paragraph in cell.findall(W + "p"):
                # The runs, not just the words. A cell's text was read flat, so
                # every character mark inside a table was lost — all 163 of the
                # set's answer blanks that live in one printed with no line at
                # all, the same way the callouts lost their highlighting.
                runs = paragraph_runs(paragraph, schema.field_renderings)
                raw = "".join(run["text"] for run in runs)
                marker = lists.get(paragraph)
                # Where the text sits across the cell, not only down it. The
                # source centres 263 of its cell paragraphs — a column of
                # 「现象」 readings centred against a left-aligned column of
                # prose is how the table is read.
                across = attribute(paragraph.find(f"{W}pPr/{W}jc"), "val")
                arrows = pending + shape_arrows(paragraph, schema)
                if arrows and raw.strip():
                    # One placement decision for the text and the runs alike.
                    _, pending, edits = fill_blanks(raw, arrows, len(arrows))
                    splice_stream(runs, edits)
                else:
                    pending = arrows
                runs = [run if is_blank(run)
                        else {**run, "text": re.sub(r"[\s\xa0]+", " ", run["text"])}
                        for run in runs]
                runs = [run for run in runs if run["text"]]
                # Trim the edges run by run, and never across an answer blank.
                # Measuring the trim on the joined string instead let an lstrip
                # run straight through a leading 「\xa0\xa0」 and swallow the
                # 18-space blank behind it, taking the whole paragraph with it.
                while runs and not is_blank(runs[0]) and not runs[0]["text"].strip():
                    runs.pop(0)
                while runs and not is_blank(runs[-1]) and not runs[-1]["text"].strip():
                    runs.pop()
                if runs and not is_blank(runs[0]):
                    runs[0] = {**runs[0], "text": runs[0]["text"].lstrip()}
                if runs and not is_blank(runs[-1]):
                    runs[-1] = {**runs[-1], "text": runs[-1]["text"].rstrip()}
                runs = [run for run in runs if run["text"]]
                body = "".join(run["text"] for run in runs)
                if body or marker:
                    paragraphs.append({"text": body, "runs": runs,
                                       **({"alignment": across} if across else {}),
                                       **({"list": marker} if marker else {})})
            # A cell holding nothing but spaces is an answer box, and an arrow
            # drawn over it is the arrow the student writes around. There is no
            # blank to fill between two words, because there are no words.
            # 「Only spaces」 is the test, not 「nothing at all」: once an answer
            # blank stopped being stripped away, the cell was no longer empty
            # and the arrow drawn over it had nowhere to go again.
            if pending and not any(p["text"].strip() for p in paragraphs):
                if paragraphs:
                    paragraphs[-1] = {**paragraphs[-1],
                                      "text": paragraphs[-1]["text"] + "".join(pending),
                                      "runs": (paragraphs[-1].get("runs") or [])
                                              + [{"text": "".join(pending), "marks": {}}]}
                else:
                    paragraphs.append({"text": "".join(pending)})
                pending = []
            if pending:
                waiting[cell_index] = pending
            refs = picture_refs_deep(cell, resolve)
            # A formula drawn as an embedded object is text wherever it sits.
            # Deep refs carry no offset, so a cell's transcription joins the
            # cell's last paragraph — cells are short enough for that to read
            # correctly, and leaving it as a picture would put an unplaceable
            # WMF into the table.
            for ref in refs:
                found = schema.embedded.get(Path(ref.get("file") or "").stem)
                if not found or found.get("kind") != "text":
                    continue
                body = expand_transcription(found["text"])
                if paragraphs:
                    paragraphs[-1]["text"] = (paragraphs[-1]["text"] + body).strip()
                else:
                    paragraphs.append({"text": body})
                ref["kind"] = "transcribed"
                ref["text"] = found["text"]
            # Diagrams inside a cell need their raster rendition too; only the
            # paragraph path had it, so one WMF still reached the compiler.
            for ref in refs:
                found = schema.embedded.get(Path(ref.get("file") or "").stem)
                if found and found.get("render"):
                    ref["render"] = found["render"]
            cells.append({
                "index": cell_index,
                "gridSpan": int(attribute(span, "val") or 1) if span is not None else 1,
                "vMerge": (attribute(merge, "val") or "continue")
                          if merge is not None else None,
                "width": attribute(width, "w"),
                "widthType": attribute(width, "type"),
                "vAlign": attribute(align, "val"),
                "borders": borders_of(cell_properties),
                "shading": shading_of(cell_properties),
                "paragraphs": paragraphs,
                "images": len(refs), "figures": refs})
        rows.append({
            "index": row_index,
            "height": attribute(height, "val"),
            "heightRule": attribute(height, "hRule"),
            "isHeader": row_properties is not None
                        and row_properties.find(W + "tblHeader") is not None,
            "cantSplit": row_properties is not None
                         and row_properties.find(W + "cantSplit") is not None,
            "cells": cells})
    for column, arrows in waiting.items():
        for arrow in arrows:
            diagnostics.add("ARROW_UNPLACED", locator,
                            f"表格第 {column + 1} 列,到表末都没有可回填的位置")

    style = properties.find(W + "tblStyle") if properties is not None else None
    width = properties.find(W + "tblW") if properties is not None else None
    layout = properties.find(W + "tblLayout") if properties is not None else None
    align = properties.find(W + "jc") if properties is not None else None
    return {
        "locator": locator,
        "style": attribute(style, "val"),
        "width": attribute(width, "w"), "widthType": attribute(width, "type"),
        "layout": attribute(layout, "type"),
        "alignment": attribute(align, "val"),
        "borders": borders_of(properties),
        "shading": shading_of(properties),
        "grid": grid,
        "rows": rows,
        "mergedCells": sum(1 for r in rows for c in r["cells"]
                           if c["gridSpan"] > 1 or c["vMerge"])}


def read_lists(root, numbering) -> dict:
    """Resolve Word's automatic numbering back into visible markers.

    The number a reader sees is generated from numbering.xml and appears in no
    text run, so 「1．书写文字表达式…」 extracts with its 1． missing. Counting
    the items of each (numId, ilvl) in document order puts it back — as list
    data, not spliced into the prose, so our own spec regenerates it on output.
    """
    counters: dict[tuple[str, str], int] = {}
    found: dict = {}
    for paragraph in root.iter(W + "p"):
        properties = paragraph.find(f"{W}pPr/{W}numPr")
        if properties is None:
            continue
        number = attribute(properties.find(W + "numId"), "val")
        level = attribute(properties.find(W + "ilvl"), "val") or "0"
        if number is None:
            continue
        definition = numbering.get((number, level), {})
        key = (number, level)
        counters[key] = counters.get(key, int(definition.get("start", 1) or 1) - 1) + 1
        template = definition.get("template") or "%1."
        found[paragraph] = {
            "numId": number, "ilvl": level,
            "numFmt": definition.get("format"),
            "lvlText": template,
            "index": counters[key],
            "marker": template.replace("%" + str(int(level) + 1), str(counters[key]))}
    return found


def read_numbering(package) -> dict[tuple[str, str], dict[str, str]]:
    try:
        root = etree.fromstring(package.read("word/numbering.xml"))
    except KeyError:
        return {}
    abstract = {attribute(a, "abstractNumId"): a for a in root.iter(W + "abstractNum")}
    resolved: dict[tuple[str, str], dict[str, str]] = {}
    for entry in root.iter(W + "num"):
        reference = entry.find(W + "abstractNumId")
        source = abstract.get(attribute(reference, "val"))
        if source is None:
            continue
        for level in source.iter(W + "lvl"):
            resolved[(attribute(entry, "numId"), attribute(level, "ilvl"))] = {
                "format": attribute(level.find(W + "numFmt"), "val"),
                "template": attribute(level.find(W + "lvlText"), "val"),
                "start": attribute(level.find(W + "start"), "val")}
    return resolved


def textbox_paragraphs(paragraph, schema: "Schema") -> list[dict[str, Any]]:
    """The paragraphs living inside a text box, one by one.

    A callout is a shape carrying its own paragraphs. Folding them into the
    host paragraph's text reads fine but leaves each of them with no identity,
    so nothing downstream can own them — and the source-coverage gate counts
    every one as dropped.
    """
    found: list[dict[str, Any]] = []
    for container in paragraph.iter(W + "txbxContent"):
        branch = container
        while branch is not None:
            if branch.tag == f"{{{MC_NS}}}Fallback":
                break
            branch = branch.getparent()
        if branch is not None:
            continue          # the compatibility copy, not a second callout
        for nested in container.findall(W + "p"):
            # The runs travel with their marks, the same way a body paragraph's
            # do. Reading only the text lost every character mark inside a
            # callout — 65 highlighted runs, which is where most of the
            # source's real highlighting lives.
            runs = paragraph_runs(nested, schema.field_renderings)
            found.append({"index": len(found) + 1,
                          "text": own_text(nested, schema.field_renderings),
                          "runs": runs})
    return found


def content_stream(runs: list[dict[str, Any]],
                   refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs and pictures in the order the source has them.

    A figure between 「汞+氧气」 and 「氧化汞」 is the reaction arrow; appended to
    the end of the paragraph instead it reads as 「汞+氧气氧化汞 →」, which is a
    different statement. The runs travel with their marks, so the character
    semantics survive the same journey the pictures now do.
    """
    placed = sorted((r for r in refs
                     if r.get("offset") is not None
                     and r.get("kind") != "transcribed"),
                    key=lambda r: r["offset"])
    stream: list[dict[str, Any]] = []
    cursor = 0
    index = 0
    for ref in placed:
        at = max(int(ref["offset"]), 0)
        while index < len(runs) and cursor + len(runs[index]["text"]) <= at:
            stream.append(dict(runs[index]))
            cursor += len(runs[index]["text"])
            index += 1
        if index < len(runs) and cursor < at:
            head = at - cursor
            run = runs[index]
            stream.append({"text": run["text"][:head], "marks": run["marks"]})
            runs[index] = {"text": run["text"][head:], "marks": run["marks"]}
            cursor = at
        stream.append({"figure": ref})
    for run in runs[index:]:
        stream.append(dict(run))
    return stream


def read_blocks(path: str, schema: Schema, diagnostics: Diagnostics,
                library: MediaLibrary | None = None) -> list[dict[str, Any]]:
    package = zipfile.ZipFile(path)
    relations = dict(re.findall(
        r'Id="([^"]+)"[^>]*Target="([^"]+)"',
        package.read("word/_rels/document.xml.rels").decode()))
    library = library or MediaLibrary(None)
    body_size = default_size_pt(package)

    def resolve(relation_id: str | None) -> dict[str, str] | None:
        target = relations.get(relation_id or "")
        if not target:
            return None
        try:
            payload = package.read("word/" + target.lstrip("/"))
        except KeyError:
            return None
        return library.store(payload, target)

    root = etree.fromstring(package.read("word/document.xml"))
    lists = read_lists(root, read_numbering(package))
    blocks: list[dict[str, Any]] = []
    para = table = 0
    # An arrow that found no blank in its own paragraph belongs to a later one:
    # the shape is drawn above the line it serves, so its anchor sits ahead of
    # its target in document order. Carried forward rather than dropped.
    pending: list[tuple[str, str, int]] = []

    def banner_of(paragraph) -> dict[str, Any] | None:
        """A banner is one bitmap but two mappable slots.

        「探新知 · 教材知识解读」 is a badge and a caption side by side. Treating
        the PNG as one indivisible title forces the private spec to reuse the
        source artwork; splitting it into label + caption lets each map to our
        own component, with the image kept only as a source fingerprint."""
        for blip in paragraph.iter(f"{{{A_NS}}}blip"):
            target = relations.get(blip.get(f"{{{R_NS}}}embed"))
            if not target:
                continue
            try:
                payload = package.read("word/" + target.lstrip("/"))
            except KeyError:
                continue
            found = schema.banners.get(hashlib.sha256(payload).hexdigest()[:12])
            if found:
                return found
        return None

    for child in root.find(W + "body"):
        if child.tag == W + "tbl":
            table += 1
            refs = picture_refs_deep(child, resolve)
            locator = f"body/tbl[{table}]"
            blocks.append({"role": "表格", "kind": "body",
                           "locator": locator,
                           "text": nested_text(child),
                           "images": len(refs), "imageRefs": refs,
                           "table": read_table(child, schema, diagnostics,
                                               locator, resolve, lists)})
            continue
        if child.tag != W + "p":
            continue
        para += 1
        locator = f"body/p[{para}]"
        runs = paragraph_runs(child, schema.field_renderings)
        text = "".join(run["text"] for run in runs)
        inner = nested_text(child)
        # The picture count is the length of the asset list, not a separate
        # tally. Counting with iter() also counted the Fallback copy of every
        # AlternateContent picture, so the two disagreed by 74 across the set —
        # and a count that no asset backs is what the gate now refuses.
        refs = picture_refs(child, resolve, schema)
        images = len(refs)
        stream = content_stream(runs, refs)
        # Role is read from the text as the source wrote it. A transcription
        # spliced in at offset 0 would sit in front of the question number and
        # 「1．…」 would stop looking like a numbered item — 10 questions went
        # missing that way.
        probe = re.sub(r"[\s\xa0]+", " ", text).strip()
        # An embedded object holding a formula is text the source drew as a
        # picture. Splice the transcription in at the object's own offset,
        # last first so the earlier offsets stay valid.
        for ref in sorted((r for r in refs if r.get("file")),
                          key=lambda r: -(r.get("offset") or 0)):
            found = schema.embedded.get(Path(ref["file"]).stem)
            if not found or found.get("kind") != "text":
                continue
            body = expand_transcription(found["text"])
            at = ref.get("offset") or 0
            text = text[:at] + body + text[at:]
            for index, piece in enumerate(stream):
                if piece.get("figure") is ref:
                    stream[index] = {"text": body,
                                     "marks": {"transcribed": True}}
                    break
            ref["kind"] = "transcribed"
            ref["text"] = found["text"]

        # A genuine diagram stored as WMF still cannot be placed; it travels
        # with a raster rendition while the WMF stays as its source identity.
        for ref in refs:
            found = schema.embedded.get(Path(ref.get("file") or "").stem)
            if found and found.get("render"):
                ref["render"] = found["render"]

        # An arrow drawn as an inline picture needs no slot found for it: the
        # picture is already sitting where the arrow belongs. 「汞+氧气 [图]
        # 氧化汞」 has no blank at all, so looking for one would have sent the
        # arrow to some other line. It is spliced at its own offset, the way a
        # transcribed formula is.
        for ref in sorted((r for r in refs if r.get("hash")),
                          key=lambda r: -(r.get("offset") or 0)):
            entry = schema.condition_arrows.get(str(ref.get("hash")))
            if not entry or ref.get("anchor"):
                continue
            condition = str(entry.get("condition") or "")
            body = (schema.arrow_render.format(condition=condition)
                    if condition else schema.arrow_plain)
            at = ref.get("offset") or 0
            text = text[:at] + body + text[at:]
            for index, piece in enumerate(stream):
                if piece.get("figure") is ref:
                    stream[index] = {"text": body,
                                     "marks": {"transcribed": True}}
                    break
            ref["kind"] = "transcribed"
            ref["text"] = condition

        carried = [arrow for arrow, _, _ in pending]
        fresh = shape_arrows(child, schema) + picture_arrows(refs, schema)
        arrows = carried + fresh
        if arrows:
            # Arrows anchored on one paragraph were drawn over one line, so
            # that many blanks on the line they reach may be filled.
            origins = [where for _, where, _ in pending] + [locator] * len(fresh)
            limit = max(Counter(origins).values()) if origins else 1
            # One placement decision, applied to both the flat text and the
            # runs, so the two cannot drift apart.
            joined = "".join(piece["text"] for piece in stream if "text" in piece)
            _, stranded, edits = fill_blanks(joined, arrows, limit)
            splice_stream(stream, edits)
            text, _ = place_arrows(text, arrows, limit)
            placed = len(arrows) - len(stranded)
            pending = pending[max(0, placed - (len(arrows) - len(carried))):]
            kept: list[tuple[str, str, int]] = []
            for arrow in stranded:
                origin, age = next(
                    ((where, age) for where, age in
                     [(w, a) for a, (x, w, a) in
                      zip(range(len(pending)), pending) if x == arrow]),
                    (locator, 0))
                if age + 1 <= schema.arrow_carry:
                    kept.append((arrow, origin, age + 1))
                else:
                    diagnostics.add("ARROW_UNPLACED", origin, text[:50])
            pending = kept
        text = re.sub(r"[\s\xa0]+", " ", text).strip()
        # An underlined run of spaces is an answer blank, and its width is
        # content: it tells the student how much to write. Collapsing runs of
        # whitespace is right for prose and wrong here — it turned all 1322
        # blanks in this set, averaging eight spaces each, into one character.
        stream = [piece if "text" not in piece
                  else ({**piece} if is_blank(piece)
                        else {**piece, "text": re.sub(r"[\s\xa0]+", " ", piece["text"])})
                  for piece in stream]
        stream = [piece for piece in stream
                  if "figure" in piece or piece["text"]]
        # Where each figure sits once the whitespace has gone. A figure's own
        # offset counts the raw runs, and everything downstream reads the
        # collapsed text: 「A．    B．    C．    D．」 loses nine spaces, so a
        # row of four picture-only options matched its pictures to the wrong
        # options and lost the last one entirely. The stream knows both, being
        # the thing that was split.
        cursor = 0
        for piece in stream:
            if "text" in piece:
                cursor += len(piece["text"])
            elif isinstance(piece.get("figure"), dict):
                piece["figure"]["streamOffset"] = cursor
        for instruction in unrendered_fields(child, schema.field_renderings):
            diagnostics.add("FIELD_UNRENDERED", locator, instruction[:60])
        role = kind = caption = layout = label = None
        if not probe:
            banner = banner_of(child)
            if banner:
                # 教学目标 is not one of the four columns — it heads a single
                # block at the top of the lesson. Treating it as a column made
                # its table read as 「section=教学目标」, a column that does not
                # exist in the document's own structure.
                if banner.get("role") == "块标题":
                    role, kind = f"标题·{banner['label']}", "section"
                else:
                    role, kind = f"栏目横幅·{banner['label']}", "section-banner"
                label = banner["label"]
                caption = banner.get("caption")
                layout = banner.get("layout")
        if role is None and probe in schema.sub_modules:
            role, kind = f"子模块横幅·{text}", "sub-module"
        if role is None and inner != probe and any(
                inner.startswith(head) for head in schema.callouts):
            head = next(h for h in schema.callouts if inner.startswith(h))
            role, kind = f"提示框·{head}", "body"
        if role is None and inner != probe and inner:
            role, kind = "文本框内容", "body"
        if role is None and probe:
            found = schema.role_of(probe)
            if found:
                role, kind = found, schema.kind_of(found)
            else:
                role, kind = "正文", "body"
        if role is None:
            # A paragraph holding nothing but an underlined blank is an answer
            # line, not an empty one. Three of them in this set carried a
            # 43-space blank each and were thrown away as layout spacers.
            answering = any(is_blank(piece) for piece in stream if "text" in piece)
            role, kind = (("图片块", "body") if images
                          else ("作答线", "body") if answering
                          else ("空段落", "body"))
        blocks.append({"role": role, "kind": kind, "locator": locator,
                       "text": text or inner, "images": images,
                       "imageRefs": refs, "caption": caption, "layout": layout,
                       "sourceSizePt": source_size_pt(child, body_size),
                       "alignment": (lambda j: j.get(W + "val") if j is not None
                                     else None)(child.find(f"{W}pPr/{W}jc")),
                       "stream": stream,
                       "label": label,
                       "textboxParagraphs": textbox_paragraphs(child, schema),
                       "listItems": [
                           {**lists[nested], "text": own_text(nested, schema.field_renderings)}
                           for nested in child.iter(W + "p") if nested in lists]})
    return blocks


def carve(blocks: list[dict[str, Any]], schema: Schema,
          diagnostics: Diagnostics) -> dict[str, Any]:
    section = subsection = node = None
    tree: list[dict[str, Any]] = []
    header: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def close() -> None:
        nonlocal current
        if current:
            questions.append(current)
            current = None

    for block in blocks:
        kind, role = block["kind"], block["role"]
        if kind == "section-banner":
            close()
            section = role.split("·")[1]
            subsection = node = None
            # The banner label is read back from the image hash, so without the
            # asset the atom says 「探新知」 with nothing behind it. A title that
            # is a picture has to carry its picture.
            tree.append({"level": "栏目", "label": section,
                         "caption": block.get("caption"),
                         "sourceLayout": block.get("layout"),
                         "locator": block["locator"], "text": block["text"],
                         "images": block["images"],
                         "figures": block["imageRefs"]})
            continue
        if kind == "sub-module":
            close()
            subsection = role.split("·")[1]
            tree.append({"level": "子模块", "section": section, "label": subsection,
                         "locator": block["locator"], "text": block["text"],
                         "images": block["images"],
                         "figures": block["imageRefs"]})
            continue
        if kind == "section":
            close()
            node = block.get("label") or block["text"][:30]
            tree.append({"level": "节", "section": section, "subsection": subsection,
                         "role": role, "label": node,
                         "locator": block["locator"], "text": block["text"],
                         "images": block["images"],
                         "figures": block["imageRefs"]})
            continue
        if kind == "header":
            header.append({"level": "课时标题", "role": role,
                           "label": block["text"][:30],
                           "locator": block["locator"], "text": block["text"],
                           "images": block["images"],
                           "figures": block["imageRefs"]})
            continue
        if kind == "stem":
            close()
            current = {"section": section, "subsection": subsection, "node": node,
                       "kind": role, "stem": block["text"], "locator": block["locator"],
                       "images": block["images"], "body": [], "options": [],
                       "subQuestions": [],
                       # A figure on the question's own opening line belongs to
                       # the stem; without this it would be the one block whose
                       # pictures no owner ever claims.
                       "figureOwners": ([{"owner": "题干",
                                          "locator": block["locator"],
                                          "count": block["images"],
                                          "figures": block["imageRefs"]}]
                                        if block["images"] else [])}
            continue
        if current is not None:
            current["images"] += block["images"]
            current["body"].append(block)
            if block.get("table"):
                current.setdefault("tables", []).append(block["table"])
            if block.get("listItems"):
                current.setdefault("listItems", []).extend(block["listItems"])
            if block["role"] not in ("选项行", "小问") and block["images"]:
                owner = (current["subQuestions"][-1]["label"]
                         if current["subQuestions"] else "题干")
                current.setdefault("figureOwners", []).append(
                    {"owner": owner, "locator": block["locator"],
                     "count": block["images"], "figures": block["imageRefs"]})
            if role == "选项行":
                placed = 0
                # Split on the stream's own text so an option's range indexes
                # the same string the runs concatenate to.
                joined = "".join(piece["text"] for piece in block.get("stream") or []
                                 if "text" in piece) or block["text"]
                for option in split_options(joined, schema, diagnostics,
                                            block["locator"],
                                            block.get("imageRefs")):
                    if option.pop("leadFragment", False):
                        continue   # counted in the remainder below
                    placed += option["images"]
                    current["options"].append(option)
                # Whatever this row carries but no option claims — pictures printed
                # before the first 「A．」, or on a row whose markers never resolved
                # — belongs to the stem fragment sharing the line. Routing the
                # remainder rather than the lead case alone keeps figures conserved
                # even when the row yields no options at all.
                claimed = {id(r) for o in current["options"] for r in o.get("figures", [])}
                left = [r for r in block["imageRefs"] if id(r) not in claimed]
                if left:
                    current.setdefault("figureOwners", []).append(
                        {"owner": (current["subQuestions"][-1]["label"]
                                   if current["subQuestions"] else "题干"),
                         "locator": f'{block["locator"]}#lead',
                         "count": len(left), "figures": left})
            elif role == "小问":
                label = re.match(r"^[（(]\s*(\d+)\s*[)）]", block["text"])
                current["subQuestions"].append(
                    {"label": f'({label.group(1)})' if label else "?",
                     "text": block["text"], "locator": block["locator"],
                     "images": block["images"],
                     "figures": block["imageRefs"]})
            continue
        tree.append({"level": "节内容", "section": section, "subsection": subsection,
                     "node": node, "role": role, "locator": block["locator"],
                     "text": block["text"], "images": block["images"],
                     "figures": block["imageRefs"],
                     **({"table": block["table"]} if block.get("table") else {}),
                     **({"listItems": block["listItems"]}
                        if block.get("listItems") else {})})
    close()
    tree[:0] = header

    for question in questions:
        question["joined"] = question["stem"] + "".join(b["text"] for b in question["body"])
        question["optionLabels"] = "".join(o["label"] for o in question["options"])
        # Options regroup at every return to A: a question with sub-questions
        # carries one group per sub-question, and judging the concatenation
        # would call a perfectly good 「ABCDABCD」 incomplete.
        groups: list[str] = []
        for option in question["options"]:
            if not groups or option["label"] <= groups[-1][-1:]:
                groups.append("")
            groups[-1] += option["label"]
        question["optionGroups"] = groups
        question["complete"] = bool(groups) and all(g == "ABCD" for g in groups)
        labels = question["optionLabels"]
        for group in groups:
            if group != "".join(schema.option_chars[:len(group)]):
                diagnostics.add("OPTION_GAP", question["locator"], f"标号组 {group}")
        question["blockCount"] = 1 + len(question["body"])
        question["id"] = hashlib.sha256(question["joined"].encode()).hexdigest()[:16]
    return {"tree": tree, "questions": questions}


def main() -> int:
    parser = argparse.ArgumentParser(description="Schema-driven handout carve.")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--structure", type=Path,
                        help="write the structural atoms — banners, headings, "
                             "the exposition that sits outside any question")
    parser.add_argument("--media-dir", type=Path,
                        help="extract the figures themselves, hashed by content")
    parser.add_argument("--atoms", type=Path,
                        help="write the atomised questions themselves, for review")
    args = parser.parse_args()

    library = MediaLibrary(args.media_dir)
    schema = Schema(json.loads(args.schema.read_text(encoding="utf-8")))
    diagnostics = Diagnostics(schema.diagnostics)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    originals = [d for d in registry["documents"] if d["role"] == "original_word"]
    annotated = {(d["lesson"], d["period"]): d
                 for d in registry["documents"] if d["role"] == "annotated_word"}

    all_questions: list[dict[str, Any]] = []
    all_structure: list[dict[str, Any]] = []
    total_blocks = accounted = 0
    roles: Counter = Counter()

    for document in originals:
        diagnostics.document = document["lesson"] + (document["period"] or "")
        blocks = read_blocks(document["physicalPath"], schema, diagnostics,
                             library)
        for block in blocks:
            roles[block["role"]] += 1
        carved = carve(blocks, schema, diagnostics)
        total_blocks += len(blocks)
        accounted += len(carved["tree"]) + sum(q["blockCount"] for q in carved["questions"])
        label_now = document["lesson"] + (document["period"] or "")
        for node in carved["tree"]:
            all_structure.append({"document": label_now, **node})

        answers: dict[str, dict[str, Any]] = {}
        partner = annotated.get((document["lesson"], document["period"]))
        # 答案可以在**同一份文档里**。2026 物理教师版就是这样:一份里既有题也有【答案】,
        # 而且它是学生版的严格超集(实测 1792/1792 段,作答线仍留空)。
        # 此前只支持「原卷 + 解析版伙伴档」两份配对,而那套配对按
        # section|subsection|node|题干前18字 做键——**两侧差一个句号或一个下划线就配不上**
        # (实测 A04即练2、C02第4题 皆因此丢了答案,而源里其实都有)。
        # 同源时不存在这个问题:题和它的答案本来就在一起。
        # 由模板表 tags.answersInSameDocument 声明,不猜:某些册确实是两份分开的。
        self_annotated = bool((schema.raw.get("tags_meta") or {}).get("answersInSameDocument")
                              or schema.raw.get("answersInSameDocument"))
        if partner or self_annotated:
            if partner:
                diagnostics.document += "（解析版）"
                other = carve(read_blocks(partner["physicalPath"], schema, diagnostics),
                              schema, diagnostics)
                diagnostics.document = diagnostics.document[:-len("（解析版）")]
            else:
                other = carved     # 答案就在本文档里,不必再读一份
            def _by_role_tag(item, want):
                """题内是否有携带该标签的角色块;有就返回它的正文。"""
                for blk in item.get("body") or []:
                    if schema.role_tags.get(blk.get("role")) == want:
                        text = (blk.get("text") or "").strip()
                        # 剥掉「分析：」「解：」这类自身前缀,只留内容
                        return re.sub(r"^\s*[^：:]{0,12}[：:]\s*", "", text, count=1)
                return None

            for item in other["questions"]:
                found = schema.tags["answer"].search(item["joined"])
                explained = schema.tags["explanation"].search(item["joined"])
                explanation = explained.group(1).strip() if explained else None
                if found:
                    body = found.group(1).strip()
                else:
                    # 教科书体例:没有【答案】,但有 role 携带 answer 标签
                    body = _by_role_tag(item, "answer")
                    if not body:
                        continue
                if explanation is None:
                    explanation = _by_role_tag(item, "explanation")
                group = f'{item["section"]}|{item["subsection"]}|{item["node"]}'
                parts = [m for m in schema.answer_split.finditer(body)
                         if not (schema.answer_split_reject_digit
                                 and m.end() < len(body) and body[m.end()].isdigit())]
                if len(parts) >= 2:
                    for index, match in enumerate(parts):
                        end = (parts[index + 1].start()
                               if index + 1 < len(parts) else len(body))
                        answers[f'{group}|#{match.group(1)}'] = {
                            "answer": body[match.end():end].strip(),
                            "explanation": explanation, "fromCombined": True}
                    continue
                answers[f'{group}|{item["stem"][:18]}'] = {
                    "answer": body, "explanation": explanation}

        label = document["lesson"] + (document["period"] or "")
        for question in carved["questions"]:
            group = f'{question["section"]}|{question["subsection"]}|{question["node"]}'
            number = schema.question_number.match(question["stem"])
            found = answers.get(f'{group}|{question["stem"][:18]}') or {}
            if not found and number:
                found = answers.get(f'{group}|#{number.group(1)}') or {}
            question["document"] = label
            placed = (sum(o.get("images", 0) for o in question["options"])
                      + sum(f["count"] for f in question.get("figureOwners", []))
                      + sum(x["images"] for x in question["subQuestions"]))
            if placed != question["images"]:
                diagnostics.add("FIGURE_UNATTRIBUTED", question["locator"],
                                f'图 {question["images"]} 张,已归属 {placed} 张')
            # Counting was never the point — the asset is. A count that balances
            # while the figure list is empty is exactly how 77 table pictures
            # passed the gate with nothing behind them.
            every = ([f for o in question["options"] for f in o.get("figures", [])]
                     + [f for w in question.get("figureOwners", [])
                        for f in w.get("figures", [])]
                     + [f for x in question["subQuestions"]
                        for f in x.get("figures", [])])
            if len(every) != question["images"]:
                diagnostics.add("FIGURE_ASSET_MISSING", question["locator"],
                                f'图形 {question["images"]} 个,归属 {len(every)} 个')
            orphan = [f for f in every
                      if f.get("kind") == "picture" and not f.get("file")]
            if orphan:
                diagnostics.add("FIGURE_ASSET_MISSING", question["locator"],
                                f'{len(orphan)} 张位图没有取到文件')
            question["answer"] = found.get("answer")
            question["explanation"] = found.get("explanation")
            # A multi-part answer is written per sub-question — 「(1)…(2)…(5)…」
            # — so split it and pair each part with the sub-question it answers.
            # Kept as data beside the whole answer, never replacing it.
            if question["answer"] and question["subQuestions"]:
                parts = list(re.finditer(r"[（(]\s*(\d+)\s*[)）]", question["answer"]))
                if len(parts) >= 2:
                    spans = []
                    for index, match in enumerate(parts):
                        end = (parts[index + 1].start()
                               if index + 1 < len(parts) else len(question["answer"]))
                        spans.append((f'({match.group(1)})',
                                      question["answer"][match.end():end].strip()))
                    # 「(2)(3) 红磷燃烧…」 — the source writes adjacent sub-questions
                    # under one merged marker. An empty span is not a miss; it
                    # shares the next non-empty one. Flagged so downstream knows
                    # the pairing is joint rather than clean.
                    mapped, shared, pending = {}, set(), []
                    for key, body in spans:
                        if not body:
                            pending.append(key)
                            continue
                        for held in pending:
                            mapped[held] = body
                            shared.add(held)
                        if pending:
                            shared.add(key)
                        pending = []
                        mapped[key] = body
                    for sub in question["subQuestions"]:
                        sub["answer"] = mapped.get(sub["label"])
                        if sub["label"] in shared:
                            sub["answerShared"] = True
                    if shared:
                        diagnostics.add("SUBANSWER_SHARED", question["locator"],
                                        f'小问 {",".join(sorted(shared))} 共用一段答案')
                    missing = [s_["label"] for s_ in question["subQuestions"]
                               if not s_.get("answer")]
                    if missing:
                        diagnostics.add("SUBANSWER_UNPAIRED", question["locator"],
                                        f'小问 {",".join(missing)} 未配到答案')
            if question["kind"] == "编号项" and not question["answer"]:
                question["kind"] = "讲解条目"
            elif not question["answer"]:
                diagnostics.add("MISSING_ANSWER",
                                f'{label} {question["locator"]}',
                                question["stem"][:40])
            elif re.match(r"^\d{1,2}[．.]", question["answer"]):
                diagnostics.add("ANSWER_STARTS_WITH_NUMBER",
                                f'{label} {question["locator"]}',
                                question["answer"][:30])
        all_questions.extend(carved["questions"])

    real = [q for q in all_questions if q["kind"] != "讲解条目"]
    report = {
        "schemaVersion": "chengziclass.schema-driven-carve.v1",
        "status": "report-only",
        "schema": str(args.schema),
        "blockTotal": total_blocks,
        "blocksAccounted": accounted,
        "roleCount": len(roles),
        "realQuestions": len(real),
        "teachingPoints": len(all_questions) - len(real),
        "optionObjects": sum(len(q["options"]) for q in real),
        "questionsWithFullABCD": sum(1 for q in real if q["complete"]),
        "subQuestionObjects": sum(len(q["subQuestions"]) for q in real),
        "subQuestionsWithAnswer": sum(
            1 for q in real for s_ in q["subQuestions"] if s_.get("answer")),
        "optionsCarryingImages": sum(
            1 for q in real for o in q["options"] if o.get("images")),
        "tables": sum(len(q.get("tables", [])) for q in all_questions)
                  + sum(1 for n in all_structure if n.get("table")),
        "tableCells": sum(
            len(r["cells"])
            for q in all_questions for t in q.get("tables", []) for r in t["rows"])
            + sum(len(r["cells"])
                  for n in all_structure if n.get("table")
                  for r in n["table"]["rows"]),
        "mergedCells": sum(t["mergedCells"]
                           for q in all_questions for t in q.get("tables", []))
                       + sum(n["table"]["mergedCells"]
                             for n in all_structure if n.get("table")),
        "listItems": sum(len(q.get("listItems", [])) for q in all_questions)
                     + sum(len(n.get("listItems", [])) for n in all_structure),
        "structureAtoms": len(all_structure),
        "structureFigures": sum(len(n.get("figures", [])) for n in all_structure),
        "structureChars": sum(len(n.get("text") or "") for n in all_structure),
        "figuresWithSize": sum(
            1 for q in real
            for f in ([g for o in q["options"] for g in o.get("figures", [])]
                      + [g for w in q.get("figureOwners", [])
                         for g in w.get("figures", [])]
                      + [g for x in q["subQuestions"] for g in x.get("figures", [])])
            if f.get("widthEmu")),
        "figuresCropped": sum(
            1 for q in real
            for f in ([g for o in q["options"] for g in o.get("figures", [])]
                      + [g for w in q.get("figureOwners", [])
                         for g in w.get("figures", [])]
                      + [g for x in q["subQuestions"] for g in x.get("figures", [])])
            if f.get("crop")),
        "mediaFiles": len(library.written),
        "graphicObjects": sum(q["images"] for q in real),
        "shapeObjects": sum(
            1 for q in real
            for f in ([g for o in q["options"] for g in o.get("figures", [])]
                      + [g for w in q.get("figureOwners", [])
                         for g in w.get("figures", [])]
                      + [g for x in q["subQuestions"] for g in x.get("figures", [])])
            if f.get("kind") == "shape"),
        "figuresOwnedByStemOrSub": sum(
            f["count"] for q in real for f in q.get("figureOwners", [])),
        "withAnswer": sum(1 for q in real if q["answer"]),
        "withExplanation": sum(1 for q in real if q["explanation"]),
        "diagnosticCounts": diagnostics.counts(),
        "diagnosticErrors": diagnostics.errors,
        "diagnostics": diagnostics.items[:40],
    }
    if args.structure:
        args.structure.parent.mkdir(parents=True, exist_ok=True)
        args.structure.write_text(
            json.dumps(all_structure, ensure_ascii=False, indent=1),
            encoding="utf-8")

    if args.atoms:
        # body 曾与 joined 一起被丢弃。joined 该丢(它是 stem+body 的拼接,纯冗余),
        # body 不该:题里凡不是题干/选项/小问的块都在里面——圈号项(①②③)首当其冲。
        # 实测(2026-08-20 两册全量):落在题内的圈号项 42 条,其中 23 条
        # (讲义 6 / 单元卷 17)在原子里一个字都找不到。**题面残缺而无人报错。**
        # 只留结构与文本,不留 imageRefs(那是对象引用,图的归属另有 figureOwners)。
        def _slim_body(blocks):
            return [{"role": b.get("role"), "text": b.get("text"),
                     "locator": b.get("locator"), "images": b.get("images", 0)}
                    for b in blocks]

        atoms = [{**{k: v for k, v in q.items() if k not in ("body", "joined")},
                  "bodyBlocks": _slim_body(q.get("body") or [])}
                 for q in all_questions]
        args.atoms.parent.mkdir(parents=True, exist_ok=True)
        args.atoms.write_text(json.dumps(atoms, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "diagnostics"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
