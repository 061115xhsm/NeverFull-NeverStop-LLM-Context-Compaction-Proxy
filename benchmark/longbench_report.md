# LongBench 压缩对比报告

> 数据源:LongBench 格式评测数据(benchmark/data/longbench_sample.jsonl)
> 完整 LongBench 可从 https://huggingface.co/datasets/THUDM/LongBench 获取

| 样例 | 输入字符 | 策略 | 压缩率 | 信息保留率 | 语义保真度 |
|------|---------|------|--------|-----------|-----------|
| LB#1: 整本小说片段属于哪个类别 | 333 | baseline | 0.493 | 1.0 | 0.941 |
| LB#1: 整本小说片段属于哪个类别 | 333 | summary | 0.788 | 0.0 | 0.904 |
| LB#1: 整本小说片段属于哪个类别 | 333 | adaptive | 0.09 | 1.0 | 0.971 |
| LB#2: 触发预压缩的条件 | 337 | baseline | 0.493 | 0.0 | 0.955 |
| LB#2: 触发预压缩的条件 | 337 | summary | 0.8 | 0.0 | 0.86 |
| LB#2: 触发预压缩的条件 | 337 | adaptive | 0.09 | 0.0 | 0.995 |
| LB#3: 多上游容灾模块 | 409 | baseline | 0.493 | 0.0 | 0.922 |
| LB#3: 多上游容灾模块 | 409 | summary | 0.837 | 0.0 | 0.868 |
| LB#3: 多上游容灾模块 | 409 | adaptive | 0.091 | 1.0 | 0.981 |

> 说明:compression_ratio 越高压缩越狠;retention 越高关键信息保留越全;
> fidelity 为语义保真度(sentence-transformers 真实嵌入,1.0 最佳)。
