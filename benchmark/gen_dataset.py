"""
生成 30+ 条多样化 LongBench 格式评测数据(benchmark/gen_dataset.py)
====================================================================
覆盖 LongBench 6 大类别:单文档问答 / 多文档问答 / 摘要 /
少样本学习 / 代码补全 / 合成任务。

用法: python3 benchmark/gen_dataset.py
输出: benchmark/data/longbench_full.jsonl(30 条)
"""

from __future__ import annotations

import json
import os
import random

_CUR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_CUR, "data", "longbench_full.jsonl")

random.seed(42)


# ── 各类别模板 ──────────────────────────────────────────────────────

def tpl_single_doc_qa(i: int) -> dict:
    topics = [
        ("上下文压缩代理的预压缩阈值", "80%"),
        ("PACMS 子模选择的重要性评分权重", "语义50% 近因25% 类型15% 质量10%"),
        ("ARC 地址化引用的触发阈值", "800字符"),
        ("QualityBreaker 的熔断条件", "连续3次低保真"),
        ("三层记忆架构的层次", "工作/短期/长期"),
        ("Rust FFI 加速的函数", "CJK token 估算"),
    ]
    t, ans = topics[i % len(topics)]
    return {
        "input": f"{t}是文档的核心话题。本文详细介绍了{t}的相关原理与实现:根据规范,{t}的取值/设定为 {ans}。在工程实践中,该机制通过多层校验与降级保护确保稳定性,该{t}相关方案已被生产环境采用。",
        "answers": [ans, f"答案:{ans}"],
        "all_classes": ["80%", "连续3次低保真", "800字符", ans],
        "query": f"请回答:{t}是什么?",
    }


def tpl_multi_doc_qa(i: int) -> dict:
    pairs = [
        ("文档A 提到系统使用 SQLite 存储", "文档B 提到生产环境推荐 PostgreSQL"),
        ("文档A 描述工作记忆保留最近 N 轮", "文档B 描述长期记忆跨会话持久化"),
        ("文档A 说明熔断器冷却 60 秒", "文档B 说明冷却后进入半开状态"),
        ("文档A 说压缩缓存 TTL 30 分钟", "文档B 说缓存键含指令 salt"),
        ("文档A 提出语义保真度底线 0.92", "文档B 提出 0.90 更平衡"),
        ("文档A 说增量压缩只压新增", "文档B 说 L1/L2 分层迭代"),
    ]
    a, b = pairs[i % len(pairs)]
    return {
        "input": f"{a};{b}。需要综合两份文档的信息才能得出完整结论。",
        "answers": [f"{a} 且 {b}", "综合两者"],
        "all_classes": [a, b, f"{a} 且 {b}"],
        "query": "请综合两份文档回答:两文档共同描述了什么?",
    }


def tpl_summary(i: int) -> dict:
    themes = [
        "项目采用模块化网关架构,15 个模块各司其职,缺失不影响核心。",
        "压缩引擎支持语义保真度校验,低于底线自动降级,防止信息丢失。",
        "记忆系统借鉴人类记忆规律,三层架构配合衰减遗忘机制。",
        "安全体系覆盖 PII 脱敏、落盘加密与多租户权限分级。",
        "可观测性提供 20+ 指标与 OpenTelemetry 风格追踪。",
        "部署支持 Docker Compose 与 Kubernetes Helm Chart。",
    ]
    body = themes[i % len(themes)] * 3
    return {
        "input": body,
        "answers": [themes[i % len(themes)][:30]],
        "all_classes": [themes[i % len(themes)][:10]],
        "query": "请用一句话概括这段文本的主旨。",
    }


def tpl_fewshot(i: int) -> dict:
    patterns = [
        ("输入:苹果->水果;输出:类别", "输入:西红柿->;", "蔬菜"),
        ("输入:1,2,3,5,8->斐波那契;输出:13", "输入:21,34,55,89->;", "144"),
        ("输入:HTTP 404->未找到;输出:状态码含义", "输入:HTTP 503->;", "服务不可用"),
        ("输入:cat->动物;输出:dog", "输入:rose->;", "植物"),
        ("输入:10*10->100;输出:平方", "输入:12*12->;", "144"),
        ("输入:北京->首都;输出:上海", "输入:南京->;", "江苏"),
    ]
    ex, q, ans = patterns[i % len(patterns)]
    return {
        "input": f"{ex}\n{q}",
        "answers": [ans],
        "all_classes": [ans, "未知"],
        "query": f"按示例规律补全:{q}",
    }


def tpl_code(i: int) -> dict:
    funcs = [
        ("def estimate_tokens(text):", "return max(1, len(text)//4)", "输入字符串返回估算 token 数"),
        ("def redact_secrets(text):", "return text.replace('sk-', '<KEY>')", "脱敏密钥"),
        ("def should_compact(pressure):", "return pressure >= 0.8", "判断是否触发压缩"),
        ("def cache_key(messages):", "return sha256(messages)", "生成缓存键"),
        ("def degrade(current):", "return 'passthrough' if current=='truncate' else 'truncate'", "降级"),
        ("def recall(query, k=5):", "return sorted(items, key=score)[:k]", "检索记忆"),
    ]
    sig, body, doc = funcs[i % len(funcs)]
    return {
        "input": f"以下是函数的补全示例:{sig} 函数功能:{doc} 实现为:{body} 该实现已被验证。",
        "answers": [body],
        "all_classes": [body, "pass"],
        "query": f"补全函数 {sig} 的返回逻辑",
    }


def tpl_synthetic(i: int) -> dict:
    ops = [
        ("列表[3,1,4,1,5]中最大值是?", "5"),
        ("字符串'abc'重复3次的结果是?", "abcabcabc"),
        ("布尔 True 与 False 的与运算结果是?", "False"),
        ("10 与 3 的整除结果是?", "3"),
        ("'hello' 反转后是?", "olleh"),
        ("集合{1,2,3}与{2,3,4}的交集是?", "2,3"),
    ]
    q, ans = ops[i % len(ops)]
    return {
        "input": f"这是合成推理任务。{q} 需要严格按规则推导。",
        "answers": [ans],
        "all_classes": [ans, "无法确定"],
        "query": q,
    }


def main() -> None:
    generators = [
        tpl_single_doc_qa, tpl_multi_doc_qa, tpl_summary,
        tpl_fewshot, tpl_code, tpl_synthetic,
    ]
    rows = []
    # 每类 5 条 → 6 类 × 5 = 30 条
    for gi, gen in enumerate(generators):
        for i in range(5):
            item = gen(gi * 5 + i)
            item["category"] = ["single-doc", "multi-doc", "summary",
                                "fewshot", "code", "synthetic"][gi]
            rows.append(item)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✅ 生成 {len(rows)} 条评测数据 → {OUT}")
    # 统计类别分布
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    for c, n in cats.items():
        print(f"  {c}: {n} 条")


if __name__ == "__main__":
    main()
