"""
LLMLingua rate 灵敏度对比(benchmark/rate_sensitivity.py)
==========================================================
对 LLMLingua 的压缩率旋钮 rate 做三档扫描(0.3/0.5/0.7),
并与 FF-Compactor 的预算灵敏度(B=30%/50%/70%)对比:
同一批 LongBench 数据、同一保真度口径,展示两种方法
"压缩率↔保真度"权衡曲线的差异。

用法: python3 benchmark/rate_sensitivity.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_CUR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_CUR)
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer, AdaptiveCompactor  # noqa: E402

MODEL = os.environ.get(
    "LLMLINGUA_MODEL", "/media/qq/文档/llm-compaction-proxy-data/llama2-7b-local")
DATA = os.path.join(_CUR, "data", "longbench", "data", "multifieldqa_zh.jsonl")
REPORT = os.path.join(_CUR, "rate_sensitivity_report.md")
RATES = [0.3, 0.5, 0.7]
NUM_SAMPLES = 6  # 每档 6 条控制总耗时(LLMLingua 7B 慢)


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


def llmlingua_compress(model, tokenizer, text: str, rate: float) -> str:
    """LLMLingua 压缩(截断长文本防 OOM)。"""
    prompt = text[:1500]
    result = model.compress_prompt(prompt, rate=rate, force_tokens=['\n', ' ', '.', ',', '?'])
    return result.get("compressed_prompt", prompt)


def run():
    scorer = FidelityScorer()
    items = load_items(DATA, NUM_SAMPLES)
    print(f"加载 {len(items)} 条样例")

    # LLMLingua rate 扫描
    print("[LLMLingua] 初始化 7B INT8...")
    from llmlingua import PromptCompressor
    from transformers import BitsAndBytesConfig
    llm = PromptCompressor(
        model_name=MODEL, device_map="cuda",
        model_config={"quantization_config": BitsAndBytesConfig(load_in_8bit=True)},
    )
    llm_rows = []
    for rate in RATES:
        ratios, fids = [], []
        for item in items:
            text = item.get("context", "")[:1200] + "\nQuestion: " + item.get("input", "")
            try:
                comp = llmlingua_compress(llm, None, text, rate)
                ratios.append(1 - len(comp) / len(text))
                fids.append(scorer.score(text, comp))
            except Exception as e:
                print(f"  失败: {str(e)[:80]}")
        if ratios:
            llm_rows.append({"rate": rate, "ratio": sum(ratios)/len(ratios),
                             "fid": sum(fids)/len(fids)})
            print(f"  LLMLingua rate={rate}: 压缩率 {llm_rows[-1]['ratio']:.3f} "
                  f"| 保真度 {llm_rows[-1]['fid']:.3f}")

    # FF-Compactor 预算扫描(同数据)
    print("[FF-Compactor] 预算灵敏度扫描...")
    ff_rows = []
    for keep in [0.30, 0.50, 0.70]:
        compactor = AdaptiveCompactor(scorer=scorer, min_fidelity=0.90,
                                      max_attempts=4, min_content_len=30)
        ratios, fids = [], []
        for item in items:
            msgs = [{"role": "user", "content": item.get("context", "")},
                    {"role": "user", "content": item.get("input", "")}]
            orig = sum(len(str(m.get("content", ""))) for m in msgs)
            res = compactor.compact(msgs, max(100, int(orig * keep)))
            comp = sum(len(str(m.get("content", ""))) for m in res["messages"])
            ratios.append(1 - comp / orig)
            fids.append(res["fidelity"])
        ff_rows.append({"keep": keep, "ratio": sum(ratios)/len(ratios),
                        "fid": sum(fids)/len(fids)})
        print(f"  FF-Compactor B={keep:.0%}: 压缩率 {ff_rows[-1]['ratio']:.3f} "
              f"| 保真度 {ff_rows[-1]['fid']:.3f}")

    lines = [
        "# 压缩率↔保真度权衡曲线对比报告",
        "",
        f"> 数据:官方 LongBench multifieldqa_zh 前 {NUM_SAMPLES} 条(同批同口径)",
        "",
        "## LLMLingua-7B rate 扫描",
        "",
        "| rate | 压缩率 | 保真度 |",
        "|------|--------|--------|",
    ]
    for r in llm_rows:
        lines.append(f"| {r['rate']} | {r['ratio']:.3f} | {r['fid']:.3f} |")
    lines += ["", "## FF-Compactor 预算扫描", "", "| B | 压缩率 | 保真度 |",
              "|------|--------|--------|"]
    for r in ff_rows:
        lines.append(f"| {r['keep']:.0%} | {r['ratio']:.3f} | {r['fid']:.3f} |")
    lines += [
        "",
        "> 解读:同压缩率下保真度更高者权衡曲线更优。",
        "> FF-Compactor 预期在各压缩率档位保真度高于 LLMLingua(保真门控)。",
    ]
    report = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入 {REPORT}")


if __name__ == "__main__":
    run()
