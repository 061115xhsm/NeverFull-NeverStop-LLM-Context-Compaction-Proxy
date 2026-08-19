# LLMLingua 压缩基线报告

- **生成时间**: 2026-08-19 19:09:00
- **llmlingua 可用**: 是
- **状态/原因**: 正常完成
- **数据集**: `/media/qq/文档/llm-compaction-proxy-data/longbench/data/multifieldqa_zh.jsonl`(前 5 条)
- **压缩参数**: rate=0.5, model=/media/qq/文档/llm-compaction-proxy-data/llama2-7b-local

## 总体指标

- **平均压缩率**: 0.6891 (68.91%)
- **平均保真度**: 0.8078
- **原始 token 总数**: 35551
- **压缩后 token 总数**: 10193
- **整体压缩率**: 0.7133 (71.33%)

## 逐条明细

| 序号 | ID | 原始tokens | 压缩后tokens | 压缩率 | 保真度 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | 5b1b8e937b83c3ff9b75ac386fae9c4575c4b9f26a4fbdad | 11539 | 3002 | 0.7398 | 0.8484 |
| 2 | 94ac1d26cf68a448f5bddf4b5400eab51717c26ab3127df0 | 7468 | 1945 | 0.7396 | 0.7743 |
| 3 | 681a3146fff714cfdc68c4a9ca0b6663d104d73e75facee1 | 3498 | 1447 | 0.5863 | 0.8224 |
| 4 | 25256db5d953fa971b88f06502dfecacbc5532aea7fb6d91 | 8142 | 1902 | 0.7664 | 0.6886 |
| 5 | 1a0baf25d7431f32becd0a34034a2b88927fa168bcccf698 | 4904 | 1897 | 0.6132 | 0.9054 |

---
*由 benchmark/llmlingua_baseline.py 自动生成。*
