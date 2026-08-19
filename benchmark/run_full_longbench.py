#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量 LongBench 基准测试脚本(压缩率 + 语义保真度)

功能:
1. 加载 /media/qq/文档/llm-compaction-proxy-data/longbench/data/ 下的多个 LongBench 子集
   (multifieldqa_zh / multifieldqa_en / narrativeqa / hotpotqa / 2wikimqa / musique /
    qasper / gov_report / qmsum / multi_news / samsum / lcc / repobench-p / trec /
    passage_count 等),每个子集只取前 10 条以控制耗时。
2. 对每条样本,使用 fidelity.AdaptiveCompactor(min_fidelity=0.90, max_attempts=4,
   min_content_len=30) 对长文档(官方格式的 context)进行压缩。
3. 使用 fidelity.FidelityScorer 计算原始文本与压缩后文本之间的语义保真度。
4. 统计每个子集的平均压缩率与平均语义保真度,汇总输出 markdown 到
   benchmark/full_longbench_report.md。

用法:
    python benchmark/run_full_longbench.py
"""

import sys
import json
import time
import datetime
from pathlib import Path

# 将项目根目录(本文件所在目录的上一级)加入 sys.path,以便导入 fidelity / longbench_adapter
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入项目内的两个模块:压缩器 + 保真度打分器、LongBench 官方数据格式适配器
try:
    import fidelity                 # 提供 AdaptiveCompactor 与 FidelityScorer
    import longbench_adapter        # 适配官方格式: input=问题 / context=长文档
except Exception as exc:
    # 导入失败时输出友好提示,并生成一份说明性报告后退出
    print(f"[错误] 导入项目模块失败: {exc}")
    print("请确认项目根目录下存在 fidelity.py 与 longbench_adapter.py,"
          "且已通过 sys.path 将项目根目录加入搜索路径。")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
# LongBench 官方数据目录(原始 JSON 文件按子集名存放)
DATA_DIR = Path("/media/qq/文档/llm-compaction-proxy-data/longbench/data")

# 重点关注的主要子集(目录下其他 .json 子集也会被自动纳入,见 collect_dataset_files)
PRIMARY_DATASETS = [
    "multifieldqa_zh",
    "multifieldqa_en",
    "narrativeqa",
    "hotpotqa",
    "2wikimqa",
    "musique",
    "qasper",
    "gov_report",
    "qmsum",
    "multi_news",
    "samsum",
    "lcc",
    "repobench-p",
    "trec",
    "passage_count",
]

MAX_ITEMS = 10          # 每个子集最多取前 N 条(控制耗时)
MIN_FIDELITY = 0.90     # AdaptiveCompactor 的最低保真度阈值
MAX_ATTEMPTS = 4        # 压缩最多尝试次数
MIN_CONTENT_LEN = 30    # 内容最小长度阈值(低于该长度不再压缩)

# 汇总报告输出路径(与脚本同目录)
REPORT_PATH = Path(__file__).resolve().parent / "full_longbench_report.md"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def write_text(path, content):
    """写入文本文件,确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def collect_dataset_files():
    """收集所有 LongBench 子集数据文件(.json)。"""
    files = []
    seen = set()
    if not DATA_DIR.exists():
        print(f"[警告] 数据目录不存在: {DATA_DIR}")
        return files
    # 1) 先按 PRIMARY_DATASETS 的顺序排列主要子集
    for name in PRIMARY_DATASETS:
        p = DATA_DIR / f"{name}.json"
        if p.exists():
            files.append(p)
            seen.add(p.name)
    # 2) 再纳入目录下其他未列出的 .jsonl 子集(满足“等”的扩展需求)
    for p in sorted(DATA_DIR.glob("*.jsonl")):
        if p.name not in seen:
            files.append(p)
    return files


def adapt_sample(sample):
    """
    把官方 LongBench 样本适配为 (question, context)。

    官方格式: input=问题, context=长文档。
    优先使用 longbench_adapter 提供的适配函数,失败时回退到官方字段直接解析。
    """
    # 1) 尝试 longbench_adapter 的各种常见适配函数
    for fn_name in ("adapt", "adapt_sample", "to_compactor_input", "format_input"):
        fn = getattr(longbench_adapter, fn_name, None)
        if fn is None:
            continue
        try:
            adapted = fn(sample)
            # 返回形式可能为字典 {input, context} 或 (question, context) 元组
            if isinstance(adapted, dict):
                question = str(adapted.get("input", adapted.get("question", "")))
                context = str(adapted.get("context", adapted.get("document", "")))
                return question, context
            if isinstance(adapted, (tuple, list)) and len(adapted) >= 2:
                return str(adapted[0]), str(adapted[1])
        except Exception as exc:
            print(f"    [调试] longbench_adapter.{fn_name} 调用失败: {exc}")
    # 2) 回退:直接解析官方字段 input / context
    question = str(sample.get("input", ""))
    context = str(sample.get("context", ""))
    return question, context


