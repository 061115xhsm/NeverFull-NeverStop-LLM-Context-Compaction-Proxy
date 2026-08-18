"""
LongBench 权威对比运行脚本(benchmark/run_longbench.py)
========================================================
加载 LongBench 格式评测数据,对每条样例跑三种压缩策略
(baseline / summary / adaptive),输出压缩率、信息保留率、语义保真度,
生成 LongBench 对比报告。

用法: python3 benchmark/run_longbench.py [data.jsonl]

数据: 默认 benchmark/data/longbench_sample.jsonl(官方 LongBench 格式),
完整数据集可从 https://huggingface.co/datasets/THUDM/LongBench 获取后指定路径。
"""

from __future__ import annotations

import importlib.util
import os
import sys

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PARENT)

from longbench_adapter import load_longbench  # noqa: E402
from fidelity import FidelityScorer  # noqa: E402

# 加载同目录 benchmark.py(避免与 benchmark 包名冲突)
_spec = importlib.util.spec_from_file_location(
    "benchmark_mod", os.path.join(_CUR_DIR, "benchmark.py")
)
_benchmark_mod = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_mod"] = _benchmark_mod
_spec.loader.exec_module(_benchmark_mod)

baseline_compress = _benchmark_mod.baseline_compress
summary_compress = _benchmark_mod.summary_compress
adaptive_compress = _benchmark_mod.adaptive_compress
calc_metrics = _benchmark_mod.calc_metrics

DEFAULT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "longbench_sample.jsonl")
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "longbench_report.md")


def sample_to_messages(item: dict) -> list:
    """将 LongBench 样例转为消息列表(问题在末尾,便于问答评测)。"""
    return [
        {"role": "user", "content": item["input"]},
        {"role": "user", "content": item["query"]},
    ]


def info_points_from_answers(item: dict) -> list:
    """从参考答案提取信息点(取首个答案的前几个关键词)。"""
    ans = (item.get("answers") or [""])[0]
    # 取答案的前 30 字符作为信息点
    return [ans[:30]] if ans else []


def run_longbench_benchmark(data_path: str = DEFAULT_DATA, limit: int = 10) -> list:
    scorer = FidelityScorer()
    items = load_longbench(data_path, limit=limit)
    rows: list = []
    for idx, item in enumerate(items):
        messages = sample_to_messages(item)
        budget = max(150, sum(len(str(m.get("content", ""))) for m in messages) // 2)
        points = info_points_from_answers(item)

        b_comp = baseline_compress(messages, budget)
        b_m = calc_metrics(messages, b_comp, scorer, points)

        s_comp = summary_compress(messages, budget)
        s_m = calc_metrics(messages, s_comp, scorer, points)

        a_result = adaptive_compress(messages, budget, scorer)
        a_m = calc_metrics(messages, a_result["messages"], scorer, points)

        rows.append({
            "name": f"LB#{idx+1}: {item['query'][:24]}",
            "input_chars": len(item["input"]),
            "baseline": b_m,
            "summary": s_m,
            "adaptive": a_m,
        })
    return rows


def write_longbench_report(rows: list) -> str:
    lines = [
        "# LongBench 压缩对比报告",
        "",
        "> 数据源:LongBench 格式评测数据(benchmark/data/longbench_sample.jsonl)",
        "> 完整 LongBench 可从 https://huggingface.co/datasets/THUDM/LongBench 获取",
        "",
        "| 样例 | 输入字符 | 策略 | 压缩率 | 信息保留率 | 语义保真度 |",
        "|------|---------|------|--------|-----------|-----------|",
    ]
    for r in rows:
        for strat in ("baseline", "summary", "adaptive"):
            m = r[strat]
            lines.append(
                f"| {r['name']} | {r['input_chars']} | {strat} | "
                f"{m['compression_ratio']} | {m['retention']} | {m['fidelity']} |"
            )
    lines += [
        "",
        "> 说明:compression_ratio 越高压缩越狠;retention 越高关键信息保留越全;",
        "> fidelity 为语义保真度(sentence-transformers 真实嵌入,1.0 最佳)。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    return report


def main() -> None:
    data_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA
    rows = run_longbench_benchmark(data_path)
    report = write_longbench_report(rows)
    print(report)
    print(f"\n报告已写入: {REPORT_PATH}")


if __name__ == "__main__":
    main()
