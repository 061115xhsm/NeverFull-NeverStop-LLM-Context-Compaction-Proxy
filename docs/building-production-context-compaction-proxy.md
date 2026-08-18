# 从 0 到 1:构建生产级 LLM 上下文压缩代理

> 永不超限,永不停歇 · Never Full, Never Stop
> 作者:LLM Context Compaction Proxy Contributors

---

## 引言:为什么需要上下文压缩

每一个把 Agent 跑进生产环境的开发者,最终都会撞上同一堵墙:**上下文窗口**。

模型上下文窗口是有限的(128K 是常见上限),而对话是无限增长的。当上下文逼近上限时,Agent 会溢出报错、丢失关键信息,或者被迫手动清空历史——之前的所有工作记忆付之东流。

业界已有多种解法:

| 方案 | 原理 | 局限 |
|------|------|------|
| 手动截断 | 丢掉最旧消息 | 关键信息丢失,无保真度控制 |
| 增大窗口 | 换更大上下文模型 | 成本线性增长,仍有物理上限 |
| 上下文压缩 | LLM 摘要旧消息 | 摘要质量不可控,可能丢关键信息 |
| 上下文压缩代理 | 压缩 + 保真度约束 + 记忆系统 | **本项目的方案** |

本文从工程角度,完整讲述如何构建一个**生产级**的上下文压缩代理:透明接入、自动触发、可控保真、记忆持久化、可观测、可部署。

---

## 一、核心架构:透明代理

### 1.1 设计原则

代理必须对 Agent 和 LLM 提供商**透明**——Agent 不需要知道它的存在,LLM 提供商不需要任何改动。这决定了它的架构形态:

```
Agent → Proxy(localhost:8198) → LLM Provider
```

代理监听 `localhost:8198`,兼容 OpenAI / Anthropic / Gemini 三种请求格式。Agent 只需把 API base URL 指向代理,其余照旧。

### 1.2 请求生命周期

```
Agent → Proxy → [context check] → Upstream
                         │
                    if > 80% full:
                         │
                    ┌────▼────┐
                    │ Compact │──▶ Compaction Model (separate provider)
                    │ Engine  │◀── Compressed Summary
                    └────┬────┘
                         │
                    Replace old messages
                    with compressed summary
                         │
                         ▼
                    → Upstream (with compacted context)
```

1. **透传**:上下文低于阈值,请求零开销直达上游;
2. **检测**:上下文达到 80% 阈值,触发压缩;
3. **压缩**:用独立压缩模型对旧消息生成摘要;
4. **恢复**:摘要替换旧消息,Agent 无缝继续。

### 1.3 Provider 抽象层

三种 API 格式的统一是关键难点。核心是一个抽象基类 + 三个实现:

```python
class ProviderAdapter(ABC):          # 抽象基类
    build_compaction_url()           # 构造压缩请求 URL
    build_compaction_headers()       # 构造认证头
    build_compaction_payload()       # 构造请求体
    extract_compaction_content()     # 提取响应内容
    detect_overflow()                # 提供商特定溢出检测
```

自动探测三路信号:模型名(含 claude/Gemini 特征词)、请求头(`anthropic-version`)、上游 URL。探测结果可用环境变量强制覆盖。

**协议转换**:OpenAI→Anthropic 请求转换(字段映射、tool_calls→tool_use)、Anthropic→OpenAI 响应转换、SSE 流式逐事件转换。

---

## 二、压缩引擎:从"粗放摘要"到"可控保真"

### 2.1 压缩主流程(do_compaction)

生产级压缩不是"调一次 LLM 完事",而是一条**17 步流水线**:

1. MemSkill 技能选择(关键词 + Gumbel-Top-K)
2. 抖动检测(压缩循环保护)
3. 预压缩 Hook(可阻断)
4. 自适应 keep_turns
5. 消息拆分(旧消息 / 近期消息)
6. 语义记忆提取(LLM + 正则兜底)
7. ARC 引用替换(长 tool_result → ID)
8. AFM 保真分级
9. CCL 承诺提取
10. 子模选择(预算内选最重要消息)
11. 并行块压缩(≥6 条时)
12. 后压缩 Hook
13. 构建压缩消息
14. 孤儿工具对清理
15. 会话持久化
16. **安全验证**(压缩后必须小于原文,否则截断)
17. **保真度校验**(V8:语义相似度低于底线,降级轻量截断)

