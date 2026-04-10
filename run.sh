#!/bin/bash
# 快速运行分析脚本的快捷方式

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 激活虚拟环境
source venv/bin/activate

# 检查参数
if [ $# -eq 0 ]; then
    echo "用法: ./run.sh <脚本名> [参数]"
    echo ""
    echo "可用脚本:"
    ls -1 *.py | grep -v "bq_client" | sed 's/^/  - /'
    echo ""
    echo "示例:"
    echo "  ./run.sh report_bom_sales_bq.py 2026-03"
    exit 1
fi

# 运行脚本
python3 "$@"
