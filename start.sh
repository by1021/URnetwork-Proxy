#!/usr/bin/env bash
set -e

# 获取脚本所在目录并切换
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 优先查找 python3，降级使用 python
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "错误: 未检测到 Python 环境，请先安装 Python 3.10 或更高版本。" >&2
    exit 1
fi

# 检查 Python 主次版本 >= 3.10
"$PYTHON_BIN" -c '
import sys
if sys.version_info < (3, 10):
    sys.exit(f"错误: 当前 Python 版本为 {sys.version.split()[0]}，URnetwork-Proxy 要求 Python 3.10+。")
'

exec "$PYTHON_BIN" main.py "$@"
