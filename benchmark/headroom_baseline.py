"""
Headroom 基线对比(benchmark/headroom_baseline.py)
====================================================
在官方 LongBench 数据上跑 Headroom(headroom-ai)压缩,
与 FF-Compactor 同口径对比压缩率/保真度。

用法: python3 benchmark/headroom_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer  # noqa: E402

DATA = os.path.join(_PARENT, "benchmark", "data", "longbench", "data",
                    "multifieldqa_zh.jsonl")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "headroom_report.md")
NUM_SAMPLES = int(os.environ.get("HEADROOM_SAMPLES", "10"))


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


def main():
    from headroom import compress

    scorer = FidelityScorer()
    items = load_items(DATA, NUM_SAMPLES)
    print(f"加载 {len(items)} 条 LongBench 样例")

    rows = []
    for i, item in enumerate(items, 1):
        text = item.get("context", "")[:1200] + "\nQuestion: " + item.get("input", "")
        orig_chars = len(text)
        # Headroom compress:messages 列表
        messages = [{"role": "user", "content": text}]
        t0 = time.time()
        try:
            result = compress(messages, model="claude-sonnet-4-5-20250929")
            dt = (time.time() - t0) * 1000
            # CompressResult -> 取压缩后 messages
            comp_msgs = result.messages if hasattr(result, "messages") else result
            if isinstance(comp_msgs, list):
                comp_text = " ".join(str(m.get("content", "")) for m in comp_msgs)
            else:
                comp_text = str(comp_msgs)
            comp_chars = len(comp_text)
            ratio = 1 - comp_chars / orig_chars if orig_chars else 0
            fid = scorer.score(text, comp_text)
            rows.append((ratio, fid, dt))
            print(f"  样例{i}: 压缩率 {ratio:.3f} | 保真度 {fid:.3f} | 耗时 {dt:.0f}ms")
        except Exception as e:
            print(f"  样例{i} 失败: {str(e)[:120]}")

    if rows:
        avg_r = sum(r[0] for r in rows) / len(rows)
        avg_f = sum(r[1] for r in rows) / len(rows)
        avg_t = sum(r[2] for r in rows) / len(rows)
        print(f"\n✅ Headroom 基线: 平均压缩率 {avg_r:.3f} | 平均保真度 {avg_f:.3f} | 平均耗时 {avg_t:.0f}ms")

        report = (
            f"# Headroom 基线报告\n\n"
            f"- 版本: headroom-ai 0.35.0\n"
            f"- 数据: {DATA.split('/')[-1]} 前 {len(rows)} 条\n"
            f"- 平均压缩率: **{avg_r:.3f}**\n"
            f"- 平均保真度: **{avg_f:.3f}**\n"
            f"- 平均耗时: **{avg_t:.0f}ms**\n"
        )
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入 {REPORT}")
    else:
        print("❌ 无有效结果")


if __name__ == "__main__":
    main()
