# LLM Context Compaction Proxy

> **永不超限,永不停歇 · Never Full, Never Stop**

**Read this in other languages:** [简体中文](https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy/blob/main/README-zh.md)

A transparent, zero-config context compaction proxy for LLM APIs. Sit it between your AI agent and any LLM provider — it automatically compresses conversation context when it approaches the model's token limit, keeping your agents running indefinitely without hitting context window walls.

**40+ compression techniques** across 7 generations, from academic research (ARC, AFM, PACMS, CoMem, MemSkill) to production patterns (Cursor, Devin, SWE-agent).

---

## ✨ Feature Highlights

### 🧠 Context Compression Engine
| Feature | Description |
|---------|-------------|
| **Preemptive Compaction** | Triggers at 80% context before hitting the limit |
| **Overflow Recovery** | Compress + retry on context overflow errors |
| **Incremental Compaction** | Only summarize new messages since last compaction (CoMem) |
| **Parallel Compaction** | Split large contexts into parallel blocks |
| **Parallel Summary Merge** | Merge parallel block summaries into one cohesive summary |
| **Dual-Layer Compression** | Gateway (85% reduction) + Agent (50% of L1), three fallback paths |
| **CJK-Aware Token Estimation** | Accurate CJK/ASCII/other token counting (two-stage: fast + accurate) |
| **Compaction Safety Verification** | Compacted result must be smaller than original, else aggressive truncation |
| **Smart Truncation** | Role-based budget allocation (head/tail split) |
| **Identifier Preservation** | Keep code identifiers intact during compression |
| **Mandatory Identifier List** | Force-preserve UUIDs/hashes/URLs/file names verbatim in summaries |
| **Compaction Cache** | 30-min TTL, instruction-aware salt keys, avoids re-compressing unchanged context |
| **Compaction-Item Pruning** | Prune low-value artifacts left by previous compactions |
| **Query Placement Optimization** | Query-at-end, tool-pair adjacency, recent-window reordering |
| **Cache-Optimized Ordering** | Layer-hash-aware message reorder + `cache_control` breakpoints |
| **Structure-Aware Tool Output Compression** | Dedicated compressors for code / logs / JSON outputs |
| **Manual Compaction (`/compact`)** | Like Claude Code's /compact — force compact any session |
| **Aggressive Truncation Fallback** | Degrade to truncation when compaction fails or grows |

### 🗂️ Memory & Session
| Feature | Description |
|---------|-------------|
| **Semantic Memory** | Episodic-semantic dual-layer memory (arXiv:2605.17625), 6 knowledge slots |
| **Cross-Session Memory + FTS5** | SQLite session persistence with full-text search |
| **User Profile Memory** | Persistent user preferences (USER.md), bounded persistence |
| **Session Transcript Archive** | Save/restore raw transcripts for session resume |
| **Session Resume** | Resume a session from transcript + summary (`POST /sessions/{id}/resume`) |
| **Background Knowledge Injection** | Inject recent session knowledge into compaction prompts |
| **Session Context Injection** | Auto-inject session context into outgoing requests |
| **ARC References** | Replace lengthy tool results with ID references |
| **ARC Persistent Recall** | Persist ARC entries to DB, recall via `GET /arc/{arc_id}` |
| **Commitment Extraction** | Extract and preserve commitments (CCL) — goals/constraints/decisions/errors |
| **LLM Memory Extraction** | LLM-driven knowledge extraction with regex fallback |
| **Thought Masking** | Strip reasoning_content to save tokens |
| **Secret Redaction** | Auto-redact API keys/JWT/passwords/tokens |
| **Orphan Tool Pair Sanitization** | Clean up dangling tool_call/tool_result pairs |
| **Tool Pair Adjacency Fix** | Repair non-adjacent tool call/result ordering |

### 🔌 Provider & Protocol
| Feature | Description |
|---------|-------------|
| **Provider Abstraction Layer** | Universal OpenAI/Anthropic/Gemini compatibility (ABC) |
| **Auto Provider Detection** | Detect format from model name / headers / URL |
| **OpenAI→Anthropic Request Conversion** | Full field mapping incl. tool_calls → tool_use |
| **Anthropic→OpenAI Response Conversion** | Content/thinking/tool_use mapped back to OpenAI |
| **SSE Streaming Conversion** | Anthropic SSE → OpenAI SSE event-by-event |
| **Anthropic Thinking-Only Retry** | Auto streaming retry when response has only empty thinking blocks |
| **Separate Compaction Provider** | Independent upstream & API key for compaction model |
| **Gemini Header Auth** | `x-goog-api-key` header, never in URL/logs |
| **Model Registry** | 67+ models with known context limits |
| **Passthrough Routing** | Any unmatched path transparently forwarded upstream |

