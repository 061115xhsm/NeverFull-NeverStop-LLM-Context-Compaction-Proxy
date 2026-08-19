#!/usr/bin/env bash
# ============================================================
# GPU 就绪一键评测脚本(run_gpu_benchmark.sh)
# ============================================================
# 接上显卡、装好驱动/CUDA 后运行本脚本,自动完成:
#   1. GPU 检测
#   2. LLMLingua 7B 基线(文档盘数据)
#   3. 本项目 adaptive 三策略对比(官方 LongBench)
#   4. Q&A 准确率评测(EM/F1 官方口径)
#   5. 汇总输出论文级对比报告
#
# 用法: bash benchmark/run_gpu_benchmark.sh
# 可选环境变量:
#   LLMLINGUA_MODEL   LLMLingua 模型(默认 NousResearch/Llama-2-7b-hf)
#   LLMLINGUA_INT8=1  启用 INT8 量化(16G 显存建议开启)
#   LLMLINGUA_RATE    压缩率(默认 0.5)
# ============================================================
set -e
cd "$(dirname "$0")/.."   # 项目根目录

echo "======================================================"
echo " GPU 就绪一键评测"
echo "======================================================"

# ---------- 1. GPU 检测 ----------
echo ""
echo "[1/5] GPU 检测"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -3
    echo "  ✅ GPU 可用"
else
    echo "  ⚠️  未检测到可用 GPU(nvidia-smi 失败)"
    echo "  请确认: 显卡已安装、NVIDIA 驱动已装(nvidia-driver-*)"
    echo "  继续使用 CPU 模式(7B 会非常慢,建议 INT8 或换小模型)"
fi

# ---------- 2. 数据确认 ----------
echo ""
echo "[2/5] 确认文档盘 LongBench 数据"
DATA_DIR="/media/qq/文档/llm-compaction-proxy-data/longbench/data"
if [ -d "$DATA_DIR" ]; then
    N=$(ls "$DATA_DIR"/*.jsonl 2>/dev/null | wc -l)
    echo "  ✅ 数据就绪: $N 个子集"
else
    echo "  ❌ 数据目录不存在: $DATA_DIR"
    exit 1
fi

# ---------- 3. LLMLingua 基线 ----------
echo ""
echo "[3/5] LLMLingua 基线(文档盘数据,前 10 条)"
python3 benchmark/llmlingua_baseline.py || echo "  ⚠️  LLMLingua 基线失败(可忽略,继续)"
cat benchmark/llmlingua_report.md 2>/dev/null | head -12

# ---------- 4. 本项目三策略对比 + 全量评测 ----------
echo ""
echo "[4/5] 本项目 adaptive 三策略(官方 LongBench)"
python3 benchmark/run_full_longbench.py 2>&1 | grep -E "\[汇总\]|平均" | tail -8

# ---------- 5. Q&A 准确率(EM/F1 官方口径) ----------
echo ""
echo "[5/5] Q&A 准确率评测(EM/F1 官方口径)"
python3 benchmark/accuracy_eval.py 2>&1 | grep -vE "Warning|Loading" | tail -12

echo ""
echo "======================================================"
echo " ✅ 评测完成,报告文件:"
echo "   - benchmark/llmlingua_report.md      (LLMLingua 基线)"
echo "   - benchmark/full_longbench_report.md (本项目全量)"
echo "   - benchmark/accuracy_report.md       (Q&A 准确率)"
echo "   - docs/paper_draft.md                (论文初稿)"
echo "======================================================"
