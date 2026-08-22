#!/usr/bin/env python3
"""把原子化的两层产物写进 atomize.* —— 原子化的真源是数据表,不是文件。

PM 2026-08-22 定。文件(atoms.normalised.json / layout.json)从此是**中间产物**,
库里那份才是真源;题库导入与编制成册都从库里取,各取各要的那一层。

三条纪律落在这一步:
  · **角色名→模块类型的映射来自模板表**(unitKindMapping),不写在这里。
    换一份源换一套角色名,写死的映射就漂了。遇到没映射的角色**直接拒绝**,不猜。
  · **相对比例现在算、存成列**。它依赖「该图所在段落的正文字号」——那是此刻才知道的事实,
    事后从 EMU 反推不出来。库里 scale_to_body_font 是 NOT NULL,算不出就写不进去。
  · **字符归属如实报**。判准是字符级不是段级;报的是覆盖了多少字符,不是覆盖了多少段。
    盖不住的部分要看得见——段落级曾报 100%,换字符级掉到 82.4%,差距就藏在那里。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import psycopg
except ImportError as _exc:
    # ★捕 ImportError 而不只是 ModuleNotFoundError。
    #   2026-08-22 实测:向导装了**裸** psycopg(不带二进制实现),
    #   包在,import 时却在 psycopg/pq 里炸——那是 ImportError 不是 ModuleNotFoundError。
    #   只捕后者,这一步就裸崩:链里只看到一个 traceback 尾巴,
    #   而旁边还留着上一次的 pass 报告,两处说法互相矛盾。
    psycopg = None
    _psycopg_error = _exc
else:
    _psycopg_error = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def body_font_half_points(block: dict) -> int | None:
    """该块的**生效**正文字号(半点)。

    取非内容层解好的 bodyFont —— 继承链(直接 rPr > 段落样式 > docDefaults)
    在 capture_layout 里解,不在这里重解:那是非内容层该记的事实,
    两处各解一遍必然漂,而且漂了没人知道。
    """
    font = block.get("bodyFont") or {}
    return int(font["halfPoints"]) if font.get("halfPoints") else None


BLANK_RE = re.compile(r"[_＿]{2,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # ★吃具名产物,不吃目录。链的模型是「消费哪几个产物」——
    #   给它一个目录,等于说「这一步要用里面的东西,具体哪些不说」,
    #   consumes 就形同虚设,上游改了哪个也没人知道该不该失效。
    ap.add_argument("--atoms", type=Path, required=True, help="atoms.normalised")
    ap.add_argument("--layout", type=Path, required=True, help="layout")
    ap.add_argument("--split-gate", dest="split_gate", type=Path, required=True,
                    help="gate.split-banner(分档清单从这里取)")
    ap.add_argument("--schema", type=Path, required=True, help="模板表(判据真源)")
    ap.add_argument("--dsn", default=os.environ.get("ATOMIZE_DSN"), help="或用环境变量 ATOMIZE_DSN")
    ap.add_argument("--source-file", type=Path, required=True, help="源 docx(算 sha256)")
    ap.add_argument("--volume-key", required=True)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if psycopg is None:
        return _fail(f"psycopg 不可用({_psycopg_error})。原子化写库这一步依赖它;"
                     f"只跑到文件层的册不需要。装:pip install 'psycopg[binary]' —— "
                     f"★裸 psycopg 不带二进制实现,包在也 import 不了。", args.report)
    if not args.dsn:
        return _fail("缺连接串。用 --dsn 或环境变量 ATOMIZE_DSN 给。"
                     "★凭据不进工序表也不进册的 bindings——那两处是要随包分发/进仓的。",
                     args.report)

    # ★一进来就把旧报告作废。
    #   2026-08-22 实测:这一步崩了(裸 traceback),旁边却留着上一次的 pass 报告——
    #   链说 failed、报告说 pass,两处说法互相矛盾,而看报告的人会信报告。
    #   报告是**这一次**的回执,不是「最近一次成功」的纪念品。
    if args.report:
        try:
            Path(args.report).write_text(json.dumps(
                {"gate": "GATE_ATOMIZE_PERSISTED", "status": "running",
                 "_note": "这一步开始了但还没写出结论。若你看到的是这一行,说明它中途崩了。"},
                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        except Exception:
            pass

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    mapping = schema.get("unitKindMapping")
    if not mapping:
        return _fail("模板表里没有 unitKindMapping —— 角色名到模块类型的映射必须由表给,"
                     "不在代码里写死。补上再跑。", args.report)
    atom_kinds = mapping["atomKinds"]
    block_roles = mapping["blockRoles"]

    atoms = json.loads(args.atoms.read_text(encoding="utf-8"))
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    blocks_detail = layout["blocksDetail"]

    # ── 先验:所有出现过的角色都必须有映射。缺一个就停,不猜 ──────────────
    seen_atom_kinds = {a["kind"] for a in atoms}
    seen_block_roles = {b.get("role") for a in atoms for b in (a.get("bodyBlocks") or []) if b.get("role")}
    missing = ([k for k in sorted(seen_atom_kinds) if k not in atom_kinds]
               + [r for r in sorted(seen_block_roles) if r not in block_roles])
    if missing:
        return _fail(f"这些角色在模板表的 unitKindMapping 里没有对应的模块类型:{missing}。"
                     f"补进表里再跑——代码不替它们猜。", args.report)

    src_hash = sha256_file(args.source_file)
    # ★身份是(文件, 册):同一份源可以是多册的源(教师版 → 讲册 + 单元卷册)。
    #   只用文件哈希当 id,后跑的册会把先跑的册整个删掉重建,而两边各自都报 pass。
    source_id = f"src-{src_hash[:16]}-{args.volume_key}"
    run_id = f"run-{src_hash[:8]}-{sha256_file(args.schema)[:8]}"

    report = {"gate": "GATE_ATOMIZE_PERSISTED", "sourceId": source_id, "runId": run_id,
              "counts": {}, "characterAttribution": {}, "figures": {}}

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from information_schema.schemata where schema_name='atomize'")
        if not cur.fetchone()[0]:
            return _fail("目标库里没有 atomize schema。先跑 skill/schema/atomize/001_init.sql。", args.report)
        if args.dry_run:
            conn.rollback()

        # 同一份源重导:先清掉它的旧行(级联),不做增量合并——
        # 增量合并要判「哪些没变」,而判错了就是静默保留旧原子。
        # ★上次的归属率要在**删之前**读。
        #   这一步开头会清掉这份源的旧行(级联),runs 也跟着没了——
        #   若在删之后再查「上一次是多少」,查到的永远是空,判据三就永远不会红。
        #   2026-08-22 验红时抓到:那是一条恒假判据,而恒假判据比没有判据更坏,
        #   因为它看起来一直在守。
        cur.execute("""select (gates->'characterAttribution'->>'ratio')::numeric
                       from atomize.runs
                       where source_id=%s and schema_hash=%s
                         and gates->'characterAttribution'->>'ratio' is not null
                       order by started_at desc limit 1""",
                    (source_id, sha256_file(args.schema)))
        _prev = cur.fetchone()
        previous_ratio = float(_prev[0]) if _prev and _prev[0] is not None else None

        cur.execute("delete from atomize.sources where source_id=%s", (source_id,))

        cur.execute("""insert into atomize.templates(template_id,display_name,subject,schema_json,schema_hash)
                       values(%s,%s,%s,%s,%s) on conflict (schema_hash) do nothing""",
                    (schema.get("id", "unknown"), schema.get("id", "unknown"),
                     schema.get("subject"), json.dumps(schema, ensure_ascii=False),
                     sha256_file(args.schema)))

        cur.execute("""insert into atomize.sources(source_id,file_sha256,file_name,role,volume_key,template_id,schema_hash)
                       values(%s,%s,%s,%s,%s,%s,%s)""",
                    (source_id, src_hash, args.source_file.name, "教师版", args.volume_key,
                     schema.get("id"), sha256_file(args.schema)))

        # ── 物理块 + 非内容层 ──────────────────────────────────────────
        # 先取源里每块的真实文本 —— blocks.text 要存它,否则没人能验 span 是否越界
        _gate = json.loads(args.split_gate.read_text(encoding="utf-8"))
        _lessons = {x["path"]: x["lesson"] for x in _gate["lessons"]}
        _src_text = {f"{d}/{l}": x for d, l, x in _source_blocks(_lessons)}
        block_id_of, facts = {}, 0
        for ordinal, blk in enumerate(blocks_detail, 1):
            loc = f"{blk['document']}/{blk['locator']}"
            text = _src_text.get(loc, "")
            cur.execute("""insert into atomize.blocks(source_id,locator,block_type,ordinal,text)
                           values(%s,%s,%s,%s,%s) returning block_id""",
                        (source_id, loc, blk.get("node") or "p", ordinal, text))
            block_id_of[loc] = cur.fetchone()[0]
            for key, val in (blk.get("pPr") or {}).items():
                cur.execute("""insert into atomize.layout_facts(source_id,locator,layer,run_index,key,value)
                               values(%s,%s,'pPr',null,%s,%s) on conflict do nothing""",
                            (source_id, loc, key, json.dumps(val, ensure_ascii=False)))
                facts += 1
            for ri, run in enumerate(blk.get("runs") or []):
                for key, val in (run.get("rPr") or {}).items():
                    cur.execute("""insert into atomize.layout_facts(source_id,locator,layer,run_index,key,value)
                                   values(%s,%s,'rPr',%s,%s,%s) on conflict do nothing""",
                                (source_id, loc, ri, key, json.dumps(val, ensure_ascii=False)))
                    facts += 1
        for doc, props in (layout.get("sectionProps") or {}).items():
            for key, val in (props or {}).items():
                cur.execute("""insert into atomize.layout_facts(source_id,locator,layer,run_index,key,value)
                               values(%s,%s,'sectPr',null,%s,%s) on conflict do nothing""",
                            (source_id, f"{doc}/sectPr", key, json.dumps(val, ensure_ascii=False)))
                facts += 1
        report["counts"]["blocks"] = len(block_id_of)
        block_text = {f'{a.get("document")}/{b.get("locator")}': (b.get("text") or "")
                      for a in atoms for b in (a.get("bodyBlocks") or [])}
        report["counts"]["layoutFacts"] = facts

        # ── 内容模块 ────────────────────────────────────────────────
        # ★ 引擎的 atom["id"] 是**内容哈希**,不唯一:2026-08-22 实测 796 条原子里
        #   5 个 id 各撞 2 次(同一讲里逐字相同的讲解条目,如「1. 质量的单位」出现在
        #   body/p[16] 与 body/p[65])。内容相同是事实,但它们是两个不同的**出现**。
        #   主键取「哪一份源的哪一档的哪个位置」——(document, locator) 实测唯一;
        #   内容哈希留在 meta.contentHash,它自有用处(查重复内容),只是不能当主键。
        units = 0
        for atom in atoms:
            uid = f'{source_id}:{atom.get("document")}/{atom.get("locator")}'
            cur.execute("""insert into atomize.units(unit_id,source_id,kind,ordinal,hierarchy_path,meta)
                           values(%s,%s,%s,%s,%s,%s)""",
                        (uid, source_id, atom_kinds[atom["kind"]], units,
                         " / ".join(x for x in (atom.get("document"), atom.get("section"),
                                                atom.get("subsection"), atom.get("node")) if x),
                         json.dumps({"engineKind": atom["kind"], "locator": atom.get("locator"),
                                     "contentHash": atom["id"], "complete": atom.get("complete")},
                                    ensure_ascii=False)))
            units += 1
            # ★选项与小问各自成**单元**,不只是 span。
            #   定义说的是「哪些东西组成了一道题:一个题干 + 若干选项 + 若干小问 + 若干图
            #   + 一个答案 + 一份解析」——「组成」要成立,它们就得各自可寻址。
            #   只当 span,题库导入时拿不到「第 3 个选项」这种东西,只能拿到一片字符。
            for i, opt in enumerate(atom.get("options") or [], 1):
                cur.execute("""insert into atomize.units(unit_id,source_id,parent_unit_id,kind,ordinal,meta)
                               values(%s,%s,%s,'option',%s,%s) on conflict do nothing""",
                            (f"{uid}#opt{i}", source_id, uid, i,
                             json.dumps({"label": opt.get("label"), "text": opt.get("text"),
                                         "locator": opt.get("locator"), "images": opt.get("images", 0)},
                                        ensure_ascii=False)))
                units += 1
            for i, sub in enumerate(atom.get("subQuestions") or [], 1):
                cur.execute("""insert into atomize.units(unit_id,source_id,parent_unit_id,kind,ordinal,meta)
                               values(%s,%s,%s,'sub_question',%s,%s) on conflict do nothing""",
                            (f"{uid}#sub{i}", source_id, uid, i,
                             json.dumps({"label": sub.get("label"), "text": sub.get("text"),
                                         "locator": sub.get("locator")}, ensure_ascii=False)))
                units += 1
            # ★单选/多选:optionGroups 只有一组时,一组几个就是几个候选;
            #   多于一组说明这道题带多个小问、各有一套选项——那种情况下题级的 max_choices
            #   没有意义(它属于各个小问),留 NULL,不猜。QTI 的 max-choices 是**声明**,
            #   我们这里也只在能明确对应时才写。
            groups = atom.get("optionGroups") or []
            if len(groups) == 1:
                cur.execute("update atomize.units set max_choices=1 where unit_id=%s", (uid,))
            for field, kind in mapping["_derivedFields"].items():
                if atom.get(field):
                    cur.execute("""insert into atomize.units(unit_id,source_id,parent_unit_id,kind,ordinal,meta)
                                   values(%s,%s,%s,%s,0,%s)""",
                                (f"{uid}#{field}", source_id, uid, kind,
                                 json.dumps({"text": atom[field]}, ensure_ascii=False)))
                    units += 1
        report["counts"]["units"] = units

        # ── 先把没有任何原子认领的块建成单元 ─────────────────────────
        #    顺序有讲究:这一步必须在「图」之前。图要找所属模块,而档标题、栏目横幅、
        #    知识点标题这些块不属于任何一道题——单元还没建,图就找不到主人。
        #    2026-08-22 实测:先建图后建这些单元,169 张图因此没有归属。
        #    「不属于任何一道题」不等于「不用记」:编制成册要排它们。
        gate = json.loads(args.split_gate.read_text(encoding="utf-8"))
        lessons = {x["path"]: x["lesson"] for x in gate["lessons"]}
        headings = {x for a in atoms for x in (a.get("section"), a.get("subsection"), a.get("node"))
                    if x} | {x["title"] for x in gate["lessons"]}
        src_blocks = _source_blocks(lessons)
        total_chars = _source_char_total(lessons)
        in_tables = sum(len(x[2]) for x in src_blocks if "/tbl[" in x[1])
        claimed = set(block_text) | {f'{a.get("document")}/{a.get("locator")}' for a in atoms}
        table_owner = {}
        for a in atoms:
            _uid = f'{source_id}:{a.get("document")}/{a.get("locator")}'
            for b in a.get("bodyBlocks") or []:
                _loc = b.get("locator") or ""
                if _loc.startswith("body/tbl["):
                    table_owner[f'{a.get("document")}/{_loc}'] = _uid
        # 带图但**没有文字**的段落:纯图片段。它没有字,所以不进字符归属,
        # 但它有图 —— 图也要有主人。2026-08-22 实测:漏掉它们,109 张图没有归属模块。
        # 「这一段没有字」不等于「这一段没有内容」。
        with_drawing = {f'{b["document"]}/{b["locator"]}' for b in blocks_detail if b.get("drawings")}
        unclaimed_units = {}
        for document, locator, text in src_blocks:
            key = f"{document}/{locator}"
            if key in claimed:
                continue
            if not text.strip():
                if key in with_drawing:
                    uid = f"{source_id}:{key}#figure-only"
                    cur.execute("""insert into atomize.units(unit_id,source_id,kind,ordinal,hierarchy_path,meta)
                                   values(%s,%s,'figure',0,%s,%s) on conflict do nothing""",
                                (uid, source_id, document,
                                 json.dumps({"unclaimedByAtom": True, "figureOnly": True,
                                             "locator": locator}, ensure_ascii=False)))
                    unclaimed_units[key] = (uid, "figure", "")
                continue
            if "/tbl[" in locator and table_owner.get(f'{document}/{locator.split("/tr[")[0]}'):
                continue                      # 表内块归表的所有者,不算未认领
            kind = "heading" if any(h and h in text for h in headings) else "prose"
            uid = f"{source_id}:{key}#unclaimed"
            cur.execute("""insert into atomize.units(unit_id,source_id,kind,ordinal,hierarchy_path,meta)
                           values(%s,%s,%s,0,%s,%s) on conflict do nothing""",
                        (uid, source_id, kind, document,
                         json.dumps({"unclaimedByAtom": True, "locator": locator}, ensure_ascii=False)))
            unclaimed_units[key] = (uid, kind, text)
        report["counts"]["unclaimedUnits"] = len(unclaimed_units)

        # ── 图:相对比例现在算 ────────────────────────────────────────
        figs, no_font, font_sources, no_owner_block = 0, 0, {}, 0
        # ordinal 是「在**所属模块**里的第几张」,不是「在这一块里的第几张」——
        # 一个模块可以横跨多个块(题干一块、选项行两块、图片块一块),
        # 按块内序号编,跨块的图就会撞在同一个 (unit_id, ordinal) 上被丢掉。
        # 2026-08-22 实测:1045 张可算,按块内序号只写进去 876 张,差的 169 张就这么没的。
        fig_ordinal = {}
        for blk in blocks_detail:
            drawings = blk.get("drawings") or []
            if not drawings:
                continue
            loc = f"{blk['document']}/{blk['locator']}"
            hp = body_font_half_points(blk)
            src = (blk.get("bodyFont") or {}).get("from", "(缺)")
            font_sources[src] = font_sources.get(src, 0) + len(drawings)
            if not hp:
                no_font += len(drawings)     # 算不出比例就不写——库里那列是 NOT NULL
                continue
            owner = _owner_unit(atoms, blk, source_id)
            if not owner:
                owner = (unclaimed_units.get(f'{blk["document"]}/{blk["locator"]}') or (None,))[0]
            if not owner:
                # 静默跳过是最坏的处置:这些块没有归属到任何原子,与「字符归属」是同一个洞。
                no_owner_block += len(drawings)
                continue
            for d in drawings:
                fig_ordinal[owner] = fig_ordinal.get(owner, 0) + 1
                i = fig_ordinal[owner]
                ext = d.get("extentEmu") or {}
                cy = int(ext.get("cy") or 0)
                if not cy:
                    no_font += 1
                    continue
                mid = f"m-{source_id}-{loc}-{d.get('kind')}-{i}".replace("/", "_")
                cur.execute("""insert into atomize.media(media_id,source_id,sha256)
                               values(%s,%s,%s) on conflict do nothing""",
                            (mid, source_id, hashlib.sha256(mid.encode()).hexdigest()))
                # 24pt 的图配 10.5pt 的正文 = 2.29 倍。换套版式,比例仍然对;绝对值不对。
                scale = round((cy / 12700.0) / (hp / 2.0), 4)
                wrap = (d.get("wrap") or [None])[0]
                cur.execute("""insert into atomize.figures(unit_id,media_id,ordinal,scale_to_body_font,
                                 body_font_half_points,width_emu,height_emu,anchoring,wrap,
                                 dist_t_emu,dist_b_emu,dist_l_emu,dist_r_emu)
                               values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               on conflict (unit_id, ordinal) do nothing""",
                            (owner, mid, i, scale, hp, int(ext.get("cx") or 0), cy,
                             "anchor" if d.get("anchoring") == "anchor" else "inline", wrap,
                             _i(d.get("distT")), _i(d.get("distB")), _i(d.get("distL")), _i(d.get("distR"))))
                figs += 1
        report["figures"] = {"written": figs, "skippedNoBodyFontOrSize": no_font,
                             "_why": "算不出相对比例的图不写——库里 scale_to_body_font 是 NOT NULL。"
                                     "宁可少一行看得见,不可填个绝对值假装记住了。",
                             "_fontSource": font_sources,
                             "noOwnerUnit": no_owner_block,
                             "_noOwnerWhy": "这些图所在的块没有归属到任何原子——与「字符归属」是同一个洞,"
                                            "不是图的问题。静默跳过是最坏的处置,所以计在这里。"}

        # ── 字符归属:选项的 range 已有,其余按块登记;如实报覆盖 ──────────
        spans, chars_attributed, image_only, out_of_bounds = 0, 0, 0, 0

        def add_span(bkey, start, end, uid, role):
            nonlocal spans, chars_attributed, out_of_bounds
            if end <= start or bkey not in block_id_of:
                return False
            # ★引擎给的 range 是**引擎文本**的偏移,不是源 w:t 的偏移:
            #   [quiz-omml] 会把公式转成 LaTeX 文本注入,引擎文本因此比源长。
            #   两套坐标系混用,span 就会伸出块尾——2026-08-22 实测归属率算出 100.87%,
            #   比 100% 还高,才发现。**越界不截断、不静默,直接拒绝并计数**:
            #   截断会让一个错的偏移看起来像对的。
            if bkey not in _src_text:
                # 这一块在源里不是文字载体(如 body/tbl[n] 这种**容器**)。
                # 表格的字属于它的单元格段落,不属于表格本身;在容器上再挂一份,
                # 同一批字就被数了两遍——2026-08-22 实测 44 个表容器多算 3,426 字符,
                # 归属率因此算出 100.87%。**比 100% 高,是量法错了,不是做得太好。**
                out_of_bounds += 1
                return False
            limit = len(_src_text[bkey])
            if end > limit:
                out_of_bounds += 1
                return False
            try:
                cur.execute("""insert into atomize.spans(block_id,char_start,char_end,unit_id,role)
                               values(%s,%s,%s,%s,%s)""",
                            (block_id_of[bkey], start, end, uid, role))
            except psycopg.errors.UniqueViolation:
                return False
            spans += 1
            chars_attributed += end - start
            return True

        # ★整块归属用**源**里那一块的真实字符数,不用原子记录的 text 长度:
        #   引擎记的 text 是规整过的(空白折叠等),比源里短。用它当跨度,
        #   块尾会剩下一截永远盖不到——2026-08-22 实测 1,774 个字符就这么漏的,
        #   而每个块都「有归属」,查块级一个都不缺。**块级看不出来的差距,只有字符级能看见。**
        src_len = {f"{d}/{l}": len(x) for d, l, x in src_blocks}
        for atom in atoms:
            uid = f'{source_id}:{atom.get("document")}/{atom.get("locator")}'
            doc = atom.get("document")
            # 选项所在的块:先放选项正文的区间,再把**空隙**补给紧随其后的那个选项。
            # ★空隙就是选项标签(「A．」「B．」)。引擎的 range 指选项正文,不含标签;
            #   标签也是字符,也必须有归属——否则「每个字符都有归属」这条判准永远差一截。
            by_block = {}
            for opt in atom.get("options") or []:
                oloc = (opt.get("locator") or "").split("#")[0]
                by_block.setdefault(f"{doc}/{oloc}", []).append(opt)
            for bkey, opts in by_block.items():
                ranges = []
                for opt in opts:
                    rng = opt.get("range")
                    if not rng:
                        continue
                    if rng[1] <= rng[0]:
                        image_only += 1     # 图片选项:内容是图不是字,确实占 0 个字符
                    ranges.append((int(rng[0]), int(rng[1]), opt))
                ranges.sort()
                cursor = 0
                for start, end, _opt in ranges:
                    if start > cursor:
                        add_span(bkey, cursor, start, uid, "option")   # ← 标签归给它后面的选项
                    add_span(bkey, start, end, uid, "option")
                    cursor = max(cursor, end)
                blk_text = block_text.get(bkey)
                if blk_text is not None and cursor < len(blk_text):
                    add_span(bkey, cursor, len(blk_text), uid, "option")

            # 其余块整块归属:块的角色决定模块类型,由模板表给
            for b in atom.get("bodyBlocks") or []:
                bkey = f'{doc}/{b.get("locator")}'
                if bkey in by_block:
                    continue                     # 选项行已在上面逐区间处理
                text = b.get("text") or ""
                if not text:
                    continue
                add_span(bkey, 0, src_len.get(bkey, len(text)), uid, block_roles[b["role"]])

            # 题干所在的块
            stem = atom.get("stem")
            if stem:
                skey = f'{doc}/{atom.get("locator")}'
                add_span(skey, 0, src_len.get(skey, len(stem)), uid, "stem")

        # ★分母必须取真值,不能拿非内容层的 runs 去数:
        #   layout 只收**带属性**的 run(`if rpr or breaks`),没属性的 run 压根不在里面。
        #   2026-08-22 用它当分母,算出「归属率 112%」——比 100% 还高,一眼就知道分母错了。
        #   量法自己会骗人,而且骗得比缺陷更像真的:这次是数字太好看,所以露了馅。
        # ── 把上面建好的单元逐块落 span ─────────────────────────────
        in_table_attributed = unclaimed = 0
        for document, locator, text in src_blocks:
            key = f"{document}/{locator}"
            if key in claimed or not text.strip():
                continue
            if "/tbl[" in locator:
                owner = table_owner.get(f'{document}/{locator.split("/tr[")[0]}')
                if owner:
                    if add_span(key, 0, len(text), owner, "table"):
                        in_table_attributed += 1
                    continue
            hit = unclaimed_units.get(key)
            if hit and text.strip() and add_span(key, 0, len(text), hit[0], hit[1]):
                unclaimed += 1
        report["counts"]["unclaimedBlocksAttributed"] = unclaimed
        report["counts"]["inTableAttributed"] = in_table_attributed
        report["counts"]["spans"] = spans
        report["counts"]["imageOnlyOptionsSkipped"] = image_only
        report["counts"]["spansRejectedNotTextCarrier"] = out_of_bounds
        report["characterAttribution"] = {
            "attributed": chars_attributed, "totalChars": total_chars,
            "ratio": round(chars_attributed / total_chars, 4) if total_chars else None,
            "_denominator": "整份源 w:t 的字符总数,含表格单元内的段落。",
            "charsInsideTables": in_tables,
            "_tablesNote": "表格单元里的段落已纳入遍历(locator 约定 body/tbl[n]/tr[r]/tc[c]/p[i])。",
            "_honest": ("判准是**每个字符都有归属**,不是「差不多了」。"
                        "没归属的字符=不知道该怎么处理的字符:编制成册时不知道排进哪里,"
                        "导入题库时不知道进哪个字段。差多少就写多少。"),
        }

        cur.execute("""insert into atomize.runs(run_id,source_id,template_id,schema_hash,package_version,
                         finished_at,gates,reconstructible,notes)
                       values(%s,%s,%s,%s,%s,now(),%s,null,%s)
                       on conflict (run_id) do update set finished_at=now(), gates=excluded.gates""",
                    (run_id, source_id, schema.get("id"), sha256_file(args.schema),
                     _pkg_version(), json.dumps(report, ensure_ascii=False),
                     "reconstructible 留 null:本步不跑还原判准,由 s4c6 单独判。null 不是通过。"))
        # ── 填空的空 ────────────────────────────────────────────────
        # 「填空的空」是 PM 定义里明写的一项。此前表建了、README 也写了它对应
        # QTI 的 textEntryInteraction —— 而写入器一行都没有。**建了表不等于做了事。**
        #
        # 定位不另起坐标系:直接在已经落好的 span 上找。span 说了「这段字符属于谁」,
        # 空就在那段字符里,偏移天然对得上。另写一套扫描必然与归属漂。
        cur.execute("""select s.unit_id, b.locator, b.block_id, s.char_start, s.char_end
                       from atomize.spans s join atomize.blocks b on b.block_id=s.block_id
                       where b.source_id=%s order by s.unit_id, b.ordinal, s.char_start""",
                    (source_id,))
        blanks = 0
        per_unit = {}
        for unit_id, locator, block_id, cs, ce in cur.fetchall():
            text = _src_text.get(f"{locator}", "") or _src_text.get(locator, "")
            if not text:
                continue
            for m in BLANK_RE.finditer(text[cs:ce]):
                per_unit[unit_id] = per_unit.get(unit_id, 0) + 1
                cur.execute("""insert into atomize.blanks(unit_id,index_in_unit,block_id,
                                 char_offset,char_length,answer_unit_id)
                               values(%s,%s,%s,%s,%s,null) on conflict do nothing""",
                            (unit_id, per_unit[unit_id], block_id,
                             cs + m.start(), m.end() - m.start()))
                blanks += 1
        report["counts"]["blanks"] = blanks
        report["counts"]["unitsWithBlanks"] = len(per_unit)
        # answer_unit_id 留 NULL:把答案按顿号/逗号切开逐个配空,是**推断**不是事实。
        # 先量一量这件事到底成不成立——空数与答案段数对得上的比例,是它可行与否的证据。
        cur.execute("""select u.unit_id, a.meta->>'text'
                       from atomize.units u join atomize.units a
                         on a.parent_unit_id=u.unit_id and a.kind='answer'
                       where u.source_id=%s""", (source_id,))
        pairable = checked = 0
        for uid, ans in cur.fetchall():
            n = per_unit.get(uid)
            if not n or not ans:
                continue
            checked += 1
            segs = [x for x in re.split(r"[、，,；;]", ans.strip().rstrip("。.")) if x.strip()]
            if len(segs) == n:
                pairable += 1
        report["counts"]["blankAnswerPairable"] = {
            "checked": checked, "segmentCountMatches": pairable,
            "_why": "答案按顿号/逗号切开逐个配空是推断不是事实,故 answer_unit_id 留 NULL。"
                    "这里只报「段数对不对得上」——它是这件事可行与否的证据,不是结论。",
        }

        # ── 判据 ────────────────────────────────────────────────────
        # ★2026-08-22 自查:此前这一步只把数字打印出来,**没有一条判据会让它红**——
        #   归属 10.38% 它绿,99.19% 它也绿。「写在规范里、没人验证,等于没有」,
        #   我做了一个记了等于没记的门,还连着几轮拿它的输出当成绩汇报。
        #   下面三条都不给百分比阈值:阈值是拍出来的,而这三条是硬事实。
        failures = []

        # 判据一:数出来的不能比源里还多。量法自证——多出来必然是重复计数或坐标系混用。
        #        (实测抓过一次:44 个表容器块把表格的字数了第二遍,归属率算出 100.87%)
        if chars_attributed > total_chars:
            failures.append(f"归属字符 {chars_attributed} > 源字符 {total_chars}:"
                            f"多出来的只能是重复计数或坐标系混用,不可能是真的")

        # 判据二:源里有非空文本的块,不能一个 span 都没有。
        #        「每个字符都有归属」的底线版:先保证每一块至少有人认领。
        # ★「什么算空」只能有一处定义。
        #   一开始这条判据写成 SQL 的 btrim(b.text) <> '' —— 而归属那侧用的是 Python 的
        #   str.strip()。两者对全角空格(U+3000)、不换行空格(U+00A0)的看法不同:
        #   Python 当空,btrim 不当。于是门报「12 个块有文字却没归属」,
        #   查下去 12 个全是只含全角空格的块 —— **判据分歧造出来的假缺陷**。
        #   这跟「两层各用各的键」是同一类错,只是这次分歧在「空」的定义上。
        #   改法:候选由 SQL 找(它只管「有没有 span」),空不空由 Python 判(归属那侧的同一套规则)。
        cur.execute("""select b.text from atomize.blocks b
                       where b.source_id=%s
                         and not exists (select 1 from atomize.spans s where s.block_id=b.block_id)""",
                    (source_id,))
        orphan_blocks = sum(1 for (txt,) in cur.fetchall() if (txt or "").strip())
        if orphan_blocks:
            failures.append(f"{orphan_blocks} 个块在源里有文字,却一个 span 都没有")

        # ── 兜底铺满:把每一块剩下的字符补齐归属 ─────────────────────
        # ★PM 2026-08-22:「覆盖率必须是 100%,即便有些地方是源文件错了,
        #   那也不影响覆盖率 100%,而且这样才知道有地方错了,而不是漏了。」
        #
        #   100% 不是「都对」,是「都算进来了」。源里错的、怪的、多余的,一样要有归属,
        #   并且**标出来**——漏掉它,就分不清是源错了还是我漏了。
        #   这两件事的处置完全不同:源错了要去改源或记缺陷;我漏了要去改判据。
        #   而没有归属的字符,两种都像。
        #
        #   所以这里不挑不拣:逐块把没被 span 盖到的区间补上,归给这一块的主人,
        #   role 记 'residual',meta 里带上那段字符本身——它是什么,看得见。
        cur.execute("""select b.block_id, b.locator, b.text,
                              (select s2.unit_id from atomize.spans s2
                                where s2.block_id=b.block_id order by s2.char_start limit 1) owner
                       from atomize.blocks b where b.source_id=%s""", (source_id,))
        residual_spans = residual_chars = 0
        for block_id, locator, text, owner in cur.fetchall():
            if not text:
                continue
            cur.execute("""select char_start, char_end from atomize.spans
                           where block_id=%s order by char_start""", (block_id,))
            covered = cur.fetchall()
            gaps, cursor = [], 0
            for cs, ce in covered:
                if cs > cursor:
                    gaps.append((cursor, cs))
                cursor = max(cursor, ce)
            if cursor < len(text):
                gaps.append((cursor, len(text)))
            if not gaps:
                continue
            if not owner:
                # 整块没人认领(纯空白块多属此类):给它一个自己的单元,不丢
                owner = f"{source_id}:{locator}#residual"
                cur.execute("""insert into atomize.units(unit_id,source_id,kind,ordinal,hierarchy_path,meta)
                               values(%s,%s,'prose',0,%s,%s) on conflict do nothing""",
                            (owner, source_id, locator.split("/")[0],
                             json.dumps({"residualOnly": True, "locator": locator,
                                         "_why": "这一块没有任何原子认领,但它的字符必须有归属。"},
                                        ensure_ascii=False)))
            for cs, ce in gaps:
                cur.execute("""insert into atomize.spans(block_id,char_start,char_end,unit_id,role)
                               values(%s,%s,%s,%s,'residual') on conflict do nothing""",
                            (block_id, cs, ce, owner))
                residual_spans += 1
                residual_chars += ce - cs
                chars_attributed += ce - cs
        # ★报告里的比例必须在**铺满之后**重算。
        #   2026-08-22 踩到:report["characterAttribution"] 在这一步之前就写好了,
        #   铺满补了 1,650 个字符,而报告仍印着 99.19% —— 门过了,数字却是旧的。
        #   门与报告说的不是同一件事,看报告的人就被骗了。
        report["characterAttribution"]["attributed"] = chars_attributed
        report["characterAttribution"]["ratio"] = round(
            (chars_attributed / total_chars) if total_chars else 0, 6)
        report["counts"]["residualSpans"] = residual_spans
        report["counts"]["residualChars"] = residual_chars
        report["_residualWhy"] = ("role='residual' 的 span 是**兜底铺满**的产物:"
                                  "它们把没被任何判据盖到的字符也纳入归属。"
                                  "★residual 多不等于没做完,但它是**一张可以逐条看的清单**——"
                                  "源里错的地方会出现在这里,判据漏的地方也会。两者都看得见,"
                                  "而不是像没有归属那样,两种都不见。")

        # 判据零:**每一个字符都有归属**。这是定义里写死的判准,不是指标。
        # ★2026-08-22 PM 追问「必须是 100% 才行吧?」——是。
        #   在此之前这道门在 99.19% 时是绿的:三条判据里只有「不许倒退」与归属率有关,
        #   而那是个**棘轮,不是那条线**。门在判准没满足时放行,与没有门无异。
        #   不设阈值(99% / 99.9% 都是拍的),线就在 100%:
        #   没归属的字符 = 不知道该怎么处理的字符,一个都不能有。
        if chars_attributed < total_chars:
            cur.execute("""select b.locator, length(b.text) - coalesce(
                             (select sum(s.char_end-s.char_start) from atomize.spans s
                              where s.block_id=b.block_id),0) gap
                           from atomize.blocks b where b.source_id=%s
                             and length(b.text) > coalesce(
                               (select sum(s.char_end-s.char_start) from atomize.spans s
                                where s.block_id=b.block_id),0)
                           order by gap desc limit 5""", (source_id,))
            worst = [{"locator": r[0], "missing": r[1]} for r in cur.fetchall()]
            report["characterAttribution"]["worstBlocks"] = worst
            failures.append(
                f"字符归属 {chars_attributed}/{total_chars} —— 差 {total_chars - chars_attributed} 个字符。"
                f"判准是**每一个字符都有归属**,不是「差不多」。缺得最多的几块:{worst}")

        # 判据三:不许倒退。不给绝对阈值(阈值是拍出来的),但同一份源、同一版判据,
        #        这次的归属率不能比上次低——低了就是改坏了,而改坏很容易看不出来。
        # ★比较必须在**同一精度**上做。
        #   报告里存的 ratio 是 round(...,4),而这里算出来的是全精度:
        #   0.99190(存)对 0.991897…(算)——算的比存的小,判成「倒退」。
        #   2026-08-22 验红时抓到这条假红:同一次数据、同一版代码,跑两遍第二遍就红。
        #   1e-9 的容差挡不住 1e-4 的舍入。判据比的是「有没有退」,不是「浮点相不相等」。
        ratio = round((chars_attributed / total_chars) if total_chars else 0, 4)
        report["characterAttribution"]["previousRatio"] = previous_ratio
        if previous_ratio is not None and ratio < round(previous_ratio, 4):
            failures.append(f"字符归属从 {previous_ratio:.4f} 退到 {ratio:.4f}:"
                            f"同一份源、同一版判据,只许涨不许退")

        report["failures"] = failures
        report["status"] = "fail" if failures else "pass"

        if args.dry_run or failures:
            conn.rollback()
            report["dryRun"] = bool(args.dry_run)
        else:
            conn.commit()

    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if report.get("failures"):
        for line in report["failures"]:
            print(f"GATE_ATOMIZE_PERSISTED: {line}", file=sys.stderr)
        return 1          # ★红的时候必须以非零退出,否则链会当它过了
    return 0


def _source_char_total(lessons: dict) -> int:
    """整份源的 w:t 字符总数 —— 含表格单元内的段落。归属率的分母只能是它。"""
    import zipfile
    from xml.etree import ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    total = 0
    for path in lessons:
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        total += sum(len(x.text or "") for x in root.iter(ns + "t"))
    return total


def _source_blocks(lessons: dict) -> list:
    """分档 docx 的每个段落:(document, locator, text)。

    分母与「哪些块没人认领」都从这里来 —— 一次读取,两处共用同一份事实。
    两处各读一遍必然漂,而且漂了没人知道。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    for path, document in lessons.items():
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        body = root.find(ns + "body")
        para = table = 0
        for child in body:
            if child.tag == ns + "tbl":
                # ★表内的段落也要遍历,locator 与 capture_layout 用**同一套约定**:
                #   body/tbl[n]/tr[r]/tc[c]/p[i]。两层各写一套键,对账时必然全对不上。
                table += 1
                for tr, row in enumerate(child.findall(ns + "tr"), 1):
                    for tc, cell in enumerate(row.findall(ns + "tc"), 1):
                        for cp, node in enumerate(cell.findall(ns + "p"), 1):
                            out.append((document,
                                        f"body/tbl[{table}]/tr[{tr}]/tc[{tc}]/p[{cp}]",
                                        "".join(x.text or "" for x in node.iter(ns + "t"))))
                continue
            if child.tag != ns + "p":
                continue
            para += 1
            out.append((document, f"body/p[{para}]",
                        "".join(x.text or "" for x in child.iter(ns + "t"))))
    return out


