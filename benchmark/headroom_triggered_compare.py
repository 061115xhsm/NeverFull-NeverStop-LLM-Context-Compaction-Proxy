"""
Headroom 触发后公平对比(benchmark/headroom_triggered_compare.py)
================================================================
Headroom 默认保护 user 消息不压缩(transforms: router:protected:user_message)。
本脚本用 assistant role + 多种内容类型(JSON/代码/日志/散文)触发其
smart_crusher/code_compressor,与 FF-Compactor 同条件对比。

用法: python3 benchmark/headroom_triggered_compare.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "headroom_triggered_report.md")


def gen_json_content(n: int = 200) -> str:
    return json.dumps(
        [{"name": f"item_{i}", "value": i * 100, "desc": "描述内容" * (i % 5 + 1)}
         for i in range(n)],
        ensure_ascii=False,
    )


def gen_code_content() -> str:
    funcs = []
    for i in range(60):
        funcs.append(f"def process_{i}(data):")
        funcs.append(f"    result = []")
        funcs.append(f"    for item in data:")
        funcs.append(f"        if item.get('value', 0) > {i * 10}:")
        funcs.append(f"            result.append({{'name': item['name'], 'val': item['value'] * 2}})")
        funcs.append(f"    return result")
    return "\n".join(funcs)


def gen_log_content() -> str:
    return "\n".join([
        f"[2026-08-20 10:{i:02d}:{i%60:02d}] INFO  module_{i%5} processing batch {i} "
        f"status=ok items={i*10} latency={i*5}ms memory={i*2}MB"
        for i in range(300)
    ])


def gen_prose_content() -> str:
    base = ("上下文压缩代理在上下文接近 80% 阈值时触发预压缩,压缩流程为拆分消息、"
            "子模选择、结构化摘要、安全验证。保真度门控以 Sim(M,M')≥τ 为约束。")
    return "\n\n".join([f"段落{i}: {base}" for i in range(150)])


CONTENT_TYPES = [
    ("JSON", gen_json_content()),
    ("代码", gen_code_content()),
    ("日志", gen_log_content()),
    ("散文", gen_prose_content()),
]


def main():
    from headroom import compress as hr_compress

    scorer = FidelityScorer()
    compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=0.90,
                                  max_attempts=4, min_content_len=30)

    print(f"内容类型: {len(CONTENT_TYPES)} 种,每种 Headroom vs FF-Compactor\n")
    rows = []

    for ctype, text in CONTENT_TYPES:
        orig = len(text)
        print(f"=== {ctype}({orig} 字符)===")

        # Headroom(assistant role 触发 smart_crusher)
        t0 = time.time()
        try:
            r = hr_compress(
                [{"role": "assistant", "content": text}],
                model="claude-sonnet-4-5-20250929",
                model_limit=1000,
            )
            hr_dt = (time.time() - t0) * 1000
            hr_text = " ".join(str(m.get("content", "")) for m in r.messages) if isinstance(r.messages, list) else str(r.messages)
            hr_ratio = 1 - len(hr_text) / orig if orig else 0
            hr_fid = scorer.score(text, hr_text)
            hr_transforms = ",".join(r.transforms_applied) if hasattr(r, "transforms_applied") else "?"
            print(f"  Headroom: ratio={hr_ratio:.3f} fid={hr_fid:.3f} dt={hr_dt:.0f}ms transforms={hr_transforms}")
        except Exception as e:
            hr_ratio, hr_fid, hr_dt, hr_transforms = -1, -1, -1, f"err:{str(e)[:60]}"
            print(f"  Headroom 失败: {str(e)[:80]}")

        # FF-Compactor
        t0 = time.time()
        try:
            res = compactor.compact([{"role": "user", "content": text}], max(100, int(orig * 0.5)))
            ff_dt = (time.time() - t0) * 1000
            ff_text = " ".join(str(m.get("content", "")) for m in res["messages"])
            ff_ratio = 1 - len(ff_text) / orig if orig else 0
            ff_fid = scorer.score(text, ff_text)
            print(f"  FF-Compactor: ratio={ff_ratio:.3f} fid={ff_fid:.3f} dt={ff_dt:.0f}ms")
        except Exception as e:
            ff_ratio, ff_fid, ff_dt = -1, -1, -1
            print(f"  FF-Compactor 失败: {str(e)[:80]}")

        rows.append({
            "ctype": ctype, "orig": orig,
            "hr_ratio": hr_ratio, "hr_fid": hr_fid, "hr_dt": hr_dt, "hr_t": hr_transforms,
            "ff_ratio": ff_ratio, "ff_fid": ff_fid, "ff_dt": ff_dt,
        })

    # 报告
    lines = [
        "# Headroom 触发后公平对比报告",
        "",
        "> 触发方式: assistant role + model_limit=1000(绕过 user 消息保护)",
        "> 内容类型: JSON / 代码 / 日志 / 散文(Headroom 的 SmartCrusher/CodeCompressor 目标)",
        "> 同条件: 同文本、同保真度口径(sentence-transformers)",
        "",
        "| 内容类型 | 原文字符 | Headroom 压缩率 | Headroom 保真度 | Headroom 延迟 | FF 压缩率 | FF 保真度 | FF 延迟 | Headroom transforms |",
        "|---------|---------|----------------|----------------|-------------|-----------|-----------|---------|---------------------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['ctype']} | {r['orig']} | {r['hr_ratio']:.3f} | {r['hr_fid']:.3f} | "
            f"{r['hr_dt']:.0f}ms | {r['ff_ratio']:.3f} | {r['ff_fid']:.3f} | "
            f"{r['ff_dt']:.0f}ms | {r['hr_t'][:40]} |"
        )
    lines += [
        "",
        "> 解读:Headroom 在 JSON/代码/日志上触发 smart_crusher/code_compressor;",
        "> FF-Compactor 统一句子级压缩。比较两者在各自擅长内容上的保真度差异。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入 {REPORT}")


if __name__ == "__main__":
    main()
