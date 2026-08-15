"""付印四步的公共底座:把 vendor 里那四个脚本的能力函数,接到工作区路径上。

**它们原本是生产线的一段**:模块级常量把整条 7 册的目录布局写死,
再由一个内部调用守卫保证「只能从注册工作流进入」。守卫是有意的——
它防的是有人绕过前置门直接出片。进包时**不拆守卫**:守卫要的环境变量
由本底座在进程内设置,因为本链就是那条注册工作流在包里的对应物,
前置门(净开/合规/清单)在工序表里排在这四步之前,由依赖强制。

**改造的只有一件事:路径。**
四个脚本的 RUN_DIR / CONTENT_ROOT / STANDARD_ROOT / OUTLINED_ROOT / DOCS
全部在 import 时按生产线布局算好。本底座在 import 之后、调用之前,
把它们重指到工作区的 output/ 下,并把 DOCS 表替换成只含本册一条。
这样能力函数一行不改,而它读写的每一个位置都归工作区所有。

**为什么不重写四个脚本。**
它们合计 1900 行,装订顺序、QA 判据、Acrobat 资源守卫都是踩过坑的。
重写一遍等于把踩过的坑再踩一遍;而路径重指是可以逐项对账的:
改前改后各跑一次,产物逐 hash 比——今天的对账器就是为这个准备的。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# parents[0]=steps, [1]=method, [2]=skill。vendor 在 skill/ 下,故取 [2]。
# 首版写成 [1],指到 method/vendor(不存在),import 报「找不到模块」——
# 报错指向模块名,而根因是一个数字。
VENDOR = Path(__file__).resolve().parents[2] / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

# 守卫要的两个值,取自 vendor 脚本自己的常量——不在这里另抄一份字符串:
# 抄一份就是两处真源,任何一边改了另一边就悄悄失效。
def _guard_env():
    import export_summer_word_standard_pdfs as m
    os.environ[m.INTERNAL_INVOCATION_ENV] = m.CANONICAL_PROCESS_ID


def bind_export(chain, key: str):
    """把 export 脚本的路径常量指到工作区;返回模块。"""
    _guard_env()
    import export_summer_word_standard_pdfs as m
    out = chain.workspace / "output" / "print"
    m.RUN_DIR = out
    m.CONTENT_ROOT = out / "content-pdf"
    m.REPORT = m.CONTENT_ROOT / "content_pdf_export_report.json"
    m.WORD_CLEAN_OPEN_REPORT = chain.path_for("word.probe")
    m.STRUCTURE_MANIFEST_DIR = chain.dir_for("word.structure-manifest")
    m.DOCS = {key: chain.only("word")}
    return m


def bind_assemble(chain, key: str, cover: Path, back: Path, pdf_name: str):
    _guard_env()
    import assemble_summer_pdf_binding_sequence as m
    out = chain.workspace / "output" / "print"
    m.RUN_DIR = out
    m.CONTENT_ROOT = out / "content-pdf"
    m.STANDARD_ROOT = out / "standard-pdf"
    m.CONTENT_REPORT = m.CONTENT_ROOT / "content_pdf_export_report.json"
    m.STANDARD_REPORT = m.STANDARD_ROOT / "standard_pdf_export_report.json"
    # 装订读参数表(页面尺寸/装订规则)。参数属于**样式模板**,由册级绑定 params 给,
    # 不是生产线布局里那个固定位置——首版漏了这一句,vendor 就去找生产线的路径。
    m.PARAMS = chain.only("params")
    m.DOCS = {key: {"docx": chain.only("word"),
                    "assetDir": cover.parent,
                    "cover": cover.name, "back": back.name,
                    "pdfName": pdf_name}}
    return m


def bind_qa(chain, key: str, pdf_name: str):
    _guard_env()
    import qa_standard_pdf_binding_sequence as m
    out = chain.workspace / "output" / "print"
    m.RUN_DIR = out
    m.PDF_ROOT = out / "standard-pdf"
    m.OUT_DIR = out / "standard-pdf-qa"
    m.REPORT = m.OUT_DIR / "standard_pdf_binding_qa.json"
    m.ASSEMBLY_REPORT = m.PDF_ROOT / "standard_pdf_export_report.json"
    # expectedFirstBodyText:第一内容页必须以「目录」开头——这是格式契约
    # (目录在 Word 里、与正文共页码、页码从目录页起 1),不是册级数据。生产线 7 册全是它。
    m.PDFS = {key: {"pdf": m.PDF_ROOT / key / pdf_name, "expectedFirstBodyText": "目录"}}
    return m


def bind_outline(chain, key: str, pdf_name: str):
    _guard_env()
    import outline_standard_pdfs_with_acrobat_preflight as m
    out = chain.workspace / "output" / "print"
    m.RUN_DIR = out
    m.STANDARD_ROOT = out / "standard-pdf"
    m.OUTLINED_ROOT = out / "outlined-pdf"
    m.WORK_ROOT = out / "acrobat-outline-work"
    m.REPORT_PATH = m.OUTLINED_ROOT / "acrobat_preflight_outline_report.json"
    m.STANDARD_EXPORT_REPORT = m.STANDARD_ROOT / "standard_pdf_export_report.json"
    m.BINDING_QA_REPORT = out / "standard-pdf-qa" / "standard_pdf_binding_qa.json"
    # MATERIALS 是 dict(键→Material),不是 list;首版写成 list,
    # 会在脚本内 for key, material in MATERIALS.items() 处炸——那正是把结构猜错的形状。
    m.MATERIALS = {key: m.Material(key, m.STANDARD_ROOT / key / pdf_name,
                                   m.OUTLINED_ROOT / key / pdf_name)}
    return m


def volume_print_config(chain) -> dict:
    """册级绑定里付印所需的三样:键名、封面、封底。缺一样如实报缺,不猜。"""
    b = chain.bindings
    key = str(b.get("pdfKey") or "").strip()
    cover = (b.get("paths") or {}).get("assets.cover")
    back = (b.get("paths") or {}).get("assets.back")
    missing = [n for n, v in (("pdfKey", key), ("paths.assets.cover", cover),
                              ("paths.assets.back", back)) if not v]
    # 绑定里的相对路径相对于工作区(与 _chain 的解析一致)。首版直接 Path(cover),
    # 相对路径就相对于进程 cwd——装订步报「缺资产」,而文件明明在 inputs/ 里。
    def _abs(v):
        if not v: return None
        q = Path(v)
        return q if q.is_absolute() else (chain.workspace / q)
    return {"key": key, "cover": _abs(cover), "back": _abs(back), "missing": missing,
            "pdfName": chain.only("word").with_suffix(".pdf").name}
