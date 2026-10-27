# 记忆基准评测：LoCoMo（补充，4 条对话）

生成日期：2026-10-27（4 会话完整结果）

## 动机

此前的三方对比基于一条 131 条消息的编码会话，只能说明“编码任务上的证据保留”。本评测用公开记忆基准 **LoCoMo**（snap-research/locomo）检验压缩后的长对话是否还能回答事实性问题，并量化不同压缩策略的证据保留差距。

## 方法

- 数据：`benchmark/data/locomo10.json`，取前 4 条对话（conv-26/30/41/42）；每条按类别分层抽 4 个问题，共 **16 问**。
- 视图（同一对话、同一摘要模型 `tierflow`，失败回退 `tierflow_pro`）：
  - `full`：全部会话原文（含 `[Session N — date]` 头）；
  - `truncate`：保留末尾 35% 字符；
  - `llm`：通用摘要 + 最近一次会话原文；
  - `v11`：状态快照（含每会话日期）+ 折叠 digest + `[EVIDENCE]` 事实表 + 显著原文行 + 分节摘要 + 最近会话原文。
- 指标：压缩率（1 − 视图 token / 原文 token）、证据保留（金标 dia_id 内容的 3-gram 存活率）、问答准确率（本地 LLM reader，精确匹配或 token-F1 ≥ 0.5）。
- Reader 提示要求用会话头解析 “yesterday / last week” 等相对时间。
- **容错**：模型调用失败按错误答案计分，摘要失败跳过该对话/策略（conv-41 的 llm/v11 摘要两次超时被跳过，故这两行 views=3）。

## 结果（4 条对话 × 4 问）

| 策略 | 压缩率 | 证据保留 | 问答准确率 | 摘要耗时/对话 | 摘要 tokens |
|---|---|---|---|---|---|
| full | 0.000 | 0.979* | 0.500 | 0 s | 0 |
| truncate | 0.650 | 0.948 | 0.125 | 0 s | 0 |
| llm（通用摘要） | 0.980 | **0.199** | 0.083 | 11.3 s | 53,414 |
| **v11（折叠 + 事实表 + 分节）** | **0.921** | **0.919** | **0.167** | 16.0 s | 54,557 |

\* full 的 0.979 是 3-gram 指标对短证据行（如单词应答 “OK”）的固有限制，不是压缩损失；相对比较不受影响。

## 解读

1. **通用 LLM 摘要在 0.980 压缩下只保留 0.199 的证据**——约 80% 的金标证据链被摘要销毁。这正是 KEEP-Bench 指出的“摘要看着合理，证据链已断”。
2. **V11 在 0.921 压缩下保留 0.919 的证据**，与 0.650 压缩的 truncate 持平，压缩率高出 27 个百分点。关键不是摘要写得更好，而是 `[EVIDENCE]` 事实表与显著原文行**原样保留**，不依赖模型复述。
3. **问答准确率与证据保留同向**：full 0.500（reader 上限），v11 0.167 vs llm 0.083（2 倍差距）；v11 vs truncate 略高。reader 是本地小模型，LoCoMo 的时间推理/对抗题对它偏难，压缩视图的绝对值不能直接解读为“答错率”。
4. **成本**：llm 与 v11 摘要成本几乎持平（53.4k vs 54.6k tokens，差距来自 v11 的显要行与事实表更长），但换来 4.6 倍的证据保留。

## 局限

- 4 条对话、16 个问题，样本量仍小；结论是方向性的。
- conv-41 的 llm/v11 摘要因端点超时被跳过（resilience 生效），这两行 views=3。
- 3-gram 证据指标偏向原样保留策略（这正是本评测想测的维度），对同义改写不友好。
- reader 为本地小模型，QA 上限 0.5；要得到可信的 QA 排名需要更强 reader。

## 复现

```bash
python benchmark/memory_eval.py \
  --data benchmark/data/locomo10.json \
  --convs 4 --questions 4 --reader llm \
  --json research/data/memory_eval.json \
  --markdown research/EVAL-2026-10-26.md
```

确定性子集（无 LLM，可进 CI）：

```bash
python benchmark/memory_eval.py --reader extractive --questions 3 --strategies full,truncate
```
