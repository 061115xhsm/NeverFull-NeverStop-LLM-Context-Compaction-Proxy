#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark/llmlingua_baseline.py
===============================
LLMLingua 压缩基线脚本。

功能:
    1. 尝试导入 llmlingua(PromptCompressor);
    2. 若 llmlingua 可用:
       - 读取 LongBench multifieldqa_zh 数据集前 10 条;
       - 用 LLMLingua 压缩 prompt, 计算压缩率(compressed_prompt vs original_tokens);
       - 复用父目录 fidelity.py 中的 FidelityScorer 计算语义保真度(处理 sys.path);
       - 生成 benchmark/llmlingua_report.md;
    3. 若 llmlingua 未安装:
       - 打印提示, 并生成占位报告(说明需 pip install llmlingua)。

输出:
    benchmark/llmlingua_report.md(与脚本同目录, 即 benchmark/ 下)
"""

import json
import numbers
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))            # benchmark/ 目录
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)                          # 项目根目录(父目录)
DATA_FILE = "/media/qq/文档/llm-compaction-proxy-data/longbench/data/multifieldqa_zh.jsonl"
REPORT_FILE = os.path.join(SCRIPT_DIR, "llmlingua_report.md")       # benchmark/llmlingua_report.md

# 将项目根目录加入 sys.path, 以便复用父目录下的 fidelity.py
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 可配置项(可通过环境变量覆盖)
LLMLINGUA_MODEL = os.environ.get("LLMLINGUA_MODEL", "NousResearch/Llama-2-7b-hf")
COMPRESS_RATE = float(os.environ.get("LLMLINGUA_RATE", "0.5"))
NUM_SAMPLES = 10


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_samples(path, limit=NUM_SAMPLES):
    """读取 jsonl 数据集, 返回前 limit 条记录; 文件缺失/解析失败时给出警告。"""
    samples = []
    if not os.path.exists(path):
        print(f"[警告] 数据文件不存在: {path}")
        return samples
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[警告] 第 {i + 1} 行 JSON 解析失败: {exc}")
                continue
            if len(samples) >= limit:
                break
    return samples


def build_prompt(item):
    """根据 LongBench multifieldqa_zh 字段构造待压缩 prompt。"""
    instruction = item.get("input", "").strip()
    context = item.get("context", "").strip()
    if instruction and context:
        return f"{instruction}\n\n{context}"
    return instruction or context


def token_count(value, fallback_text):
    """兼容 LLMLingua 返回的 token 统计(可能为 int 或 list)。"""
    if isinstance(value, numbers.Number):
        return int(value)
    if isinstance(value, (list, tuple)):
        return len(value)
    return len(fallback_text.split())


def load_fidelity_scorer():
    """
    复用父目录 fidelity.py 中的 FidelityScorer。
    PROJECT_ROOT 已加入 sys.path, 因此可直接 from fidelity import ...
    若导入失败或不存在, 返回 None(不影响压缩率计算)。
    """
    try:
        from fidelity import FidelityScorer  # noqa: F401
        return FidelityScorer
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 无法从父目录 fidelity.py 导入 FidelityScorer: {exc}")
        return None


def compute_fidelity(FidelityScorer, original, compressed):
    """
    灵活调用 FidelityScorer, 兼容多种常见接口:
      - FidelityScorer(original, compressed) 直接返回数值;
      - FidelityScorer(original, compressed).score();
      - FidelityScorer().score(original, compressed)。
    返回 0~1 浮点数; 调用失败返回 None。
    """
    if FidelityScorer is None:
        return None

    # 情况A: 构造时传入两个文本
    try:
        obj = FidelityScorer(original, compressed)
        if isinstance(obj, numbers.Number):
            return float(obj)
        for method in ("score", "fidelity", "compute", "calculate"):
            fn = getattr(obj, method, None)
            if fn is None:
                continue
            try:
                val = fn()
            except TypeError:
                continue
            if isinstance(val, numbers.Number):
                return float(val)
    except Exception:  # noqa: BLE001
        pass

    # 情况B: 无参构造 + 方法带两个文本参数(尝试两种参数顺序)
    try:
        obj = FidelityScorer()
        for method in ("score", "fidelity", "compute", "calculate"):
            fn = getattr(obj, method, None)
            if fn is None:
                continue
            for args in ((original, compressed), (compressed, original)):
                try:
                    val = fn(*args)
                except TypeError:
                    continue
                if isinstance(val, numbers.Number):
                    return float(val)
    except Exception:  # noqa: BLE001
        pass

    return None


def write_report(available, reason, avg_rate, avg_fidelity, details,
                 origin_total=None, compressed_total=None):
    """生成 benchmark/llmlingua_report.md。"""
    lines = []
    lines.append("# LLMLingua 压缩基线报告\n")
    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **llmlingua 可用**: {'是' if available else '否'}")
    lines.append(f"- **状态/原因**: {reason}")
    lines.append(f"- **数据集**: `{DATA_FILE}`(前 {NUM_SAMPLES} 条)")
    lines.append(f"- **压缩参数**: rate={COMPRESS_RATE}, model={LLMLINGUA_MODEL}\n")

    if not available:
        lines.append("## 说明\n")
        lines.append("本环境未安装 `llmlingua` 库, 无法执行真实压缩实验。")
        lines.append("请先安装依赖后重新运行本脚本:\n")
        lines.append("```bash")
        lines.append("pip install llmlingua")
        lines.append("```\n")
        lines.append("> 此为占位报告, 各项指标暂不可用。\n")

    lines.append("## 总体指标\n")
    if avg_rate is not None:
        lines.append(f"- **平均压缩率**: {avg_rate:.4f} ({avg_rate * 100:.2f}%)")
    else:
        lines.append("- **平均压缩率**: N/A")
    if avg_fidelity is not None:
        lines.append(f"- **平均保真度**: {avg_fidelity:.4f}")
    else:
        lines.append("- **平均保真度**: N/A")
    if origin_total:
        overall = 1.0 - compressed_total / origin_total
        lines.append(f"- **原始 token 总数**: {origin_total}")
        lines.append(f"- **压缩后 token 总数**: {compressed_total}")
        lines.append(f"- **整体压缩率**: {overall:.4f} ({overall * 100:.2f}%)")
    lines.append("")

    if details:
        lines.append("## 逐条明细\n")
        lines.append("| 序号 | ID | 原始tokens | 压缩后tokens | 压缩率 | 保真度 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for d in details:
            rate = f"{d['compression_rate']:.4f}" if d["compression_rate"] is not None else "N/A"
            fid = f"{d['fidelity']:.4f}" if d["fidelity"] is not None else "N/A"
            lines.append(
                f"| {d['index']} | {d['id']} | {d['origin_tokens']} | "
                f"{d['compressed_tokens']} | {rate} | {fid} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*由 benchmark/llmlingua_baseline.py 自动生成。*\n")

    with open(REPORT_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main():
    # 1) 检查 llmlingua 是否可用
    try:
        from llmlingua import PromptCompressor  # noqa: F401
        llmlingua_available = True
    except ImportError:
        llmlingua_available = False
        PromptCompressor = None
        print("[提示] llmlingua 未安装。")

    if not llmlingua_available:
        # 未安装: 输出提示并生成占位报告
        write_report(
            available=False,
            reason="llmlingua 未安装",
            avg_rate=None,
            avg_fidelity=None,
            details=[],
        )
        print("已生成占位报告:", REPORT_FILE)
        print("请先执行: pip install llmlingua, 再重新运行本脚本。")
        return

    # 2) 加载数据
    samples = load_samples(DATA_FILE)
    if not samples:
        write_report(
            available=True,
            reason=f"数据文件为空或不存在: {DATA_FILE}",
            avg_rate=None,
            avg_fidelity=None,
            details=[],
        )
        print("已生成报告(无数据):", REPORT_FILE)
        return

    # 3) 初始化 LLMLingua 与 FidelityScorer
    print(f"[信息] 初始化 LLMLingua (model={LLMLINGUA_MODEL}, rate={COMPRESS_RATE}) ...")
    llm_lingua = PromptCompressor(model_name=LLMLINGUA_MODEL)
    FidelityScorer = load_fidelity_scorer()

    # 4) 逐条压缩并计算指标
    details = []
    rate_sum = 0.0
    rate_count = 0
    fidelity_values = []

    for idx, item in enumerate(samples, start=1):
        prompt = build_prompt(item)
        if not prompt:
            print(f"[警告] 第 {idx} 条样本为空, 跳过。")
            continue
        try:
            result = llm_lingua.compress_prompt(
                prompt,
                rate=COMPRESS_RATE,
                force_tokens=["\n", " ", ".", ","],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 第 {idx} 条样本压缩失败: {exc}")
            continue

        compressed_prompt = result.get("compressed_prompt", "") or ""
        if isinstance(compressed_prompt, list):  # 兼容列表输出(分段压缩)
            compressed_prompt = "".join(compressed_prompt)

        # 压缩率: compressed_prompt 的 token 数 vs 原始 prompt 的 token 数
        origin_tokens = token_count(result.get("origin_tokens"), prompt)
        compressed_tokens = token_count(result.get("compressed_tokens"), compressed_prompt)
        rate = 1.0 - (compressed_tokens / origin_tokens) if origin_tokens else None

        # 语义保真度: 复用父目录 fidelity.py 的 FidelityScorer
        fidelity = compute_fidelity(FidelityScorer, prompt, compressed_prompt)

        if rate is not None:
            rate_sum += rate
            rate_count += 1
        if fidelity is not None:
            fidelity_values.append(fidelity)

        details.append({
            "index": idx,
            "id": item.get("_id", idx),
            "origin_tokens": origin_tokens,
            "compressed_tokens": compressed_tokens,
            "compression_rate": rate,
            "fidelity": fidelity,
        })
        print(
            f"[进度] 第 {idx} 条: tokens {origin_tokens} -> {compressed_tokens}, "
            f"压缩率 {rate if rate is not None else 'N/A'}, "
            f"保真度 {fidelity if fidelity is not None else 'N/A'}"
        )

    avg_rate = (rate_sum / rate_count) if rate_count else None
    avg_fidelity = (sum(fidelity_values) / len(fidelity_values)) if fidelity_values else None

    # 5) 写报告
    write_report(
        available=True,
        reason="正常完成",
        avg_rate=avg_rate,
        avg_fidelity=avg_fidelity,
        details=details,
        origin_total=sum(d["origin_tokens"] for d in details),
        compressed_total=sum(d["compressed_tokens"] for d in details),
    )
    print("已生成报告:", REPORT_FILE)
    print(
        f"[结果] 平均压缩率={avg_rate if avg_rate is not None else 'N/A'}, "
        f"平均保真度={avg_fidelity if avg_fidelity is not None else 'N/A'}"
    )


if __name__ == "__main__":
    main()
