-- atomize schema v1 —— 原子化的真源(handout-atomizer)
--
-- PM 2026-08-22 定:原子化的真源是数据表,不是文件;非内容层不进题库;
-- 入题库是一次转化,归题库线,不在本技能内。
--
-- 落法:与题库同一个 Postgres 实例、独立 schema。平台已是「一引擎多 schema」,
-- 运维成本≈0,边界仍硬——题库的迁移碰不到这里的表,反之亦然。
--
-- 两层分开存:内容层回答「这是什么」,非内容层回答「它该怎么呈现」。
-- 同一块内容可以进讲义、进单元卷、进错题本,版式各不相同而内容只有一份;
-- 混在一起就没法各用各的。
--
-- 判准:据 blocks + spans + layout_facts + figures 能逐块逐字逐属性重建源文件。
-- 不给百分比——还原是全有或全无。

BEGIN;

-- spans 的「同块内区间不得重叠」用 EXCLUDE 约束表达,其中 block_id 要在 GiST 索引里做等值比较,
-- 而 bigint 默认没有 gist 操作符类 —— 必须先装 btree_gist。
-- (2026-08-22:这一条是把 SQL 真跑一遍才抓到的,读代码看不出来。)
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA IF NOT EXISTS atomize;

-- ─────────────────────────────────────────────────────────────────────
-- 判据词表:模块类型不写死在 CHECK 里,做成表
--
-- 「业务规则与系统剥离解耦」:加一种新模块不该要一次迁移。
-- qti_equivalent 记与 QTI 3.0(1EdTech)的对应——那是评测内容交换的国际标准,
-- 我们的模块几乎与它一一对得上,记下来省得将来出题库互换时再考古。
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE atomize.unit_kinds (
  kind             text PRIMARY KEY,
  label            text NOT NULL,
  qti_equivalent   text,
  note             text
);

INSERT INTO atomize.unit_kinds (kind, label, qti_equivalent, note) VALUES
  ('question',    '题',        'assessmentItem',        '一个题干 + 若干选项 + 若干小问 + 若干图 + 一个答案 + 一份解析'),
  ('stem',        '题干',      'itemBody',              NULL),
  ('option',      '选项',      'choiceInteraction',     '单选/多选之别由 questions.max_choices 声明,不靠数字母'),
  ('sub_question','小问',      NULL,                    '（1）（2）… 本身可再带答案'),
  ('blank',       '填空的空',  'textEntryInteraction',  '结构见 atomize.blanks'),
  ('answer',      '答案',      'responseDeclaration',   NULL),
  ('analysis',    '解析',      'modalFeedback',         NULL),
  ('figure',      '图',        NULL,                    '结构见 atomize.figures'),
  ('table',       '表',        NULL,                    NULL),
  ('heading',     '标题',      NULL,                    '★标题不是知识点。原子化只记结构:这道题落在哪个标题下、标题原文是什么'),
  ('caption',     '图注',      NULL,                    NULL),
  ('prose',       '正文',      NULL,                    '非题的讲解文字');

