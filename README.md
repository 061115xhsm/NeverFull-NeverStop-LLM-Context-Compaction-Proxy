# LLM Context Compaction Proxy

A transparent, zero-config context compaction proxy for LLM APIs. Sit it between your AI agent and any LLM provider — it automatically compresses conversation context when it approaches the model's token limit, keeping your agents running indefinitely without hitting context window walls.

**40 compression techniques** across 7 generations, from academic research (ARC, AFM, PACMS, CoMem, MemSkill) to production patterns (Cursor, Devin, SWE-agent).

## How It Works

```
┌─────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  AI Agent   │────▶│  Compaction Proxy    │────▶│  LLM Provider│
│ (Claude/GPT)│◀────│  localhost:8198      │◀────│  (any API)   │
└─────────────┘     │                      │     └──────────────┘
                    │  • Monitor context % │
                    │  • Auto-compress     │
                    │  • Preserve key info │
                    │  • Route to provider │
                    └──────────────────────┘
```

1. **Passthrough** — Requests under the context limit go straight through, zero overhead
2. **Detect** — When context reaches the threshold (default 80%), compaction triggers
3. **Compress** — Older messages are summarized using a separate compaction model
4. **Resume** — The compressed context replaces old messages; the agent continues seamlessly

## Quick Start

### Prerequisites

- Python 3.10+
- `aiohttp` (`pip install aiohttp`)

### Install & Run

```bash
# Clone
git clone https://github.com/your-org/llm-compaction-proxy.git
cd llm-compaction-proxy

# Set your upstream LLM API
export COMPACTION_PROXY_UPSTREAM=https://api.openai.com/v1
export COMPACTION_PROXY_MODEL=gpt-4o-mini

# Run
python3 compaction-proxy.py
```

The proxy listens on `localhost:8198`. Point your agent's API endpoint there.

### With Environment File

```bash
cp .env.example .env
# Edit .env with your settings
source .env
python3 compaction-proxy.py
```

### As a systemd Service

```bash
cp systemd/compaction-proxy.service ~/.config/systemd/user/
# Edit the service file to set your paths and environment
systemctl --user daemon-reload
systemctl --user enable --now compaction-proxy.service
```

## Connecting AI Agents

### Claude Code (Anthropic)

```bash
# Set the API base to the proxy
export ANTHROPIC_BASE_URL=http://localhost:8198
claude
```

Or in Claude Code settings:
```json
{
  "apiBaseUrl": "http://localhost:8198"
}
```

### OpenAI-Compatible Agents (GPT, DeepSeek, Ollama, vLLM, etc.)

```bash
export OPENAI_BASE_URL=http://localhost:8198/v1
export OPENAI_API_KEY=your-key
```

Works with any agent that uses the OpenAI SDK or compatible API.

### Cursor / Continue / Other IDE Extensions

Set the API base URL in your extension settings:
- **Cursor**: Settings → Models → OpenAI API Base → `http://localhost:8198/v1`
- **Continue**: `config.json` → `"apiBase": "http://localhost:8198/v1"`

### Custom Agents (Direct API Call)

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8198/v1",
    api_key="your-key"
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
```

## Supported Providers

| Provider | Format | Auto-Detected |
|----------|--------|:---:|
| OpenAI | OpenAI | ✅ |
| Anthropic | Anthropic | ✅ |
| Google Gemini | Gemini | ✅ |
| DeepSeek | OpenAI | ✅ |
| Ollama | OpenAI | ✅ |
| vLLM | OpenAI | ✅ |
| LiteLLM | OpenAI | ✅ |
| OpenRouter | OpenAI | ✅ |
| Together AI | OpenAI | ✅ |
| Groq | OpenAI | ✅ |
| Fireworks | OpenAI | ✅ |
| MaaS Proxies | Anthropic | ✅ |

Set `COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=auto` (default) for automatic detection, or `true`/`false` to override.

## Connecting Your LLM Provider

The proxy sits between your agent and any LLM API. **One variable decides the upstream**: `COMPACTION_PROXY_UPSTREAM`. Everything else is optional.

### OpenAI / OpenAI-Compatible (OpenAI, DeepSeek, Ollama, vLLM, LiteLLM, OpenRouter, Groq, etc.)

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.openai.com/v1      # or any OpenAI-compatible endpoint
export COMPACTION_PROXY_MODEL=gpt-4o-mini                        # compaction model
python3 compaction-proxy.py
```

Works out of the box — the proxy forwards requests and converts the compaction result back to the OpenAI format your agent expects.

