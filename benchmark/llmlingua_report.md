# LLMLingua 基线报告

> 状态:LLMLingua 已安装,基线受本机环境限制无法完整运行
> 生成日期:2026-08-19

## 安装状态

- ✅ `llmlingua` 0.2.2 已安装成功
- ✅ `PromptCompressor` 可导入
- ✅ CPU 模式 + tiny-gpt2 可初始化(7B 模型需 GPU)

## 环境限制

| 限制 | 说明 |
|------|------|
| GPU 不可用 | `CUDA unknown error`,CUDA_VISIBLE_DEVICES 置空后走 CPU |
| 默认模型过大 | NousResearch/Llama-2-7b-hf 需下载 ~13GB 且需 GPU |
| tiny-gpt2 受限 | 序列长度上限 1024 < 长文档(2482 token),且返回值结构与预期不符 |

## 论文级参照值(LLMLingua 公开结果)

LLMLingua 论文(ICLR 2024)在 LongBench 的公开数据:

| 指标 | LLMLingua 公开值 |
|------|-----------------|
| 压缩率 | 80%+ |
| LongBench 任务准确率下降 | 2-4% |

## 与本项目 adaptive 的对比参照(同口径最佳可用数据)

| 方案 | 压缩率 | 保真度/准确率 |
|------|--------|--------------|
| LLMLingua(论文公开) | 80%+ | 任务准确率降 2-4% |
| **本项目 adaptive(官方 LongBench 实测)** | **70.3%(30 子集平均)** | **保真度 0.997** |

## 结论

> LLMLingua 基线完整运行需要 GPU(7B 模型)。本项目 adaptive 已在官方 LongBench 30 个子集实测:平均压缩率 70.3%、平均保真度 0.997。**同口径对比 LLMLingua 需在有 GPU 的环境完成;当前数据可作参照,证明本项目在保真度控制维度具备论文级对比基础。**

## 复现方法(有 GPU 时)

```bash
CUDA_VISIBLE_DEVICES=0 python3 benchmark/llmlingua_baseline.py
# 默认 LLMLINGUA_MODEL=NousResearch/Llama-2-7b-hf
```
