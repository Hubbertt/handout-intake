# handout-intake

**把 Word 讲义原稿变成付印 PDF 的可安装工具包** —— 切分 → 按样式重排 → Word → 装订转曲。
给三样东西（源 Word、封面、封底）和这一册的切分规则，它出成品；遇到不敢猜的会停下来问你。

> 面向有固定版式约定的成套教辅资料（讲义、练习册、知识清单）。由橙子教室在真实生产中打磨出来，
> 首个用例是一本 118 页的八年级物理讲义。方法、引擎、门、样式模板与切分规则全部随包分发；**不含任何来源原文**。

---

## 原子化是什么

> PM 2026-08-20 定义。**这一节是本包对「原子化」的唯一口径**,与它不符的实现是实现的问题。

原子化 = 把一份资料拆成**内容模块**,并记下**排版布局等非内容信息**。两方面缺一不可。

**一、内容模块** —— 拆到"这是什么"的粒度:

- 什么是**题干**、什么是**选项**、什么是**解析**、什么是**答案**、什么是**填空的空**
- 它们分别属于**哪一道题**
- **哪些东西组成了一道题**(一个题干 + 若干选项 + 若干小问 + 若干图 + 一个答案 + 一份解析)

**二、排版布局等非内容信息** —— 记下"它在源文件里长什么样、在哪"。

**这一半是为展示与编制成册服务的。** 内容模块回答"这是什么",排版布局回答"它该怎么呈现"。
没有后者,编制成册只能靠猜,而猜出来的东西会**漂**。

举例(**只是举例,不是清单**):

- 每个模块在源文件中处于**哪道题的什么位置**
- 图片大小**相对于所在模块正文字号的比例** —— ★这一条尤其要紧:
  **只记绝对尺寸,图片大小必然漂移**。同一个 `widthEmu` 放进不同页宽、不同正文字号的版式里,
  观感完全不同;而相对比例是跟着正文走的,换一套版式仍然对。
- 图片的**环绕方式**
- 其余版式事实同理

### 最终判准:**能据两类信息完整还原出源文件**

> PM 2026-08-20:「具体的非内容信息有很多,我没办法直接全部告诉你,但你可以做的时候不断汇总整理,
> 完善规范,并且记录到包内,做到原子化后能根据原子化的两类信息完整地还原出源文件。」

这条判准自带一个好处:**不必猜「还有哪些非内容信息」**。列清单永远列不全,而且列出来的
多半是自己想得到的那几样。**让还原去告诉你——往返一圈丢了什么,什么就是没记。**

机制(`s1d-noncontent-survey`,产物 `quality/noncontent-facts.survey.json`):

1. 扫源里**实际出现过**的版式事实并计数(段落属性、字符属性、图形锚定与环绕、表格、分节…)
2. 逐项标注「原子化记了没有、记在哪」
3. 没记的自动成为下一轮的清单;**登记表里都没有的**单独报出来——那是连想都还没想到的

★**只登记这一份源里真实出现过的**,不列理论上可能有的字段。
登记一个从未出现的字段,就是给自己造一条恒假的待办。

**做法:非内容层与内容层分开存**(`s4c5-capture-layout` → `work/layout.json`)。
不逐个补字段——**逐字捕获** pPr / rPr / 图形锚定与环绕 / 表格三层属性 / 分节属性,
按 locator 归位。新出现的事实自动被记下,不必等人想起来。
照着清单逐个补,下一份源换一批事实又得重来,**枚举永远不全**。

**两道门**:
- `s1d-noncontent-survey` —— 源里出现的每一类事实,记了没有。**「记了没有」由它读非内容层的实际产物自动核**,
  不手维护清单:手维护的表必然与实现漂移,而且漂了没人知道。
- `s4c6-reconstructible` —— **最终判准**。逐块、逐字、逐属性地比:
  源里每一段文本必须在内容层里有归属,每个属性必须在非内容层里有同 locator 的记录且值相等,
  每个图形的锚定与环绕必须对上。**不给百分比**——还原是全有或全无,少一项重建出来的就不是原来那份。

**实测(2026 物理教师版,2026-08-20)**:

| | 首轮 | 建了非内容层之后 |
|---|---|---|
| 版式事实类别覆盖 | 11 / 47 | **47 / 47** |
| 版式逐属性比对 | — | **0 差异**(pPr 键、值、图形锚定全部对上) |
| 字符归属 | 段落级报 100%(把差距盖住了) | **0 未归属** |
| `s4c6` 逐块逐字逐属性 | — | **pass,零损失** |

