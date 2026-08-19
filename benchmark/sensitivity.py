"""
τ×B 参数灵敏度扫描(benchmark/sensitivity.py)
==============================================
对 FF-Compactor 的两个核心旋钮做网格扫描:
- τ(保真底线)∈ {0.85, 0.90, 0.92, 0.95}
- B(目标预算 = 原文保留比例)∈ {30%, 50%, 70%}

在官方 LongBench(multifieldqa_zh 前 20 条)上测量每组组合的
平均压缩率 / 平均保真度 / 平均保留率,验证策略的鲁棒性与可控性。

用法: python3 benchmark/sensitivity.py
"""

from __future__ import annotations

import json
import os
import sys

_CUR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_CUR)
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

DATA = os.path.join(_CUR, "data", "longbench", "data", "multifieldqa_zh.jsonl")
REPORT = os.path.join(_CUR, "sensitivity_report.md")

TAUS = [0.85, 0.90, 0.92, 0.95]
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


def to_messages(item: dict) -> list:
    return [
        {"role": "user", "content": item.get("context", "")},
        {"role": "user", "content": item.get("input", "")},
    ]


def run():
    scorer = FidelityScorer()
    items = load_items(DATA, NUM_SAMPLES)
    print(f"加载 {len(items)} 条 LongBench 样例,网格 {len(TAUS)}×{len(KEEP_RATIOS)} = "
          f"{len(TAUS) * len(KEEP_RATIOS)} 组")

    results = []
    for tau in TAUS:
        for keep in KEEP_RATIOS:
            compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=tau,
                                          max_attempts=4, min_content_len=30)
            ratios, fids, rets = [], [], []
            for item in items:
                msgs = to_messages(item)
                orig_chars = sum(len(str(m.get("content", ""))) for m in msgs)
                budget = max(100, int(orig_chars * keep))
                res = compactor.compact(msgs, budget)
                comp_chars = sum(len(str(m.get("content", ""))) for m in res["messages"])
                ratios.append(1 - comp_chars / orig_chars if orig_chars else 0)
                fids.append(res["fidelity"])
                # 保留率:参考答案关键词是否在压缩文本中
                ref = (item.get("answers") or [""])[0]
                ref_tokens = [t for t in ref if len(t) > 1][:3]
                comp_text = "".join(str(m.get("content", "")) for m in res["messages"])
                if ref_tokens:
                    rets.append(sum(1 for t in ref_tokens if t in comp_text) / len(ref_tokens))
                else:
                    rets.append(1.0)
            n = len(ratios)
            results.append({
                "tau": tau, "keep": keep,
                "ratio": sum(ratios) / n, "fid": sum(fids) / n, "ret": sum(rets) / n,
            })
            print(f"  τ={tau} B={keep:.0%}: 压缩率 {results[-1]['ratio']:.3f} "
                  f"| 保真度 {results[-1]['fid']:.3f} | 保留率 {results[-1]['ret']:.3f}")

    lines = [
        "# τ×B 参数灵敏度扫描报告",
        "",
        f"> 数据:官方 LongBench multifieldqa_zh 前 {NUM_SAMPLES} 条",
        "> τ = 保真底线; B = 目标预算(原文保留比例)",
        "",
        "| τ \\ B | 30% 压缩率/保真度/保留率 | 50% 压缩率/保真度/保留率 | 70% 压缩率/保真度/保留率 |",
        "|-------|------------------------|------------------------|------------------------|",
    ]
    for tau in TAUS:
        cells = []
        for keep in KEEP_RATIOS:
            r = next(x for x in results if x["tau"] == tau and x["keep"] == keep)
            cells.append(f"{r['ratio']:.3f}/{r['fid']:.3f}/{r['ret']:.3f}")
        lines.append(f"| {tau} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "> 解读:固定 τ 时,预算越小压缩率越高;固定 B 时,τ 越高保真度底线越严。",
        "> 理想策略:压缩率随预算单调,保真度守住 τ 底线,保留率在高 τ 下更高。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入 {REPORT}")


if __name__ == "__main__":
    run()
