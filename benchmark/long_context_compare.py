"""
超长上下文公平对比(benchmark/long_context_compare.py)
========================================================
在 ~400K 字符(≈200K token)合成超长上下文上,同条件对比
Headroom 与 FF-Compactor 的压缩率/保真度/延迟。

用法: python3 benchmark/long_context_compare.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

CONTEXT_FILE = "/tmp/long_context_400k.txt"
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "long_context_compare_report.md")


def main():
    scorer = FidelityScorer()
    with open(CONTEXT_FILE, encoding="utf-8") as f:
        text = f.read()
    orig_chars = len(text)
    print(f"合成上下文: {orig_chars} 字符(≈{orig_chars//2} token)")

    results = {}

    # ── 1. Headroom ──
    print("\n[Headroom] 压缩中...")
    try:
        from headroom import compress as hr_compress
        t0 = time.time()
        hr_result = hr_compress(
            [{"role": "user", "content": text}],
            model="claude-sonnet-4-5-20250929",
            model_limit=200000,
        )
        hr_dt = (time.time() - t0) * 1000
        hr_msgs = hr_result.messages if hasattr(hr_result, "messages") else hr_result
        if isinstance(hr_msgs, list):
            hr_text = " ".join(str(m.get("content", "")) for m in hr_msgs)
        else:
            hr_text = str(hr_msgs)
        hr_chars = len(hr_text)
        hr_ratio = 1 - hr_chars / orig_chars if orig_chars else 0
        hr_fid = scorer.score(text, hr_text)
        results["Headroom"] = {
            "ratio": hr_ratio, "fid": hr_fid, "delay_ms": hr_dt,
            "comp_chars": hr_chars,
        }
        print(f"  压缩率 {hr_ratio:.3f} | 保真度 {hr_fid:.3f} | 延迟 {hr_dt:.0f}ms | 压缩后 {hr_chars} 字符")
    except Exception as e:
        results["Headroom"] = {"error": str(e)[:200]}
        print(f"  失败: {str(e)[:150]}")

    # ── 2. FF-Compactor ──
    print("\n[FF-Compactor] 压缩中...")
    try:
        compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=0.90,
                                       max_attempts=4, min_content_len=30)
        messages = [{"role": "user", "content": text}]
        budget = max(100, int(orig_chars * 0.5))  # 50% 预算
        t0 = time.time()
        ff_result = compactor.compact(messages, budget)
        ff_dt = (time.time() - t0) * 1000
        ff_msgs = ff_result["messages"]
        ff_text = " ".join(str(m.get("content", "")) for m in ff_msgs)
        ff_chars = len(ff_text)
        ff_ratio = 1 - ff_chars / orig_chars if orig_chars else 0
        ff_fid = scorer.score(text, ff_text)
        results["FF-Compactor"] = {
            "ratio": ff_ratio, "fid": ff_fid, "delay_ms": ff_dt,
            "comp_chars": ff_chars,
        }
        print(f"  压缩率 {ff_ratio:.3f} | 保真度 {ff_fid:.3f} | 延迟 {ff_dt:.0f}ms | 压缩后 {ff_chars} 字符")
    except Exception as e:
        results["FF-Compactor"] = {"error": str(e)[:200]}
        print(f"  失败: {str(e)[:150]}")

    # ── 3. 报告 ──
    lines = [
        "# 超长上下文公平对比报告",
        "",
        f"> 合成上下文: {orig_chars} 字符(≈{orig_chars//2} token,接近 200K 窗口阈值)",
        "> 同条件同数据: Headroom(model_limit=200000) vs FF-Compactor(预算 50%)",
        "",
        "| 项目 | 压缩率 | 保真度 | 延迟 | 压缩后字符 |",
        "|------|--------|--------|------|-----------|",
    ]
    for name, r in results.items():
        if "error" in r:
            lines.append(f"| {name} | ❌ 失败 | — | — | — |")
        else:
            lines.append(
                f"| {name} | {r['ratio']:.3f} | {r['fid']:.3f} | "
                f"{r['delay_ms']:.0f}ms | {r['comp_chars']} |"
            )
    lines += [
        "",
        "> 解读:超长上下文下 Headroom 应触发 CCR 压缩;",
        "> FF-Compactor 以保真门控压缩,两者在此场景下才真正可比。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入 {REPORT}")


if __name__ == "__main__":
    main()
