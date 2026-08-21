#!/usr/bin/env bash
# GATE_ATOMIZE_SCHEMA —— 把 001_init.sql 真跑一遍,再逐条验判据会不会咬。
#
# 为什么要真跑:2026-08-22 首版 SQL 读起来没问题,跑起来第一次就炸——
# spans 的 EXCLUDE 约束要在 GiST 索引里对 bigint 做等值比较,而 bigint 默认没有 gist 操作符类。
# 读代码看不出来,必须让数据库说话。
#
# 用法:PGHOST=127.0.0.1 PGPORT=25432 PGUSER=czclass ./check_schema.sh [psql 所在目录]
set -euo pipefail
BIN="${1:-/Users/Shared/ChengziClass/quiz/runtime/Postgres.app/Contents/Versions/16/bin}"
DB="atomize_schema_check_$$"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PGDATABASE="$DB"

cleanup() { PGDATABASE=postgres "$BIN/dropdb" --if-exists "$DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT

PGDATABASE=postgres "$BIN/createdb" "$DB"
"$BIN/psql" -q -v ON_ERROR_STOP=1 -f "$HERE/001_init.sql"

tables=$("$BIN/psql" -q -t -A -c "select count(*) from information_schema.tables where table_schema='atomize'")
[ "$tables" = "11" ] || { echo "GATE_ATOMIZE_SCHEMA: 期望 11 张表,实得 $tables"; exit 1; }

"$BIN/psql" -q -v ON_ERROR_STOP=1 <<'SQL'
insert into atomize.sources(source_id,file_sha256,file_name,volume_key) values ('s1','h1','a.docx','v1');
insert into atomize.blocks(source_id,locator,block_type,ordinal,text) values ('s1','p1','paragraph',1,'1．下列说法正确的是（ ）A．甲 B．乙');
insert into atomize.units(unit_id,source_id,kind,ordinal) values ('u-q','s1','question',1),('u-stem','s1','stem',1),('u-opt','s1','option',1);
insert into atomize.media(media_id,source_id,sha256) values ('m1','s1','mh1');
insert into atomize.spans(block_id,char_start,char_end,unit_id,role)
  select block_id,0,14,'u-stem','stem' from atomize.blocks limit 1;
SQL

# 每条判据都必须**先验红**:插一条它该拒的,拒了才算这道门真在守。
fail_expected() {   # $1=说明  $2=SQL
  if "$BIN/psql" -q -v ON_ERROR_STOP=1 -c "$2" >/dev/null 2>&1; then
    echo "GATE_ATOMIZE_SCHEMA: 【$1】本该被拒却通过了 —— 判据没在守"; exit 1
  fi
  echo "  验红通过: $1"
}

fail_expected "同一块内字符区间重叠(一个字符只能有一个归属)" \
  "insert into atomize.spans(block_id,char_start,char_end,unit_id,role) select block_id,10,24,'u-opt','option' from atomize.blocks limit 1;"
fail_expected "图不带相对正文字号的比例(只记绝对尺寸必然漂)" \
  "insert into atomize.figures(unit_id,media_id,ordinal,body_font_half_points,width_emu,height_emu,anchoring) values ('u-q','m1',1,21,393700,304800,'anchor');"
fail_expected "未登记的模块类型(如把「知识点」当成一种模块)" \
  "insert into atomize.units(unit_id,source_id,kind,ordinal) values ('u-x','s1','知识点',1);"
fail_expected "空的字符区间(char_end 不大于 char_start)" \
  "insert into atomize.spans(block_id,char_start,char_end,unit_id,role) select block_id,30,30,'u-opt','option' from atomize.blocks limit 1;"

# 再验绿:不相交的区间必须插得进去,否则约束是把好人也拦了
"$BIN/psql" -q -v ON_ERROR_STOP=1 -c \
  "insert into atomize.spans(block_id,char_start,char_end,unit_id,role) select block_id,14,24,'u-opt','option' from atomize.blocks limit 1;"
echo "  验绿通过: 不相交的区间可插入"

echo "GATE_ATOMIZE_SCHEMA: pass (11 张表 · 4 条判据验红 · 1 条验绿)"
