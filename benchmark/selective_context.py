"""
Selective Context 风格基线(benchmark/selective_context.py)
============================================================
复现 Selective Context(Li et al. 2023)的核心机制:
对文本按句子粒度打分(重要性),保留高重要性句子至预算上限。

Selective Context 原文用 LLM 对句子打分;本地无可用 LLM 端点时,
采用近似打分(论文口径的工程近似,已注明):
- 句子与 query 的嵌入余弦相似度(语义相关性)
- 句子信息密度(实体/数字/专名密度,启发式)
两者加权后贪心保留 top 句子至预算 B。

用法: python3 benchmark/selective_context.py
"""

from __future__ import annotations

import json
import os
import re
import sys

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer  # noqa: E402

DATA = os.path.join(_PARENT, "benchmark", "data", "longbench", "data",
                    "multifieldqa_zh.jsonl")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "selective_context_report.md")
KEEP_RATIOS = [0.30, 0.50, 0.70]
NUM_SAMPLES = 20


def load_items(path: str, limit: int) -> list:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            if len(items) >= limit:
                break
    return items


def split_sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"[。！？!?；;\n]", text) if s.strip()]


def sentence_scores(sentences: list, query: str, embedder) -> list:
    """对每句打分:嵌入相似度(query) + 信息密度。"""
    scores = []
    q_vec = embedder.encode([query])[0] if query else None
    s_vecs = embedder.encode(sentences)
    import numpy as np
    for i, s in enumerate(sentences):
        sim = 0.0
        if q_vec is not None:
            n1 = np.linalg.norm(q_vec)
            n2 = np.linalg.norm(s_vecs[i])
            sim = float(np.dot(q_vec, s_vecs[i]) / (n1 * n2 + 1e-9)) if n1 and n2 else 0.0
        # 信息密度:数字/大写/专名占比
        nums = len(re.findall(r"[0-9]", s))
        upp = len(re.findall(r"[A-Z\u4e00-\u9fff]", s))
        density = (nums + upp) / max(1, len(s))
        scores.append(0.7 * sim + 0.3 * density)
    return scores


def selective_compress(text: str, query: str, keep: float, embedder) -> str:
    """按打分保留 top-keep 比例句子(保持原顺序)。"""
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return text
    scores = sentence_scores(sentences, query, embedder)
    n_keep = max(1, int(len(sentences) * keep))
    top_idx = sorted(range(len(sentences)), key=lambda i: scores[i],
                     reverse=True)[:n_keep]
    kept = [sentences[i] for i in sorted(top_idx)]
    return " ".join(kept)


def run():
    from sentence_transformers import SentenceTransformer
    scorer = FidelityScorer()
    print("[嵌入] 加载 sentence-transformers 模型...")
    embedder = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    items = load_items(DATA, NUM_SAMPLES)
    print(f"加载 {len(items)} 条样例,预算档 {KEEP_RATIOS}")

    results = []
    for keep in KEEP_RATIOS:
        ratios, fids = [], []
        for item in items:
            text = item.get("context", "")
            query = item.get("input", "")
            orig = len(text)
            comp = selective_compress(text, query, keep, embedder)
            ratios.append(1 - len(comp) / orig if orig else 0)
            fids.append(scorer.score(text, comp))
        results.append({"keep": keep, "ratio": sum(ratios) / len(ratios),
                        "fid": sum(fids) / len(fids)})
        print(f"  B={keep:.0%}: 压缩率 {results[-1]['ratio']:.3f} | 保真度 {results[-1]['fid']:.3f}")

    lines = [
        "# Selective Context 风格基线报告",
        "",
        f"> 数据:官方 LongBench multifieldqa_zh 前 {NUM_SAMPLES} 条",
        "> 机制:query 嵌入相似度(0.7)+ 信息密度(0.3)打分,贪心保留 top 句子",
        "> 注:原文用 LLM 打分,此处为本地嵌入近似(工程口径)",
        "",
        "| 预算 B | 压缩率 | 保真度 |",
        "|--------|--------|--------|",
    ]
    for r in results:
        lines.append(f"| {r['keep']:.0%} | {r['ratio']:.3f} | {r['fid']:.3f} |")
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入 {REPORT}")


if __name__ == "__main__":
    run()
