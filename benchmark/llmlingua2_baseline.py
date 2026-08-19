"""
LLMLingua2 风格基线(benchmark/llmlingua2_baseline.py)
========================================================
llmlingua2 包不在 PyPI,故用 transformers 直接加载已下载的
xlm-roberta 模型(allenai/llmlingua-2-0.7b 风格),实现
LLMLingua2 的核心机制——逐 token 困惑度(重要性)剪枝:

1. 加载模型,对输入分词
2. 用 MLM head 计算每个 token 的重要性(被 mask 后恢复难度 ≈ 困惑度)
3. 按压缩率保留高重要性 token,删除低重要性 token
4. 计算压缩率 + 语义保真度(FidelityScorer)

用法: python3 benchmark/llmlingua2_baseline.py
环境变量: LLMLINGUA2_MODEL(默认文档盘已下载模型), LLMLINGUA2_RATE(默认 0.5)
"""

from __future__ import annotations

import json
import os
import sys
import time

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)

from fidelity import FidelityScorer  # noqa: E402

MODEL_PATH = os.environ.get(
    "LLMLINGUA2_MODEL",
    "/media/qq/文档/llm-compaction-proxy-data/llmlingua2-local",
)
RATE = float(os.environ.get("LLMLINGUA2_RATE", "0.5"))
NUM_SAMPLES = int(os.environ.get("LLMLINGUA2_SAMPLES", "10"))
DATA_PATH = os.environ.get(
    "LLMLINGUA2_DATA",
    "/media/qq/文档/llm-compaction-proxy-data/longbench/data/multifieldqa_zh.jsonl",
)
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llmlingua2_report.md")


def load_model():
    """加载 xlm-roberta 模型 + MLM head(GPU 优先)。"""
    from transformers import AutoModelForMaskedLM, AutoTokenizer
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[设备] {device}")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    model = AutoModelForMaskedLM.from_pretrained(MODEL_PATH).to(device)
    model.eval()
    print(f"[模型] 加载完成({time.time()-t0:.0f}s, {model.num_parameters()/1e6:.0f}M 参数)")
    return model, tokenizer, device


def token_importance(model, tokenizer, device, text: str, max_len: int = 512) -> list:
    """
    逐 token 重要性:对每个 token 单独 mask,用 MLM 预测,恢复难度即重要性。
    近似困惑度(LLMLingua2 的 trained scorer)。
    分批处理避免 OOM;返回 [(token, importance), ...]。
    """
    import torch

    enc = tokenizer(text, return_tensors="pt", truncation=True, max_length=max_len)
    input_ids = enc["input_ids"].to(device)
    attn = enc["attention_mask"].to(device)
    seq_len = input_ids.shape[1]

    importances = []
    batch_ids = []
    with torch.no_grad():
        # 对每个位置做 mask 预测(小批量 16)
        for i in range(seq_len):
            batch_ids.append(i)
            if len(batch_ids) == 16 or i == seq_len - 1:
                masked = input_ids.clone()
                masked[0, batch_ids] = tokenizer.mask_token_id
                out = model(input_ids=masked, attention_mask=attn)
                logits = out.logits[0]  # [seq, vocab]
                for pos in batch_ids:
                    true_id = input_ids[0, pos].item()
                    probs = torch.softmax(logits[pos], dim=-1)
                    p_true = probs[true_id].item()
                    importance = -torch.log(torch.tensor(max(p_true, 1e-9))).item()
                    importances.append((pos, importance))
                batch_ids = []

    # 还原顺序
    importances.sort(key=lambda x: x[0])
    return importances


def compress_prompt(model, tokenizer, device, text: str, rate: float) -> str:
    """按 token 重要性剪枝:保留 top (1-rate) 高重要性 token。"""
    import torch

    importances = token_importance(model, tokenizer, device, text)
    tokens = tokenizer.tokenize(text)
    if len(tokens) <= 2:
        return text

    n_keep = max(1, int(len(tokens) * (1 - rate)))
    # 按重要性降序取 top-k,再按原顺序重组
    ranked = sorted(importances, key=lambda x: x[1], reverse=True)[:n_keep]
    keep_pos = sorted(p[0] for p in ranked)
    kept_tokens = [tokens[p] for p in keep_pos if p < len(tokens)]
    compressed = tokenizer.convert_tokens_to_string(kept_tokens)
    return compressed


def load_samples(path: str, limit: int) -> list:
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
    model, tokenizer, device = load_model()
    scorer = FidelityScorer()
    samples = load_samples(DATA_PATH, NUM_SAMPLES)
    print(f"[数据] {len(samples)} 条 LongBench 样例")

    rows = []
    for i, item in enumerate(samples, 1):
        text = item.get("context", "")[:1500] + "\nQuestion: " + item.get("input", "")
        orig_chars = len(text)
        try:
            compressed = compress_prompt(model, tokenizer, device, text, RATE)
            comp_chars = len(compressed)
            ratio = 1 - comp_chars / orig_chars if orig_chars else 0
            fid = scorer.score(text, compressed)
            rows.append((ratio, fid))
            print(f"  样例{i}: 压缩率 {ratio:.3f} | 保真度 {fid:.3f}")
        except Exception as e:
            print(f"  样例{i} 失败: {str(e)[:100]}")

    if rows:
        avg_r = sum(r[0] for r in rows) / len(rows)
        avg_f = sum(r[1] for r in rows) / len(rows)
        print(f"\n✅ LLMLingua2 风格基线: 平均压缩率 {avg_r:.3f} | 平均保真度 {avg_f:.3f}")

        report = (
            f"# LLMLingua2 基线报告\n\n"
            f"- 模型: xlm-roberta(LLMLingua2 风格,{model.num_parameters()/1e6:.0f}M 参数)\n"
            f"- 数据: {DATA_PATH.split('/')[-1]} 前 {len(rows)} 条\n"
            f"- rate: {RATE}\n"
            f"- 平均压缩率: **{avg_r:.3f}**\n"
            f"- 平均保真度: **{avg_f:.3f}**\n\n"
            f"> 注: llmlingua2 包不在 PyPI,此为 transformers 直调 MLM 困惑度剪枝的近似实现。\n"
        )
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入 {REPORT}")
    else:
        print("❌ 无有效结果")


if __name__ == "__main__":
    main()
