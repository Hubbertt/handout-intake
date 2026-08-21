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
except ModuleNotFoundError:
    sys.exit("需要 psycopg。原子化写库这一步依赖它;只跑到文件层不需要。")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def body_font_half_points(block: dict) -> int | None:
    """该块的正文字号(半点)。

    取块内**出现字符最多**的那个 run 的 sz —— 不取第一个 run:
    第一个 run 常常是编号或空 run(chars=0),它的字号不代表正文。
    """
    best, best_chars = None, -1
    for run in block.get("runs") or []:
        chars = int(run.get("chars") or 0)
        sz = ((run.get("rPr") or {}).get("sz") or {}).get("val")
        if sz and chars > best_chars:
            best, best_chars = int(sz), chars
    if best is None:  # 整块没有显式字号 → 落到文档默认
        for run in block.get("runs") or []:
            sz = ((run.get("rPr") or {}).get("szCs") or {}).get("val")
            if sz:
                return int(sz)
    return best


BLANK_RE = re.compile(r"[_＿]{2,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True, help="册的 work/ 目录")
    ap.add_argument("--schema", type=Path, required=True, help="模板表(判据真源)")
    ap.add_argument("--dsn", default=os.environ.get("ATOMIZE_DSN"), help="或用环境变量 ATOMIZE_DSN")
    ap.add_argument("--source-file", type=Path, required=True, help="源 docx(算 sha256)")
    ap.add_argument("--volume-key", required=True)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        return _fail("缺 --dsn / ATOMIZE_DSN")

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    mapping = schema.get("unitKindMapping")
    if not mapping:
        return _fail("模板表里没有 unitKindMapping —— 角色名到模块类型的映射必须由表给,"
                     "不在代码里写死。补上再跑。")
    atom_kinds = mapping["atomKinds"]
    block_roles = mapping["blockRoles"]

    atoms = json.loads((args.work / "atoms.normalised.json").read_text(encoding="utf-8"))
    layout = json.loads((args.work / "layout.json").read_text(encoding="utf-8"))
    blocks_detail = layout["blocksDetail"]

    # ── 先验:所有出现过的角色都必须有映射。缺一个就停,不猜 ──────────────
    seen_atom_kinds = {a["kind"] for a in atoms}
    seen_block_roles = {b.get("role") for a in atoms for b in (a.get("bodyBlocks") or []) if b.get("role")}
    missing = ([k for k in sorted(seen_atom_kinds) if k not in atom_kinds]
               + [r for r in sorted(seen_block_roles) if r not in block_roles])
    if missing:
        return _fail(f"这些角色在模板表的 unitKindMapping 里没有对应的模块类型:{missing}。"
                     f"补进表里再跑——代码不替它们猜。")

    src_hash = sha256_file(args.source_file)
    source_id = f"src-{src_hash[:16]}"
    run_id = f"run-{src_hash[:8]}-{sha256_file(args.schema)[:8]}"

    report = {"gate": "GATE_ATOMIZE_PERSISTED", "sourceId": source_id, "runId": run_id,
              "counts": {}, "characterAttribution": {}, "figures": {}}

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from information_schema.schemata where schema_name='atomize'")
        if not cur.fetchone()[0]:
            return _fail("目标库里没有 atomize schema。先跑 skill/schema/atomize/001_init.sql。")
        if args.dry_run:
            conn.rollback()

        # 同一份源重导:先清掉它的旧行(级联),不做增量合并——
        # 增量合并要判「哪些没变」,而判错了就是静默保留旧原子。
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
        block_id_of, facts = {}, 0
        for ordinal, blk in enumerate(blocks_detail, 1):
            loc = f"{blk['document']}/{blk['locator']}"
            text = "".join("" for _ in ())  # 文本在内容层,这里只落位置与类型
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
            for field, kind in mapping["_derivedFields"].items():
                if atom.get(field):
                    cur.execute("""insert into atomize.units(unit_id,source_id,parent_unit_id,kind,ordinal,meta)
                                   values(%s,%s,%s,%s,0,%s)""",
                                (f"{uid}#{field}", source_id, uid, kind,
                                 json.dumps({"text": atom[field]}, ensure_ascii=False)))
                    units += 1
        report["counts"]["units"] = units

        # ── 图:相对比例现在算 ────────────────────────────────────────
        figs, no_font = 0, 0
        for blk in blocks_detail:
            drawings = blk.get("drawings") or []
            if not drawings:
                continue
            loc = f"{blk['document']}/{blk['locator']}"
            hp = body_font_half_points(blk)
            if not hp:
                no_font += len(drawings)     # 算不出比例就不写——库里那列是 NOT NULL
                continue
            owner = _owner_unit(atoms, blk, source_id)
            if not owner:
                continue
            for i, d in enumerate(drawings, 1):
                ext = d.get("extentEmu") or {}
                cy = int(ext.get("cy") or 0)
                if not cy:
                    no_font += 1
                    continue
                mid = f"m-{source_id}-{loc}-{i}".replace("/", "_")
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
                                     "宁可少一行看得见,不可填个绝对值假装记住了。"}

        # ── 字符归属:选项的 range 已有,其余按块登记;如实报覆盖 ──────────
        spans, chars_attributed, image_only = 0, 0, 0
        for atom in atoms:
            for opt in atom.get("options") or []:
                rng, oloc = opt.get("range"), (opt.get("locator") or "").split("#")[0]
                key = f"{atom.get('document')}/{oloc}"
                if not rng or key not in block_id_of:
                    continue
                if rng[1] <= rng[0]:
                    # 零宽区间不是缺陷:这是**图片选项**——选项的内容是图不是字
                    # (实测 1057 个带 range 的选项里 29 个如此,如「A．[图] B．[图]」)。
                    # 不造一个假的区间把它填上:它确实占 0 个字符,图的归属走 figures。
                    # ★但顺带看见一件事:选项标签「A．」这些字符现在谁也没归属——
                    #   引擎的 range 指的是「选项正文」的范围,不含标签。记在报告里,别装作没有。
                    image_only += 1
                    continue
                try:
                    cur.execute("""insert into atomize.spans(block_id,char_start,char_end,unit_id,role)
                                   values(%s,%s,%s,%s,'option')""",
                                (block_id_of[key], int(rng[0]), int(rng[1]),
                                 f'{source_id}:{atom.get("document")}/{atom.get("locator")}'))
                    spans += 1
                    chars_attributed += int(rng[1]) - int(rng[0])
                except psycopg.errors.ExclusionViolation:
                    conn.rollback()          # 区间重叠 = 归属打架,不是可以忽略的小事
                    return _fail(f"字符区间重叠:{key} {rng} —— 一个字符只能有一个归属")
        total_chars = sum(int(r.get("chars") or 0) for b in blocks_detail for r in (b.get("runs") or []))
        report["counts"]["spans"] = spans
        report["counts"]["imageOnlyOptionsSkipped"] = image_only
        report["characterAttribution"] = {
            "attributed": chars_attributed, "totalChars": total_chars,
            "ratio": round(chars_attributed / total_chars, 4) if total_chars else None,
            "_honest": ("这一版只把**选项**的字符区间落了库(引擎已经给了 range),"
                        "题干/小问/答案解析尚未逐字符定位,故比例很低。"
                        "★这不是「差不多了」——判准是每个字符都有归属,"
                        "低就是低,写在这里让它看得见。"),
            "_alsoUnattributed": ("选项标签(「A．」「B．」)不在任何 span 里——引擎的 range 指选项正文,不含标签。"
                                  "要做到「每个字符都有归属」,标签得单独归给选项。"),
        }

        cur.execute("""insert into atomize.runs(run_id,source_id,template_id,schema_hash,package_version,
                         finished_at,gates,reconstructible,notes)
                       values(%s,%s,%s,%s,%s,now(),%s,null,%s)
                       on conflict (run_id) do update set finished_at=now(), gates=excluded.gates""",
                    (run_id, source_id, schema.get("id"), sha256_file(args.schema),
                     _pkg_version(), json.dumps(report, ensure_ascii=False),
                     "reconstructible 留 null:本步不跑还原判准,由 s4c6 单独判。null 不是通过。"))
        if args.dry_run:
            conn.rollback()
            report["dryRun"] = True
        else:
            conn.commit()

    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


def _i(v):
    return int(v) if v not in (None, "") else None


def _owner_unit(atoms, blk, source_id):
    loc = blk["locator"]
    for a in atoms:
        if a.get("document") != blk["document"]:
            continue
        for b in a.get("bodyBlocks") or []:
            if b.get("locator") == loc:
                return f'{source_id}:{a.get("document")}/{a.get("locator")}'
    return None


def _pkg_version():
    p = Path(__file__).resolve().parents[3] / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _fail(msg):
    print(f"GATE_ATOMIZE_PERSISTED: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
