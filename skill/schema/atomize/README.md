# atomize schema —— 原子化的真源

PM 2026-08-22 定的三条,这份 schema 就是它们的落地:

- 原子化的真源是**数据表**,不是文件
- **非内容层不进题库**;题库只要内容层
- **入题库不归原子化技能** —— 那是一次转化,归题库线的导入功能

落法:与题库**同一个 Postgres 实例、独立 schema**。平台已是「一引擎多 schema」,
运维成本≈0,边界仍硬:题库的迁移碰不到这里的表,反之亦然。

## 十一张表

| | |
|---|---|
| `sources` | 源文件。`file_sha256` 唯一——哈希才是「同一份源」的判据,文件名不是 |
| `blocks` | **物理块**,`(source_id, locator)` 唯一。「每个字符都有归属」的底座 |
| `spans` | **字符区间 → 归属**。判准是字符级不是段级 |
| `units` | **内容模块**(树)。`kind` 外键到 `unit_kinds` |
| `blanks` | **填空的空**(QTI: `textEntryInteraction`) |
| `figures` | 图。横跨两层,故单独一张 |
| `media` | 图的哈希与指针;二进制落对象存储 |
| `layout_facts` | **非内容层**,逐属性 `(source_id, locator, layer, run_index, key) → value` |
| `unit_kinds` | 模块类型词表(**数据,不是 CHECK**) |
| `templates` | **模板表(判据真源)**。题库的 `carve_templates` 是它的投影 |
| `runs` | 每次原子化的台账 + 门结果 |

## 四条判据由数据库自己守(不靠代码记得)

```
spans   EXCLUDE 同块内区间不得重叠   一个字符只能有一个归属,重叠等于没归属
figures scale_to_body_font NOT NULL  只记绝对尺寸,图片大小必然漂移
units   kind → unit_kinds 外键        未登记的类型插不进去(含把「知识点」当模块)
spans   CHECK char_end > char_start   空区间不是归属
```

★ 第三条顺带守住了 PM 那句「标题不是知识点」:`知识点` 不在 `unit_kinds` 里,插不进去。
原子化只记结构——这道题落在哪个标题下、标题原文是什么;它是不是知识点、叫什么名字,**是后面的事**。

## 借了 QTI 3.0 的两条

[QTI 3.0](https://www.imsglobal.org/spec/qti/v3p0/oview)(1EdTech)是评测内容交换的国际标准,
我们的模块几乎与它一一对应,`unit_kinds.qti_equivalent` 逐条记着。其中两条直接改了做法:

1. **`units.max_choices`** —— 单选/多选**声明出来**,不靠数答案里的字母。
   2026-08-21 实测:21 道被判多选的题里 13 道是「（1）C；（2）3.7 m/s」这样数出来的,
   而全语料里真正标注多选的只有 8 处。**数出来的判据会骗人,声明的不会。**
2. **`blanks`** 对应 `textEntryInteraction` —— 「填空的空」在标准里本来就有名字,不用自己造。

## 验

```bash
PGHOST=127.0.0.1 PGPORT=25432 PGUSER=czclass ./check_schema.sh
```

建临时库 → 跑 `001_init.sql` → 逐条**先验红**(插一条它该拒的)→ 再验绿 → 删库。

★ 为什么必须真跑:首版 SQL 读起来没问题,一跑就炸 ——
`spans` 的 `EXCLUDE` 要在 GiST 索引里对 `bigint` 做等值比较,而 `bigint` 默认没有 gist 操作符类,
得先 `CREATE EXTENSION btree_gist`。**读代码看不出来,只能让数据库说话。**
