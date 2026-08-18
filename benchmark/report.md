# 压缩基准评测报告

| 样例 | 策略 | 压缩率 | 信息保留率 | 语义保真度 | 说明 |
|------|------|--------|-----------|-----------|------|
| 通用对话 | baseline | 0.0 | 1.0 | 1.0 |  |
| 通用对话 | summary | 0.0 | 1.0 | 1.0 |  |
| 通用对话 | adaptive | 0.0 | 1.0 | 1.0 | attempts=1, met_floor=True |
| 工具调用 | baseline | 0.0 | 1.0 | 1.0 |  |
| 工具调用 | summary | 0.0 | 1.0 | 1.0 |  |
| 工具调用 | adaptive | 0.0 | 1.0 | 1.0 | attempts=1, met_floor=True |
| 代码对话 | baseline | 0.0 | 0.667 | 1.0 |  |
| 代码对话 | summary | 0.261 | 0.667 | 0.978 |  |
| 代码对话 | adaptive | 0.0 | 0.667 | 1.0 | attempts=1, met_floor=True |

> 说明:compression_ratio 越高压缩越狠;retention 越高关键信息保留越全;
> fidelity 越高压缩前后语义越接近(1.0 最佳)。
> 完整接入 LongBench/BFCL/SWE-bench 见 longbench_adapter.py。
