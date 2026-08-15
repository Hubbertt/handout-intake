# AGENTS.md · handout-intake(Codex 入口)

本文件与 `README.md` 正文相同,是给 Codex 的入口壳。**只改 README.md**,再跑
`python3 skill/method/scripts/sync_entrypoints.py` 同步到这里与 SKILL.md。

Codex 特别注意:
- 所有 Word/Acrobat 交互已是静默的(无 activate、无弹窗),不要为了「看见」而改成 activate。
- 交给 Word 的文件必须在它的沙盒容器内(`~/Library/Containers/com.microsoft.Word/…`),包内脚本已按此中转;不要直接把工作区路径塞给 Word。
- 每步跑完看 `volumes/<册>/.handout-intake/volumes/<册>/runs/<时间戳>/`,不要凭屏幕输出判断成功。

---

# 讲义入库(handout-intake)

你拿到的是一个**可安装、可配置、可分享**的产品,不是一堆脚本。它分五层,各层独立目录:

| 目录 | 是什么 | 你会碰它吗 |
|---|---|---|
| `skill/` | 工序表 + 引擎 + 门 + 脚本 | 不碰。整体升级。 |
| `styles/` | 样式模板库(参数表 + 规范 + 预览) | 用户选模板时看;改样式时改 `params.json` |
| `runtime/` | 环境依赖 + 安装向导 + 路径配置 | 第一次用、换机器、缺依赖时 |
| `volumes/<册>/` | 每册一个工作区 | 每次做一册都在这 |
| `experience/` | 经验规律 + 候选 | 每次做完沉淀时 |

---

## 第一次使用:先问用户,再装

**不要直接开跑。** 先跑安装向导,它会探测环境并列出缺什么:

```bash
python3 runtime/install_wizard.py --probe-only
```

它会告诉你:Python(要 ≥3.12 且带 pip)、5 个 Python 包、Microsoft Word(必需)、Adobe Acrobat(付印时必需)、样式模板声明的字体,各自有没有。

**把缺项告诉用户,逐项征得授权**,然后:

```bash
python3 runtime/install_wizard.py            # 交互:逐项问
python3 runtime/install_wizard.py --yes      # 用户已全部同意时
```

- Python 包:向导 `pip install`,一键装
- Word / Acrobat / 字体:向导**不代装**(涉及许可与管理员权限),会给出装法,由用户自己装
- 找不到 ≥3.12 时向导给三种方式;已装在别处则 `export HANDOUT_INTAKE_PYTHON=/path/to/python3.12`

装好后向导**自动渲染一次样式预览**——预览图不在包里,必须在这台机器上真渲一次才算数(字体、Word 版本都会让别人机器上的图不准)。

---

## 选样式

```bash
python3 styles/render_catalog.py       # 渲染所有模板的预览(按 params 的 sha256 判过期)
```

看 `styles/catalog.json` 与 `styles/renders/<模板>/*.png`,把图给用户选。选定后写进册的 `bindings.json` 的 `paths.params` / `paths.spec`。

**改了样式就重渲**——渲染器自己会发现参数变了。

---

## 做一册

1. 初始化一册(会生成 `bindings.json` 模板,**不要从别的册拷**——拷来的带着别的机器的事实):

```bash
python3 skill/method/scripts/init_workspace.py --workspace volumes/<册名> --volume <册id>
```

2. 用户只放三样进 `volumes/<册名>/inputs/`:源 Word、封面(`cover.pdf`)、封底(`back.svg` 或 `.pdf`)。
   再放这一册的切分规则(`carve-rules-provisional/`,含私有规范映射)与真值图(`quality/step0-truth-map.v1.md`,人写)。
3. 打开 `volumes/<册名>/.handout-intake/volumes/<册id>/bindings.json`,只改带 `<…>` 的几处:
   源文件名、册主题、册键、**选一组样式**(`params` 指向 `styles/compositions/<根>+<包>/params.json`,
   没有的组合先 `python3 styles/compose.py --root X --pack Y`)。输出默认落 `output/`,可改。
   **不要写死解释器**——由安装向导探测(`runtime/probe-report.json`)。
4. 跑:

```bash
python3 skill/method/scripts/run_chain.py --workspace volumes/<册名> --volume <册id>
```

它按工序表拓扑执行,每步产物带 sha256,上游一变下游自动失效。**它遇到不敢猜的会停**并说明为什么。
成品:`output/<按规范命名>.docx`(Word)与 `output/print-master.pdf`(付印件:装订+转曲,0 字体 0 文本)。
没有 Acrobat 时付印步会明确拒绝——装订件 `output/print/standard-pdf/` 可预览校对,**不可付印**。

5. 有待裁的:把问题和选项讲给用户,用户定了就:

```bash
python3 skill/method/scripts/decisions.py decide --volume-dir volumes/<册名> --id <id> --choice <选项> --by <谁> --why "<理由>"
```

理由必填——没有理由的裁决进不了经验层。裁完再跑链,它从队列里取已裁的,不再问。

---

## 每次做完:记录 + 沉淀

每次跑链都会自动落 `volumes/<册名>/runs/<时间戳>/`:
- `run.json` 逐步结果 · `state-after.json` 产物哈希 · `debrief.json` **待你填**

**未填的 debrief 不算完。** 把这次裁决了什么、哪里出乎意料、有没有可归纳的写进 `debrief.json` 的 `fill`,把 `status` 改成 `filled`。

然后收割:

```bash
python3 skill/method/scripts/harvest_debriefs.py --volumes-root volumes --out experience/candidates.json
```

候选不是规律。要进 `experience/rules.v1.json` 必须过四判据(现象/判据/处置/本质、扫全类命中数、一道门、破坏性自证)并带 `fromRuns`,由准入门校验:

```bash
python3 skill/method/gates/gate_experience_admission.py --rules experience/rules.v1.json --package skill --volumes-root volumes
```

---

## 出了问题怎么查

每一步都有记录:`runs/<时间戳>/run.json` 里有每步的状态、命令、stderr 尾巴。`check_chain.py --workspace <册>` 看哪个产物漂了。链**只在有 failed 时才停**,blocked/awaiting-human/external-runner 各自有明确状态,不会混成一个「没跑」。

---

## 分享

```bash
python3 skill/method/scripts/export_package.py --out handout-intake.zip
```

默认只导 `skill/` + `styles/<id>/`(不含渲染图)+ `runtime/` 的声明与向导。册子与经验层含源文片段,要 `--include-grown --i-understand-copyright` 两个开关。导出前自检:包内无本机路径、版本一致、清单可解析——不过就不导。

---

## 三条纪律(写给你,也写给我自己)

- **不猜。** 判不了的挂队列问人,不填一个看着合理的值。
- **不手敲绕过链。** 执行器存在的理由就是不给机会;绕过它的那次今天已经把 ❌ 带进了 PDF。
- **未填 debrief 不算完。** 写在规范里没有门守着的纪律等于没有——所以这一条有门。