### 2.2 安全验证:防膨胀

压缩最怕"越压越大"。安全验证是底线:

```python
def verify_compaction_safety(original, compacted):
    return estimate_tokens(compacted) < estimate_tokens(original)
```

不达标 → 降级激进截断。

### 2.3 语义保真度(V8 核心)

V8 引入**保真度底线**——压缩不只是省 token,还要保证语义不丢:

- `FidelityScorer`:优先 sentence-transformers 嵌入余弦相似度,无依赖时降级 n-gram Jaccard;
- `AdaptiveCompactor`:保真度 < 0.92 时降低压缩强度重试,最多 3 次;
- `QualityBreaker`:连续 3 次低保真 → 熔断暂停压缩。

### 2.4 CJK 感知 Token 估算

中英文 token 密度差异巨大,精确估算是压缩决策的基础:

```
T = N_cjk / 1.5 + N_ascii / 4.0 + N_other / 2.5
```

两阶段:快速启发式预筛 + 精确逐消息估算。高频路径还有 Rust FFI 版本(纯 Python 降级)。

### 2.5 增量压缩(L1/L2 分层)

不每次全量重压缩,而是分层迭代:

- **L2 层**:历史深度摘要,跨多次压缩累积;
- **L1 层**:本轮新增消息,只压缩新增并入 L2;
- **滑动窗口**:窗口内保留原文,窗口外分级压缩。

### 2.6 可逆压缩

有损压缩 + 无损还原:长内容替换为 `[REF:id]` 引用,原文存入 store,需要时按 ID 还原——兼顾 token 节省与信息完整性。

---

## 三、记忆系统:从"会话存档"到"主动知识引擎"

### 3.1 三层记忆架构

借鉴人类记忆规律:

| 层 | 内容 | 生命周期 |
|----|------|---------|
| 工作记忆 | 最近 N 轮原文 | 会话内 |
| 短期记忆 | 本轮压缩摘要 | 会话内 |
| 长期记忆 | 跨会话知识/偏好 | 持久化 |

### 3.2 知识图谱 + 混合召回

从对话中抽取实体/关系/属性,构建图;召回时组合关键词匹配 + 图关联推理(BFS)+ 文本回退:

```
query → 关键词命中实体 → 图邻居扩展 → 文本重叠回退 → 排序 top-k
```

### 3.3 记忆衰减与遗忘

```
weight = importance × (1 - decay_rate)^(hours/24)
```

低权重记忆自动降级/遗忘;核心决策、用户偏好标记 permanent 永不忘。

### 3.4 主动检索 + 动态注入

基于当前 query 主动检索相关记忆,按上下文剩余空间动态调整注入量——空间足多注入,紧张只留最高相关。

---

## 四、可靠性:面向生产的容错设计

### 4.1 熔断器(三状态)

```
CLOSED --3次失败--> OPEN --冷却60s--> HALF_OPEN --成功--> CLOSED
```

### 4.2 抖动检测

窗口内压缩次数达阈值 → 判定蠕变,切换激进截断,避免"压缩→重填→再压缩"死循环。

### 4.3 多级降级

```
压缩失败 → 轻量压缩 → 智能截断 → 纯透传
```

逐级降级,业务永不中断。

### 4.4 多上游容灾

主上游故障自动切换备用,冷却期后恢复。

### 4.5 压缩缓存

30 分钟 TTL + 指令感知 salt 键(不同指令不串缓存);多级:内存 LRU → SQLite → 上游 cache_control 断点。

### 4.6 预测式异步预压缩

压力达到预测线(75%)时后台提前压缩,用户请求时无感知,彻底消除压缩阻塞。

---

## 五、安全:生产环境的底线

- **PII 脱敏**:API Key、身份证、手机号、邮箱、自定义敏感词(正则 + LLM 双识别);
- **落盘加密**:Fernet 优先,降级内置加密;支持纯内存模式(数据不落地);
- **多租户权限**:多 API key 隔离、读写/管理分级、每日 token 限额;
- **端点认证**:22+ 端点受保护,无密钥时仅回环放行;
- **Gemini Key**:走 `x-goog-api-key` header,绝不进 URL/日志;
- **并发安全**:语义记忆线程锁、无竞态提示词(参数传递而非全局变量)。

