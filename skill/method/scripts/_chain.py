#!/usr/bin/env python3
"""工序链的公共底座:读表、解析路径、算 hash、拓扑排序。

check_chain 与 run_chain 都从这里拿这四件事。分成两份写过一次映射的项目,
最后都会在两份映射漂移的那天出事——注册册里 P8「判据恒假」记的正是这一类。
所以路径解析在整个包里只有这一处实现。

**路径不在工序表里。** 工序表只有逻辑 id;id → 路径由两层决定:
  1. 表内 artifacts.<id>.defaultPath —— 公有约定,随包分发
  2. 册级 bindings.json 的 paths.<id> —— 私有覆盖,随工作区
external 的 id 没有默认值,必须由册级绑定,否则报 unbound 而不是猜一个。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

GROWTH_DIRNAME = ".handout-intake"
SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLE = SKILL_ROOT / "method" / "steps.v1.json"


class ChainError(RuntimeError):
    """表或绑定本身有问题——不是「还没做」,是「说不通」。"""


def _bare(token: str) -> str:
    """去掉 @sha256 / @sha256' 后缀。后缀只标记要记 hash 与代次,不入 id。"""
    return token.split("@", 1)[0]


def _generation(token: str) -> str:
    """media@sha256' 与 media@sha256 是同一个 id 的两个代次。

    撇号不是笔误:它表达「本步之后 media 的内容变了」。s5 consumes 的是撇号那一代,
    所以 s4h 必须在 s5 之前——JPEG 修复被重建蓝图冲掉那个坑,结构上就死在这里。
    """
    suffix = token.split("@", 1)[1] if "@" in token else ""
    return "'" if suffix.endswith("'") else ""