### 🛡️ Reliability & Security
| Feature | Description |
|---------|-------------|
| **Circuit Breaker** | 3 failures → 60s cooldown, three-state machine |
| **Thrashing Detection** | Detect compaction loops (3 compacts / 5 msgs) → aggressive truncation |
| **Auth (require_auth)** | 22+ endpoints protected; loopback-only when no secret |
| **Concurrency Safety** | Thread-locked semantic memory, race-free prompts (parameter-passed) |
| **Pre/Post Compaction Hooks** | HTTP webhooks before/after compaction (blockable) |
| **Health & Metrics Endpoints** | `/health`, `/metrics` (Prometheus-style counters) |
| **Adaptive Keep-Turns** | Auto-reduce kept turns when conversation is short |
| **Retry with Shrinking Window** | Retry compaction with fewer turns per attempt |

### 🤖 MemSkill (Self-Evolving, V7)
| Feature | Description |
|---------|-------------|
| **Skill Registry** | CRUD + activation lifecycle + snapshot rollback (persisted to SQLite) |
| **Skill Controller** | Keyword matching + Gumbel-Top-K selection |
| **Parameter Override Pipeline** | Importance weights + fidelity multipliers per skill |
| **DELETE-type Skill Pipeline Skip** | Skip semantic extraction for DELETE operations |
| **Skill Performance Tracking** | Reward/success stats per skill (`/skills/{id}/performance`) |
| **Skill Trajectories** | Recent compaction trajectories (`/skills/trajectories`) |
| **Skill Designer** | Auto-design new skills (`/skills/designer/trigger`) |

### 🔌 HTTP API Endpoints (31)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat/completions` (+ `/v1/chat/completions`) | OpenAI-compatible chat (streaming & non-streaming) |
| POST | `/v1/messages` | Anthropic Messages API |
| POST | `/compact` | Manual compaction of a session |
| POST | `/summarize` | Summary-only endpoint for CompactionProvider plugins |
| POST | `/session` | Create a session |
| GET | `/sessions/search` | FTS5 full-text session search |
| GET | `/sessions/recent` | Recent sessions |
| GET | `/sessions/{id}` | Session detail |
| GET | `/sessions/{id}/transcript` | Raw transcript export |
| POST | `/sessions/{id}/resume` | Resume a session |
| GET | `/profile` · POST `/profile` | User profile get/set |
| GET | `/memory` · DELETE `/memory` | Semantic memory get/clear |
| GET | `/arc/{arc_id}` | ARC reference recall |
| GET | `/skills` · GET `/skills/{id}` | Skill list/detail |
| POST | `/skills` | Create skill (draft) |
| POST | `/skills/{id}/activate` · `/deprecate` | Skill lifecycle |
| POST | `/skills/{id}/rollback` | Rollback to snapshot version |
| GET | `/skills/{id}/performance` | Skill reward stats |
| GET | `/skills/trajectories` | Compaction trajectories |
| POST | `/skills/designer/trigger` | Trigger skill designer |
| GET | `/health` · `/metrics` | Health & metrics |
| ANY | `/{path:.*}` | Passthrough to upstream |

---

## Quick Start

### Prerequisites

- Python 3.10+
- `aiohttp` (`pip install aiohttp`)

### Install & Run

```bash
# Clone
git clone https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy.git
cd NeverFull-NeverStop-LLM-Context-Compaction-Proxy

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

---

## Connecting Your LLM Provider

The proxy sits between your agent and any LLM API. **One variable decides the upstream**: `COMPACTION_PROXY_UPSTREAM`. Everything else is optional.

### OpenAI / OpenAI-Compatible (OpenAI, DeepSeek, Ollama, vLLM, LiteLLM, OpenRouter, Groq, etc.)

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.openai.com/v1
export COMPACTION_PROXY_MODEL=gpt-4o-mini
python3 compaction-proxy.py
```

