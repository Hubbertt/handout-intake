#!/usr/bin/env python3
"""按工序表执行整条链:拓扑排序 → 校验输入 hash → 执行 → 记录产物 hash。

**为什么必须有它。** 没有执行器,工序表只是一张说明书:顺序仍然靠人敲命令,
而首轮所有的顺序错误都出在人手敲的那一段——建蓝图时对象清单还不存在、
修完 JPEG 又重建蓝图把修复冲掉、跳过第 0 步与第 2 步、源比对与引文门从头到尾没跑。
落在既有 9 步子表里的部分一次都没错,因为它不给机会。

三条纪律,全部由数据而非提示词强制:

  跳步在结构上不可能
      输入产物不存在就拒绝运行。不需要任何人守规矩。

  上游变了下游自动失效
      每个 consumes 校验当前 hash 与上次记录一致;不一致即 HOLD_INPUT_DRIFT。
      先例:gate_semantic_blueprint_source_coverage 早就这么做了
      (manifest.blueprintSha256 对不上就拒绝)。本执行器把同一条纪律推广到全链。

  人做的步骤只等,不假装
      runner=human 的步骤不执行,只检查产物在不在;不在就停下并说清楚等谁。

用法:
  run_chain.py --workspace X [--volume V] [--only STEP] [--from STEP] [--dry-run]
退出码 0=全部完成或干跑通过 1=有步骤失败或被拒绝
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chain import Chain, ChainError  # noqa: E402

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _probed_engine(chain: Chain):
    """安装向导写下的引擎解释器(runtime/probe-report.json)。找不到报告则 None。"""
    for base in (PACKAGE_ROOT.parent, PACKAGE_ROOT):
        rep = base / "runtime" / "probe-report.json"
        if rep.exists():
            try:
                eng = ((json.loads(rep.read_text(encoding="utf-8")).get("python") or {})
                       .get("engine") or {}).get("found") or {}
                if eng.get("exe") and Path(eng["exe"]).exists():
                    return eng["exe"]
            except Exception:
                pass
    return None


def substitute(token: str, chain: Chain) -> str:
    """命令模板里的占位符 → 实际值。

    {artifact:id} 走与 check_chain 同一套解析,不另写一份映射——
    两处各写一张映射,是登记册 P8 反复抓到的形状。
    """
    # 表里有三种产物占位符,含义不同:
    #   {artifact:id} 智能取——单文件给路径,多文件给目录
    #   {path:id}     明确要单一路径
    #   {dir:id}      明确要目录
    # ★P6 空手复现抓出:执行器原先只实现了第一种,{path:schema} 被**原样当成文件名**
    #   传给了子进程,报错是「找不到名为 {path:schema} 的文件」。
    #   未知占位符静默透传,是把配置错误伪装成运行时错误。
    # 占位符可能**嵌在更长的字符串里**(如 "{artifact:root}/report.json")。
    # 原先只处理「整个 token 就是占位符」,内嵌的原样透传——
    # 报错信息指向一个含花括号的文件名,看起来像文件系统的问题,其实是替换没做。
    import re as _re
    if _re.search(r"\{(?:artifact|path|dir):[^}]+\}", token) and not (
            token.startswith("{") and token.endswith("}")):
        return _re.sub(r"\{(?:artifact|path|dir):[^}]+\}",
                       lambda m: substitute(m.group(0), chain), token)
    if token.startswith("{path:") and token.endswith("}"):
        aid = token[len("{path:"):-1]
        # {path:} 对多文件工件:已存在恰好一个就给它(only),否则按目录给。
        # 首版直接 path_for,glob 工件当场抛 ChainError,整条链崩在执行器里——
        # 而崩溃和「这一步失败」是两回事:前者连报告都不留。
        try:
            return str(chain.path_for(aid))
        except ChainError:
            found = chain.resolve(aid)
            return str(found[0]) if len(found) == 1 else str(chain.dir_for(aid))
    if token.startswith("{dir:") and token.endswith("}"):
        return str(chain.dir_for(token[len("{dir:"):-1]))
    if token.startswith("{artifact:") and token.endswith("}"):
        aid = token[len("{artifact:"):-1]
        found = chain.resolve(aid)
        if found:
            return str(found[0]) if len(found) == 1 else str(chain.dir_for(aid))
        # 尚未产出的多文件产物要给目录,不能给单一路径——path_for 对它会直接抛错。
        # ★P6 抓出:原先只在「已存在」时才走 dir_for,于是第一次产出 media 的那一步
        # 反而是唯一走不通的那一步。判断依据取产物登记(glob/模式含 *),不取「现在有没有」。
        spec = chain.spec(aid) if aid in chain.artifacts else {}
        pattern = chain.pattern(aid) or ""
        if spec.get("glob") or "*" in str(pattern):
            return str(chain.dir_for(aid))
        return str(chain.path_for(aid))
    # 未知的 {xxx:yyy} 一律报错,不透传。透传等于把配置错误推迟到运行时,
    # 而那时的报错信息指向的是文件系统,不是表。
    import re as _re
    if _re.fullmatch(r"\{[a-z]+:[^}]+\}", token):
        raise ChainError(f"工序表里出现执行器不认识的占位符 {token};"
                         f"已实现的是 {{artifact:id}} / {{path:id}} / {{dir:id}}。")
    interp = (chain.bindings.get("interpreter") or {})
    # {python} 的来源按优先级:册级绑定显式给的 > 安装向导探到的引擎 > 当前解释器。
    # ★向导探到了正确的 3.12(带 pip、装了 docx),而册级绑定里是从别处拷来的
    #   精简 3.12(无 pip、无 docx)——两处真源,链信了旧的那个,编译器 import 失败。
    #   向导的探测报告是这台机器的事实,绑定里的解释器若不存在或不可用就不该赢。
    py = interp.get("python")
    if not py or not Path(py).exists():
        py = _probed_engine(chain) or sys.executable
    return (token
            .replace("{python}", py)
            .replace("{package}", str(PACKAGE_ROOT))
            .replace("{workspace}", str(chain.workspace))
            .replace("{volume}", chain.volume or ""))


CODE_KEY = "__code__"
"""状态里记「这一步的产物是被哪个版本的代码做出来的」。