def _i(v):
    return int(v) if v not in (None, "") else None


def _owner_unit(atoms, blk, source_id):
    """这一块归哪个原子。

    表内段落(body/tbl[n]/tr[r]/tc[c]/p[i])归给认领了 body/tbl[n] 的那个原子——
    原子认领的是整张表,单元格里的东西属于它。此前只按整串 locator 精确匹配,
    表内的图因此一张都找不到主人(2026-08-22 实测 196 张)。
    """
    loc = blk["locator"]
    table_root = loc.split("/tr[")[0] if "/tbl[" in loc else None
    for a in atoms:
        if a.get("document") != blk["document"]:
            continue
        for b in a.get("bodyBlocks") or []:
            if b.get("locator") == loc or (table_root and b.get("locator") == table_root):
                return f'{source_id}:{a.get("document")}/{a.get("locator")}'
    return None


def _pkg_version():
    p = Path(__file__).resolve().parents[3] / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _fail(msg, report_path=None):
    """失败要**留在盘上**,不只写 stderr。

    2026-08-22 实测:单元卷那册这一步在链里失败,而 run.json 里的 stderrTail 是空的——
    操作的人只看到「failed」,看不到为什么。失败了不说话,是这个包最反对的那种失败。
    """
    line = f"GATE_ATOMIZE_PERSISTED: {msg}"
    print(line, file=sys.stderr)
    if report_path:
        try:
            Path(report_path).write_text(
                json.dumps({"gate": "GATE_ATOMIZE_PERSISTED", "status": "fail",
                            "failures": [msg]}, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8")
        except Exception:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
