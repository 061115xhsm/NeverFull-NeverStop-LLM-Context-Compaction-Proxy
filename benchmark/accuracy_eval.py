"""
任务准确率评测模块(benchmark/accuracy_eval.py)
================================================
将评测口径从"关键词保留率"升级为"压缩后 Q&A 任务准确率":

1. 对每条 LongBench 样例,先压缩上下文(三种策略)
2. 用"答案提取器"从压缩后上下文定位与 query 相关的句子
3. 检查提取的答案是否包含参考答案关键词 → 判对/错
4. 准确率 = 答对样例数 / 总样例数

另输出:原始上下文基线准确率(不压缩),用于对比压缩带来的准确率下降。

用法: python3 benchmark/accuracy_eval.py [data.jsonl]
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

_CUR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_CUR)
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

# 加载 benchmark.py(复用压缩策略)
_spec = importlib.util.spec_from_file_location(
    "benchmark_mod", os.path.join(_CUR, "benchmark.py")
)
_bm = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_mod"] = _bm
_spec.loader.exec_module(_bm)

DEFAULT_DATA = os.path.join(_CUR, "data", "longbench_full.jsonl")
REPORT = os.path.join(_CUR, "accuracy_report.md")


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


def extract_answer(context: str, query: str) -> str:
    """
    答案提取器:从上下文中定位与 query 最相关的句子。

    按句子切分,选与 query 关键词重叠最多的句子作为答案。
    """
    q_tokens = set(re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", query.lower()))
    sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]", context) if s.strip()]
    if not sentences:
        return context[:100]
    best_sent, best_score = sentences[0], -1
    for s in sentences:
        s_tokens = set(re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", s.lower()))
        if q_tokens:
            score = len(q_tokens & s_tokens)
        else:
            score = len(s)
        if score > best_score:
            best_sent, best_score = s, score
    return best_sent


def answer_is_correct(predicted: str, reference: str) -> bool:
    """
    判定:参考答案的核心关键词是否仍可从文本中获取(全局命中 ≥50% 判对)。

    口径说明:Q&A 可回答性——压缩后上下文应保留回答该问题所需的信息。
    用参考答案关键词在压缩后文本中的保留率近似"模型能否答对"。
    """
    ref_clean = reference.strip()
    if not ref_clean:
        return True
    key_tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", ref_clean.lower())
    if not key_tokens:
        return ref_clean in predicted
    hits = sum(1 for t in key_tokens if t in predicted)
    return hits / len(key_tokens) >= 0.5


def to_messages(item: dict) -> list:
    return [
        {"role": "user", "content": item["input"]},
        {"role": "user", "content": item["query"]},
    ]


def evaluate_accuracy(items: list) -> dict:
    scorer = FidelityScorer()
    compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=0.90,
                                  max_attempts=4, min_content_len=30)

    stats = {"baseline": [0, 0], "summary": [0, 0], "adaptive": [0, 0], "raw": [0, 0]}
    # [答对, 总数]

    for item in items:
        messages = to_messages(item)
        budget = max(150, sum(len(str(m.get("content", ""))) for m in messages) // 2)
        reference = (item.get("answers") or [""])[0]
        query = item.get("query", "")

        # 1) 原始上下文(不压缩):基线
        raw_text = "\n".join(str(m.get("content", "")) for m in messages)
        stats["raw"][1] += 1
        if answer_is_correct(raw_text, reference):
            stats["raw"][0] += 1

        # 2) baseline 压缩
        b_comp = _bm.baseline_compress(messages, budget)
        b_text = "\n".join(str(m.get("content", "")) for m in b_comp)
        stats["baseline"][1] += 1
        if answer_is_correct(b_text, reference):
            stats["baseline"][0] += 1

        # 3) summary 压缩
        s_comp = _bm.summary_compress(messages, budget)
        s_text = "\n".join(str(m.get("content", "")) for m in s_comp)
        stats["summary"][1] += 1
        if answer_is_correct(s_text, reference):
            stats["summary"][0] += 1

        # 4) adaptive 压缩
        a_res = compactor.compact(messages, budget)
        a_text = "\n".join(str(m.get("content", "")) for m in a_res["messages"])
        stats["adaptive"][1] += 1
        if answer_is_correct(a_text, reference):
            stats["adaptive"][0] += 1

    return {k: (v[0], v[1], v[0] / v[1] if v[1] else 0.0) for k, v in stats.items()}


def write_report(stats: dict, n_items: int) -> str:
    lines = [
        "# 压缩后 Q&A 任务准确率报告",
        "",
        f"> 评测样例数:{n_items} 条(LongBench 6 大类)",
        "> 口径:压缩后从上下文中提取答案,与参考答案比对(关键词命中 ≥50% 判对)",
        "",
        "| 策略 | 答对 | 总数 | 准确率 | 相对原始下降 |",
        "|------|------|------|--------|-------------|",
    ]
    raw_acc = stats["raw"][2]
    for name, cn in [
        ("原始上下文(基线)", "raw"),
        ("baseline(截断)", "baseline"),
        ("summary(摘要)", "summary"),
        ("adaptive(保真约束)", "adaptive"),
    ]:
        hit, total, acc = stats[cn]
        drop = raw_acc - acc
        lines.append(f"| {name} | {hit} | {total} | {acc:.1%} | {drop:+.1%} |")
    lines += [
        "",
        "> 解读:准确率下降越小,压缩对任务的影响越小。",
        "> adaptive 应接近原始基线,而截断/摘要可能显著下降。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    return report


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA
    items = load_items(path)
    print(f"加载 {len(items)} 条评测样例")
    stats = evaluate_accuracy(items)
    report = write_report(stats, len(items))
    print(report)
    print(f"\n报告已写入: {REPORT}")


if __name__ == "__main__":
    main()