class Chain:
    def __init__(self, workspace: Path, volume: str | None = None,
                 table_path: Path | None = None):
        self.workspace = workspace.resolve()
        self.table_path = (table_path or DEFAULT_TABLE).resolve()
        self.table = json.loads(self.table_path.read_text(encoding="utf-8"))
        self.artifacts = {k: v for k, v in self.table["artifacts"].items()
                          if not k.startswith("_")}
        self.steps = self.table["steps"]
        self.volume, self.volume_dir, self.bindings = self._load_bindings(volume)
        self._validate_table()

    # ---- 绑定 ----------------------------------------------------------

    def _load_bindings(self, volume: str | None):
        # 产品布局(使用方 2026-08-15 定):册目录本身就是工作区,bindings.json 在它根下。
        #   handout-intake/volumes/<册>/{inputs,work,output,decisions,runs,bindings.json}
        # 旧布局(工作区/.handout-intake/volumes/<册>/)仍认,不强迫已有工作区搬家。
        # 两种布局的判定只看事实:根下有没有 bindings.json。
        direct = self.workspace / "bindings.json"
        if direct.exists():
            return (volume or self.workspace.name), self.workspace, \
                   json.loads(direct.read_text(encoding="utf-8"))
        vols = self.workspace / GROWTH_DIRNAME / "volumes"
        if volume is None:
            found = sorted(p for p in vols.glob("*") if (p / "bindings.json").exists()) \
                if vols.is_dir() else []
            if len(found) == 1:
                volume = found[0].name
            elif len(found) > 1:
                raise ChainError(
                    f"工作区有 {len(found)} 册,必须用 --volume 指定其一:"
                    f"{[p.name for p in found]}")
            else:
                return None, None, {}
        vdir = vols / volume
        bpath = vdir / "bindings.json"
        if not bpath.exists():
            return volume, vdir, {}
        return volume, vdir, json.loads(bpath.read_text(encoding="utf-8"))

    def _validate_table(self):
        """表自身的自洽:consumes/produces 引用的 id 必须在 artifacts 里登记过。

        这一条先于依赖检查——id 打错字时,不登记就该当场报,而不是被当成
        「一个没人产出的外部输入」静默放行。
        """
        unknown = []
        for step in self.steps:
            for token in list(step["consumes"]) + list(step["produces"]):
                if _bare(token) not in self.artifacts:
                    unknown.append((step["id"], token))
        if unknown:
            lines = "\n".join(f"    {sid} 引用了未登记的 {tok}" for sid, tok in unknown)
            raise ChainError(f"表引用了 artifacts 里没有的 id:\n{lines}")

    # ---- 解析 ----------------------------------------------------------

    def spec(self, artifact_id: str) -> dict[str, Any]:
        try:
            return self.artifacts[artifact_id]
        except KeyError:
            raise ChainError(f"未登记的产物 id:{artifact_id}")

    def pattern(self, artifact_id: str) -> str | None:
        """逻辑 id → 路径模式。返回 None 表示 external 未绑定。"""
        override = (self.bindings.get("paths") or {}).get(artifact_id)
        if override:
            return override
        return self.spec(artifact_id).get("defaultPath")

    def resolve(self, artifact_id: str) -> list[Path]:
        """逻辑 id → 实际存在的文件列表。不存在则返回空表(不抛)。"""
        pat = self.pattern(artifact_id)
        if pat is None:
            return []
        pat = os.path.expanduser(pat)
        if not os.path.isabs(pat):
            pat = str(self.workspace / pat)
        if not glob.has_magic(pat):
            p = Path(pat)
            return [p] if p.is_file() else []
        return [Path(p) for p in sorted(glob.glob(pat, recursive=True)) if os.path.isfile(p)]

    def path_for(self, artifact_id: str) -> Path:
        """逻辑 id → 单一绝对路径,**不要求文件已存在**。

        resolve() 回答「现在有哪些」,path_for 回答「该写到哪」。产物在被产出之前
        resolve() 必然为空,步骤脚本要的是后者。多文件产物在这里报错而不是猜一个——
        「宁可拒绝,不可猜」。
        """
        pat = self.pattern(artifact_id)
        if pat is None:
            raise ChainError(f"{artifact_id} 是 external 且册级 bindings.json 未绑定,"
                             f"没有默认路径可用")
        if glob.has_magic(pat):
            raise ChainError(f"{artifact_id} 是多文件产物({pat}),"
                             f"要单一路径请用 dir_for(),要现有文件请用 resolve()")
        pat = os.path.expanduser(pat)
        return Path(pat) if os.path.isabs(pat) else self.workspace / pat

    def dir_for(self, artifact_id: str) -> Path:
        """多文件产物的容身目录(通配符之前的那一段)。"""
        pat = self.pattern(artifact_id)
        if pat is None:
            raise ChainError(f"{artifact_id} 未绑定")
        pat = os.path.expanduser(pat)
        base = Path(pat) if os.path.isabs(pat) else self.workspace / pat
        while glob.has_magic(base.name) or base.name == "**":
            base = base.parent
        return base

    def only(self, artifact_id: str) -> Path:
        """恰好一个现存文件;零个或多个都报错,不静默取第一个。"""
        found = self.resolve(artifact_id)
        if len(found) != 1:
            raise ChainError(f"{artifact_id} 期望恰好 1 个文件,实际 {len(found)} 个"
                             f"(模式 {self.pattern(artifact_id)})")
        return found[0]

    def scope_lessons(self) -> list[int] | None:
        """本册做哪几讲。None = 源里有几讲就做几讲。

        首版没有这个接口,于是 fingerprint / census / split_and_normalise 各自
        在文件头写死 `WANT = {10, 11, 12, 13, 14}`——同一个事实抄了三份,而且是
        某一册的事实抄进了产品。第二册要做别的讲就得改代码,这正是 _bootstrap
        的 docstring 里说要消灭的东西:路径那一层治好了,范围这一层没治。

        bindings 写法(三种等价):
            "scope": {"lessons": "all"}      源里有几讲做几讲(缺省)
            "scope": {"lessons": "10-14"}    闭区间
            "scope": {"lessons": [1, 3, 7]}  显式列举
        """
        raw = (self.bindings.get("scope") or {}).get("lessons")
        if raw in (None, "", "all", "ALL"):
            return None
        if isinstance(raw, str):
            text = raw.strip()
            match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", text)
            if match:
                low, high = int(match.group(1)), int(match.group(2))
                if low > high:
                    raise ChainError(f"scope.lessons 区间反了:{text}")
                return list(range(low, high + 1))
            try:
                return sorted({int(part) for part in re.split(r"[,、\s]+", text) if part})
            except ValueError:
                raise ChainError(f"scope.lessons 看不懂:{raw!r};"
                                 f'用 "all"、"10-14" 或 [1, 3, 7]')
        if isinstance(raw, list):
            try:
                return sorted({int(item) for item in raw})
            except (TypeError, ValueError):
                raise ChainError(f"scope.lessons 列表里有非整数:{raw!r}")
        raise ChainError(f"scope.lessons 类型不支持:{type(raw).__name__}")

    def unbound_externals(self) -> list[str]:
        return sorted(aid for aid, spec in self.artifacts.items()
                      if spec.get("external") and self.pattern(aid) is None)

    def externals(self) -> set[str]:
        return {aid for aid, spec in self.artifacts.items() if spec.get("external")}

    # ---- hash ----------------------------------------------------------

    def digest(self, artifact_id: str) -> str | None:
        """产物指纹。多文件时按文件名排序串联,文件名参与摘要——
        少了一个文件与改了一个文件都必须让指纹变化。"""
        paths = self.resolve(artifact_id)
        if not paths:
            return None
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(p.name.encode())
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        return h.hexdigest()[:16]

    # ---- 拓扑 ----------------------------------------------------------

    def produced_by(self) -> dict[str, str]:
        """(id, 代次) → 产出它的步骤 id。"""
        out = {}
        for step in self.steps:
            for token in step["produces"]:
                out.setdefault((_bare(token), _generation(token)), step["id"])
        return out

    def dangling(self) -> list[tuple[str, str]]:
        """既无上游产出、又不是 external 的依赖——表写错了。"""
        made = self.produced_by()
        ext = self.externals()
        bad = []
        for step in self.steps:
            for token in step["consumes"]:
                key = (_bare(token), _generation(token))
                if key in made or _bare(token) in ext:
                    continue
                bad.append((step["id"], token))
        return bad

    def topo(self) -> tuple[list[str], list[str]]:
        """(可排序的步骤 id 序列, 排不进去的步骤 id)。后者非空即成环或依赖不可满足。"""
        done = {(aid, "") for aid in self.externals()}
        order, remaining, progressed = [], list(self.steps), True
        while remaining and progressed:
            progressed = False
            for step in list(remaining):
                need = [(_bare(t), _generation(t)) for t in step["consumes"]]
                if all(k in done or (k[0] in self.externals() and k[1] == "") for k in need):
                    order.append(step["id"])
                    for token in step["produces"]:
                        done.add((_bare(token), _generation(token)))
                    remaining.remove(step)
                    progressed = True
        return order, [s["id"] for s in remaining]

    # ---- 状态 ----------------------------------------------------------

    def state_path(self) -> Path:
        """链状态落在册级私有目录。公有的包目录只读,不接受运行时写入。"""
        if self.volume_dir is None:
            return self.workspace / GROWTH_DIRNAME / "volumes" / "_unbound" / "chain-state.json"
        return self.volume_dir / "chain-state.json"

    def load_state(self) -> dict[str, str]:
        p = self.state_path()
        return json.loads(p.read_text(encoding="utf-8")).get("digests", {}) if p.exists() else {}

    def save_state(self, digests: dict[str, str]) -> Path:
        p = self.state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "schemaVersion": "handout-intake.chain-state.v1",
            "table": str(self.table_path),
            "volume": self.volume,
            "digests": digests,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return p
