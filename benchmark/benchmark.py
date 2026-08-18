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
            {"role": "user", "content": "这个项目用到了哪些核心算法?比如压缩上下文的时候。我们团队最近在调研长上下文 Agent 的工程方案,想了解具体的技术选型。"},
            {"role": "assistant", "content": "我们用了 PACMS 子模选择算法来挑选最重要的消息,还引入了语义保真度量化确保压缩质量。PACMS 是论文 arXiv:2606.20047 提出的,核心思想是在预算约束下用贪心近似求解子模最大化,每条消息按重要性/成本比排序选择。"},
            {"role": "user", "content": "那子模选择具体怎么工作的?预算不够的时候怎么处理?"},
            {"role": "assistant", "content": "PACMS 是贪心近似:每次选重要性/成本比最高的消息,直到预算耗尽,系统消息强制保留。重要性评分融合了四个信号:语义 50%、近因 25%、类型 15%、质量 10%,还支持按 AFM 保真级别加权调整。预算不足时优先保证 system 消息完整。"},
            {"role": "user", "content": "语义保真度量化是怎么实现的?会不会有额外的推理开销?"},
            {"role": "assistant", "content": "FidelityScorer 优先用 bge-small 嵌入模型计算余弦相似度,无依赖时降级为 n-gram Jaccard。AdaptiveCompactor 设 0.92 保真底线,不达标自动降低压缩强度重试最多 3 次,QualityBreaker 在连续 3 次低保真时熔断暂停压缩。"},
        ],
    },
    {
        "name": "工具调用",
        "query": "调用了什么外部工具",
        "info_points": ["search_web", "read_file", "token_meter"],
        "messages": [
            {"role": "user", "content": "请帮我查一下今天的天气,然后读取一下本地配置文件,最后统计一下我这次对话消耗了多少 token。"},
            {"role": "assistant", "content": "好的,我先调用 search_web 工具查询今天的天气情况,工具会返回未来 7 天的天气预报数据,包括温度、湿度、风向和降水概率。"},
            {"role": "assistant", "content": "天气查询完成后,我又用 read_file 读取了 /home/qq/proxy/config.yaml 配置文件,里面包含上游 API 地址、模型名称、超时时间等关键配置项。"},
            {"role": "assistant", "content": "最后我调用了 token_meter 工具统计本次对话消耗,结果显示输入 1284 tokens、输出 356 tokens,总计 1640 tokens,并给出了优化建议:启用上下文压缩可以节省约 60% 的重复输入。"},
        ],
    },
    {
        "name": "代码对话",
        "query": "函数签名是什么",
        "info_points": ["def compact_messages", "session_id", "return summary"],
        "messages": [
            {"role": "user", "content": "这个函数的签名能给我看看吗?我想知道参数类型和返回值,这样我可以正确调用它来处理长对话的压缩任务。"},
            {"role": "assistant", "content": "def compact_messages(old_messages: list, api_key: str, session: aiohttp.ClientSession, session_id: str = 'default', _save_state: bool = True, selected_skills: list = None, system_prompt_override: str = None) -> Optional[str]: 它接收旧消息列表和 HTTP 会话,返回压缩后的摘要字符串。"},
            {"role": "user", "content": "session_id 参数有什么用?它和跨会话记忆有什么关系吗?"},
            {"role": "assistant", "content": "session_id 用于跨会话持久化 prior_summary,实现增量压缩——只对新增消息做摘要再与旧摘要合并,避免全量重压缩。它同时作为 SessionStore 的键,支持 FTS5 全文搜索和会话恢复。"},
            {"role": "user", "content": "如果压缩失败会怎样?有没有降级保护机制?"},
            {"role": "assistant", "content": "有完整的降级链路:压缩失败走轻量压缩,再失败走智能截断(角色预算分配),最后兜底纯透传。压缩前有 verify_compaction_safety 检查 token 必须变小,否则立即降级激进截断,防止上下文膨胀。"},
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
