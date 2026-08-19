#!/bin/bash
# token-running macOS 启动器：双击运行（或终端执行 ./start.command）
# 首次运行：先执行下面“Mac 打包/运行”一节中的 pip install。
set -e
cd "$(dirname "$0")"

PY=""
if [ -x "./venv/bin/python3" ]; then
  PY="./venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "未找到 python3，请先安装 Python 3.10+：https://www.python.org/downloads/"
  read -n 1 -s -r -p "按任意键退出..."
  exit 1
fi

# 首次运行自动补装依赖
if ! "$PY" -c "import PySide6, zstandard" >/dev/null 2>&1; then
  echo "首次运行：安装依赖（需要网络）..."
  "$PY" -m pip install -r requirements.txt
fi

exec "$PY" main.py
