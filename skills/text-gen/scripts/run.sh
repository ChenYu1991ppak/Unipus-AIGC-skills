#!/bin/sh
# 极薄壳：用脚本自身位置定位 plugin 根，把 lib/ 挂上 PYTHONPATH 后执行 CLI。
#
# 不依赖 CWD，也不依赖 ${CLAUDE_PLUGIN_ROOT}（该变量只对 plugin commands/hooks
# 有文档保证，SKILL.md 正文里没有使用先例）。定位方式是向上找
# .claude-plugin/plugin.json，所以 skill 目录层级变动也不会失效。
#
# 用法： run.sh <子命令> [参数...]     等价于 python3 -m unipus_aigc.cli ...
set -e

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

ROOT=$HERE
while [ "$ROOT" != "/" ] && [ ! -f "$ROOT/.claude-plugin/plugin.json" ]; do
  ROOT=$(dirname -- "$ROOT")
done
if [ ! -f "$ROOT/.claude-plugin/plugin.json" ]; then
  echo "run.sh: 从 $HERE 向上找不到 plugin 根（.claude-plugin/plugin.json）" >&2
  exit 1
fi

PYTHON=${PYTHON:-python3}
PYTHONPATH="$ROOT/lib${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m unipus_aigc.cli "$@"