-- ─────────────────────────────────────────────────────────────────────
-- 源
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE atomize.sources (
  source_id     text PRIMARY KEY,
  file_sha256   text NOT NULL UNIQUE,          -- 哈希才是「同一份源」的判据,文件名不是
  file_name     text NOT NULL,
  role          text,                          -- 学生版 / 教师版 / 合并版 …
  subject       text, grade text, term text,
  volume_key    text NOT NULL,
  template_id   text,                          -- → atomize.templates
  schema_hash   text,                          -- 切这份源时用的判据版本
  ingested_at   timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────
-- 内容层
-- ─────────────────────────────────────────────────────────────────────

-- 物理块:「每个字符都有归属」的底座。
-- 归属要能被逐块核对,就必须先有块;locator 是块在源里的位置,与非内容层共用同一个键。
CREATE TABLE atomize.blocks (
  block_id     bigserial PRIMARY KEY,
  source_id    text NOT NULL REFERENCES atomize.sources ON DELETE CASCADE,
  locator      text NOT NULL,
  block_type   text NOT NULL,                  -- paragraph / table_cell
  ordinal      integer NOT NULL,
  text         text NOT NULL,                  -- 原样,不做任何规整
  UNIQUE (source_id, locator)
);

-- 逻辑模块(树)
CREATE TABLE atomize.units (
  unit_id         text PRIMARY KEY,
  source_id       text NOT NULL REFERENCES atomize.sources ON DELETE CASCADE,
  parent_unit_id  text REFERENCES atomize.units ON DELETE CASCADE,
  kind            text NOT NULL REFERENCES atomize.unit_kinds,
  ordinal         integer NOT NULL,
  hierarchy_path  text NOT NULL DEFAULT '',    -- 第A01讲 / 知识点01 / …(结构,不是语义)
  -- ★ QTI 的 max-choices:单选还是多选**声明出来**,不靠数答案里的字母。
  --   2026-08-21 实测:21 道被判多选的题里 13 道是「（1）C；（2）3.7 m/s」这样数出来的,
  --   而全语料里真正标注多选的只有 8 处。数出来的判据会骗人,声明的不会。
  max_choices     integer,                     -- 仅 kind='question' 有意义;NULL=未声明
  meta            jsonb NOT NULL DEFAULT '{}'::jsonb,
  CHECK (max_choices IS NULL OR max_choices >= 1)
);
CREATE INDEX ON atomize.units (source_id, kind);
CREATE INDEX ON atomize.units (parent_unit_id);

-- 字符区间 → 归属。
-- ★ 判准是字符级不是段级:「1．下列说法正确的是（ ）A．… B．…」一段里混着题干和选项,
--   段级归属只能整段判成一种,另一种就没了归属。
--   段落级曾报 100% 覆盖,换成字符级立刻掉到 82.4%——盖住的正是这一类。
CREATE TABLE atomize.spans (
  span_id     bigserial PRIMARY KEY,
  block_id    bigint  NOT NULL REFERENCES atomize.blocks ON DELETE CASCADE,
  char_start  integer NOT NULL,
  char_end    integer NOT NULL,                -- 半开区间 [start, end)
  unit_id     text    NOT NULL REFERENCES atomize.units ON DELETE CASCADE,
  role        text    NOT NULL REFERENCES atomize.unit_kinds,
  CHECK (char_end > char_start),
  -- 同一块内区间不得重叠:一个字符只能有一个归属,重叠等于没归属
  EXCLUDE USING gist (block_id WITH =, int4range(char_start, char_end) WITH &&)
);
CREATE INDEX ON atomize.spans (unit_id);

-- 填空的空(QTI: textEntryInteraction)
-- 定义里明写的一项,旧实现完全缺失:187/402 道题的文本里有空,却 0 个结构字段。
CREATE TABLE atomize.blanks (
  blank_id        bigserial PRIMARY KEY,
  unit_id         text    NOT NULL REFERENCES atomize.units ON DELETE CASCADE,
  index_in_unit   integer NOT NULL,            -- 这道题里的第几个空
  block_id        bigint  NOT NULL REFERENCES atomize.blocks ON DELETE CASCADE,
  char_offset     integer NOT NULL,            -- 空在该块文本中的位置
  char_length     integer NOT NULL,            -- 源里画了多长(下划线本身是版式线索)
  answer_unit_id  text    REFERENCES atomize.units ON DELETE SET NULL,  -- 对应哪一段答案
  UNIQUE (unit_id, index_in_unit)
);

-- ─────────────────────────────────────────────────────────────────────
-- 非内容层
-- ─────────────────────────────────────────────────────────────────────

-- ★ 不逐个补字段——逐字捕获。
--   照着清单逐个写提取器,下一份源换一批事实又得重来;枚举永远不全,
--   而且列出来的多半是自己想得到的那几样。
--   只登记这份源里**真实出现过**的:登记一个从未出现的字段,就是给自己造一条恒假的待办。
CREATE TABLE atomize.layout_facts (
  fact_id     bigserial PRIMARY KEY,
  source_id   text NOT NULL REFERENCES atomize.sources ON DELETE CASCADE,
  locator     text NOT NULL,                   -- 与 blocks.locator 同键
  layer       text NOT NULL,                   -- pPr / rPr / tblPr / tcPr / sectPr / drawing
  run_index   integer,                         -- rPr 才有:块内第几个 run
  key         text NOT NULL,                   -- spacing / ind / shd / rFonts / vertAlign / …
  value       jsonb NOT NULL,                  -- 属性字典原样;无属性的元素记 true
  UNIQUE (source_id, locator, layer, run_index, key)
);
CREATE INDEX ON atomize.layout_facts (source_id, layer);

CREATE TABLE atomize.media (
  media_id    text PRIMARY KEY,
  source_id   text NOT NULL REFERENCES atomize.sources ON DELETE CASCADE,
  sha256      text NOT NULL,
  mime_type   text,
  bytes       bigint,
  object_key  text,                            -- 二进制落对象存储,库里只留指针
  UNIQUE (source_id, sha256)
);

-- 图:横跨两层,故单独一张
-- 内容层的部分(属于哪道题、在题内第几块之后、相对正文多大)换套版式仍成立;
-- 非内容层的部分(锚定方式、环绕、离正文多远、绝对 EMU)换套版式就作废。
CREATE TABLE atomize.figures (
  figure_id              bigserial PRIMARY KEY,
  unit_id                text   NOT NULL REFERENCES atomize.units ON DELETE CASCADE,
  media_id               text   NOT NULL REFERENCES atomize.media ON DELETE CASCADE,
  ordinal                integer NOT NULL,     -- 在所属模块里的次序 ← 内容层
  -- ★ 相对比例必须**存成一列,不能从 EMU 现算**:它依赖「该处正文字号是多少」,
  --   而同一份源不同段落的正文字号可以不同——那是原子化当时才知道的事实。
  --   只记绝对尺寸,图片大小必然漂移:同一个 widthEmu 放进不同页宽、不同正文字号的版式里,
  --   观感完全不同;相对比例是跟着正文走的,换一套版式仍然对。
  --   实测第 23 段:图高 304800 EMU = 24pt,该段正文 szCs=21 = 10.5pt → 2.29 倍。
  scale_to_body_font     numeric(8,4) NOT NULL,
  body_font_half_points  integer NOT NULL,     -- 算出上面那个比例时用的正文字号,留着可复核
  width_emu              bigint NOT NULL,      -- ← 非内容层:这一次排版的事实
  height_emu             bigint NOT NULL,
  anchoring              text NOT NULL,        -- inline / anchor
  wrap                   text,                 -- wrapNone / wrapSquare / …;inline 时为 NULL
  dist_t_emu             integer, dist_b_emu integer,
  dist_l_emu             integer, dist_r_emu integer,
  CHECK (scale_to_body_font > 0),
  CHECK (body_font_half_points > 0),
  CHECK (anchoring IN ('inline','anchor')),
  UNIQUE (unit_id, ordinal)
);

-- ─────────────────────────────────────────────────────────────────────
-- 判据与台账
-- ─────────────────────────────────────────────────────────────────────

-- 模板表(判据真源)。题库的 carve_templates 是它的投影,不是第二真源。
CREATE TABLE atomize.templates (
  template_id   text PRIMARY KEY,
  display_name  text NOT NULL,
  subject       text,
  schema_json   jsonb NOT NULL,
  schema_hash   text NOT NULL UNIQUE,          -- 改了没推 = 裁决只写在纸上,且失败方式是延迟的
  status        text NOT NULL DEFAULT 'seed',
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 每次原子化的运行台账。
-- 没有它,「这批原子是哪一版判据切出来的」无从追。
CREATE TABLE atomize.runs (
  run_id        text PRIMARY KEY,
  source_id     text NOT NULL REFERENCES atomize.sources ON DELETE CASCADE,
  template_id   text REFERENCES atomize.templates,
  schema_hash   text NOT NULL,
  package_version text,                        -- handout-atomizer 的版本
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  -- 门结果逐条记。★ GATE_RECONSTRUCTIBLE 不给百分比:还原是全有或全无。
  gates         jsonb NOT NULL DEFAULT '{}'::jsonb,
  reconstructible boolean,                     -- NULL = 没跑过,不是通过
  notes         text
);
CREATE INDEX ON atomize.runs (source_id, started_at DESC);

COMMENT ON SCHEMA atomize IS
  '原子化真源(handout-atomizer)。内容层 blocks/spans/units/blanks/figures,'
  '非内容层 layout_facts。判准:据两层能逐块逐字逐属性重建源文件。'
  '非内容层不进题库——题库只要内容层,那一步转化归题库线的导入功能。';

COMMIT;
