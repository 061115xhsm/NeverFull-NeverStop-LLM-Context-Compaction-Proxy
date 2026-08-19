"""
RECOMP 风格基线(benchmark/recomp_style.py)
==========================================
复现 RECOMP(Xu et al. 2024)的核心机制:用摘要器生成压缩文本,
目标是"压缩后仍可支撑下游任务"(压缩-再检索联合优化的抽取式近似)。

RECOMP 原文用监督训练的摘要器;本地无该模型,采用工程近似:
- 任务无关抽取式摘要:按信息密度(数字/专名/长度)+ 位置先验加权打分,
  贪心保留 top 句子至预算 B(保留原顺序)
- 该近似对应 RECOMP 的抽取式(extractive)变体

用法: python3 benchmark/recomp_style.py
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
                      "recomp_style_report.md")
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


def density_score(s: str) -> float:
    """信息密度:数字/专名/关键信息占比。"""
    nums = len(re.findall(r"[0-9]+", s))
    names = len(re.findall(r"[A-Z][a-z]{2,}", s))  # 英文专名
    length = len(s)
    return (nums * 2 + names) / max(1, length)


def position_prior(i: int, total: int) -> float:
    """位置先验:开头 0.6,结尾 0.3,中间 0.1(RECOMP/抽取摘要常见先验)。"""
    if i == 0:
        return 0.6
    if i == total - 1:
        return 0.3
    return 0.1


def extractive_summarize(text: str, keep: float) -> str:
    """抽取式摘要:按 信息密度×0.6 + 位置先验×0.4 打分,贪心保留 top 句子。"""
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return text
    total = len(sentences)
    scores = [
        density_score(s) * 0.6 + position_prior(i, total) * 0.4
        for i, s in enumerate(sentences)
    ]
    n_keep = max(1, int(total * keep))
    top_idx = sorted(range(total), key=lambda i: scores[i], reverse=True)[:n_keep]
    kept = [sentences[i] for i in sorted(top_idx)]
    return " ".join(kept)


def run():
    scorer = FidelityScorer()
    items = load_items(DATA, NUM_SAMPLES)
    print(f"加载 {len(items)} 条样例,预算档 {KEEP_RATIOS}")

    results = []
    for keep in KEEP_RATIOS:
        ratios, fids = [], []
        for item in items:
            text = item.get("context", "")
            orig = len(text)
            comp = extractive_summarize(text, keep)
            ratios.append(1 - len(comp) / orig if orig else 0)
            fids.append(scorer.score(text, comp))
        results.append({"keep": keep, "ratio": sum(ratios) / len(ratios),
                        "fid": sum(fids) / len(fids)})
        print(f"  B={keep:.0%}: 压缩率 {results[-1]['ratio']:.3f} | 保真度 {results[-1]['fid']:.3f}")

    lines = [
        "# RECOMP 风格基线报告(抽取式近似)",
        "",
        f"> 数据:官方 LongBench multifieldqa_zh 前 {NUM_SAMPLES} 条",
        "> 机制:任务无关抽取式摘要(信息密度×0.6 + 位置先验×0.4)",
        "> 注:RECOMP 原文用监督摘要器,此为抽取式工程近似",
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
