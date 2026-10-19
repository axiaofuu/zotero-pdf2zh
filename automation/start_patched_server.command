#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
SERVER_DIR="${PDF2ZH_SERVER_DIR:-$SCRIPT_DIR/../server}"
ENV_DIR="$SERVER_DIR/zotero-pdf2zh-next-venv"
PYTHON="$ENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  print -u2 "找不到 uv 翻译环境：$PYTHON。请先按官方指南完成安装。"
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/pdf2zh_patch_manager.py" ]]; then
  print -u2 '请把启动脚本与 pdf2zh_patch_manager.py 放在同一目录。'
  exit 1
fi

unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
export PYTHONUNBUFFERED=1
export PATH="$ENV_DIR/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
# Use the managed environment's trusted CA bundle; retain TLS verification.
export SSL_CERT_FILE="$("$PYTHON" -m certifi)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$SERVER_DIR/../.pdf2zh-local/uv-cache}"
exec "$PYTHON" "$SCRIPT_DIR/pdf2zh_patch_manager.py" --server-dir "$SERVER_DIR" "$@"