★「类别覆盖全」不等于「能还原」:记住「源里有 spacing 这一类」与「第 137 段的 spacing 是多少」是两回事。
前者是 `s1d` 在问的,后者才是 `s4c6`。

★**量法本身也会骗人,而且骗得比缺陷更像真的。** 建这两道门的过程里,量法错了四次:

| 错法 | 报出来的假象 |
|---|---|
| 拿 mcparse 的段号比引擎的 `locator`(引擎只数 body 直接子节点、表格另编号) | 「138 个孤立答案」 |
| 逐档对账的正则只认「讲/章」,不认「卷」 | 「10 档数目不一致」 |
| 表格与分节其实记了,但计数口径漏了 | 普查报「tcPr/gridCol/sectPr 未记」 |
| `s4c6` 用文件名 `A01-序言-…` 去查内容层,而那里的键是 `第A01讲` | **「3990 段无归属」,真实值是 0** |

四次都是**一查就散**,但若不复核就报出去,每一条都会变成一场虚惊、或一次白干的返工。
所以本包的纪律是:**报一个坏消息之前,先证明量法是对的**——尤其当数字大得吓人时,
先怀疑量法,再怀疑数据。

★文档标识统一用 registry 的 `lesson`(`第A01讲`),不用分档文件名(`A01-序言-…`)。
两层各用各的键,对账时必然全对不上;`s4c5`/`s4c6` 都从 registry 取,不各猜一套。
其中最要紧的几条:

| 未记的事实 | 出现 | 不记会怎样 |
|---|---|---|
| `rPr/rFonts` | 9,731 | 字体族丢失 |
| `pPr/spacing` `pPr/ind` | 6,631 / 5,999 | 行距与缩进丢失,版面必然漂 |
| `pPr/shd` `rPr/shd` | 1,175 / 40 | 底纹丢失(源里的强调标记) |
| `drawing/anchor` + `wrapNone` | 65 / 65 | **浮动图与环绕方式丢失**——而原子里 535 张图**全部**记作 `floating:false`,与源对不上 |
| `tcPr` `gridCol` | 1,067 / 287 | 表格的单元格属性与列宽丢失 |
| `sectPr/pgSz` `pgMar` `cols` | 各 1 | 页面尺寸、页边距、分栏丢失 |

### 完整性的判准之一:**每一个字符都应该有归属和分类**

不是"每一段有没有落地",是**每一个字符**:它属于哪一道题、是那道题的哪一个模块
(题干 / 选项 / 空 / 答案 / 解析 / 图注 / 表格单元 …)。

这条判准很硬,但它是对的——因为**没有归属的字符,就是不知道该怎么处理的字符**:
编制成册时它不知道该排进哪里,导入题库时它不知道该进哪个字段。
段落级的归属看起来够用,直到遇到一段里混着两种模块:
「1．下列说法正确的是（ ）A．… B．…」这一段里,题干和选项挤在同一段,
段落级归属只能整段判成一种,另一种就没了归属。

★**判准不是"字段填满了没有",是"能不能据此重现原貌"。**
上面列的几项是例子,不代表全部;遇到一个新的版式事实,问的是
「不记它,编制成册时会不会漂、会不会只能靠猜」——会,就得记。

★**标题不是知识点。** 「知识点01 长度的单位」是那一段的标语,把它直接当成一个知识点实体,
是把结构层的东西升格成语义层。原子化**只记结构**:这道题落在哪个标题底下、那个标题的原文是什么。
它是不是一个知识点、叫什么名字、跟别的知识点什么关系——**那是后面的事,不在原子化里**。

★**源里没有的,不要造。** 难度、知识点归属、题型分类,源里有就记、没有就没有。
系统若因下游数据模型必须填一个值,**必须另记来源**,让"源没说"这件事看得见。

★原子化在链条中的位置:**原子化 → 导入题库 → 私有规范 → 编制成册**。
导入题库在原子化之后即可做,是有实际意义的最短线路;编制成册是最长线路。

### 当前实现对照(2026-08-20 实测,诚实记账)

