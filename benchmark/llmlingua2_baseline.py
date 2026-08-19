"""
LLMLingua-2 基线(benchmark/llmlingua2_baseline.py)
====================================================
LLMLingua-2(Xu et al., ACL Findings 2024)核心机制:token 分类。
用 XLMRobertaForTokenClassification 对每个 token 输出 keep/delete
两类 logits,按压缩率 rate 删除"删除概率最高"的 token。

llmlingua2 包不在 PyPI,故用 transformers 直调官方模型:
  microsoft/llmlingua-2-xlm-roberta-large-meetingbank

用法: python3 benchmark/llmlingua2_baseline.py
环境变量: LLMLINGUA2_MODEL, LLMLINGUA2_RATE, LLMLINGUA2_SAMPLES
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
    "LLMLINGUA2_MODEL", "microsoft/llmlingua-2-xlm-roberta-large-meetingbank")
RATE = float(os.environ.get("LLMLINGUA2_RATE", "0.5"))
NUM_SAMPLES = int(os.environ.get("LLMLINGUA2_SAMPLES", "15"))
DATA_PATH = os.environ.get(
    "LLMLINGUA2_DATA",
    "/media/qq/文档/llm-compaction-proxy-data/longbench/data/multifieldqa_zh.jsonl",
)
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llmlingua2_report.md")


def load_model():
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[设备] {device}")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    model = AutoModelForTokenClassification.from_pretrained(MODEL_PATH).to(device)
    model.eval()
    print(f"[模型] 加载完成({time.time()-t0:.0f}s, {model.num_parameters()/1e6:.0f}M 参数)")
    return model, tokenizer, device


def compress_prompt(model, tokenizer, device, text: str, rate: float,
                    max_len: int = 500) -> str:
    """
    LLMLingua-2 式 token 分类压缩(分块处理,适配 514 token 上限):
    1. 按 max_len 分块(保留句边界)
    2. 每块:分词 → 前向 → 按 rate 删除"删除概率最高"的 token
    3. 用偏移量重建文本,拼接各块
    """
    import torch

    # 1) 分块:按 tokenizer 分词后每 max_len 个 token 一块(粗分)
    raw_enc = tokenizer(text, return_offsets_mapping=True, truncation=False)
    raw_offsets = raw_enc["offset_mapping"]
    n_tokens = len(raw_enc["input_ids"])
    if n_tokens <= max_len:
        chunks = [(0, n_tokens)]
    else:
        chunks = []
        for start in range(0, n_tokens, max_len):
            end = min(start + max_len, n_tokens)
            chunks.append((start, end))

    result_parts = []
    for start, end in chunks:
        # 该块对应的原始字符区间
        s_char = raw_offsets[start][0] if start < len(raw_offsets) else 0
        e_char = raw_offsets[end - 1][1] if end - 1 < len(raw_offsets) else len(text)
        block_text = text[s_char:e_char]

        # 2) 块内 token 分类
        enc = tokenizer(block_text, return_offsets_mapping=True, truncation=True,
                        max_length=max_len, return_tensors="pt")
        offsets = enc["offset_mapping"][0].tolist()
        input_ids = enc["input_ids"].to(device)
        attn = enc["attention_mask"].to(device)
        with torch.no_grad():
            out = model(input_ids=input_ids, attention_mask=attn)
            probs = torch.softmax(out.logits[0], dim=-1)
            delete_probs = probs[:, 1].cpu().numpy()

        seq_len = len(offsets)
        n_delete = max(0, int(seq_len * rate))
        keep_mask = [True] * seq_len
        candidates = []
        for i in range(seq_len):
            tok = tokenizer.convert_ids_to_tokens(input_ids[0][i].item())
            if offsets[i] == (0, 0) or tok in (tokenizer.cls_token,
                                               tokenizer.sep_token,
                                               tokenizer.pad_token):
                continue
            candidates.append((i, delete_probs[i]))
        candidates.sort(key=lambda x: x[1], reverse=True)
        for i, _ in candidates[:n_delete]:
            keep_mask[i] = False

        # 3) 用偏移量重建块文本
        kept = "".join(block_text[s:e] for i, (s, e) in enumerate(offsets)
                       if keep_mask[i] and s != e)
        result_parts.append(kept)

    return "".join(result_parts)


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
    print(f"[数据] {len(samples)} 条 LongBench 样例, rate={RATE}")

    rows = []
    for i, item in enumerate(samples, 1):
        text = item.get("context", "")[:1200] + "\nQuestion: " + item.get("input", "")
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
        print(f"\n✅ LLMLingua-2 基线: 平均压缩率 {avg_r:.3f} | 平均保真度 {avg_f:.3f}")

        report = (
            f"# LLMLingua-2 基线报告\n\n"
            f"- 模型: {MODEL_PATH}({model.num_parameters()/1e6:.0f}M 参数, token 分类)\n"
            f"- 数据: {DATA_PATH.split('/')[-1]} 前 {len(rows)} 条\n"
            f"- rate: {RATE}\n"
            f"- 平均压缩率: **{avg_r:.3f}**\n"
            f"- 平均保真度: **{avg_f:.3f}**\n"
        )
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入 {REPORT}")
    else:
        print("❌ 无有效结果")


if __name__ == "__main__":
    main()