---

## 六、可观测与评测:用数据说话

### 6.1 基准评测

内置 benchmark CLI:对通用对话/工具调用/代码对话三类样例,对比 baseline(截断)/ summary(摘要)/ adaptive(保真度约束)三种策略,输出压缩率、信息保留率、语义保真度三项指标。

#### 6.1.1 官方 LongBench 实测(权威验证)

在 **THUDM/LongBench 官方数据集**(`multifieldqa_zh`,真实 6K-15K 字符长文档)上验证:

| 策略 | 平均压缩率 | 平均语义保真度 |
|------|-----------|---------------|
| baseline(纯截断) | 0.499 | 1.000 |
| summary(粗暴摘要) | 0.982 | 0.796 |
| **adaptive(保真约束)** | **0.710** | **0.993** |

**实测结论**:在官方真实长文档上,adaptive 实现 **71% 压缩率 + 99.3% 语义保真度**——压缩率接近粗暴摘要(0.982),但保真度比其高 20 个百分点(0.993 vs 0.796),是唯一兼具"高压缩 + 近似无损"的策略。这印证了保真度底线设计的核心价值:**压缩不是越狠越好,信息完整才是底线**。

完整评测体系:`benchmark/`(三策略对比 CLI、LongBench 适配器、Q&A 准确率评测、36 组并发参数搜索、官方数据集适配)。

### 6.2 指标与追踪

20+ Prometheus 指标(压缩次数/成功率/缓存命中/上游错误/低保真计数)+ OpenTelemetry 风格追踪(可选 otel,降级本地计时)。

---

## 七、可插拔架构:15 个模块

项目演进为模块化网关——核心 + 可选增强模块,全部 `try/except` 加载,缺失任一模块不影响核心:

| 模块 | 职责 |
|------|------|
| `fidelity.py` | 保真度评分、自适应压缩、质量熔断 |
| `memory_engine.py` | 三层记忆 + 衰减遗忘 |
| `knowledge_graph.py` | 知识图谱 + 混合召回 |
| `compression_advanced.py` | AST 压缩、差分、可逆压缩 |
| `incremental_compaction.py` | L1/L2 分层增量压缩 |
| `model_hub.py` | 可插拔摘要模型 |
| `security_enhanced.py` | PII 脱敏、加密、多租户 |
| `protocol_extra.py` | 协议适配 |
| `cache_engine.py` | 多级缓存 |
| `async_precompressor.py` | 异步预压缩 |
| `observability.py` | 容灾、降级、指标 |
| `storage_backend.py` | SQLite/PostgreSQL/Redis |
| `rust_ffi.py` | Rust FFI 加速 |
| `benchmark/` | 基准评测 |

---

## 八、Agent 接入:四大框架实测

| Agent | 接入方式 | 原生压缩 |
|-------|---------|---------|
| **OpenClaw** | provider baseUrl 指向代理 + v6-compaction-provider 插件 | 关闭(compaction.enabled=false) |
| **Claude Code** | ANTHROPIC_BASE_URL 指向代理 | 关闭(autoCompactEnabled=false) |
| **Hermes** | model.base_url 指向代理 | 关闭(compression.enabled=false) |
| **DeepSeek Harness** | settings.yaml baseURL 指向代理 | 关闭(dsh-compaction-basic auto=false) |

原则:**单一压缩源**——只有代理压缩,接入方自身不压缩,避免双重压缩与重复刷记忆。

---

## 九、部署

- Dockerfile + docker-compose(healthcheck + 持久化卷 + 日志轮转);
- Helm values(K8s 多实例,配合共享存储);
- systemd 用户服务。

---

## 结语:从 0 到 1 的启示

构建这个代理的过程,本质上是在回答一个问题:**"压缩"到底在优化什么?**

早期的答案是"token 数"——压缩率越高越好。但生产环境给出了更真实的答案:**信息完整性**。压缩不是数学题,是决策题——决定哪些信息值得保留,哪些可以舍弃。

于是有了保真度底线、增量分层、可逆压缩、记忆衰减、质量熔断……每一层都是在"省 token"和"保信息"之间寻找平衡。

技术会过时,但这个问题不会。希望这篇文章能帮你在自己的 Agent 项目里,找到同样的平衡。

---

*项目地址:https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy*
*MIT License*
