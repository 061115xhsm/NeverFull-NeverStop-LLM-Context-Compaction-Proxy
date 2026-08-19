# Headroom 触发后公平对比报告

> 触发方式: assistant role + model_limit=1000(绕过 user 消息保护)
> 内容类型: JSON / 代码 / 日志 / 散文(Headroom 的 SmartCrusher/CodeCompressor 目标)
> 同条件: 同文本、同保真度口径(sentence-transformers)

| 内容类型 | 原文字符 | Headroom 压缩率 | Headroom 保真度 | Headroom 延迟 | FF 压缩率 | FF 保真度 | FF 延迟 | Headroom transforms |
|---------|---------|----------------|----------------|-------------|-----------|-----------|---------|---------------------|
| JSON | 12178 | 0.538 | 0.399 | 435ms | 0.500 | 0.516 | 7ms | router:smart_crusher:0.52 |
| 代码 | 11558 | 0.000 | 1.000 | 5ms | 0.500 | 0.636 | 6ms | router:protected:recent_code |
| 日志 | 31801 | 0.000 | 1.000 | 6ms | 0.500 | 0.518 | 14ms | router:protected:recent_code |
| 散文 | 12638 | 0.000 | 1.000 | 35ms | 0.500 | 0.612 | 15ms | router:noop |

> 解读:Headroom 在 JSON/代码/日志上触发 smart_crusher/code_compressor;
> FF-Compactor 统一句子级压缩。比较两者在各自擅长内容上的保真度差异。