### Anthropic (Claude)

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.anthropic.com
export COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=true
export COMPACTION_PROXY_MODEL=claude-sonnet-4-5
python3 compaction-proxy.py
```

### Google Gemini

```bash
export COMPACTION_PROXY_UPSTREAM=https://generativelanguage.googleapis.com
export COMPACTION_PROXY_MODEL=gemini-2.0-flash
python3 compaction-proxy.py
```

### DeepSeek

```bash
export COMPACTION_PROXY_UPSTREAM=https://api.deepseek.com/v1
export COMPACTION_PROXY_MODEL=deepseek-chat
python3 compaction-proxy.py
```

### Local Models (Ollama)

```bash
export COMPACTION_PROXY_UPSTREAM=http://localhost:11434/v1   # default
export COMPACTION_PROXY_MODEL=llama3.1
python3 compaction-proxy.py
```

---

## 🤖 Supported Agents — All Verified

The proxy works with **OpenClaw, Claude Code, Hermes, and DeepSeek Harness** — all verified live. Each keeps running indefinitely because context is compacted by the proxy, never by the agent itself.

### OpenClaw

Point the model provider at the proxy in `openclaw.json`, and enable the `v6-compaction-provider` plugin:

```json
{
  "models": {
    "providers": {
      "compaction-proxy": {
        "baseUrl": "http://127.0.0.1:8198",
        "api": "openai-completions",
        "models": [{ "id": "xopdeepseekv4flash" }]
      }
    }
  },
  "plugins": {
    "entries": {
      "v6-compaction-provider": {
        "enabled": true,
        "config": { "proxyUrl": "http://127.0.0.1:8198", "model": "xsparkx2agent" }
      }
    }
  }
}
```

**Verified**: OpenClaw's `xunfei` provider already points at `127.0.0.1:8198`; the `v6-compaction-provider` plugin routes `/summarize` through the proxy. OpenClaw's native compaction is disabled (`compaction.enabled=false`) so the proxy owns compression entirely.

### Claude Code (Anthropic)

```bash
export ANTHROPIC_BASE_URL=http://localhost:8198
claude
```

Or in `~/.claude/settings.json`:

```json
{
  "env": { "ANTHROPIC_BASE_URL": "http://localhost:8198" },
  "autoCompactEnabled": false
}
```

**Verified**: `POST /v1/messages` routes through the proxy (401 on missing key confirms Anthropic format detection). `autoCompactEnabled: false` disables Claude Code's own compaction.

### Hermes Agent

Edit `~/.hermes/config.yaml`:

```yaml
model:
  api_mode: chat_completions          # OpenAI format
  base_url: http://127.0.0.1:8198/v1  # point at the proxy
  default: xopglm51                   # any model available upstream
```

**Verified**: Hermes' `chat_completions` mode is fully compatible with the proxy's `/v1/chat/completions`. Hermes' own compression is disabled (`compression.enabled: false`).

### DeepSeek Harness

Edit `$DSH_HOME/settings.yaml` (default `~/.dsh/settings.yaml`):

```yaml
llm-pi-ai:
  providers:
    xfyun-maas:
      apiKeyEnv: XF_MASS_API_KEY
      api: openai-completions           # OpenAI-compatible protocol
      baseURL: http://127.0.0.1:8198    # point at the proxy
      models:
        - id: xsparkx2agent
          contextWindow: 131072
          maxTokens: 8192
```

**Verified**: Harness' `xfyun-maas` provider already points at `127.0.0.1:8198`. Harness' native compaction is disabled via `cordis.patch.yml` (`dsh-compaction-basic` → `auto: false`).

---

## Verify It Works

```bash
# Health check
curl http://localhost:8198/health

# Send a test request through the proxy (OpenAI format)
curl -X POST http://localhost:8198/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello"}]}'
```

---

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

---

## 40+ Compression Techniques

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
| Secret Redaction | Auto-redact API keys in messages |
| Auth (require_auth) | 22+ endpoints, loopback-only when no secret |
| Concurrency Safety | Thread-locked memory, race-free prompts |

---

## Configuration

All settings are environment variables with sensible defaults. See [.env.example](.env.example) for the full list.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPACTION_PROXY_PORT` | `8198` | Proxy listen port |
| `COMPACTION_PROXY_UPSTREAM` | `http://localhost:11434/v1` | Upstream LLM API base URL |
| `COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC` | `auto` | Force Anthropic format (`true`/`false`/`auto`) |
| `COMPACTION_PROXY_MODEL` | `gpt-4o-mini` | Model for compaction/summarization |

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

---

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

---

## API Compatibility

The proxy is transparent — it forwards all API endpoints and preserves the original request/response format. Your agent doesn't need to know it's there.

| Format | Endpoints |
|--------|-----------|
| OpenAI | `POST /v1/chat/completions`, `POST /v1/models` |
| Anthropic | `POST /v1/messages`, `POST /v1/messages/count_tokens` |
| Gemini | `POST /v1beta/models/{model}:generateContent`, `.../streamGenerateContent` |

---

## License

MIT — see [LICENSE](LICENSE).
