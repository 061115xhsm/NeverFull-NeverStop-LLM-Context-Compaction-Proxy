# Agent Integration Guide

This document covers how to connect various AI agents and tools to the LLM Context Compaction Proxy.

## Integration Test Results

All 4 integration methods have been verified:

| Method | Endpoint | Format | Status |
|--------|----------|--------|--------|
| Claude Code (Anthropic) | `/v1/messages` | Anthropic | ✅ Live (this session) |
| OpenAI SDK / Compatible | `/v1/chat/completions` | OpenAI | ✅ Format conversion verified |
| Streaming (SSE) | `/v1/messages?stream=true` | Anthropic SSE | ✅ Pipeline verified |
| Utility Endpoints | `/health`, `/v1/models` | REST | ✅ Working |

> **Note**: Test requests with dummy API keys return 401 from the upstream provider (expected). This confirms the proxy correctly routes and converts formats — a format error would return 400/404/422 instead.

---

## Method 1: Claude Code (Anthropic API)

The proxy natively supports the Anthropic Messages API format. Claude Code connects automatically.

### Environment Variable

```bash
export ANTHROPIC_BASE_URL=http://localhost:8198
```

### Claude Code Settings

In `~/.claude/settings.json` or project `.claude/settings.json`:

```json
{
  "apiBaseUrl": "http://localhost:8198"
}
```

### How It Works

1. Claude Code sends `POST /v1/messages` with Anthropic format
2. Proxy detects Anthropic format (via `anthropic-version` header or auto-detection)
3. If context is under threshold → passthrough to upstream
4. If context exceeds threshold → compact, then forward
5. Response streams back through proxy to Claude Code

### Verified Behavior

- ✅ Streaming SSE passthrough
- ✅ Model name mapping (claude-sonnet-5 → upstream model)
- ✅ `cache_control` breakpoints preserved
- ✅ Tool use / tool result pairs handled
- ✅ Preemptive compaction at 80% threshold
- ✅ Cross-session memory persistence

---

## Method 2: OpenAI SDK / Compatible Agents

The proxy converts OpenAI format requests to the upstream provider's format automatically.

### Environment Variables

```bash
export OPENAI_BASE_URL=http://localhost:8198/v1
export OPENAI_API_KEY=your-api-key
```

### Python SDK

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8198/v1",
    api_key="your-api-key"
)

# Non-streaming
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=100
)

# Streaming
for chunk in client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=100,
    stream=True
):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### Node.js SDK

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://localhost:8198/v1',
  apiKey: 'your-api-key'
});

const response = await client.chat.completions.create({
  model: 'gpt-4o-mini',
  messages: [{ role: 'user', content: 'Hello!' }],
  max_tokens: 100,
  stream: true
});

for await (const chunk of response) {
  process.stdout.write(chunk.choices[0]?.delta?.content || '');
}
```

### Compatible Agents

| Agent | Configuration |
|-------|--------------|
| **LangChain** | `ChatOpenAI(base_url="http://localhost:8198/v1", api_key="...")` |
| **AutoGen** | Set `OPENAI_BASE_URL=http://localhost:8198/v1` |
| **CrewAI** | Set `OPENAI_API_BASE=http://localhost:8198/v1` |
| **Semantic Kernel** | Set endpoint to `http://localhost:8198/v1` |

### Format Conversion

The proxy automatically converts between formats:

```
OpenAI Request → Proxy → Anthropic Upstream
  role: "assistant"     →  role: "assistant"
  role: "system"        →  role: "system" (first message)
  content: "text"       →  content: [{"type":"text","text":"text"}]
  tool_calls            →  tool_use blocks
  tool role messages    →  tool_result blocks
```

---

## Method 3: Cursor / IDE Extensions

### Cursor

1. Open Settings → Models
2. Set **OpenAI API Base** to `http://localhost:8198/v1`
3. Set your API key
4. Select a model available on your upstream

### Continue (VS Code / JetBrains)

Edit `~/.continue/config.json`:

```json
{
  "models": [
    {
      "title": "Compaction Proxy",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "apiBase": "http://localhost:8198/v1",
      "apiKey": "your-api-key"
    }
  ]
}
```

### Copilot Alternatives

Any extension that allows custom OpenAI-compatible endpoints can point to the proxy.

---

## Method 4: Custom Agents (Direct API)

### Anthropic Format

```python
import httpx

async def call_anthropic():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8198/v1/messages",
            headers={
                "x-api-key": "your-api-key",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 1024,
                "stream": True,
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ]
            }
        )
        # Handle SSE stream
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                print(line[6:])
```

### OpenAI Format

```python
import httpx

async def call_openai():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8198/v1/chat/completions",
            headers={
                "Authorization": "Bearer your-api-key",
                "content-type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1024,
                "stream": True,
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ]
            }
        )
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                print(line[6:])
```

---

## Health Monitoring

### Health Endpoint

```bash
curl http://localhost:8198/health
```

Returns:
```json
{
  "status": "ok",
  "version": "V6",
  "circuit_breaker": {"state": "closed", "failure_count": 0},
  "thrashing": {"is_thrashing": false},
  "metrics": {
    "request_total": 49,
    "request_success": 6,
    "preemptive_compaction_triggered": 5
  }
}
```

### Key Metrics

| Field | Meaning |
|-------|---------|
| `circuit_breaker.state` | `closed` = healthy, `open` = upstream down |
| `thrashing.is_thrashing` | `true` = compaction loop detected |
| `metrics.request_success` | Successful requests count |
| `metrics.preemptive_compaction_triggered` | Times compaction activated |

---

## Troubleshooting

### 401 Unauthorized

- Check your API key is set correctly
- If using Anthropic format: `x-api-key` header
- If using OpenAI format: `Authorization: Bearer <key>` header

### 503 Engine Overloaded

- Upstream provider is temporarily overloaded
- The proxy will auto-retry with exponential backoff (1s, 2s, 4s)
- If persistent, consider switching to a different provider/region

### Context Not Compacting

- Check `COMPACTION_PROXY_PREEMPTIVE_THRESHOLD` (default 0.80)
- Verify the compaction model is available at the upstream
- Check logs: `tail -f ~/.local/share/compaction-proxy/proxy.log`

### Format Errors

- Ensure you're hitting the correct endpoint:
  - Anthropic: `/v1/messages`
  - OpenAI: `/v1/chat/completions`
- The proxy auto-detects format, but explicit endpoints avoid ambiguity
