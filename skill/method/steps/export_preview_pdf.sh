#!/bin/zsh
# 预览 PDF:Word 原生导出,走容器沙盒,不抢前台。
#
# **这不是印刷件。** 受治理的四步流程(内容PDF → 页码编排 → 标准PDF装订页序 →
# Acrobat 逐页转曲)按注册键表取件,要出印刷级 PDF 必须在 word_export.DOCS /
# binding.DOCS / outline.MATERIALS 注册键。本步只做「看得见」的那一层,
# 走 SKILL.md 能力分级的降级路径,并在文件名里标明。
#
# 首版这里写着「该目录租约在别人手上」作为跳过印刷件的理由。那个约束后来消失了,
# 而这行注释留着,于是一个临时状况看起来像一条长期结论。理由留档,但不再成立——
# 现在阻断项只有一个:注册键没建。
#
# 沙盒:Word 有容器沙盒,直接开外置卷上的文件会被拦。照正式流程的做法,
# 先拷进容器 Documents,在容器内导出,再把 PDF 转存回工作区。
# AppleScript 用 launch 不用 activate —— 不抢前台。
#
# 用法: export_preview_pdf.sh <源docx> <输出目录> [成品名(不含扩展名)]
set -e

SRC="$1"
OUT="$2"
NAME="${3:-$(basename "${SRC%.*}")预览}"

if [ -z "$SRC" ] || [ -z "$OUT" ]; then
  echo "用法: $0 <源docx> <输出目录> [成品名]" >&2
  exit 2
fi
if [ ! -f "$SRC" ]; then
  echo "源文件不存在: $SRC" >&2
  exit 1
fi

BOX="$HOME/Library/Containers/com.microsoft.Word/Data/Documents/handout-intake-export"

mkdir -p "$BOX" "$OUT"
cp "$SRC" "$BOX/$NAME.docx"
echo "已拷入沙盒: $BOX/$NAME.docx"

/usr/bin/osascript <<APPLESCRIPT
tell application "Microsoft Word"
    launch
    set theDoc to open file name POSIX file "$BOX/$NAME.docx" with read only
    save as theDoc file format format PDF file name POSIX file "$BOX/$NAME.pdf"
    close theDoc saving no
end tell
APPLESCRIPT

if [ -f "$BOX/$NAME.pdf" ]; then
  mv "$BOX/$NAME.pdf" "$OUT/$NAME.pdf"
  rm -f "$BOX/$NAME.docx"
  echo "导出成功: $OUT/$NAME.pdf"
  ls -la "$OUT/$NAME.pdf"
else
  echo "导出失败:沙盒里没有生成 PDF" >&2
  rm -f "$BOX/$NAME.docx"
  exit 1
fi