### Anthropic (Claude)

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.anthropic.com
export COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=true
export COMPACTION_PROXY_MODEL=claude-sonnet-4-5
python3 compaction-proxy.py
```

The proxy speaks the Anthropic Messages API (`/v1/messages`), so Anthropic-native agents connect directly. Format conversion is applied automatically for OpenAI-format agents pointing at the same proxy.

### Google Gemini

```bash
export COMPACTION_PROXY_UPSTREAM=https://generativelanguage.googleapis.com
export COMPACTION_PROXY_MODEL=gemini-2.0-flash
python3 compaction-proxy.py
```

The API key travels in the `x-goog-api-key` header (never in the URL), so it stays out of access logs.

### DeepSeek

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.deepseek.com/v1
export COMPACTION_PROXY_MODEL=deepseek-chat
python3 compaction-proxy.py
```

DeepSeek context windows (deepseek-chat / coder / r1 / v3, 128K) are pre-registered, so pressure estimation works without extra setup.

### Local Models (Ollama)

```bash
export COMPACTION_PROXY_UPSTREAM=http://localhost:11434/v1   # default
export COMPACTION_PROXY_MODEL=llama3.1
python3 compaction-proxy.py
```

### Verify It Works

```bash
# Health check
curl http://localhost:8198/health

# Send a test request through the proxy (OpenAI format)
curl -X POST http://localhost:8198/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello"}]}'
```

### Tips

- **Compaction model ≠ main model**: set `COMPACTION_PROXY_COMPACTION_UPSTREAM` / `COMPACTION_PROXY_COMPACTION_API_KEY` to use a cheaper model for summarization while your agent uses the main model.
- **Anthropic-format MaaS proxies** (e.g. iFlytek coding API): set `COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=true` so request conversion is applied.
- **Never hardcode secrets in config**: use environment variables or the `apiKeyEnv` pattern; the proxy never logs API keys.

## 40 Compression Techniques

### V3 Core (8)

| # | Technique | Description |
|---|-----------|-------------|
| 1 | **ARC** Addressable References | Replace repeated content with compact `@ref` pointers |
| 2 | **AFM** Adaptive Fidelity | Message-level Full/Compressed/Placeholder + submodular boost |
| 3 | **PACMS** Submodular Selection | Greedy, fidelity-aware message selection |
| 4 | **CCL** Commitment Extraction | Extract and preserve commitments from conversation |
| 5 | Thrashing Detection | Detect compaction loops (3 compacts / 5 msgs) |
| 6 | Two-Stage Token Estimation | CJK-aware accurate token counting |
| 7 | Preflight Safety Verification | Validate context before sending to model |
| 8 | Parallel Compaction | Split large contexts into parallel blocks |

### V4 Enhanced (10)

| # | Technique | Source |
|---|-----------|--------|
| 9 | Episodic-Semantic Dual-Layer Memory | arXiv:2605.17625 |
| 10 | Thought Masking | Kevin-32B / Devin pattern |
| 11 | Tag-based Selective Retention | SWE-agent / memor-ai |
| 12 | Structure-Aware Tool Output Compression | memor-ai |
| 13 | Secret Redaction | Cursor / memor-ai |
| 14 | Cache-Optimized Message Ordering | layer-hash-aware + `cache_control` breakpoints |
| 15 | Incremental Compaction | CoMem (arXiv:2605.30842) |
| 16 | Four-Signal Memory Scoring | semantic 50%, recency 25%, kind 15%, quality 10% |
| 17 | Query Placement Optimization | query-at-end, pair-adjacency, recent-window |
| 18 | Compaction-Item Pruning | OpenAI pattern |

### V5 Session (5)

| # | Technique | Description |
|---|-----------|-------------|
| 19 | Cross-Session Memory + FTS5 | SQLite-backed session persistence with full-text search |
| 20 | Pre/Post Compaction Hooks | HTTP webhooks before/after compaction |
| 21 | Orphan Tool Pair Sanitization | Clean up dangling tool call/result pairs |
| 22 | User Profile Memory | Persistent user preferences (USER.md) |
| 23 | Pluggable Compression Engine | Swap compression backends (default / dual-layer) |

### V6 Provider (5)

| # | Technique | Description |
|---|-----------|-------------|
| 24 | Provider Abstraction Layer | Universal OpenAI/Anthropic/Gemini compatibility |
| 25 | Compaction Provider | Separate upstream & API key for compaction model |
| 26 | Provider-Specific Overflow Detection | Per-provider error pattern matching |
| 27 | Dual-Layer Compression | Gateway (85% reduction) + Agent (50% of L1) |
| 28 | CachedSystemPrompt | 10-layer prefix cache + `cache_control` breakpoints |

