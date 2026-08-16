#!/usr/bin/env python3
"""GATE_ACCEPTANCE_RECONCILIATION:每条验收项都必须有人在查。

**起因是一次对账。** saveGate.requiredBeforeFormalSave 有 8 条验收项,
全量合规审计有 20 项检查,两边从未对过账。逐条核对后发现 3 条**没有任何检查项在查**:

  toc-is-in-word-and-shares-content-page-numbering-with-body
  toc-pages-own-their-pages
  content-page-numbers-start-at-1-on-first-toc-page

而这 8 条的名字叫 requiredBeforeFormalSave——**没人查的验收项等于没有验收**,
可它在报告里和其余 5 条长得一模一样,都只是一行字。

这是本方法反复抓到的同一个形状:**完整性只存在于治理触及之处**。
验收项写在规范里、检查项写在脚本里,两张表各自都「齐」,
中间那层对应关系没人维护,于是缺口恰好落在没人看的接缝上。

本门做两件事:
  ① 每条验收项必须声明 verifiedBy,且不得为空——空即 unverified,整门失败。
  ② 声明的检查项必须真在审计报告里出现且为 pass——写了但没跑,与没写等价。

另附三条本门自带的判据,补上上面那 3 条缺口里可测的部分(见 --docx)。

用法:
  gate_acceptance_reconciliation.py --params P --docx D --compliance-report R
退出码 0=全部验收项都有人查且通过 1=有验收项无人查或检查未过
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


def section_numbering(docx: Path) -> dict:
    """从 sectPr 读页码序列:首节是否 start=1,后续节是否重启。"""
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    secs = re.findall(r"<w:sectPr[ >].*?</w:sectPr>", xml, re.S)
    starts = []
    for s in secs:
        m = re.search(r'<w:pgNumType[^>]*w:start="(\d+)"', s)
        starts.append(int(m.group(1)) if m else None)
    return {"sections": len(secs), "starts": starts}


def builtin_checks(docx: Path, extra: dict | None = None) -> list[dict]:
    """补上对账查出的缺口里可机器判定的部分。

    三条判据都在:page-numbers-start-at-1 / toc-shares-body-numbering 读 sectPr;
    toc-pages-own-their-pages 读**成品 PDF**(--content-pdf)——它是分页后的结果,判得了。
    给不了 PDF 时报 unverifiable,不假装查过。
    """
    num = section_numbering(docx)
    out = []
    first = num["starts"][0] if num["starts"] else None
    out.append({
        "check": "builtin.page-numbers-start-at-1",
        "status": "pass" if first == 1 else "fail",
        "detail": {"firstSectionStart": first, "sections": num["sections"]},
        "asserts": "首节声明 w:pgNumType w:start=1。缺声明则由本地 Word 决定起始页,"
                   "而目录页码与正文页码必须落在同一序列上。",
    })
    # toc-pages-own-their-pages:目录页上不许有正文。
    # ★这一条曾长期 unverified,理由是「页与内容的对应只有 Word 分页后才知道,静态读 docx 判不了」——
    #   那是对的,但结论下早了:**成品 PDF 就是分页后的结果**。给了 --content-pdf 就能判,
    #   给不了才是真的判不了。判据从「读不到就说查不了」改成「读得到就查,读不到就如实说没查」。
    content_pdf = extra.get("contentPdf") if extra else None
    if content_pdf and Path(content_pdf).exists():
        try:
            import fitz
            doc = fitz.open(content_pdf)
            toc_pages, offenders = [], []
            for index in range(doc.page_count):
                text = doc[index].get_text()
                if text.count("....") > 3 or text.count("…") > 3:
                    toc_pages.append(index + 1)
                    for line in (l.strip() for l in text.split("\n")):
                        # 正文行的特征:长句、不带页码引导点、不以页码结尾
                        if len(line) > 34 and "...." not in line and not line.replace(" ", "").endswith(
                                tuple("0123456789")):
                            offenders.append({"page": index + 1, "line": line[:60]})
            out.append({
                "check": "builtin.toc-pages-own-their-pages",
                "status": "pass" if (toc_pages and not offenders) else "fail",
                "detail": {"tocPages": toc_pages, "bodyLinesOnTocPages": offenders[:5]},
                "asserts": "目录所占的页上不得出现正文行。目录是读者的检索面,"
                           "正文混进来会让「翻到这一页」和「这一页是什么」对不上。",
            })
        except ImportError:
            out.append({"check": "builtin.toc-pages-own-their-pages", "status": "unverifiable",
                        "detail": {"why": "缺 PyMuPDF,读不了成品 PDF。不假装查过。"}})
    restarts = [i + 2 for i, v in enumerate(num["starts"][1:]) if v is not None]
    out.append({
        "check": "builtin.toc-shares-body-numbering",
        "status": "pass" if not restarts else "fail",
        "detail": {"sectionsRestartingNumbering": restarts},
        "asserts": "首节之后没有任何节重启页码。一旦有节重启,目录页码与正文页码"
                   "就不再是同一序列,而目录里的数字仍会照排——错得不显眼。",
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--compliance-report", required=True, type=Path)
    ap.add_argument("--content-pdf", type=Path, help="成品内容 PDF;给了才能判目录页归属")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    params = json.loads(args.params.read_text(encoding="utf-8"))
    save_gate = params.get("saveGate") or {}
    criteria = save_gate.get("requiredBeforeFormalSave") or []
    reconciliation = save_gate.get("acceptanceReconciliation") or {}

    compliance = json.loads(args.compliance_report.read_text(encoding="utf-8"))
    ran = {}
    for result in compliance.get("results") or []:
        for check in result.get("checks") or []:
            # 键是 id 不是 code。首版按审计脚本调用点的参数名(code)去读,
            # 于是 20 项全 pass 的报告被读成「一项都没跑」——**判据看的是自己的 bug**。
            # 幸而它当时报的是 fail:恒真的解析错误会静默通过,恒假的会吵。
            ran[check.get("id") or check.get("code")] = check.get("status")
    extra = {"contentPdf": str(args.content_pdf)} if args.content_pdf else None
    for check in builtin_checks(args.docx, extra):
        ran[check["check"]] = check["status"]

    rows, findings = [], []
    for criterion in criteria:
        verifiers = reconciliation.get(criterion)
        if verifiers is None:
            findings.append({"criterion": criterion, "kind": "no-mapping",
                             "why": "该验收项没有登记 verifiedBy。对应关系没人维护时,"
                                    "两张表各自都『齐』而缺口落在接缝上。"})
            rows.append({"criterion": criterion, "verifiedBy": None, "status": "unmapped"})
            continue
        if not verifiers:
            findings.append({"criterion": criterion, "kind": "unverified",
                             "why": "该验收项**没有任何检查项在查**,而它属于 "
                                    "requiredBeforeFormalSave——没人查的验收项等于没有验收。"})
            rows.append({"criterion": criterion, "verifiedBy": [], "status": "unverified"})
            continue
        states = {}
        for v in verifiers:
            states[v] = ran.get(v, "**未出现在报告里**")
            if v not in ran:
                findings.append({"criterion": criterion, "kind": "verifier-did-not-run",
                                 "verifier": v,
                                 "why": "登记的检查项没有出现在审计报告里。"
                                        "写了但没跑,与没写等价。"})
            elif ran[v] != "pass":
                findings.append({"criterion": criterion, "kind": "verifier-failed",
                                 "verifier": v, "status": ran[v]})
        rows.append({"criterion": criterion, "verifiedBy": verifiers, "states": states,
                     "status": "ok" if all(s == "pass" for s in states.values()) else "bad"})

    report = {"gate": "GATE_ACCEPTANCE_RECONCILIATION",
              "criteria": len(criteria), "checksAvailable": len(ran),
              "rows": rows, "findings": findings,
              "builtin": builtin_checks(args.docx, extra),
              "status": "pass" if not findings else "fail",
              "shape": "完整性只存在于治理触及之处。验收项写在规范里、检查项写在脚本里,"
                       "两张表各自都齐,中间那层对应关系没人维护——"
                       "缺口恰好落在没人看的接缝上。"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps({"criteria": len(criteria), "findings": findings,
                      "status": report["status"]}, ensure_ascii=False, indent=1))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
