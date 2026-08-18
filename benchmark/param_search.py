"""
多 agent 并发参数搜索(benchmark/param_search.py)
==================================================
在 LongBench 数据上并发评估 AdaptiveCompactor 的不同参数组合
(min_fidelity × max_attempts × min_content_len),找出
"压缩率↔保真度"最优的 Pareto 组合。

用 ThreadPoolExecutor 并发跑参数组合(等价于多 agent 并行搜索),
输出排序结果与最优组合。

用法: python3 benchmark/param_search.py
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

_CUR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_CUR)
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

# 加载 LongBench 数据
DATA = os.path.join(_CUR, "data", "longbench_sample.jsonl")

# 参数网格(供"多 agent"并发搜索)
GRID = {
    "min_fidelity": [0.85, 0.90, 0.92, 0.95],
    "max_attempts": [2, 3, 4],
    "min_content_len": [30, 50, 80],
}


def load_items(path: str) -> list:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return items


def to_messages(item: dict) -> list:
    return [
        {"role": "user", "content": item["input"]},
        {"role": "user", "content": item["query"]},
    ]


def evaluate_one(params: dict, items: list) -> dict:
    """单个参数组合在所有样例上的平均表现(一个"agent"的评估)。"""
    scorer = FidelityScorer()
    compactor = AdaptiveCompactor(
        scorer=scorer,
        min_fidelity=params["min_fidelity"],
        max_attempts=params["max_attempts"],
        min_content_len=params["min_content_len"],
    )
    total_ratio, total_fid, total_ret = 0.0, 0.0, 0.0
    n = len(items)
    for item in items:
        messages = to_messages(item)
        budget = max(150, sum(len(str(m.get("content", ""))) for m in messages) // 2)
        res = compactor.compact(messages, budget)
        orig_chars = sum(len(str(m.get("content", ""))) for m in messages)
        comp_chars = sum(len(str(m.get("content", ""))) for m in res["messages"])
        ratio = 1.0 - (comp_chars / orig_chars) if orig_chars else 0.0
        total_ratio += ratio
        total_fid += res["fidelity"]
        # 信息保留:检查 query 关键词是否仍在压缩结果中
        q_tokens = [t for t in item["query"].split() if len(t) > 1][:2]
        comp_text = "".join(str(m.get("content", "")) for m in res["messages"])
        retained = sum(1 for t in q_tokens if t in comp_text) / max(1, len(q_tokens))
        total_ret += retained
    return {
        **params,
        "avg_compression_ratio": round(total_ratio / n, 3),
        "avg_fidelity": round(total_fid / n, 3),
        "avg_retention": round(total_ret / n, 3),
    }


def main() -> None:
    items = load_items(DATA)
    print(f"加载 {len(items)} 条 LongBench 样例,参数组合数: "
          f"{len(GRID['min_fidelity']) * len(GRID['max_attempts']) * len(GRID['min_content_len'])}")
    combos = [dict(zip(GRID.keys(), c)) for c in itertools.product(*GRID.values())]

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(evaluate_one, p, items): p for p in combos}
        done = 0
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"组合评估失败: {e}")
            done += 1

    # 按保真度达标(>=0.90)的前提下压缩率排序
    results.sort(key=lambda r: (r["avg_fidelity"] >= 0.90, r["avg_compression_ratio"]),
                 reverse=True)

    print("\n=== 全部组合(按 压缩率 降序) ===")
    print("| 保真底线 | 尝试次数 | 最小长度 | 压缩率 | 保真度 | 保留率 | 达标 |")
    print("|---------|---------|---------|--------|--------|--------|------|")
    for r in results:
        ok = "✅" if r["avg_fidelity"] >= 0.90 else "❌"
        print(f"| {r['min_fidelity']} | {r['max_attempts']} | {r['min_content_len']} "
              f"| {r['avg_compression_ratio']} | {r['avg_fidelity']} | {r['avg_retention']} | {ok} |")

    # 最优:保真度 >= 0.90 中压缩率最高
    feasible = [r for r in results if r["avg_fidelity"] >= 0.90]
    best = max(feasible, key=lambda r: r["avg_compression_ratio"]) if feasible else results[0]
    print("\n=== 最优组合 ===")
    print(json.dumps(best, ensure_ascii=False, indent=2))

    with open(os.path.join(_CUR, "param_search_report.md"), "w", encoding="utf-8") as f:
        f.write("# 参数搜索报告\n\n| 保真底线 | 尝试次数 | 最小长度 | 压缩率 | 保真度 | 保留率 | 达标 |\n")
        f.write("|---------|---------|---------|--------|--------|--------|------|\n")
        for r in results:
            ok = "✅" if r["avg_fidelity"] >= 0.90 else "❌"
            f.write(f"| {r['min_fidelity']} | {r['max_attempts']} | {r['min_content_len']} "
                    f"| {r['avg_compression_ratio']} | {r['avg_fidelity']} | {r['avg_retention']} | {ok} |\n")
        f.write(f"\n## 最优组合\n\n```json\n{json.dumps(best, ensure_ascii=False, indent=2)}\n```\n")
    print("\n报告已写入: benchmark/param_search_report.md")


if __name__ == "__main__":
    main()