| 定义要求 | 现状 |
|---|---|
| 题干 / 选项 / 答案 / 解析 / 小问分离 | 有,各成字段 |
| 哪些组成一道题 | 有(`optionGroups` / `subQuestions` / `figureOwners`) |
| 在源文件中的位置 | 有,`locator` 到段;选项另带 `range` 字符区间 |
| **填空的空** | **缺**。187/402 道题的文本里有空,但 **0 个结构字段**——空有几个、在句中什么位置、每个空对应哪一段答案,都没记 |
| 图相对正文字号的比例 | **缺**。只有绝对值 `widthEmu/heightEmu` |
| 图的环绕方式 | **形同虚设**。只有一个 `floating` 布尔,535 张全为 false——**一条从未说过话的判据** |
| **每个字符都有归属和分类** | **82.4%**(238,454 字符中 196,389 有归属)。差的 17.6% 不是边角料,而是**小问 15,471 / 正文 13,486 / 答案解析段 7,968 / 选项行 2,698 / 圈号项 1,244** 字符——都是正经模块没对上,说明 `locator` 的对齐有系统性问题(表格内段落、mc 分支等),不是"少记几个字段" |

★注意此前报的「源文本未见 0 段」是**段落级**的覆盖,它把上面这 17.6% 的差距整个盖住了:
一段只要有**任何**归属就算数,段内混着的另一类就此消失。**判准换成字符级,数字立刻从 100% 掉到 82.4%。**
段落级的数只能证明"没有整段丢失",不能证明"每个字符都有归属"。

缺的几项不写在"待办"里而写在这里,是因为**它们属于定义**:做不到就是原子化没做完,
不能因为题库那条线用不上就当作已完成。

★这张表也不是验收清单——它只是"当前已知的差距"。判准仍是**能不能据此重现原貌**:
今天没列进来的版式事实,不等于不需要记。

## 需要什么

| | 必需 | 用途 |
|---|---|---|
| **macOS + Microsoft Word** | ✅ | 净开探针、目录域、PDF 导出、页面审计全走 Word 原生引擎（LibreOffice 会误判字距，不可替代） |
| Adobe Acrobat | 付印时 | 成品转曲（0 残留字体、0 可提取文本，印刷厂机器无关）。没有它可出装订件预览，不能付印 |
| Python ≥ 3.12 + 5 个包 | ✅ | 安装向导探测并帮你装（lxml / python-docx / PyMuPDF / pypdf / Pillow / openpyxl） |

其它平台未验证。所有验证在一台 Apple Silicon Mac 上完成，含一次 `env -i` 干净沙盒。

## 三条命令跑起来

```bash
unzip handout-intake-*.zip && cd handout-intake
python3 runtime/install_wizard.py        # 探测环境、逐项征得同意后安装、装好自动渲一遍样式预览
```
看 `styles/renders/<模板>/*.png` 挑样式（预览是在**你的机器**上渲的，不是包里带的图）。

```bash
mkdir -p volumes/my-book && python3 skill/method/scripts/init_workspace.py --workspace volumes/my-book --volume my-book
# 把源 Word / cover.pdf / back.svg 放进 volumes/my-book/inputs/,切分规则放 carve-rules-provisional/
# 打开生成的 bindings.json,只填带 <…> 的几处
python3 skill/method/scripts/run_chain.py --workspace volumes/my-book --volume my-book
```
成品在 `volumes/my-book/output/`：`.docx` 与 `print-master.pdf`。每次运行的逐步记录在 `runs/<时间戳>/`。

## 它是怎么工作的

```
skill/       方法:工序表(36 步,拓扑排序,每步产物带 sha256,上游一变下游自动失效)+ 引擎 + 门 + 种子
styles/      样式:1 个全局默认根 × N 个局部偏离包 = 一组模板;根与包独立、任意组合、三层可命名
runtime/     环境:依赖声明 + 安装向导(探测→授权→安装→渲预览)
volumes/     每册一个目录:inputs / work / output / decisions / runs   ← 你的东西,不在包里
experience/  经验层:规律 / 观察 / 否决,准入门守着(每条须配一道门 + 一次破坏性自证)
```

三条设计纪律，全部由数据与门强制而非靠人记得：
- **跳步在结构上不可能**——输入产物不存在就拒绝运行
- **不猜**——不确定的停下报状态，不填一个看着合理的值
- **每次改样式都渲一次**——渲染图不进包，在目标环境渲一次才算数

## 装到别处、被别人拷走:两件事要有门守着

**① 开发树 ≠ 安装树。** 改代码在开发树,真正被跑的是安装树
(Claude Code 的 `.claude/skills/handout-intake` 就是指向安装树的符号链接)。
两者之间不要手敲 `rsync`——漏一次就在跑旧代码,而没有任何东西会说话。

```bash
python3 skill/method/scripts/install_package.py --to <安装树>            # 导出自检 → 同步 → 写 INSTALLED.json
python3 skill/method/scripts/install_package.py --to <安装树> --check    # 只核不写
```