**这不是重跑判据,是失效判据。** 执行器每次被调用都真跑那一步(实测:同一步连跑两次,
产物 mtime 都会更新),所以「代码变了要不要重跑本步」不是问题——你调它它就跑。

问题在**下游**:改了引擎却只重跑了本步,上游产物的 sha256 一个字没变,
于是 HOLD_INPUT_DRIFT 不响,下游看起来仍然是新鲜的。2026-08-20 实测撞上过一次:
carve_engine 补了 bodyBlocks,而蓝图那几步的输入哈希没变——链无从知道
它们的产物是旧代码的产物。当时是靠人删产物强制重跑的,「靠人记得」正是要消灭的东西。

记下来之后,--dry-run 就能回答「现在有哪些产物是旧代码做的」。
不阻断:阻断会把一次无害的注释改动变成全链重跑;报出来,由人决定重跑到哪一层。
"""


def code_digest(step: dict, chain: Chain) -> str | None:
    """这一步命令里引用的**包内脚本**的内容摘要。

    只认 {package}/ 开头的:那是随版本走的方法体。{python} 是解释器、
    {workspace} 是数据,都不算「这一步的代码」。
    """
    files = []
    for key in ("command", "command2", "command3", "command4"):
        for token in step.get(key) or []:
            if isinstance(token, str) and token.startswith("{package}/"):
                files.append(PACKAGE_ROOT / token[len("{package}/"):])
    if not files:
        return None
    h = hashlib.sha256()
    for path in sorted(set(files)):
        h.update(str(path).encode("utf-8"))
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def run_one(step: dict, chain: Chain, state: dict, dry: bool) -> dict:
    sid = step["id"]
    result = {"step": sid, "phase": step.get("phase")}

    # 1) 输入必须存在——跳步在结构上不可能
    #
    # 唯一的例外是**声明为 optional 的产物**:有些册天然没有某样东西(物理暑假
    # 讲义在 2026-08 之前就没有解析版),缺它不是错误,是这一册的形态。
    # optional 不是「可以不管」:它仍在工序表里、仍按 sha256 追踪、册里一旦提供
    # 就参与失效判定。它只是允许「这一册没有」这件事被如实表达,而不是逼人
    # 要么伪造一个空文件、要么把这一路径挪到表外私下判断——后者会让它脱离
    # 哈希追踪,源变了下游不会失效,那才是真的漏。
    missing = []
    for token in step["consumes"]:
        aid = token.split("@", 1)[0]
        if chain.resolve(aid):
            continue
        if (chain.spec(aid) or {}).get("optional"):
            continue
        missing.append(aid)
    if missing:
        result.update(status="blocked", why="输入不存在,拒绝运行", missing=missing)
        return result

    # 2) 输入 hash 必须与上次记录一致——上游变了下游自动失效
    drift = []
    for token in step["consumes"]:
        aid = token.split("@", 1)[0]
        if "@sha256" not in token:
            continue
        now = chain.digest(aid)
        was = state.get(aid)
        if was and now and was != now:
            drift.append({"artifact": aid, "was": was, "now": now})
    if drift:
        result.update(status="HOLD_INPUT_DRIFT",
                      why="上游产物已变,本步的既有产物已失效,须重跑上游后再来",
                      drift=drift)
        return result

    # 2.5) 代码漂移:本步的产物已经在,但做出它们的代码已经不是现在这份。
    # 只报不拦,理由见 CODE_KEY 的说明。
    now_code = code_digest(step, chain)
    was_code = (state.get(CODE_KEY) or {}).get(sid)
    if now_code and was_code and now_code != was_code:
        produced = [t.split("@", 1)[0].rstrip("'") for t in step["produces"]]
        if any(chain.resolve(aid) for aid in produced):
            result["codeDrift"] = {"was": was_code, "now": now_code,
                                   "why": "既有产物由另一版本的方法体做出,"
                                          "其下游的输入哈希不会因此变化"}

    # 3) 人做的步骤:只等,不假装
    if step.get("runner") == "human":
        absent = [t.split("@", 1)[0] for t in step["produces"]
                  if not chain.resolve(t.split("@", 1)[0])]
        if absent:
            result.update(status="awaiting-human", why="这一步由人做,产物尚不存在",
                          expected=absent)
        else:
            result.update(status="satisfied", why="人做的步骤,产物已在")
        return result

    # 3.5) 先把产物的父目录备好。
    # ★P6 空手复现抓出:多个步骤直接 open(OUT,'w'),假定目录已存在。
    # 原工作区里那些目录是历史遗留的,空工作区一个都没有——
    # 「以前建过」在工序表里没有痕迹,下一个人会在同一处失败。
    # 统一由执行器做,不逐个步骤打补丁:表已经写明每步产出什么,
    # 让每个步骤各自 mkdir 是同一件事写 N 遍,迟早漏掉一个。
    for token in step["produces"]:
        aid = token.split("@", 1)[0].rstrip("'")
        try:
            pattern = chain.pattern(aid)
        except Exception:
            continue
        if not pattern:
            continue
        target = chain.dir_for(aid) if "*" in str(pattern) else chain.path_for(aid).parent
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # 4) 有命令就跑;没有命令则如实报「无执行器」,不静默跳过
    commands = [step[k] for k in ("command", "command2", "command3", "command4") if step.get(k)]
    if not commands and step.get("externalRunner"):
        # 「由外部注册流程执行」与「表还没写全」必须分开——两者原先都报 no-runner,
        # 于是一个已知的交接点和一个未填的空洞长得一模一样。
        # 声明了入口的按 external-runner 报出,并把入口一并带上,让人按图索骥。
        result.update(status="external-runner",
                      entry=step["externalRunner"].get("entry"),
                      why=step["externalRunner"].get("why"))
        return result
    if not commands:
        result.update(status="no-runner",
                      why="表里没有给这一步命令。它可能仍由外部流程执行"
                          "(如 s5-word 走既有 9 步子表),也可能是表还没写全——"
                          "**两者必须分开**,故这里如实报出而不静默跳过。",
                      runner=step.get("runner"))
        return result
    if dry:
        result.update(status="dry-run",
                      commands=[[substitute(t, chain) for t in c] for c in commands])
        return result

    for cmd in commands:
        try:
            argv = [substitute(t, chain) for t in cmd]
        except ChainError as exc:
            # 占位符解析失败是这一步的失败,不是执行器的崩溃。崩溃连运行记录都不留。
            result.update(status="failed", why=f"命令占位符解析失败:{exc}", command=cmd)
            return result
        # 册级绑定的 interpreter.pythonpath 必须真的进子进程环境。
        # ★P6 空手复现抓出:这个键声明了、写了 why、**而没有任何代码读它**——
        # 子进程照默认环境跑,import lxml 直接失败。
        # 原工作区一直没暴露,因为我每次手敲都自己 export 了 PYTHONPATH:
        # 手跑掩盖了它,只有空手复现能看见。这正是「声明了但没落地」那一类,
        # 而它长得和「配置好了」一模一样。
        env = dict(os.environ)
        # vendor 里的 Word/PDF 脚本带内部调用守卫:必须由注册流程调用,防人手敲绕过。
        # 在包里,**执行器就是那个注册流程**。守卫保留(它防的正是我今天犯过的事——
        # 手敲一条命令绕过自家门),只是执行者从生产线的 orchestrator 换成本执行器。
        env.setdefault("CHENGZI_SUMMER_FORMAL_WORKFLOW_INTERNAL",
                       "chengziclass.summer-handout-word-production.v1")
        # 样式模板的参数表与规范:由册级绑定给出,注入给 vendor 脚本。
        # 生产线的审计/装订脚本原本自己猜参数表在 templates/summer-class-layout/——
        # 那是只有一套模板时代的假设;现在哪套模板由绑定说了算(技能包与样式模板解耦)。
        # 「资料根」在包里就是工作区:vendor 的审计脚本要求被审文档落在资料根之下
        # (生产线的安全边界),而在产品布局里册目录本身就是那个边界。
        env.setdefault("HANDOUT_INTAKE_MATERIALS_ROOT", str(chain.workspace))
        for env_key, art_id in (("HANDOUT_INTAKE_PARAMS_PATH", "params"),
                                ("HANDOUT_INTAKE_SPEC_PATH", "spec")):
            try:
                found = chain.resolve(art_id)
                if found:
                    env.setdefault(env_key, str(found[0]))
            except Exception:
                pass
        extra = (chain.bindings.get("interpreter") or {}).get("pythonpath")
        if extra:
            paths = [p if os.path.isabs(p) else str(chain.workspace / p)
                     for p in str(extra).split(os.pathsep) if p]
            if env.get("PYTHONPATH"):
                paths.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(paths)
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            result.update(status="failed", returncode=proc.returncode,
                          command=argv, stderrTail=proc.stderr[-800:],
                          stdoutTail=proc.stdout[-800:])
            return result
    result.update(status="ok", commands=len(commands))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--volume")
    ap.add_argument("--table", type=Path)
    ap.add_argument("--only", help="只跑这一步")
    ap.add_argument("--from", dest="from_step", help="从这一步开始")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    try:
        chain = Chain(args.workspace, args.volume, args.table)
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    dangling = chain.dangling()
    order, stuck = chain.topo()
    if dangling or stuck:
        print(json.dumps({"ok": False, "error": "表不自洽,拒绝执行",
                          "dangling": dangling, "unorderable": stuck},
                         ensure_ascii=False))
        return 1

    by_id = {s["id"]: s for s in chain.steps}
    if args.only:
        order = [args.only] if args.only in by_id else []
    elif args.from_step and args.from_step in order:
        order = order[order.index(args.from_step):]

    state = chain.load_state()
    results = []
    stopped = None
    for sid in order:
        r = run_one(by_id[sid], chain, state, args.dry_run)
        results.append(r)
        if r["status"] in ("failed", "HOLD_INPUT_DRIFT"):
            stopped = sid
            break                      # 硬停:继续跑下去只会在坏输入上叠加
        if r["status"] == "ok" and not args.dry_run:
            for token in by_id[sid]["produces"]:
                aid = token.split("@", 1)[0]
                d = chain.digest(aid)
                if d:
                    state[aid] = d
            cd = code_digest(by_id[sid], chain)
            if cd:
                state.setdefault(CODE_KEY, {})[sid] = cd
    if not args.dry_run:
        chain.save_state(state)

    summary = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    code_stale = [r["step"] for r in results if r.get("codeDrift")]
    if code_stale:
        summary["codeDrift"] = len(code_stale)
    report = {
        "schemaVersion": "handout-intake.chain-run.v1",
        "ranAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": str(chain.workspace), "volume": chain.volume,
        "dryRun": args.dry_run, "planned": len(order),
        "summary": summary, "stoppedAt": stopped, "results": results,
        "codeDriftSteps": code_stale,
        "codeDriftNote": ("这些步骤的既有产物由另一版本的方法体做出。不阻断——"
                          "重跑到哪一层由人定。**不重跑的话,下游不会因此失效**:"
                          "产物的 sha256 没变,HOLD_INPUT_DRIFT 看不见代码。"),
        # ★ok 必须意味着「这条链跑完了」,不能只意味着「没有步骤崩溃」。
        # P6 空手复现里,一条只跑完 6/25、其余 16 步 blocked 的链报了 ok:true——
        # 因为 blocked 不是 failed。**报 ok 的空转链,比报错的链更难发现。**
        "ok": stopped is None and not summary.get("blocked")
              and not summary.get("no-runner") and not summary.get("awaiting-human"),
        "okRule": "ok = 没有步骤失败/被拒/无执行器/等人。任一存在即 false——"
                  "只跑完一部分不算跑完。",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")

    # ★每次运行自动落一份记录到册目录 runs/<时间戳>/。
    # 使用方 2026-08-15 定:「每次做完都要有记录、经验总结……每一步要有记录,遇到问题才能排查。」
    # 记录不是可选项:它是经验层的输入。跑完不落记录,经验层就只能靠人事后回忆——
    # 而今天最大的教训是,写在规范里没有门守着的纪律等于没有(我自己写了执行器又手敲绕过)。
    # 记录里带一个 debrief 段,由人(或智能体代人问过后)填:本次裁决了什么、学到什么。
    # 未填时 debrief.status=pending;经验层准入门只收 filled 的。
    if not args.dry_run and chain.volume_dir:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = chain.volume_dir / "runs" / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
        (run_dir / "state-after.json").write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                                                  encoding="utf-8")
        debrief = {"schemaVersion": "handout-intake.debrief.v1", "run": stamp,
                   "status": "pending",
                   "fill": {"decisionsMade": [], "surprises": [], "candidateRules": [],
                            "filledBy": None, "filledAt": None},
                   "why": "本段未填时本次运行不算完:经验层准入门只收 filled 的记录。"
                          "填的是人:这一次裁决了什么、哪里出乎意料、有没有可归纳成规律的。"}
        (run_dir / "debrief.json").write_text(json.dumps(debrief, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
        report["runRecord"] = str(run_dir)
    print(json.dumps({k: report.get(k) for k in
                      ("planned", "summary", "stoppedAt", "ok", "runRecord")},
                     ensure_ascii=False, indent=1))
    return 0 if stopped is None else 1


if __name__ == "__main__":
    sys.exit(main())