### V7 MemSkill (5)

| # | Technique | Source |
|---|-----------|--------|
| 36 | Self-evolving Memory Skills | arXiv:2602.02474 |
| 37 | Skill Controller | Keyword matching + Gumbel-Top-K selection |
| 38 | Skill Registry | CRUD + activation lifecycle + snapshot rollback |
| 39 | Parameter Override Pipeline | Importance weights + fidelity multipliers |
| 40 | DELETE-type Skill Pipeline Skip | Skip semantic extraction for DELETE operations |

### Infrastructure

| Feature | Description |
|---------|-------------|
| Auto Provider Detection | Automatically detect OpenAI vs Anthropic vs Gemini |
| Model Registry | 67+ models with known context limits |
| Circuit Breaker | 3 failures → 60s cooldown |
| Compaction Cache | 30-min TTL, avoids re-compressing unchanged context |
| Preemptive Compaction | Trigger at 80% threshold before hitting the limit |
| Overflow Recovery | Compress + retry on context overflow errors |
| Smart Truncation | Role-based budget allocation |
| Identifier Preservation | Keep code identifiers intact during compression |
| Summary Merge | Merge parallel compaction block results |

## Configuration

All settings are environment variables with sensible defaults. See [.env.example](.env.example) for the full list.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_PORT` | `8198` | Proxy listen port |
| `COMPACTION_PROXY_UPSTREAM` | `http://localhost:11434/v1` | Upstream LLM API base URL |
| `COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC` | `auto` | Force Anthropic format (`true`/`false`/`auto`) |
| `COMPACTION_PROXY_MODEL` | `gpt-4o-mini` | Model for compaction/summarization |
| `COMPACTION_PROXY_DEFAULT_MODEL` | `gpt-4o-mini` | Default model for requests without one |

### Compaction Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_KEEP_TURNS` | `6` | Recent turns to keep (not compacted) |
| `COMPACTION_PROXY_MAX_RETRIES` | `3` | Max retries for compaction API calls |
| `COMPACTION_PROXY_TIMEOUT` | `120` | Timeout (seconds) for compaction |
| `COMPACTION_PROXY_PARALLEL_BLOCKS` | `3` | Parallel compaction blocks |
| `COMPACTION_PROXY_PREEMPTIVE_THRESHOLD` | `0.80` | Trigger compaction at 80% context |

### Dual-Layer Compression

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_ENGINE` | `default` | Compression engine (`default` / `dual-layer`) |
| `COMPACTION_PROXY_DUAL_GATEWAY_RATIO` | `0.15` | L1 gateway: keep 15% (85% reduction) |
| `COMPACTION_PROXY_DUAL_AGENT_RATIO` | `0.50` | L2 agent: keep 50% of L1 output |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_API_SECRET` | *(empty)* | Require this secret for proxy access |
| `COMPACTION_PROXY_REDACT_SECRETS` | `1` | Auto-redact secrets in messages |

### Session & Memory

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_BACKGROUND_SESSIONS` | `3` | Background sessions for parallel compaction |
| `COMPACTION_PROXY_LLM_MEMORY` | `1` | LLM-based memory extraction |
| `COMPACTION_PROXY_MEMSKILL` | `0` | Enable self-improving MemSkill |

## Architecture

```
Request Flow:
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

### Dual-Layer Compression (V6)

```
Original Context (100%)
       │
  ┌────▼────┐
  │  L1 Gateway  │  Keep 15% → 85% reduction
  │  Compression │
  └────┬────┘
       │ 15% retained
  ┌────▼────┐
  │  L2 Agent   │  Keep 50% of L1 → 92.5% total reduction
  │  Compression │
  └────┬────┘
       │ 7.5% of original
       ▼
  Final Context
```

## API Compatibility

The proxy is transparent — it forwards all API endpoints and preserves the original request/response format. Your agent doesn't need to know it's there.

### OpenAI Format

```
POST /v1/chat/completions
POST /v1/models
```

### Anthropic Format

```
POST /v1/messages
POST /v1/messages/count_tokens
```

### Gemini Format

```
POST /v1beta/models/{model}:generateContent
POST /v1beta/models/{model}:streamGenerateContent
```

## License

MIT — see [LICENSE](LICENSE).