`--check` 分两问报,因为处置不同:
`TARGET_MODIFIED` = 有人手改了安装树(安装树是产物,不该手改);
`SOURCE_NEWER` = 源改了没重装(重装即可)。
同步**不是镜像**:`volumes/`、`styles/compositions/`、`runtime/probe-report.json`
是在安装树那边长出来的,属于使用方,不删。

**② 被别的系统拷走(vendoring)。** 生产环境读不到本包所在的外置卷时,
消费方会把引擎与种子拷一份进自己那里。拷贝本身没错——是边界所迫;
错在拷完就没人再看一眼上游有没有动。

```bash
python3 skill/method/scripts/check_vendored_consumers.py
```

登记表 `runtime/vendor-consumers.json` **只记去哪找**,拷了什么由消费方自己的
`PROVENANCE.md` 记(那是它的真源,在这里再抄一份就是第二真源)。
门把 `PROVENANCE.md` 声明的每个上游 sha256 拿到当前包里重算,并区分两类:
`copied`(逐字拷贝,可机械重拷)与 `ported`(重写移植,**不能机械同步**,
要有人读懂上游改了什么再决定跟不跟)。

★这道门可能长期为红,而且**不该由本包来消红**——重新 vendor 是消费方的事。
红是交接信号,不是失败。

## 两个能力,两个去处

PM 2026-08-22 定:要的是**两个技能** —— 原子化、编制成册。切口不是设计出来的,是实测出来的:
按 `consumes`/`produces` 算依赖闭包,接口面只有三个产物,**反向边 0 条**,
由 `GATE_CAPABILITY_CUT` 守着(它守的不是「现在是不是 0 条」,而是「以后还是不是」)。

| | 步数 | 需要 | 分发 |
|---|---|---|---|
| **原子化** | 14 | `lxml` / `Pillow` / `psycopg`。**不要 macOS + Word** | [`handout-atomizer`](https://github.com/Hubbertt/handout-atomizer)(private) |
| **编制成册** | 28 | macOS + Word + Acrobat + `styles/` | 本仓 |

```bash
python3 skill/method/scripts/export_package.py --out <目录或.zip> --capability atomise
python3 skill/method/scripts/run_chain.py --workspace <册> --volume <册> --capability atomise
```

★ **`handout-atomizer` 是分发仓,不是开发仓。** 代码、门、判据都在本仓守着;
在那边改会被下一次导出覆盖,而且同一段逻辑就有了两个真源——
那条学费 2026-08-22 刚付过一轮(题库那份 vendored 引擎已经分叉到 86 道题切法不同)。

## 给不同宿主的入口

任何能开 shell、读文件的智能体或人都能用；包对智能体零依赖。宿主认哪个入口文件：

| 宿主 | 读 |
|---|---|
| Claude Code | `SKILL.md` |
| Codex | `AGENTS.md` |
| Kimi / 其他 / 人 | 本 `README.md` |

三份正文相同，只改 README，`skill/method/scripts/sync_entrypoints.py` 同步。

## 状态

`VERSION` 是当前版本。`PACKAGE.json` 记录每一版的验收证据（全新安装、沙盒、对账）与所有已知缺口——**包能不能用不看目录建得漂不漂亮，看空手复现能不能跑完并对上账**。
---

## 适用的智能体

任何能开 shell、能读文件的智能体都能用:Claude Code、Codex、Kimi、或人。
包对智能体零依赖——全部是 `python3 xxx.py`,没有一处调任何智能体专属接口。
差别只在你的宿主认哪个入口文件:

| 宿主 | 读哪个 |
|---|---|
| Claude Code | `SKILL.md` |
| Codex | `AGENTS.md` |
| Kimi / 其他 / 人 | 本 `README.md` |

## 环境边界(装之前先看)

- **macOS + Microsoft Word 必需。** 净开探针、目录域、PDF 导出、页面审计全走 Word 原生引擎;LibreOffice 会误判字距,不可替代。
- **Adobe Acrobat:付印转曲时必需。** 没有它能出装订件(可预览校对),不能出付印件——包会明确说,不会假装。
- **字体属于样式模板,不属于环境。** 某模板要的字体本机没有,向导会说「这套不可用,换一套」,不会去装字体。
- 全部验证在一台 Mac 上做过;换机器请先跑向导。

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

---

## 许可

[MIT](LICENSE) © 2026 橙子教室 (ChengziClass)

包内 `skill/vendor/` 的 15,393 行是橙子教室在真实生产中写就的 Word/PDF 编制与审计实现，同样以 MIT 发布。
**不含任何来源教材原文**——讲义内容、封面封底素材、册级裁决均不在仓内。
