"""
压缩基准评测 CLI(benchmark/benchmark.py)
==========================================
用法: python3 benchmark/benchmark.py  或  python3 -m benchmark.benchmark

对内置评测样例运行 3 种压缩策略(baseline/summary/adaptive),
输出压缩率、信息保留率、语义保真度,生成 markdown 报告。

依赖:上一级目录的 fidelity.py(sys.path 已处理),其余纯标准库。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.md")


# ── 内置评测样例:通用对话 / 工具调用 / 代码对话 ─────────────────────

SAMPLES = [
    {
        "name": "通用对话",
        "query": "项目的核心算法是什么",
        "info_points": ["子模选择", "PACMS", "语义保真度"],
        "messages": [
            {"role": "user", "content": "这个项目用到了哪些核心算法?比如压缩上下文的时候。"},
            {"role": "assistant", "content": "我们用了 PACMS 子模选择算法来挑选最重要的消息,还引入了语义保真度量化确保压缩质量。"},
            {"role": "user", "content": "那子模选择具体怎么工作的?"},
            {"role": "assistant", "content": "PACMS 是贪心近似:每次选重要性/成本比最高的消息,直到预算耗尽,系统消息强制保留。"},
        ],
    },
    {
        "name": "工具调用",
        "query": "调用了什么外部工具",
        "info_points": ["search_web", "read_file", "token_meter"],
        "messages": [
            {"role": "user", "content": "请帮我查一下今天的天气。"},
            {"role": "assistant", "content": "好的,我先调用 search_web 工具查询。"},
            {"role": "assistant", "content": "工具返回结果后,我又用 read_file 读取了配置文件,并经过 token_meter 统计了消耗。"},
        ],
    },
    {
        "name": "代码对话",
        "query": "函数签名是什么",
        "info_points": ["def compact_messages", "session_id", "return summary"],
        "messages": [
            {"role": "user", "content": "这个函数的签名能给我看看吗?"},
            {"role": "assistant", "content": "def compact_messages(old_messages, api_key, session, session_id='default', _save_state=True): 它会返回 summary 字符串。"},
            {"role": "user", "content": "session_id 参数有什么用?"},
            {"role": "assistant", "content": "session_id 用于跨会话持久化 prior_summary,实现增量压缩。"},
        ],
    },
]


# ── 压缩策略 ────────────────────────────────────────────────────────

def baseline_compress(messages: list, budget: int) -> list:
    """baseline:简单截断后半部分。"""
    total = sum(len(str(m.get("content", ""))) for m in messages)
    kept: list = []
    used = 0
    for m in messages:
        c = str(m.get("content", ""))
        if used + len(c) > budget:
            kept.append({**m, "content": c[: max(0, budget - used)] + "..."})
            break
        kept.append(m)
        used += len(c)
    return kept


def summary_compress(messages: list, budget: int) -> list:
    """summary:每轮消息压缩为简短摘要。"""
    out: list = []
    for m in messages:
        c = str(m.get("content", ""))
        if len(c) > 60:
            out.append({**m, "content": c[:60] + "..."})
        else:
            out.append(m)
    return out


def adaptive_compress(messages: list, budget: int, scorer: FidelityScorer) -> dict:
    """adaptive:调用 fidelity.AdaptiveCompactor 做保真度约束压缩。"""
    compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=0.80, max_attempts=3)
    return compactor.compact(messages, budget)


# ── 指标计算 ────────────────────────────────────────────────────────

def calc_metrics(original: list, compacted: list, scorer: FidelityScorer,
                 info_points: list) -> dict:
    orig_chars = sum(len(str(m.get("content", ""))) for m in original)
    comp_chars = sum(len(str(m.get("content", ""))) for m in compacted)
    compression_ratio = 1.0 - (comp_chars / orig_chars) if orig_chars else 0.0
    comp_text = "\n".join(str(m.get("content", "")) for m in compacted).lower()
    retained = sum(1 for p in info_points if p.lower() in comp_text)
    retention = retained / len(info_points) if info_points else 1.0
    orig_text = "\n".join(str(m.get("content", "")) for m in original)
    fidelity = scorer.score(orig_text, comp_text)
    return {
        "compression_ratio": round(compression_ratio, 3),
        "retention": round(retention, 3),
        "fidelity": round(fidelity, 3),
    }


# ── 主流程 ──────────────────────────────────────────────────────────

def run_benchmark() -> list:
    scorer = FidelityScorer()
    rows: list = []
    for sample in SAMPLES:
        messages = sample["messages"]
        budget = max(200, sum(len(str(m.get("content", ""))) for m in messages) // 2)

        # baseline
        b_comp = baseline_compress(messages, budget)
        b_m = calc_metrics(messages, b_comp, scorer, sample["info_points"])

        # summary
        s_comp = summary_compress(messages, budget)
        s_m = calc_metrics(messages, s_comp, scorer, sample["info_points"])

        # adaptive
        a_result = adaptive_compress(messages, budget, scorer)
        a_m = calc_metrics(messages, a_result["messages"], scorer, sample["info_points"])
        a_m["attempts"] = a_result["attempts"]
        a_m["met_floor"] = a_result["met_floor"]

        rows.append({
            "name": sample["name"],
            "query": sample["query"],
            "baseline": b_m,
            "summary": s_m,
            "adaptive": a_m,
        })
    return rows


def write_report(rows: list) -> str:
    lines = [
        "# 压缩基准评测报告",
        "",
        "| 样例 | 策略 | 压缩率 | 信息保留率 | 语义保真度 | 说明 |",
        "|------|------|--------|-----------|-----------|------|",
    ]
    for r in rows:
        for strat in ("baseline", "summary", "adaptive"):
            m = r[strat]
            note = ""
            if strat == "adaptive":
                note = f"attempts={m.get('attempts', '')}, met_floor={m.get('met_floor', '')}"
            lines.append(
                f"| {r['name']} | {strat} | {m['compression_ratio']} | {m['retention']} "
                f"| {m['fidelity']} | {note} |"
            )
    lines += [
        "",
        "> 说明:compression_ratio 越高压缩越狠;retention 越高关键信息保留越全;",
        "> fidelity 越高压缩前后语义越接近(1.0 最佳)。",
        "> 完整接入 LongBench/BFCL/SWE-bench 见 longbench_adapter.py。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    return report


def main() -> None:
    rows = run_benchmark()
    report = write_report(rows)
    print(report)
    print(f"\n报告已写入: {REPORT_PATH}")


if __name__ == "__main__":
    main()