def get_fidelity_scorer():
    """获取 FidelityScorer 实例,兼容不同的导入路径。"""
    # 1) fidelity.FidelityScorer 直接作为属性
    scorer_cls = getattr(fidelity, "FidelityScorer", None)
    if scorer_cls is not None:
        try:
            return scorer_cls()
        except Exception as exc:
            print(f"    [调试] 实例化 fidelity.FidelityScorer 失败: {exc}")
    # 2) 常见的子模块导入路径
    for mod_path in ("fidelity.fidelity", "fidelity.scorers", "fidelity.scorer"):
        try:
            mod = __import__(mod_path, fromlist=["FidelityScorer"])
            cls = getattr(mod, "FidelityScorer", None)
            if cls is not None:
                return cls()
        except Exception:
            continue
    return None


def compress_text(compactor, text):
    """
    压缩文本,兼容压缩器返回 str 或结果对象两种形式。

    本项目 AdaptiveCompactor.compact(messages, budget) 返回
    {"messages": [...], "fidelity": ...};也兼容返回 str 的用法。
    """
    # 1) 本项目 API:compact(messages, budget) -> dict with 'messages'
    try:
        msgs = [{"role": "user", "content": text}]
        budget = max(100, len(text) // 2)
        result = compactor.compact(msgs, budget)
        if isinstance(result, dict):
            comp_msgs = result.get("messages")
            if isinstance(comp_msgs, list) and comp_msgs:
                joined = " ".join(str(m.get("content", "")) for m in comp_msgs)
                if joined:
                    return joined
            # 返回对象时尝试常见的属性名
            for attr in ("text", "compressed", "result", "content"):
                val = result.get(attr)
                if isinstance(val, str) and val:
                    return val
    except (TypeError, AttributeError):
        pass
    # 2) 兼容直接 compress(text) -> str
    try:
        result = compactor.compress(text)
        if isinstance(result, str):
            return result
        for attr in ("text", "compressed", "result", "content"):
            val = getattr(result, attr, None)
            if isinstance(val, str) and val:
                return val
    except (TypeError, AttributeError):
        pass
    # 3) 兜底:直接字符串化
    return str(result)


def compute_fidelity(scorer, original, compressed):
    """计算原始文本与压缩文本之间的语义保真度,失败时返回 None。"""
    if not scorer:
        return None
    # 常见接口:score(original, compressed)
    try:
        return float(scorer.score(original, compressed))
    except Exception:
        pass
    # 兼容其他命名的接口
    for fn_name in ("fidelity", "evaluate", "compute"):
        fn = getattr(scorer, fn_name, None)
        if fn is None:
            continue
        try:
            return float(fn(original, compressed))
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def write_report(rows, all_ratios, start_time, all_scores=None,
                 total_samples=None, data_error=None):
    """生成 markdown 汇总报告并写入 benchmark/full_longbench_report.md。"""
    lines = []
    lines.append("# Full LongBench 压缩基准报告")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 耗时: {time.time() - start_time:.1f} 秒")
    lines.append(f"- 压缩器: `fidelity.AdaptiveCompactor(min_fidelity={MIN_FIDELITY}, "
                 f"max_attempts={MAX_ATTEMPTS}, min_content_len={MIN_CONTENT_LEN})`")
    lines.append(f"- 数据目录: `{DATA_DIR}`")
    lines.append(f"- 每个子集取前 {MAX_ITEMS} 条(控制耗时)")
    lines.append("- 压缩率 = 1 - 压缩后字符数 / 原始字符数(越大表示压缩越多)")
    lines.append("- 保真度: 由 `fidelity.FidelityScorer` 计算原始文本与压缩文本的语义相似度(0~1)")

    # 数据不可用的情况:只输出错误说明
    if data_error:
        lines.append("")
        lines.append(f"> ⚠️ **数据加载失败**: {data_error}")
        lines.append("")
        lines.append("未生成任何样本统计。")
        write_text(REPORT_PATH, "\n".join(lines))
        print(f"\n[报告] 已写入 {REPORT_PATH}(数据不可用)")
        return

    # 各子集统计表
    lines.append("")
    lines.append("## 各子集统计")
    lines.append("")
    lines.append("| 子集 | 样本数 | 平均原始长度(字符) | 平均压缩后长度(字符) | 平均压缩率 | 平均保真度 |")
    lines.append("|------|-------:|-------------------:|---------------------:|-----------:|-----------:|")
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['name']} | - | - | - | - | 加载失败: {row['error']} |")
        else:
            fid = f"{row['avg_fidelity']:.3f}" if row["avg_fidelity"] is not None else "N/A"
            lines.append(
                f"| {row['name']} | {row['samples']} | {row['avg_orig_len']:.0f} | "
                f"{row['avg_comp_len']:.0f} | {row['avg_ratio']:.1%} | {fid} |"
            )

    # 总体统计
    lines.append("")
    lines.append("## 总体统计")
    lines.append("")
    n_total = total_samples if total_samples is not None else sum(
        r.get("samples", 0) for r in rows)
    avg_ratio_all = sum(all_ratios) / len(all_ratios) if all_ratios else 0.0
    avg_fid_all = sum(all_scores) / len(all_scores) if all_scores else None
    lines.append(f"- 总样本数: {n_total}")
    lines.append(f"- 平均压缩率: {avg_ratio_all:.1%}")
    if avg_fid_all is not None:
        lines.append(f"- 平均保真度: {avg_fid_all:.3f}")
    else:
        lines.append("- 平均保真度: N/A")
    lines.append(f"- 保真度有效样本数: {len(all_scores) if all_scores else 0}")

    write_text(REPORT_PATH, "\n".join(lines))
    print(f"\n[报告] 已写入 {REPORT_PATH}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Full LongBench 压缩基准测试")
    print(f"数据目录: {DATA_DIR}")
    print(f"压缩器: AdaptiveCompactor(min_fidelity={MIN_FIDELITY}, "
          f"max_attempts={MAX_ATTEMPTS}, min_content_len={MIN_CONTENT_LEN})")
    print(f"每个子集取前 {MAX_ITEMS} 条")
    print("=" * 70)

    start_time = time.time()

    # 初始化压缩器(兼容构造函数参数差异)
    try:
        compactor = fidelity.AdaptiveCompactor(
            min_fidelity=MIN_FIDELITY,
            max_attempts=MAX_ATTEMPTS,
            min_content_len=MIN_CONTENT_LEN,
        )
    except TypeError:
        compactor = fidelity.AdaptiveCompactor()

    # 初始化语义保真度打分器
    scorer = get_fidelity_scorer()
    if scorer is None:
        print("[警告] 未找到 FidelityScorer,保真度将显示为 N/A")

    # 收集所有子集数据文件
    dataset_files = collect_dataset_files()
    if not dataset_files:
        write_report([], [], start_time,
                     data_error=f"数据目录不存在或为空: {DATA_DIR}")
        return

    rows = []          # 每个子集的统计信息
    all_ratios = []    # 全部样本的压缩率(用于总体统计)
    all_scores = []    # 全部样本的保真度(用于总体统计)
    total_samples = 0  # 总样本数

    for file_path in dataset_files:
        dataset_name = file_path.stem
        print(f"\n[子集] {dataset_name} ({file_path.name})")

        # 加载该子集的官方数据(JSONL:每行一个 JSON 对象)
        try:
            samples = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        samples.append(json.loads(line))
        except Exception as exc:
            print(f"  [错误] 加载失败: {exc}")
            rows.append({"name": dataset_name, "error": str(exc)})
            continue

        # 每个子集只取前 MAX_ITEMS 条,控制总耗时
        samples = samples[:MAX_ITEMS]

        ratios = []    # 本子集压缩率列表
        scores = []    # 本子集保真度列表
        orig_lens = [] # 本子集原始长度列表
        comp_lens = [] # 本子集压缩后长度列表

        for idx, sample in enumerate(samples, 1):
            try:
                # 1) 适配官方格式: input=问题, context=长文档
                question, context = adapt_sample(sample)
                if not context:
                    print(f"  [跳过] 第 {idx} 条: context 为空")
                    continue

                # 2) 使用 AdaptiveCompactor 压缩长文档
                compressed = compress_text(compactor, context)

                # 3) 使用 FidelityScorer 计算语义保真度
                score = compute_fidelity(scorer, context, compressed)

                # 4) 计算压缩率: 1 - 压缩后字符数 / 原始字符数
                olen = len(context)
                clen = len(compressed)
                ratio = 1.0 - (clen / olen if olen else 0.0)

                orig_lens.append(olen)
                comp_lens.append(clen)
                ratios.append(ratio)
                if score is not None:
                    scores.append(score)

                print(f"  [第{idx}条] 原始={olen}字符 -> 压缩后={clen}字符, "
                      f"压缩率={ratio:.1%}, "
                      f"保真度={score if score is not None else 'N/A'}")
            except Exception as exc:
                print(f"  [错误] 第 {idx} 条处理失败: {exc}")

        # 汇总该子集的平均压缩率与平均保真度
        n = len(ratios)
        total_samples += n
        avg_ratio = sum(ratios) / n if n else 0.0
        avg_score = sum(scores) / len(scores) if scores else None
        avg_orig = sum(orig_lens) / len(orig_lens) if orig_lens else 0.0
        avg_comp = sum(comp_lens) / len(comp_lens) if comp_lens else 0.0

        rows.append({
            "name": dataset_name,
            "samples": n,
            "avg_orig_len": avg_orig,
            "avg_comp_len": avg_comp,
            "avg_ratio": avg_ratio,
            "avg_fidelity": avg_score,
        })
        all_ratios.extend(ratios)
        if scores:
            all_scores.extend(scores)

        print(f"  [汇总] {dataset_name}: 样本={n}, 平均压缩率={avg_ratio:.1%}, "
              f"平均保真度={avg_score if avg_score is not None else 'N/A'}")

    # 生成 markdown 汇总报告
    write_report(rows, all_ratios, start_time,
                 all_scores=all_scores, total_samples=total_samples)


if __name__ == "__main__":
    main()
