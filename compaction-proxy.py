#!/usr/bin/env python3
"""
LLM Context Compaction Proxy
=====================================
在 agent framework 和 LLM API 之间插入的透明代理。
当检测到上下文溢出错误时，自动压缩对话历史并重试，
防止 OpenClaw 内置压缩失败后对话直接死掉。

V6 基于 V5 升级，新增 Provider Abstraction Layer:
  [V6 新增]
  1. Provider Abstraction Layer — 通用 API 兼容层
     支持 OpenAI/Anthropic/Gemini 三大 API 格式，自动检测 Provider
  2. OpenAIProvider — 覆盖 OpenAI/xfyun/Ollama/vLLM/LiteLLM/OpenRouter/DeepSeek/Together/Groq/Fireworks
  3. AnthropicProvider — Anthropic Messages API (x-api-key, system top-level, content blocks)
  4. GeminiProvider — Google Gemini API (key= query param, contents format, systemInstruction)
  5. Provider Auto-Detection — 从 model name/headers/upstream URL 自动识别 Provider
  6. Provider-Specific Overflow Detection — 各 Provider 独立的上下文溢出检测模式
  7. Expanded Model Context Limits — 覆盖 OpenAI/Anthropic/Gemini/DeepSeek/Qwen/Mistral/Llama 等
  8. Separate Compaction Upstream/API Key — 压缩模型可使用独立的 upstream 和 API key

V5 基于 V4 升级，新增 6 项技术补齐 Hermes/Claude Code 短板:
  [V5 新增]
  1. Cross-Session Memory + FTS5 Search — SQLite 持久化会话记忆 + 全文搜索
     (Hermes dual-layer compression + FTS5 session search)
  2. Pre/Post Compaction Hooks — 压缩前后 HTTP webhook 钩子
     (Claude Code PreCompact/PostCompact hooks)
  3. Orphan Tool Pair Sanitization — 孤立 tool_call/tool_result 对清理
     (Hermes orphan pair cleanup)
  4. User Profile Memory — 持久化用户画像 (USER.md)
     (Hermes USER.md bounded persistence)
  5. Pluggable Compression Engine — 可插拔压缩引擎 ABC
     (Hermes ContextEngine ABC)
  6. Config env-var-ization + persistence fixes — 配置全面环境变量化 + 持久化修复

V4 基于 2025-2026 前沿论文研究升级，新增 10 项技术:
  [V4 新增]
  1. Episodic-Semantic Dual-Layer Memory — 跨压缩持久化语义记忆
     (arXiv:2605.17625 "Episodic-Semantic Memory")
  2. Thought Masking — 剥离 reasoning_content，节省 token
     (arXiv:2606.03618 "Cross-Lingual Token Arbitrage" masking pattern)
  3. Tag-based Selective Retention — 按工具类型分级保留输出
     (arXiv:2511.12712 "Adaptive Focus Memory" tool-tag extension)
  4. Structure-Aware Tool Output Compression — AST/Log/JSON 感知压缩
     (arXiv:2605.23296 "Parallel Context Compaction" structure-aware)
  5. Secret Redaction — 自动脱敏 API Key/JWT/密码/Token
     (arXiv:2606.03618 safety pattern extension)
  6. Cache-Optimized Message Ordering — 缓存友好的消息排序
     (arXiv:2607.25066 "Addressable Recall Compaction" ordering)
  7. Incremental Compaction — 增量压缩，只压缩新增部分
     (arXiv:2605.23296 "Parallel Context Compaction" incremental)
  8. Four-Signal Memory Scoring — 语义(50%)+近因(25%)+类型(15%)+质量(10%)
     (arXiv:2606.20047 "Submodular Context Selection" multi-signal)
  9. Query Placement Optimization — 查询位置优化
     (arXiv:2607.25066 "Addressable Recall Compaction" placement)
  10. Compaction-Item Pruning — 压缩项剪枝，移除低价值项
      (arXiv:2605.17304 "Context Codec / CCL" pruning)

  [V3 保留]
  1. ARC 地址化引用 — tool_result 用 ID 替代，代理维护外部日志
     (arXiv:2607.25066 "Addressable Recall Compaction")
  2. 并行分块压缩 — 旧消息分块并行压缩，消除阻塞
     (arXiv:2605.23296 "Parallel Context Compaction")
  3. 自适应保真度 AFM — 三级保真(Full/Compressed/Placeholder)
     (arXiv:2511.12712 "Adaptive Focus Memory")
  4. 子模选择 PACMS — 贪心子模优化在 token 预算下选消息
     (arXiv:2606.20047 "Submodular Context Selection")
  5. 承诺级压缩 CCL — 提取语义承诺原子，结构化保留关键约束
     (arXiv:2605.17304 "Context Codec / CCL")
  6. 预飞安全重写 — 压缩后验证 token 数不超过原始，否则降级
     (arXiv:2606.03618 "Cross-Lingual Token Arbitrage" safety pattern)
  7. Cline 双策略 — Basic(无LLM) + Agentic(LLM) 压缩策略
  8. Claude Code 蠕变检测 — 连续快速重填时停止压缩循环
  9. Aider 递归分割 — head/tail 分割，递归压缩 head
  10. 两阶段 token 估算 — 快速启发式预筛 + 精确计数

  [V2 保留]
  - 流式双请求 bug 修复
  - System 消息合并
  - 流式 SSE 错误处理
  - 熔断器
  - 压缩缓存
  - 标识符提取
  - 智能截断
  - 指标端点

架构:
  Smart Agent → localhost:8198 (本代理) → Any AI Provider
                      ↓
                 正常请求 → 透传 (OpenAI/Anthropic/Gemini/xfyun/Ollama/vLLM/...)
                 溢出错误 → 自动压缩 messages → 重试请求
"""

import ast
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import threading
import unicodedata
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from aiohttp import web

# ── V8 可选增强模块(独立模块,导入失败不影响核心功能) ────────────────
try:
    from fidelity import FidelityScorer, AdaptiveCompactor, QualityBreaker, query_relevance_weighting
    ENH_FIDELITY = True
except Exception:
    FidelityScorer = AdaptiveCompactor = QualityBreaker = query_relevance_weighting = None
    ENH_FIDELITY = False

try:
    from memory_engine import ThreeLayerMemory, MemoryDecay, MemoryItem
    ENH_MEMORY = True
except Exception:
    ThreeLayerMemory = MemoryDecay = MemoryItem = None
    ENH_MEMORY = False

try:
    from knowledge_graph import KnowledgeGraph, HybridRetriever
    ENH_KG = True
except Exception:
    KnowledgeGraph = HybridRetriever = None
    ENH_KG = False

try:
    from compression_advanced import CodeCompressor, DiffCompressor, StructFoldCompressor, ReversibleCompactor
    ENH_COMPRESS = True
except Exception:
    CodeCompressor = DiffCompressor = StructFoldCompressor = ReversibleCompactor = None
    ENH_COMPRESS = False

try:
    from security_enhanced import PIIRedactor, EncryptedStore, TenantManager
    ENH_SECURITY = True
except Exception:
    PIIRedactor = EncryptedStore = TenantManager = None
    ENH_SECURITY = False

try:
    from protocol_extra import ResponsesAPIAdapter, LangChainAdapter, LlamaIndexAdapter, ReasoningPassthrough
    ENH_PROTOCOL = True
except Exception:
    ResponsesAPIAdapter = LangChainAdapter = LlamaIndexAdapter = ReasoningPassthrough = None
    ENH_PROTOCOL = False

try:
    from cache_engine import LRUCache, MultiLevelCache, PredictivePrecompressor
    ENH_CACHE = True
except Exception:
    LRUCache = MultiLevelCache = PredictivePrecompressor = None
    ENH_CACHE = False

try:
    from observability import FailoverManager, GracefulDegrader, MetricsCollector, Tracer
    ENH_OBSERVABILITY = True
except Exception:
    FailoverManager = GracefulDegrader = MetricsCollector = Tracer = None
    ENH_OBSERVABILITY = False

# ── 配置 ──────────────────────────────────────────────────────────────
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("COMPACTION_PROXY_PORT", "8198"))

UPSTREAM_BASE = os.environ.get(
    "COMPACTION_PROXY_UPSTREAM",
    "http://localhost:11434/v1"
)
# Default upstream is a local Ollama endpoint. Set COMPACTION_PROXY_UPSTREAM
# to your provider (OpenAI / Anthropic / DeepSeek / iFlytek MaaS, etc.).
# For Anthropic-format upstreams (e.g. iFlytek MaaS coding API), set
# COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=true so requests are converted.
UPSTREAM_IS_ANTHROPIC = os.environ.get(
    "COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC",
    "auto"  # "auto" = detect from URL, "true" = always, "false" = never
)


def is_upstream_anthropic() -> bool:
    """Determine if the upstream API speaks Anthropic Messages format."""
    if UPSTREAM_IS_ANTHROPIC == "true":
        return True
    if UPSTREAM_IS_ANTHROPIC == "false":
        return False
    # auto: detect from URL
    url = UPSTREAM_BASE.lower()
    return "anthropic" in url or "claude" in url or "coding-api" in url

COMPACTION_MODEL = os.environ.get("COMPACTION_PROXY_MODEL", "gpt-4o-mini")

KEEP_RECENT_TURNS = int(os.environ.get("COMPACTION_PROXY_KEEP_TURNS", "6"))
MAX_COMPACTION_RETRIES = int(os.environ.get("COMPACTION_PROXY_MAX_RETRIES", "3"))
COMPACTION_TIMEOUT = int(os.environ.get("COMPACTION_PROXY_TIMEOUT", "120"))

# V3: 并行压缩块数
PARALLEL_COMPACTION_BLOCKS = int(os.environ.get("COMPACTION_PROXY_PARALLEL_BLOCKS", "3"))

# V3: 蠕变检测阈值 — N 次压缩在 M 条消息内视为蠕变
THRASHING_COMPACTS = int(os.environ.get("COMPACTION_PROXY_THRASHING_COMPACTS", "3"))
THRASHING_WINDOW_MSGS = int(os.environ.get("COMPACTION_PROXY_THRASHING_WINDOW", "5"))

LOG_LEVEL = os.environ.get("COMPACTION_PROXY_LOG_LEVEL", "INFO")
LOG_FILE = os.environ.get("COMPACTION_PROXY_LOG_FILE", "")

# V4: 语义记忆持久化路径
SEMANTIC_MEMORY_PATH = os.environ.get("COMPACTION_PROXY_SEMANTIC_MEMORY", os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "semantic-memory.json"))

# V4: 秘密脱敏开关
REDACT_SECRETS = os.environ.get("COMPACTION_PROXY_REDACT_SECRETS", "1") == "1"

# ── 模型上下文窗口 ──────────────────────────────────────────────────

MODEL_CONTEXT_LIMITS = {
    # xfyun models
    "xopglm51": 200000, "xopglm5": 128000,
    "xsparkx2agent": 32000, "xsparkx2flash": 32000, "xsparkx2": 32000,
    "xopqwen35397b": 128000, "xopdeepseekv4flash": 128000, "xopdeepseekv32": 128000,
    "xopkimik26": 128000, "xopkimik25": 128000, "xminimaxm25": 128000,
    "xop3qwencodernext": 128000, "xopglmv47flash": 128000,
    # OpenAI models
    "gpt-4o": 128000, "gpt-4o-mini": 128000, "gpt-4-turbo": 128000, "gpt-4": 8192, "gpt-3.5-turbo": 16385,
    "o1": 200000, "o1-mini": 128000, "o1-pro": 200000, "o3": 200000, "o3-mini": 200000, "o4-mini": 200000,
    "gpt-4.1": 1047576, "gpt-4.1-mini": 1047576, "gpt-4.1-nano": 1047576,
    # Anthropic models
    "claude-opus-4": 200000, "claude-sonnet-4": 200000, "claude-haiku-3-5": 200000,
    "claude-3-5-sonnet": 200000, "claude-3-opus": 200000, "claude-3-haiku": 200000,
    "claude-sonnet-5": 200000, "claude-opus-5": 200000, "claude-fable-5": 200000,
    "claude-instant": 100000,
    # Google Gemini models
    "gemini-2.5-pro": 1048576, "gemini-2.5-flash": 1048576,
    "gemini-2.0-flash": 1048576, "gemini-1.5-pro": 2097152, "gemini-1.5-flash": 1048576,
    "gemma3": 128000,
    # DeepSeek models
    "deepseek-chat": 128000, "deepseek-coder": 128000, "deepseek-r1": 128000, "deepseek-v3": 128000,
    # Qwen models
    "qwen2.5": 131072, "qwen3": 131072, "qwq": 131072, "qwen-max": 32768,
    # Mistral models
    "mistral-large": 128000, "mistral-medium": 32000, "mistral-small": 32000,
    "codestral": 32768, "mixtral": 32768,
    # Meta Llama models
    "llama-3.1-405b": 128000, "llama-3.1-70b": 128000, "llama-3.1-8b": 128000,
    "llama-3.3-70b": 128000, "llama-4-maverick": 1048576, "llama-4-scout": 1048576,
    # Other models
    "command-r-plus": 128000, "command-r": 128000,
    "yi-large": 200000,
    # Special
    "auto": 200000,
    "_default": 128000,
}

# V3: 响应预算 — 保留给模型输出的 token 数
RESPONSE_BUDGET = int(os.environ.get("COMPACTION_PROXY_RESPONSE_BUDGET", "8000"))
# V3: 安全边际
SAFETY_MARGIN = int(os.environ.get("COMPACTION_PROXY_SAFETY_MARGIN", "4000"))

PREEMPTIVE_THRESHOLD = float(os.environ.get("COMPACTION_PROXY_PREEMPTIVE_THRESHOLD", "0.80"))

# V5: Cross-Session Memory (SQLite + FTS5)
SESSION_DB_PATH = os.environ.get("COMPACTION_PROXY_SESSION_DB", os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "sessions.db"))
# V5: User Profile Memory
USER_PROFILE_PATH = os.environ.get("COMPACTION_PROXY_USER_PROFILE", os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "user-profile.md"))

# V5: 压缩 Hooks
PRE_COMPACT_HOOK_URL = os.environ.get("COMPACTION_PROXY_PRE_HOOK", "")
POST_COMPACT_HOOK_URL = os.environ.get("COMPACTION_PROXY_POST_HOOK", "")
HOOK_TIMEOUT = int(os.environ.get("COMPACTION_PROXY_HOOK_TIMEOUT", "5"))

# V5: 背景会话数（新会话加载最近N个会话摘要作为背景知识）
BACKGROUND_SESSIONS = int(os.environ.get("COMPACTION_PROXY_BACKGROUND_SESSIONS", "3"))

# V5: 可插拔压缩引擎
COMPRESSION_ENGINE = os.environ.get("COMPACTION_PROXY_ENGINE", "default")

# V5: Dual-Layer Compression (inspired by Hermes gateway+agent architecture)
DUAL_LAYER_GATEWAY_RATIO = float(os.environ.get("COMPACTION_PROXY_DUAL_GATEWAY_RATIO", "0.15"))  # Keep 15% (85% reduction)
DUAL_LAYER_AGENT_RATIO = float(os.environ.get("COMPACTION_PROXY_DUAL_AGENT_RATIO", "0.50"))  # Keep 50% of L1 output

# V5: LLM-driven memory extraction (like Claude Code's auto MEMORY.md generation)
LLM_MEMORY_EXTRACTION = os.environ.get("COMPACTION_PROXY_LLM_MEMORY", "1") == "1"

# V5: API Secret for sensitive endpoint authentication
API_SECRET = os.environ.get("COMPACTION_PROXY_API_SECRET", "")

# V6: Provider configuration
COMPACTION_UPSTREAM = os.environ.get("COMPACTION_PROXY_COMPACTION_UPSTREAM", "")  # Separate upstream for compaction model (empty = same as UPSTREAM_BASE)
COMPACTION_API_KEY = os.environ.get("COMPACTION_PROXY_COMPACTION_API_KEY", "")  # Separate API key for compaction model (empty = use request key)
COMPACTION_PROVIDER = os.environ.get("COMPACTION_PROXY_COMPACTION_PROVIDER", "auto")  # Provider for compaction: auto/openai/anthropic/gemini
REQUEST_PROVIDER = os.environ.get("COMPACTION_PROXY_REQUEST_PROVIDER", "auto")  # Provider for request forwarding: auto/openai/anthropic/gemini

# V7: MemSkill — Self-evolving memory skills for compaction (arXiv:2602.02474)
MEMSKILL_ENABLED = os.environ.get("COMPACTION_PROXY_MEMSKILL", "0") == "1"
MEMSKILL_DESIGNER_INTERVAL = int(os.environ.get("COMPACTION_PROXY_MEMSKILL_DESIGNER_INTERVAL", "100"))
MEMSKILL_MAX_EDITS_PER_ROUND = int(os.environ.get("COMPACTION_PROXY_MEMSKILL_MAX_EDITS", "3"))
MEMSKILL_EXPLORATION_TAU = float(os.environ.get("COMPACTION_PROXY_MEMSKILL_TAU", "0.3"))
MEMSKILL_AUTO_ACTIVATE = os.environ.get("COMPACTION_PROXY_MEMSKILL_AUTO_ACTIVATE", "1") == "1"


def get_model_context_limit(model: str) -> int:
    model_lower = model.lower().strip()
    if model_lower in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model_lower]
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key != "_default" and model_lower.startswith(key):
            return limit
    return MODEL_CONTEXT_LIMITS["_default"]


# ── V6: Provider Abstraction Layer ──────────────────────────────────────

class ProviderAdapter(ABC):
    """V6: Provider abstraction — universal API compatibility layer."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def build_compaction_url(self, base_url: str) -> str:
        """Build URL for compaction LLM call."""
        ...

    @abstractmethod
    def build_compaction_headers(self, api_key: str) -> dict:
        """Build auth headers for compaction LLM call."""
        ...

    @abstractmethod
    def build_compaction_payload(self, model: str, messages: list, max_tokens: int, temperature: float) -> dict:
        """Build request body for compaction LLM call."""
        ...

    @abstractmethod
    def extract_compaction_content(self, response_data: dict) -> str:
        """Extract text content from compaction LLM response."""
        ...

    @abstractmethod
    def detect_overflow(self, status_code: int, body: bytes) -> bool:
        """Provider-specific context overflow detection."""
        ...

    @abstractmethod
    def extract_api_key(self, headers: dict) -> str:
        """Extract API key from request headers."""
        ...

    @abstractmethod
    def build_forward_headers(self, headers: dict, api_key: str) -> dict:
        """Build headers for forwarding request to upstream."""
        ...

    @abstractmethod
    def get_forward_path(self, original_path: str) -> str:
        """Get the path to use when forwarding to upstream."""
        ...


class OpenAIProvider(ProviderAdapter):
    """OpenAI-compatible provider — covers OpenAI, xfyun, Ollama, vLLM, LiteLLM, OpenRouter, DeepSeek, Together, Groq, Fireworks."""

    @property
    def name(self) -> str: return "openai"

    def build_compaction_url(self, base_url): return base_url.rstrip("/") + "/chat/completions"
    def build_compaction_headers(self, api_key): return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    def build_compaction_payload(self, model, messages, max_tokens, temperature): return {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": messages}
    def extract_compaction_content(self, data):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            content = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        return content
    def detect_overflow(self, status_code, body):
        # OpenAI-compatible generic detection (inline to avoid circular call with is_context_overflow)
        if status_code not in (400, 403, 413, 500, 503):
            return False
        try:
            text = body.decode("utf-8", errors="replace").lower()
        except Exception:
            return False
        non_overflow = [
            "model not found", "pathdomainerror", "invalid_api_key",
            "authentication", "rate_limit", "overloaded", "engine is overloaded",
            "quota", "billing", "insufficient_quota",
            "overloaded_error", "permission_error",
        ]
        if any(n in text for n in non_overflow):
            return False
        for pattern in OVERFLOW_PATTERNS:
            if pattern.search(text):
                return True
        return False
    def extract_api_key(self, headers):
        auth = headers.get("Authorization", headers.get("authorization", ""))
        if auth.startswith("Bearer "): return auth[7:]
        return ""
    def build_forward_headers(self, headers, api_key):
        # Keep original headers, just ensure auth is set
        return headers
    def get_forward_path(self, original_path): return original_path


class AnthropicProvider(ProviderAdapter):
    """Anthropic Messages API provider."""

    @property
    def name(self) -> str: return "anthropic"

    def build_compaction_url(self, base_url): return base_url.rstrip("/") + "/v1/messages"
    def build_compaction_headers(self, api_key):
        # iFlytek MaaS (xf-yun.com) is an Anthropic-format proxy that requires
        # Authorization: Bearer instead of x-api-key. Include both headers so
        # it works with native Anthropic API AND iFlytek MaaS proxies.
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
    def build_compaction_payload(self, model, messages, max_tokens, temperature):
        # Anthropic Messages API: system is top-level, messages have no system role
        system_text = ""
        non_system = []
        for msg in messages:
            if msg.get("role") == "system":
                c = msg.get("content", "")
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                system_text += c + "\n"
            else:
                non_system.append(msg)
        return {"model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system_text.strip(), "messages": non_system}
    def extract_compaction_content(self, data):
        # Anthropic response: content[0].text
        # V6 fix: xsparkx2agent returns only thinking blocks with empty text in
        # non-streaming mode. Fall back to thinking content if no text blocks.
        blocks = data.get("content", [])
        text_parts = []
        thinking_parts = []
        for block in blocks:
            if block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    text_parts.append(t)
            elif block.get("type") == "thinking":
                t = block.get("text", "")
                if t:
                    thinking_parts.append(t)
        if text_parts:
            return "\n".join(text_parts)
        elif thinking_parts:
            return "\n".join(thinking_parts)
        return ""
    def detect_overflow(self, status_code, body):
        if status_code not in (400, 413, 500, 503): return False
        try:
            text = body.decode("utf-8", errors="replace").lower()
        except: return False
        # Anthropic-specific patterns
        anthropic_overflow = ["max_tokens_too_large", "too many tokens", "prompt is too long", "input is too long", "request too large"]
        # Also check generic patterns
        for p in OVERFLOW_PATTERNS:
            if p.search(text): return True
        for p in anthropic_overflow:
            if p in text: return True
        # Exclude non-overflow
        non_overflow = ["overloaded_error", "rate_limit", "authentication", "invalid_api_key", "permission_error"]
        if any(n in text for n in non_overflow): return False
        return False
    def extract_api_key(self, headers):
        # Anthropic uses x-api-key
        key = headers.get("x-api-key", headers.get("X-Api-Key", ""))
        if key: return key
        # Fallback to Bearer
        auth = headers.get("Authorization", headers.get("authorization", ""))
        if auth.startswith("Bearer "): return auth[7:]
        return ""
    def build_forward_headers(self, headers, api_key):
        # Ensure x-api-key is set
        h = dict(headers)
        if "x-api-key" not in h and "X-Api-Key" not in h:
            h["x-api-key"] = api_key
        return h
    def get_forward_path(self, original_path):
        # Anthropic agents send to /v1/messages
        return original_path


class GeminiProvider(ProviderAdapter):
    """Google Gemini API provider."""

    @property
    def name(self) -> str: return "gemini"

    def build_compaction_url(self, base_url):
        # Gemini uses x-goog-api-key header, model in URL path
        return base_url  # Will be constructed with model in URL
    def build_compaction_headers(self, api_key):
        # Auth via x-goog-api-key header (NOT query param — avoids leaking the
        # key into access logs / proxied URLs)
        return {"Content-Type": "application/json", "x-goog-api-key": api_key}
    def build_compaction_payload(self, model, messages, max_tokens, temperature):
        # Convert OpenAI messages to Gemini contents format
        system_text = ""
        contents = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                content = "\n".join(text_parts)
            if role == "system":
                system_text += content + "\n"
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
        if system_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}
        return payload
    def extract_compaction_content(self, data):
        # Gemini: candidates[0].content.parts[0].text
        try:
            return data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except (IndexError, KeyError, TypeError):
            return ""
    def detect_overflow(self, status_code, body):
        if status_code not in (400, 413, 500, 503): return False
        try:
            text = body.decode("utf-8", errors="replace").lower()
        except: return False
        gemini_overflow = ["exceeds the maximum number of tokens", "request too large", "resource exhausted"]
        for p in OVERFLOW_PATTERNS:
            if p.search(text): return True
        for p in gemini_overflow:
            if p in text: return True
        return False
    def extract_api_key(self, headers):
        # Gemini: prefer x-goog-api-key header, fall back to Bearer
        gkey = headers.get("x-goog-api-key", headers.get("X-Goog-Api-Key", ""))
        if gkey:
            return gkey
        auth = headers.get("Authorization", headers.get("authorization", ""))
        if auth.startswith("Bearer "): return auth[7:]
        return ""
    def build_forward_headers(self, headers, api_key):
        h = dict(headers)
        # Gemini auth via x-goog-api-key header (never Bearer / query param)
        h.pop("Authorization", None)
        h.pop("authorization", None)
        if api_key:
            h["x-goog-api-key"] = api_key
        return h
    def get_forward_path(self, original_path): return original_path


# ── V3: 两阶段 Token 估算 ──────────────────────────────────────────

# CJK Unicode 范围
CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
    (0x2A700, 0x2B73F), # CJK Extension C
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
]


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    for start, end in CJK_RANGES:
        if start <= cp <= end:
            return True
    return False


def estimate_tokens_v3(text: str) -> int:
    """
    V3: CJK 感知的 token 估算。
    - 英文/ASCII: ~4 chars/token
    - CJK: ~1.5 chars/token
    - 空白/标点: ~5 chars/token
    """
    cjk_chars = 0
    ascii_chars = 0
    other_chars = 0

    for ch in text:
        if _is_cjk(ch):
            cjk_chars += 1
        elif ord(ch) < 128:
            ascii_chars += 1
        else:
            other_chars += 1

    # CJK: ~1.5 chars/token, ASCII: ~4 chars/token, Other: ~2.5 chars/token
    tokens = cjk_chars / 1.5 + ascii_chars / 4.0 + other_chars / 2.5
    return max(1, int(tokens))


def estimate_messages_tokens(messages: list) -> int:
    """估算消息列表的 token 数（V3: CJK 感知）"""
    total = 0
    for msg in messages:
        # 每条消息有 ~4 token 的角色/格式开销
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens_v3(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        total += estimate_tokens_v3(block.get("text", ""))
                    elif btype == "tool_use":
                        total += estimate_tokens_v3(block.get("name", ""))
                        inp = block.get("input", {})
                        total += estimate_tokens_v3(json.dumps(inp, ensure_ascii=False))
                    elif btype == "tool_result":
                        result = block.get("content", "")
                        if isinstance(result, str):
                            total += estimate_tokens_v3(result)
                        elif isinstance(result, list):
                            for r in result:
                                if isinstance(r, dict) and r.get("type") == "text":
                                    total += estimate_tokens_v3(r.get("text", ""))
                    elif btype == "image":
                        # 图片约 1000-4000 tokens
                        total += 2500
        # tool_calls (OpenAI format)
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                total += estimate_tokens_v3(json.dumps(tc, ensure_ascii=False))
    return total


# 保留 V2 的简单估算作为快速预筛
def estimate_tokens_fast(messages: list) -> int:
    """快速估算：纯字符数/3，用于 Stage 1 预筛"""
    total_chars = len(json.dumps(messages, ensure_ascii=False))
    return total_chars // 3


def estimate_tokens_accurate(messages: list) -> int:
    """精确估算：CJK 感知逐字符计算，用于 Stage 2"""
    return estimate_messages_tokens(messages)


# ── 熔断器 ──────────────────────────────────────────────────────────

class CircuitBreaker:
    def __init__(self, failure_threshold=3, cooldown_seconds=60):
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_seconds

    def can_attempt(self):
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.cooldown:
                self.state = "half_open"
                return True
            return False
        return True

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def __repr__(self):
        return f"CircuitBreaker(state={self.state}, failures={self.failure_count})"


compaction_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)

# ── V3: 蠕变检测器 ──────────────────────────────────────────────────

class ThrashingDetector:
    """
    V3: 检测压缩蠕变 — 连续快速重填上下文窗口。
    灵感来自 Claude Code 的 thrashing detection。
    如果在 N 条消息内触发 M 次压缩，视为蠕变。
    """
    def __init__(self, max_compacts=THRASHING_COMPACTS, window_msgs=THRASHING_WINDOW_MSGS):
        self.max_compacts = max_compacts
        self.window_msgs = window_msgs
        self._compaction_times = []  # (timestamp, msg_count_at_time)
        self._msg_count = 0

    def record_message(self):
        self._msg_count += 1

    def record_compaction(self):
        self._compaction_times.append((time.time(), self._msg_count))
        # 清理旧记录
        cutoff = self._msg_count - self.window_msgs * 2
        self._compaction_times = [
            (t, c) for t, c in self._compaction_times if c > cutoff
        ]

    def is_thrashing(self) -> bool:
        """检查是否处于蠕变状态"""
        if len(self._compaction_times) < self.max_compacts:
            return False
        recent = [
            (t, c) for t, c in self._compaction_times
            if self._msg_count - c <= self.window_msgs
        ]
        return len(recent) >= self.max_compacts

    def reset(self):
        self._compaction_times.clear()
        self._msg_count = 0

    @property
    def status(self) -> dict:
        return {
            "msg_count": self._msg_count,
            "recent_compactions": len(self._compaction_times),
            "is_thrashing": self.is_thrashing(),
        }


thrashing_detector = ThrashingDetector()

# ── V3: ARC 地址化引用日志 ──────────────────────────────────────────

class ARCLog:
    """
    V3: Addressable Recall Compaction 日志。
    维护一个 append-only 的 tool_result 存储日志。
    压缩时用短 ID 替代冗长的 tool_result，
    LLM 可通过 ID 请求回查原始内容。
    (arXiv:2607.25066)
    """
    def __init__(self, max_entries=500):
        self._log = {}  # id -> content
        self._counter = 0
        self._max = max_entries

    def store(self, content: str, source_msg_idx: int = -1) -> str:
        """存储 tool_result 内容，返回短 ID"""
        self._counter += 1
        arc_id = f"ARC-{self._counter:04d}"
        entry = {
            "content": content,
            "source_idx": source_msg_idx,
            "stored_at": time.time(),
            "size": len(content),
        }
        self._log[arc_id] = entry
        # 淘汰最旧的
        if len(self._log) > self._max:
            oldest_key = min(self._log, key=lambda k: self._log[k]["stored_at"])
            del self._log[oldest_key]
        # V5 FIX: Persist ARC entry to SQLite for cross-restart survival
        if session_store:
            try:
                session_store.save_arc_entry(arc_id, content, source_msg_idx, entry["stored_at"], entry["size"])
            except Exception as e:
                logger.debug(f"ARC persist error (non-fatal): {e}")
        return arc_id

    def retrieve(self, arc_id: str) -> Optional[str]:
        """按 ID 回查原始内容"""
        entry = self._log.get(arc_id)
        return entry["content"] if entry else None

    def make_citation(self, arc_id: str, preview_len: int = 120) -> str:
        """生成引用文本：ID + 预览"""
        entry = self._log.get(arc_id)
        if not entry:
            return f"[{arc_id}: NOT FOUND]"
        content = entry["content"]
        size = entry["size"]
        preview = content[:preview_len].replace("\n", " ")
        if len(content) > preview_len:
            preview += "..."
        return f"[{arc_id} ({size} chars): {preview}]"

    @property
    def size(self):
        return len(self._log)

    def clear(self):
        self._log.clear()
        self._counter = 0


arc_log = ARCLog(max_entries=500)

# ── 压缩缓存 ──────────────────────────────────────────────────────────

class CompactionCache:
    def __init__(self, ttl_seconds=1800):
        self._cache = {}
        self._ttl = ttl_seconds

    def _make_key(self, messages: list, salt: str = "") -> str:
        raw = json.dumps(messages, sort_keys=True, ensure_ascii=False)
        if salt:
            raw = raw + "\x00" + salt
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, messages: list, salt: str = "") -> Optional[str]:
        key = self._make_key(messages, salt)
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        return value

    def put(self, messages: list, summary: str, salt: str = ""):
        key = self._make_key(messages, salt)
        self._cache[key] = (summary, time.time())

    def clear(self):
        self._cache.clear()

    @property
    def size(self):
        now = time.time()
        expired = [k for k, (_, ts) in self._cache.items() if now - ts > self._ttl]
        for k in expired:
            del self._cache[k]
        return len(self._cache)


compaction_cache = CompactionCache(ttl_seconds=1800)

# ── 指标 ──────────────────────────────────────────────────────────────

class Metrics:
    def __init__(self):
        self.counters = defaultdict(int)
        self.start_time = time.time()

    def inc(self, name: str, delta: int = 1):
        self.counters[name] += delta

    def get(self, name: str) -> int:
        return self.counters.get(name, 0)

    def snapshot(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": round(uptime, 1),
            "counters": dict(self.counters),
        }


metrics = Metrics()

# ── V4: Episodic-Semantic Dual-Layer Memory ──────────────────────────

class SemanticMemory:
    """V4: Episodic-Semantic Dual-Layer Memory (arXiv:2605.17625)"""
    def __init__(self, path=SEMANTIC_MEMORY_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._knowledge = {"goals": [], "decisions": [], "errors": [], "files": [], "constraints": [], "insights": []}
        self._load()

    def _load(self):
        try:
            with open(self._path) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    with self._lock:
                        self._knowledge = data
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(self._path), suffix='.tmp'
            )
            try:
                with self._lock:
                    snapshot = json.dumps(self._knowledge, ensure_ascii=False, indent=2)
                with os.fdopen(tmp_fd, 'w') as f:
                    f.write(snapshot)
                os.replace(tmp_path, self._path)  # atomic on POSIX
            except BaseException:
                # Clean up temp file on any error
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"Failed to save semantic memory: {e}")

    def extract_from_messages(self, messages, llm_result=None):
        """Extract knowledge from messages. Use LLM result if available, else regex."""
        with self._lock:
            if llm_result:
                # Merge LLM-extracted knowledge
                for key in ["goals", "decisions", "errors", "files", "constraints", "insights"]:
                    if key in llm_result:
                        for item in llm_result[key]:
                            if isinstance(item, str) and item not in self._knowledge.get(key, []):
                                self._knowledge.setdefault(key, []).append(item)
                # Bound each category
                for cat in self._knowledge:
                    self._knowledge[cat] = list(dict.fromkeys(self._knowledge[cat]))[-20:]
            else:
                # Fall back to regex-based extraction (existing code)
                commitments = extract_commitments(messages)
                cat_map = {"goal": "goals", "constraint": "constraints", "decision": "decisions", "error": "errors", "file_op": "files"}
                for ctype, text in commitments:
                    cat = cat_map.get(ctype, "goals")
                    lst = self._knowledge.setdefault(cat, [])
                    if text not in lst:
                        lst.append(text)
                for k in self._knowledge:
                    self._knowledge[k] = list(dict.fromkeys(self._knowledge[k]))[-20:]
        self._save()

    def format_for_prompt(self):
        with self._lock:
            knowledge = self._knowledge
            if not any(knowledge.values()):
                return ""
            lines = ["## PERSISTENT SEMANTIC MEMORY (survives across compactions)"]
            for cat, items in knowledge.items():
                if items:
                    lines.append(f"### {cat.title()}")
                    for item in items[-10:]:
                        lines.append(f"- {item}")
            return "\n".join(lines)

    def clear(self):
        with self._lock:
            self._knowledge = {"goals": [], "decisions": [], "errors": [], "files": [], "constraints": [], "insights": []}
        self._save()


semantic_memory = SemanticMemory()

# ── V7: MemSkill — Self-evolving Memory Skills ────────────────────────────

@dataclass
class CompactionSkill:
    """V7: A learnable, evolvable compaction skill (MemSkill arXiv:2602.02474)"""
    skill_id: str              # "error-pattern-boost-v1"
    version: int = 1           # Designer edits increment this
    status: str = "draft"      # "draft" | "active" | "deprecated" | "failed"

    # MemSkill standard fields
    description: str = ""      # "Boost importance of error/bug messages"
    purpose: str = ""          # "Improve retention of error context during compaction"
    when_to_use: str = ""      # "Conversation contains error/bug/fix/traceback indicators"
    how_to_apply: str = ""     # "Increase error message importance, extract error context to semantic memory"
    constraints: list = None   # ["importance weights must not exceed 0.9", "max 5 error patterns per compaction"]
    action_type: str = "UPDATE"  # "INSERT" | "UPDATE" | "DELETE" | "SKIP"

    # V7-specific: which pipeline stages this skill affects
    pipeline_stages: list = None  # ["importance_scoring", "semantic_extraction"]
    # V7-specific: parameter overrides for pipeline stages
    params: dict = None           # {"importance_weights": {"semantic": 0.60, ...}}
    # V7-specific: prompt modifications
    prompt_additions: dict = None # {"system_prompt_append": "..."}

    # Learning metadata
    usage_count: int = 0
    success_count: int = 0
    avg_reward: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    parent_skill_id: str = None

    def __post_init__(self):
        if self.constraints is None:
            self.constraints = []
        if self.pipeline_stages is None:
            self.pipeline_stages = []
        if self.params is None:
            self.params = {}
        if self.prompt_additions is None:
            self.prompt_additions = {}
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.updated_at == 0.0:
            self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id, "version": self.version, "status": self.status,
            "description": self.description, "purpose": self.purpose,
            "when_to_use": self.when_to_use, "how_to_apply": self.how_to_apply,
            "constraints": self.constraints, "action_type": self.action_type,
            "pipeline_stages": self.pipeline_stages, "params": self.params,
            "prompt_additions": self.prompt_additions,
            "usage_count": self.usage_count, "success_count": self.success_count,
            "avg_reward": self.avg_reward, "created_at": self.created_at,
            "updated_at": self.updated_at, "parent_skill_id": self.parent_skill_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompactionSkill":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Parameter bounds for safety validation
SKILL_PARAM_BOUNDS = {
    "importance_weights": {"semantic": (0.0, 1.0), "recency": (0.0, 1.0), "kind": (0.0, 1.0), "quality": (0.0, 1.0)},
    "fidelity_multipliers": {"full": (0.1, 3.0), "placeholder": (0.0, 1.5)},
    "keep_turns_override": (1, 20),
}


def _validate_skill_params(skill: CompactionSkill) -> list[str]:
    """Validate skill parameters against bounds. Returns list of violations."""
    violations = []
    for key, bounds in SKILL_PARAM_BOUNDS.items():
        if key not in skill.params:
            continue
        val = skill.params[key]
        if isinstance(bounds, tuple) and len(bounds) == 2:
            # Scalar bound
            if not (bounds[0] <= val <= bounds[1]):
                violations.append(f"{key}={val} not in [{bounds[0]}, {bounds[1]}]")
        elif isinstance(bounds, dict):
            # Dict of sub-key bounds
            for sub_key, (lo, hi) in bounds.items():
                if sub_key in val:
                    if not (lo <= val[sub_key] <= hi):
                        violations.append(f"{key}.{sub_key}={val[sub_key]} not in [{lo}, {hi}]")
    return violations


class SkillRegistry:
    """V7: MemSkill skill registry — CRUD + activation lifecycle + snapshot rollback"""

    def __init__(self, session_store=None):
        self._skills: dict[str, CompactionSkill] = {}
        self._session_store = session_store
        self._load_seed_skills()
        if session_store:
            self._load_from_db()

    def _load_seed_skills(self):
        """Load 5 seed skills encoding current V6 hardcoded behavior as explicit evolvable skills."""
        seeds = [
            CompactionSkill(
                skill_id="default-importance-scoring-v1", status="active",
                description="Default four-signal importance scoring weights",
                purpose="Encode V6's hardcoded importance weights as an evolvable skill",
                when_to_use="Every compaction (baseline skill)",
                how_to_apply="Apply semantic=0.50, recency=0.25, kind=0.15, quality=0.10 weights",
                constraints=["Weights must sum to ~1.0 after normalization"],
                action_type="UPDATE",
                pipeline_stages=["importance_scoring"],
                params={"importance_weights": {"semantic": 0.50, "recency": 0.25, "kind": 0.15, "quality": 0.10}},
            ),
            CompactionSkill(
                skill_id="error-pattern-boost-v1", status="active",
                description="Boost importance of messages containing error/bug/fix indicators",
                purpose="Improve retention of error context during compaction",
                when_to_use="Conversation contains error/bug/fix/traceback indicators",
                how_to_apply="Increase kind_score and quality_score for error messages",
                constraints=["Importance weights must not exceed 0.9"],
                action_type="UPDATE",
                pipeline_stages=["importance_scoring"],
                params={"importance_weights": {"kind": 0.25, "quality": 0.20}},
                prompt_additions={"system_prompt_append": "Pay special attention to error messages, bug reports, and fix attempts. Preserve full error context including stack traces and reproduction steps."},
            ),
            CompactionSkill(
                skill_id="afm-fidelity-selection-v1", status="active",
                description="AFM fidelity multipliers for submodular selection",
                purpose="Encode V6's hardcoded fidelity boost/penalty as evolvable parameters",
                when_to_use="Every compaction with AFM-classified messages",
                how_to_apply="FULL fidelity ×1.5, PLACEHOLDER fidelity ×0.5",
                constraints=["Multipliers must be positive", "FULL multiplier should be > PLACEHOLDER multiplier"],
                action_type="UPDATE",
                pipeline_stages=["submodular_selection"],
                params={"fidelity_multipliers": {"full": 1.5, "placeholder": 0.5}},
            ),
            CompactionSkill(
                skill_id="semantic-memory-extraction-v1", status="active",
                description="Extract 6 knowledge categories into semantic memory during compaction",
                purpose="Preserve key knowledge across compaction cycles",
                when_to_use="Every compaction with substantive content",
                how_to_apply="Run llm_extract_memory() to extract goals/decisions/errors/files/constraints/insights",
                constraints=["Max 10 items per category", "Extraction must not add >500 tokens overhead"],
                action_type="INSERT",
                pipeline_stages=["semantic_extraction"],
            ),
            CompactionSkill(
                skill_id="aggressive-truncation-fallback-v1", status="active",
                description="Skip LLM compaction when thrashing detected, use aggressive truncation instead",
                purpose="Prevent compaction thrashing from wasting LLM calls",
                when_to_use="Thrashing detected (3+ compactions within 5 messages)",
                how_to_apply="Skip semantic extraction and LLM compaction, use aggressive_truncate_messages()",
                constraints=["Only activates when thrashing_detector.is_thrashing()"],
                action_type="DELETE",
                pipeline_stages=["semantic_extraction", "llm_compaction"],
            ),
        ]
        for seed in seeds:
            if seed.skill_id not in self._skills:
                self._skills[seed.skill_id] = seed

    def _load_from_db(self):
        """Load skills from SQLite skills table."""
        if not self._session_store:
            return
        try:
            conn = self._session_store._conn
            rows = conn.execute("SELECT skill_id, skill_data FROM skills WHERE status != 'failed'").fetchall()
            for skill_id, skill_data in rows:
                try:
                    d = json.loads(skill_data)
                    skill = CompactionSkill.from_dict(d)
                    # DB skills override seed skills (they may have been evolved)
                    self._skills[skill_id] = skill
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to load skill {skill_id}: {e}")
            logger.info(f"MemSkill: loaded {len(rows)} skills from DB")
        except Exception as e:
            logger.warning(f"MemSkill DB load error (non-fatal, using seeds): {e}")

    def _save_to_db(self, skill: CompactionSkill):
        """Persist a skill to SQLite."""
        if not self._session_store:
            return
        try:
            conn = self._session_store._conn
            skill_data = json.dumps(skill.to_dict())
            conn.execute(
                "INSERT OR REPLACE INTO skills (skill_id, version, status, skill_data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (skill.skill_id, skill.version, skill.status, skill_data, skill.created_at, skill.updated_at),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"MemSkill DB save error for {skill.skill_id}: {e}")

    def get_active_skills(self) -> list[CompactionSkill]:
        return [s for s in self._skills.values() if s.status == "active"]

    def get_all_skills(self) -> list[CompactionSkill]:
        return list(self._skills.values())

    def get_skill(self, skill_id: str) -> Optional[CompactionSkill]:
        return self._skills.get(skill_id)

    def register_skill(self, skill: CompactionSkill) -> str:
        """Register a new skill (→ draft status). Returns skill_id."""
        skill.status = "draft"
        skill.created_at = time.time()
        skill.updated_at = time.time()
        self._skills[skill.skill_id] = skill
        self._save_to_db(skill)
        logger.info(f"MemSkill: registered new skill '{skill.skill_id}' as draft")
        return skill.skill_id

    def activate_skill(self, skill_id: str) -> bool:
        """Activate a draft skill after validation. Returns True on success."""
        skill = self._skills.get(skill_id)
        if not skill:
            return False
        if skill.status != "draft":
            logger.warning(f"MemSkill: cannot activate '{skill_id}' — status is '{skill.status}', expected 'draft'")
            return False
        # Validate parameters
        violations = _validate_skill_params(skill)
        if violations:
            logger.warning(f"MemSkill: activation blocked for '{skill_id}' — param violations: {violations}")
            skill.status = "failed"
            self._save_to_db(skill)
            return False
        # Snapshot before activation
        self.snapshot_skill(skill_id, "pre_activation")
        skill.status = "active"
        skill.updated_at = time.time()
        self._save_to_db(skill)
        logger.info(f"MemSkill: activated skill '{skill_id}' v{skill.version}")
        return True

    def deprecate_skill(self, skill_id: str) -> bool:
        """Deprecate an active skill."""
        skill = self._skills.get(skill_id)
        if not skill or skill.status != "active":
            return False
        self.snapshot_skill(skill_id, "pre_deprecation")
        skill.status = "deprecated"
        skill.updated_at = time.time()
        self._save_to_db(skill)
        logger.info(f"MemSkill: deprecated skill '{skill_id}'")
        return True

    def snapshot_skill(self, skill_id: str, reason: str) -> str:
        """Create a snapshot of a skill for rollback. Returns snapshot_id."""
        skill = self._skills.get(skill_id)
        if not skill:
            return ""
        snapshot_id = f"snap-{skill_id}-v{skill.version}-{int(time.time())}"
        if self._session_store:
            try:
                conn = self._session_store._conn
                conn.execute(
                    "INSERT INTO skill_snapshots (snapshot_id, skill_id, version, skill_data, snapshot_reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (snapshot_id, skill_id, skill.version, json.dumps(skill.to_dict()), reason, time.time()),
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"MemSkill snapshot error: {e}")
        return snapshot_id

    def rollback_skill(self, skill_id: str, target_version: int) -> bool:
        """Rollback a skill to a specific version from snapshots."""
        if not self._session_store:
            return False
        try:
            conn = self._session_store._conn
            row = conn.execute(
                "SELECT skill_data FROM skill_snapshots WHERE skill_id=? AND version=? ORDER BY created_at DESC LIMIT 1",
                (skill_id, target_version),
            ).fetchone()
            if not row:
                logger.warning(f"MemSkill: no snapshot found for '{skill_id}' v{target_version}")
                return False
            d = json.loads(row[0])
            skill = CompactionSkill.from_dict(d)
            skill.version = target_version + 1  # New version from rollback
            skill.updated_at = time.time()
            self._skills[skill_id] = skill
            self._save_to_db(skill)
            logger.info(f"MemSkill: rolled back '{skill_id}' to v{target_version} (now v{skill.version})")
            return True
        except Exception as e:
            logger.warning(f"MemSkill rollback error: {e}")
            return False

    def update_skill(self, skill_id: str, updates: dict) -> Optional[CompactionSkill]:
        """Update a skill with automatic snapshot. Returns updated skill or None."""
        skill = self._skills.get(skill_id)
        if not skill:
            return None
        # Snapshot before edit
        self.snapshot_skill(skill_id, "pre_designer_edit")
        # Apply updates
        for k, v in updates.items():
            if hasattr(skill, k):
                setattr(skill, k, v)
        skill.version += 1
        skill.updated_at = time.time()
        self._save_to_db(skill)
        logger.info(f"MemSkill: updated skill '{skill_id}' to v{skill.version}")
        return skill

    def update_skill_stats(self, skill_id: str, reward: float, success: bool):
        """Update usage/success/reward stats for a skill after compaction."""
        skill = self._skills.get(skill_id)
        if not skill:
            return
        skill.usage_count += 1
        if success:
            skill.success_count += 1
        # Exponential moving average for reward
        alpha = 0.1
        skill.avg_reward = skill.avg_reward * (1 - alpha) + reward * alpha
        skill.updated_at = time.time()
        self._save_to_db(skill)


# V7: MemSkill global registry (initialized in main())
skill_registry: Optional[SkillRegistry] = None

# ── V5: Multi-Layer Cached System Prompt ──────────────────────────────────

# Layer ordering: most stable (lowest index) to most volatile (highest index)
LAYER_ORDER = [
    "core_system",          # 1. Core system instructions (never changes)
    "user_profile",         # 2. User profile (changes rarely)
    "semantic_memory",      # 3. Semantic memory (changes on compaction)
    "background_sessions",  # 4. Background session knowledge (changes per session)
    "compaction_summary",   # 5. Compaction summary (changes on each compaction)
    "ccl_commitments",      # 6. CCL commitments (changes on each compaction)
    "identifier_preservation",  # 7. Identifier preservation list (changes on each compaction)
    "tool_retention",       # 8. Tool retention policy (static)
    "safety_instructions",  # 9. Safety instructions (static)
    "session_context",      # 10. Session-specific context (changes per request)
]


class CachedSystemPrompt:
    """
    V5: Multi-layer cached system prompt with prefix boundaries.
    Inspired by Hermes's 10-layer cached system prompt.

    Each layer has a content hash. When assembling the prompt,
    we compute a cumulative hash. If a layer's hash matches
    the previous request, everything up to that layer is a cache hit.

    This enables:
    1. Logical caching — skip re-computation of unchanged layers
    2. API-level prompt caching — inject cache_control breakpoints
       for Anthropic-style prompt caching at layer boundaries
    3. Metrics — track cache hit rates and estimated token savings
    """

    def __init__(self):
        self._layers = {}          # layer_name -> content (str)
        self._hashes = {}          # layer_name -> sha256 hex
        self._prev_hashes = {}     # layer_name -> previous sha256 (for diff detection)
        self._cache_boundary = -1  # Last layer index that was a cache hit (-1 = none)
        self._last_assembly_hash = None
        self._total_layers = len(LAYER_ORDER)
        # Metrics tracking
        self._total_assemblies = 0
        self._total_layer_hits = 0
        self._total_layer_misses = 0
        self._estimated_tokens_saved = 0

    def set_layer(self, name: str, content: str):
        """Set a layer's content and update its hash."""
        if name not in LAYER_ORDER:
            logger.warning(f"CachedSystemPrompt: unknown layer '{name}', ignoring")
            return
        self._layers[name] = content
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        self._hashes[name] = new_hash

    def get_layer(self, name: str) -> str:
        """Get a layer's content, or empty string if not set."""
        return self._layers.get(name, "")

    def assemble(self) -> str:
        """
        Assemble all layers into a single system prompt string.
        Tracks which layers changed since the last assembly for cache metrics.
        """
        parts = []
        hit_count = 0
        miss_count = 0
        boundary = -1

        for i, name in enumerate(LAYER_ORDER):
            content = self._layers.get(name, "")
            if not content:
                # Empty layer — still counts as a "match" if previously empty
                prev_hash = self._prev_hashes.get(name)
                curr_hash = self._hashes.get(name)
                if prev_hash is None and curr_hash is None:
                    # Both absent: implicit hit
                    hit_count += 1
                    if boundary == i - 1:
                        boundary = i
                continue

            parts.append(content)

            # Check cache hit
            curr_hash = self._hashes.get(name)
            prev_hash = self._prev_hashes.get(name)
            if curr_hash == prev_hash and prev_hash is not None:
                hit_count += 1
                if boundary == i - 1:
                    boundary = i
            else:
                miss_count += 1

        # Update metrics
        self._cache_boundary = boundary
        self._total_assemblies += 1
        self._total_layer_hits += hit_count
        self._total_layer_misses += miss_count

        # Estimate tokens saved: all layers up to cache boundary are a prefix hit
        if boundary >= 0:
            saved_text = ""
            for i in range(boundary + 1):
                name = LAYER_ORDER[i]
                saved_text += self._layers.get(name, "")
            self._estimated_tokens_saved += estimate_tokens_v3(saved_text)

        # Save current hashes as previous for next assembly
        self._prev_hashes = dict(self._hashes)

        # Compute assembly hash
        combined = "\n\n".join(parts)
        self._last_assembly_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

        return combined

    def get_cache_boundary(self) -> int:
        """Return the index of the last layer that matched the previous request, or -1."""
        return self._cache_boundary

    def get_cache_hit_rate(self) -> float:
        """Return the cache hit rate across all assemblies (0.0 to 1.0)."""
        total = self._total_layer_hits + self._total_layer_misses
        if total == 0:
            return 0.0
        return self._total_layer_hits / total

    def get_last_hit_rate(self) -> float:
        """Return the cache hit rate for the most recent assembly (0.0 to 1.0)."""
        active_layers = sum(1 for name in LAYER_ORDER if self._layers.get(name, "") != "")
        if active_layers == 0:
            return 0.0
        hits = self._cache_boundary + 1  # boundary is 0-indexed, so +1 layers hit
        if hits <= 0:
            return 0.0
        return min(1.0, hits / active_layers)

    def format_for_anthropic_api(self, base_content) -> list:
        """
        Format system message content with Anthropic cache_control breakpoints
        at layer boundaries. This enables actual API-level prompt caching.

        Args:
            base_content: The original system message content (str or list of blocks).
                If str, it will be split into blocks with cache_control markers.
                If list, cache_control markers will be injected at strategic points.

        Returns:
            List of content blocks with cache_control markers for Anthropic API.
        """
        # Build layer content blocks with cache breakpoints
        blocks = []

        for i, name in enumerate(LAYER_ORDER):
            content = self._layers.get(name, "")
            if not content:
                continue

            # Add this layer as a text block
            block = {"type": "text", "text": content}

            # Add cache_control breakpoint at layer boundaries
            # Anthropic allows up to 4 cache breakpoints per request,
            # so we place them at the most stable layer boundaries
            if i in (0, 2, 4, 6):
                block["cache_control"] = {"type": "ephemeral"}

            blocks.append(block)

        if not blocks:
            # No layers set — return base_content as-is
            if isinstance(base_content, str):
                return [{"type": "text", "text": base_content}]
            elif isinstance(base_content, list):
                return base_content
            return [{"type": "text", "text": str(base_content)}]

        # If base_content has existing text, prepend it as the first block
        if isinstance(base_content, str) and base_content:
            blocks.insert(0, {"type": "text", "text": base_content})
        elif isinstance(base_content, list):
            # Insert existing blocks before our layer blocks
            existing = []
            for b in base_content:
                if isinstance(b, dict) and b.get("type") == "text":
                    existing.append(b)
                else:
                    existing.append(b)
            blocks = existing + blocks

        return blocks

    def reset(self):
        """Reset all layer content and hashes (e.g., on session change)."""
        self._layers.clear()
        self._hashes.clear()
        self._prev_hashes.clear()
        self._cache_boundary = -1
        self._last_assembly_hash = None

    @property
    def stats(self) -> dict:
        """Return current cache statistics."""
        active_layers = sum(1 for name in LAYER_ORDER if self._layers.get(name, "") != "")
        return {
            "layers_total": self._total_layers,
            "layers_active": active_layers,
            "last_hit_rate": round(self.get_last_hit_rate(), 3),
            "cumulative_hit_rate": round(self.get_cache_hit_rate(), 3),
            "total_assemblies": self._total_assemblies,
            "total_layer_hits": self._total_layer_hits,
            "total_layer_misses": self._total_layer_misses,
            "estimated_tokens_saved": self._estimated_tokens_saved,
            "cache_boundary": self._cache_boundary,
        }


# Global instance
cached_system_prompt = CachedSystemPrompt()

# V5: Global component instances (initialized in main())
session_store = None
user_profile = None
hook_manager = None
compression_engine = None

# ── V5: Cross-Session Memory + FTS5 Search ──────────────────────────────

class SessionStore:
    """
    V5: Cross-Session Memory with FTS5 full-text search.
    Stores per-session compaction summaries and semantic memory snapshots.
    Replaces global _prior_summary / _last_compaction_msg_count with per-session persistence.
    """
    def __init__(self, db_path=None):
        self._db_path = db_path or SESSION_DB_PATH
        self._conn = None
        self._write_lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with FTS5"""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Sessions table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                summary TEXT,
                semantic_memory TEXT,
                msg_count INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
        """)
        # FTS5 virtual table for searching summaries
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                session_id, summary, semantic_memory,
                content=sessions, content_rowid=rowid
            )
        """)
        # Triggers to keep FTS in sync
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
                INSERT INTO sessions_fts(rowid, session_id, summary, semantic_memory)
                VALUES (new.rowid, new.session_id, new.summary, new.semantic_memory);
            END
        """)
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, session_id, summary, semantic_memory)
                VALUES ('delete', old.rowid, old.session_id, old.summary, old.semantic_memory);
            END
        """)
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, session_id, summary, semantic_memory)
                VALUES ('delete', old.rowid, old.session_id, old.summary, old.semantic_memory);
                INSERT INTO sessions_fts(rowid, session_id, summary, semantic_memory)
                VALUES (new.rowid, new.session_id, new.summary, new.semantic_memory);
            END
        """)
        # Prior summary per session (replaces global _prior_summary)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS prior_summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT,
                msg_count INTEGER DEFAULT 0,
                updated_at REAL
            )
        """)
        # ARC log persistence
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_entries (
                arc_id TEXT PRIMARY KEY,
                content TEXT,
                source_idx INTEGER DEFAULT -1,
                stored_at REAL,
                size INTEGER DEFAULT 0
            )
        """)
        # V5: Transcript persistence — add transcript column if missing (session resume)
        try:
            self._conn.execute("SELECT transcript FROM sessions LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("Adding transcript column to sessions table (migration)")
            self._conn.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")

        # V7: MemSkill tables — skills, skill_snapshots, trajectories
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                version INTEGER DEFAULT 1,
                status TEXT DEFAULT 'draft',
                skill_data TEXT,
                created_at REAL,
                updated_at REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                skill_id TEXT,
                version INTEGER,
                skill_data TEXT,
                snapshot_reason TEXT,
                created_at REAL,
                FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                trajectory_id TEXT PRIMARY KEY,
                session_id TEXT,
                timestamp REAL,
                message_count INTEGER,
                has_errors INTEGER DEFAULT 0,
                has_code INTEGER DEFAULT 0,
                token_pressure REAL,
                skills_activated TEXT,
                reward REAL,
                token_savings_ratio REAL,
                safety_passed INTEGER DEFAULT 1,
                compaction_succeeded INTEGER DEFAULT 1,
                llm_calls INTEGER DEFAULT 0,
                total_tokens_used INTEGER DEFAULT 0,
                context_summary TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trajectories_reward ON trajectories(reward)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trajectories_session ON trajectories(session_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_snapshots_skill ON skill_snapshots(skill_id)")

        self._conn.commit()

    def save_session(self, session_id, summary, semantic_memory_data, msg_count):
        """Save/update a session record"""
        with self._write_lock:
            now = time.time()
            sem_json = json.dumps(semantic_memory_data, ensure_ascii=False) if semantic_memory_data else ""
            self._conn.execute("""
                INSERT INTO sessions (session_id, summary, semantic_memory, msg_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary=excluded.summary,
                    semantic_memory=excluded.semantic_memory,
                    msg_count=excluded.msg_count,
                    updated_at=excluded.updated_at
            """, (session_id, summary, sem_json, msg_count, now, now))
            self._conn.commit()

    def search_sessions(self, query, limit=5):
        """FTS5 full-text search across session summaries"""
        try:
            cursor = self._conn.execute("""
                SELECT s.session_id, s.summary, s.msg_count, s.updated_at, rank
                FROM sessions_fts f JOIN sessions s ON f.session_id = s.session_id
                WHERE sessions_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))
            results = []
            for row in cursor:
                results.append({
                    "session_id": row[0],
                    "summary": row[1][:500] if row[1] else "",
                    "msg_count": row[2],
                    "updated_at": row[3],
                    "rank": row[4],
                })
            return results
        except Exception as e:
            logger.warning(f"FTS5 search error: {e}")
            return []

    def get_recent_sessions(self, n=5):
        """Get N most recently updated sessions"""
        cursor = self._conn.execute("""
            SELECT session_id, summary, msg_count, updated_at
            FROM sessions ORDER BY updated_at DESC LIMIT ?
        """, (n,))
        results = []
        for row in cursor:
            results.append({
                "session_id": row[0],
                "summary": row[1][:500] if row[1] else "",
                "msg_count": row[2],
                "updated_at": row[3],
            })
        return results

    def get_session(self, session_id):
        """Get a single session by ID"""
        cursor = self._conn.execute("""
            SELECT session_id, summary, semantic_memory, msg_count, created_at, updated_at
            FROM sessions WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        sem_data = None
        if row[2]:
            try:
                sem_data = json.loads(row[2])
            except Exception:
                pass
        return {
            "session_id": row[0],
            "summary": row[1],
            "semantic_memory": sem_data,
            "msg_count": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    def get_prior_summary(self, session_id):
        """Get per-session prior summary (replaces global _prior_summary)"""
        cursor = self._conn.execute(
            "SELECT summary, msg_count FROM prior_summaries WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if row:
            return row[0], row[1]  # (summary, msg_count)
        return None, 0

    def save_prior_summary(self, session_id, summary, msg_count):
        """Save per-session prior summary"""
        with self._write_lock:
            now = time.time()
            self._conn.execute("""
                INSERT INTO prior_summaries (session_id, summary, msg_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary=excluded.summary,
                    msg_count=excluded.msg_count,
                    updated_at=excluded.updated_at
            """, (session_id, summary, msg_count, now))
            self._conn.commit()

    def get_background_knowledge(self, n=3):
        """Get recent session summaries as background knowledge for new sessions"""
        cursor = self._conn.execute("""
            SELECT summary FROM sessions
            WHERE summary IS NOT NULL AND summary != ''
            ORDER BY updated_at DESC LIMIT ?
        """, (n,))
        summaries = [row[0] for row in cursor if row[0]]
        return summaries

    def save_transcript(self, session_id: str, messages: list):
        """V5: Save full conversation transcript for session resume"""
        transcript_json = json.dumps(messages, ensure_ascii=False)
        with self._write_lock:
            self._conn.execute(
                "UPDATE sessions SET transcript = ? WHERE session_id = ?",
                (transcript_json, session_id),
            )
            self._conn.commit()

    def get_transcript(self, session_id: str) -> Optional[list]:
        """V5: Load full conversation transcript for session resume"""
        cursor = self._conn.execute(
            "SELECT transcript FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return None
        return None

    def save_arc_entry(self, arc_id, content, source_idx=-1):
        """Persist ARC log entry to SQLite"""
        with self._write_lock:
            now = time.time()
            self._conn.execute("""
                INSERT OR REPLACE INTO arc_entries (arc_id, content, source_idx, stored_at, size)
                VALUES (?, ?, ?, ?, ?)
            """, (arc_id, content, source_idx, now, len(content)))
            self._conn.commit()

    def load_arc_entries(self):
        """Load all ARC entries from SQLite (for startup recovery)"""
        cursor = self._conn.execute("SELECT arc_id, content, source_idx, stored_at, size FROM arc_entries")
        entries = {}
        for row in cursor:
            entries[row[0]] = {
                "content": row[1],
                "source_idx": row[2],
                "stored_at": row[3],
                "size": row[4],
            }
        return entries

    @property
    def session_count(self):
        cursor = self._conn.execute("SELECT COUNT(*) FROM sessions")
        return cursor.fetchone()[0]

    def close(self):
        with self._write_lock:
            if self._conn:
                self._conn.close()

# ── V5: User Profile Memory ──────────────────────────────────────────────

class UserProfile:
    """
    V5: User Profile Memory — persistent user profile (like Hermes USER.md).
    Injected into system prompt as a frozen snapshot for cache stability.
    """
    def __init__(self, path=None):
        self._path = path or USER_PROFILE_PATH
        self._content = ""
        self._snapshot = ""  # Frozen snapshot for injection
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._path):
                with open(self._path) as f:
                    self._content = f.read().strip()
                self._snapshot = self._content
        except Exception as e:
            logger.warning(f"Failed to load user profile: {e}")

    def save(self, content: str):
        """Save user profile content"""
        self._content = content.strip()
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, 'w') as f:
                f.write(self._content)
            # Update snapshot
            self._snapshot = self._content
        except Exception as e:
            logger.error(f"Failed to save user profile: {e}")

    def load(self) -> str:
        """Reload from disk and return content"""
        self._load()
        return self._content

    def format_for_prompt(self) -> str:
        """Return injection text for system prompt, or empty string"""
        if not self._snapshot:
            return ""
        return f"\n\n## USER PROFILE (persistent)\n{self._snapshot}"

    @property
    def has_profile(self) -> bool:
        return bool(self._snapshot)

    @property
    def size(self) -> int:
        return len(self._snapshot)

# ── V5: Orphan Tool Pair Sanitization ────────────────────────────────────

def sanitize_tool_pairs(messages: list) -> list:
    """
    V5: Orphan Tool Pair Sanitization — ensure tool_call/tool_result pairs are complete.
    After compaction, some tool_calls may lose their tool_results or vice versa.
    This function:
    1. Collects all tool_call IDs from assistant messages
    2. Removes tool_results that reference non-existent tool_calls
    3. Injects placeholder tool_results for tool_calls missing their results
    """
    # Step 1: Collect all tool_call IDs and their positions
    tool_call_ids = set()
    tool_call_positions = {}  # id -> (msg_idx, call_idx)
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                for j, block in enumerate(content):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tc_id = block.get("id", "")
                        if tc_id:
                            tool_call_ids.add(tc_id)
                            tool_call_positions[tc_id] = (i, j)
            # Also check OpenAI-format tool_calls
            for j, tc in enumerate(msg.get("tool_calls", [])):
                tc_id = tc.get("id", "")
                if tc_id:
                    tool_call_ids.add(tc_id)
                    tool_call_positions[tc_id] = (i, j)

    # Step 2: Collect all tool_result IDs
    tool_result_ids = set()
    tool_result_msg_indices = []  # (msg_idx, block_idx, tool_use_id)
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_use_id = msg.get("tool_call_id", "")
            if tool_use_id:
                tool_result_ids.add(tool_use_id)
                tool_result_msg_indices.append((i, None, tool_use_id))
        content = msg.get("content", "")
        if isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    if tool_use_id:
                        tool_result_ids.add(tool_use_id)
                        tool_result_msg_indices.append((i, j, tool_use_id))

    # Step 3: Find orphans
    orphan_result_ids = tool_result_ids - tool_call_ids  # Results without calls
    missing_result_ids = tool_call_ids - tool_result_ids  # Calls without results

    if not orphan_result_ids and not missing_result_ids:
        return messages  # No orphans, return as-is

    result = list(messages)  # Shallow copy
    sanitized_count = 0

    # Step 4: Remove orphan tool_results
    if orphan_result_ids:
        new_result = []
        for i, msg in enumerate(result):
            new_msg = msg
            modified = False

            # OpenAI format: role="tool" with tool_call_id
            if msg.get("role") == "tool" and msg.get("tool_call_id") in orphan_result_ids:
                continue  # Skip this orphan tool result entirely

            # Anthropic format: content list with tool_result blocks
            content = msg.get("content", "")
            if isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("tool_use_id") in orphan_result_ids:
                            modified = True
                            sanitized_count += 1
                            continue  # Skip orphan
                    new_blocks.append(block)
                if modified:
                    new_msg = dict(msg)
                    new_msg["content"] = new_blocks
            new_result.append(new_msg)
        result = new_result

    # Step 5: Inject placeholder tool_results for missing results
    # V5 FIX: Process in descending order of msg_idx to avoid index drift from insertions
    if missing_result_ids:
        # Build list of (msg_idx, tc_id) pairs and sort descending
        insertions = []
        for tc_id in missing_result_ids:
            if tc_id in tool_call_positions:
                msg_idx, _ = tool_call_positions[tc_id]
                insertions.append((msg_idx, tc_id))
        # Sort descending by msg_idx so insertions don't shift earlier positions
        insertions.sort(key=lambda x: x[0], reverse=True)

        for msg_idx, tc_id in insertions:
            placeholder = {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": "[Tool result removed during compaction — context preserved in summary]",
            }
            # Find insertion point (after the assistant message with the tool_call)
            insert_idx = msg_idx + 1
            # Make sure we don't insert in the middle of a multi-tool-call sequence
            while insert_idx < len(result) and result[insert_idx].get("role") == "tool":
                insert_idx += 1
            result.insert(insert_idx, placeholder)
            sanitized_count += 1

    if sanitized_count > 0:
        metrics.inc("tool_pairs_sanitized", sanitized_count)
        logger.info(f"Tool pair sanitization: {sanitized_count} orphan pairs fixed")

    return result

def extract_session_id(body: dict, headers: dict) -> str:
    """
    V5: Extract session identifier from request body or headers.
    Priority: header > body metadata > conversation_id > stable content hash > UUID.
    """
    # 1. Try explicit session/conversation ID from headers
    for key in ("X-Session-Id", "X-Session-ID", "X-Conversation-Id", "x-session-id"):
        if key in headers:
            return headers[key][:64]

    # 1b. Try cookie (set by POST /session endpoint)
    cookie_header = headers.get("Cookie", headers.get("cookie", ""))
    if cookie_header:
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("X-Session-Id="):
                return part.split("=", 1)[1][:64]

    # 2. Try from body metadata
    metadata = body.get("metadata", {})
    if isinstance(metadata, dict):
        for key in ("session_id", "session"):
            if key in metadata and metadata[key]:
                return str(metadata[key])[:64]

    # 3. Try conversation_id in request body (some APIs provide this)
    for key in ("conversation_id", "conversationId"):
        val = body.get(key)
        if val:
            return str(val)[:64]

    # 4. Fallback: stable hash of (model + system message fingerprint + first 3 user messages)
    #    This is more stable than hashing just the first user message, which changes on edits.
    messages = body.get("messages", [])
    fingerprint_parts = []

    # Include model for differentiation
    model = body.get("model", "")
    if model:
        fingerprint_parts.append(f"model:{model}")

    # Include system message fingerprint (hash of system content)
    for msg in messages:
        if msg.get("role") == "system":
            sys_content = msg.get("content", "")
            if isinstance(sys_content, str):
                fingerprint_parts.append(f"sys:{hashlib.sha256(sys_content.encode()).hexdigest()[:8]}")
            elif isinstance(sys_content, list):
                sys_text = ""
                for block in sys_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        sys_text += block.get("text", "")
                if sys_text:
                    fingerprint_parts.append(f"sys:{hashlib.sha256(sys_text.encode()).hexdigest()[:8]}")
            break  # only first system message

    # Include first 3 user messages for stability (not just 1)
    user_count = 0
    for msg in messages:
        if msg.get("role") == "user" and user_count < 3:
            content = msg.get("content", "")
            if isinstance(content, str):
                fingerprint_parts.append(f"u{user_count}:{content[:200]}")
            elif isinstance(content, list):
                text = ""
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
                fingerprint_parts.append(f"u{user_count}:{text[:200]}")
            user_count += 1

    if fingerprint_parts:
        fingerprint = "|".join(fingerprint_parts)
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]

    # 5. Last resort: generate a UUID (caller should echo it back via header/cookie)
    return f"gen-{uuid.uuid4().hex[:16]}"

# ── 日志设置 ──────────────────────────────────────────────────────────
logger = logging.getLogger("compaction-proxy")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
logger.addHandler(sh)
if LOG_FILE:
    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

# ── V5: Pre/Post Compaction Hooks ────────────────────────────────────────

class HookManager:
    """
    V5: Pre/Post Compaction Hooks — inspired by Claude Code's PreCompact/PostCompact hooks.
    Supports HTTP webhook hooks that can:
    - Pre-compact: inspect/modify messages before compaction, or block compaction
    - Post-compact: inspect/modify summary after compaction
    Hooks are non-blocking: errors are logged but don't stop the main flow.
    """
    def __init__(self, pre_url=None, post_url=None, timeout=5):
        self._pre_url = pre_url or PRE_COMPACT_HOOK_URL
        self._post_url = post_url or POST_COMPACT_HOOK_URL
        self._timeout = timeout or HOOK_TIMEOUT
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def call_pre_compact(self, messages, metadata=None):
        """
        Call pre-compact hook. Returns (messages, should_proceed).
        If hook returns should_proceed=False, compaction is blocked.
        If hook fails or is not configured, returns (messages, True).
        """
        if not self._pre_url:
            return messages, True

        try:
            session = await self._get_session()
            payload = {
                "hook_event": "pre_compact",
                "messages": messages,
                "metadata": metadata or {},
                "timestamp": time.time(),
            }
            async with session.post(
                self._pre_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Hook can return modified messages
                    modified_messages = data.get("messages", messages)
                    should_proceed = data.get("continue", True)
                    if not should_proceed:
                        logger.info(f"Pre-compact hook blocked compaction: {data.get('stopReason', 'no reason')}")
                    metrics.inc("hooks_pre_compact_called")
                    return modified_messages, should_proceed
                else:
                    logger.warning(f"Pre-compact hook returned HTTP {resp.status}")
                    return messages, True
        except asyncio.TimeoutError:
            logger.warning(f"Pre-compact hook timeout ({self._timeout}s)")
            return messages, True
        except Exception as e:
            logger.warning(f"Pre-compact hook error (non-fatal): {e}")
            return messages, True

    async def call_post_compact(self, summary, messages, metadata=None):
        """
        Call post-compact hook. Returns possibly modified summary.
        If hook fails or is not configured, returns original summary.
        """
        if not self._post_url:
            return summary

        try:
            session = await self._get_session()
            payload = {
                "hook_event": "post_compact",
                "summary": summary,
                "messages_count": len(messages),
                "metadata": metadata or {},
                "timestamp": time.time(),
            }
            async with session.post(
                self._post_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    modified_summary = data.get("summary", summary)
                    metrics.inc("hooks_post_compact_called")
                    return modified_summary
                else:
                    logger.warning(f"Post-compact hook returned HTTP {resp.status}")
                    return summary
        except asyncio.TimeoutError:
            logger.warning(f"Post-compact hook timeout ({self._timeout}s)")
            return summary
        except Exception as e:
            logger.warning(f"Post-compact hook error (non-fatal): {e}")
            return summary

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

# ── V5: Pluggable Compression Engine ─────────────────────────────────────

class CompressionEngine(ABC):
    """
    V5: Pluggable Compression Engine ABC — inspired by Hermes ContextEngine ABC.
    Allows swapping out the compression strategy via environment variable.
    Subclasses MUST implement name, should_compress, and compress.
    """
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the engine name for logging/metrics"""
        ...

    @abstractmethod
    def should_compress(self, messages, model) -> bool:
        """Determine if compression is needed"""
        ...

    @abstractmethod
    async def compress(self, messages, api_key, session, session_id="default",
                       hook_manager=None, session_store=None, user_profile=None,
                       selected_skills=None):
        """
        Execute the full compression pipeline.
        Returns (compacted_messages, summary) or (None, None) on failure.
        selected_skills: V7 MemSkill — list of CompactionSkill to apply (None = no skills).
        """
        ...


class DefaultCompressionEngine(CompressionEngine):
    """V5: Default compression engine — wraps the existing V4 compaction flow"""
    @property
    def name(self) -> str:
        return "default"

    def should_compress(self, messages, model) -> bool:
        context_limit = get_model_context_limit(model)
        usable_limit = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
        threshold = int(usable_limit * PREEMPTIVE_THRESHOLD)
        fast_est = estimate_tokens_fast(messages)
        if fast_est < threshold * 0.7:
            return False
        accurate_est = estimate_tokens_accurate(messages)
        return accurate_est > threshold

    async def compress(self, messages, api_key, session, session_id="default",
                       hook_manager=None, session_store=None, user_profile=None,
                       selected_skills=None):
        body = {"messages": messages, "model": COMPACTION_MODEL}
        if selected_skills:
            body["_memskill_selected"] = selected_skills
        compacted = await do_compaction(body, api_key, session, is_preemptive=True, session_id=session_id)
        if compacted is not None:
            return compacted.get("messages", messages), None
        return None, None


class DualLayerCompressor(CompressionEngine):
    """
    V5: Dual-layer compression inspired by Hermes gateway+agent architecture.
    Layer 1 (Gateway): Aggressive compression — keep only 15% of old messages (85% reduction).
    Layer 2 (Agent): Moderate compression — keep 50% of Layer 1 output.
    If Layer 1 succeeds and the result fits within the token budget, skip Layer 2.
    If Layer 1 output still exceeds the threshold, apply Layer 2 as a second independent pass.
    This provides defense-in-depth: if one layer fails, the other can still save the conversation.
    """

    @property
    def name(self) -> str:
        return "dual-layer"

    def should_compress(self, messages, model) -> bool:
        """Same threshold logic as DefaultCompressionEngine"""
        context_limit = get_model_context_limit(model)
        usable_limit = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
        threshold = int(usable_limit * PREEMPTIVE_THRESHOLD)
        fast_est = estimate_tokens_fast(messages)
        if fast_est < threshold * 0.7:
            return False
        accurate_est = estimate_tokens_accurate(messages)
        return accurate_est > threshold

    def _messages_still_over_threshold(self, messages, model) -> bool:
        """Check if compacted messages still exceed the preemptive threshold"""
        context_limit = get_model_context_limit(model)
        usable_limit = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
        threshold = int(usable_limit * PREEMPTIVE_THRESHOLD)
        accurate_est = estimate_tokens_accurate(messages)
        return accurate_est > threshold

    async def _run_compaction_layer(
        self, messages, api_key, session, session_id, keep_turns, layer_name,
        selected_skills=None
    ):
        """
        Run a single compaction layer using the existing do_compaction pipeline.
        Returns (compacted_messages, summary_text) or (None, None) on failure.
        """
        # Split messages with the specified keep_turns
        old_msgs, recent_msgs = split_messages(messages, keep_turns)

        if not old_msgs:
            logger.warning(f"[{layer_name}] No old messages to compact after split (keep_turns={keep_turns})")
            return None, None

        # V5: LLM-driven memory extraction (if enabled)
        llm_memory = None
        if LLM_MEMORY_EXTRACTION and semantic_memory:
            try:
                llm_memory = await llm_extract_memory(
                    old_msgs, api_key, session,
                    existing_memory=semantic_memory._knowledge if semantic_memory else None,
                )
                if llm_memory:
                    metrics.inc("llm_memory_extraction_success")
            except Exception as e:
                logger.debug(f"[{layer_name}] LLM memory extraction failed (non-fatal): {e}")
                metrics.inc("llm_memory_extraction_fallback")

        # Extract knowledge into semantic memory before compaction
        if semantic_memory:
            try:
                semantic_memory.extract_from_messages(old_msgs, llm_result=llm_memory)
                metrics.inc("semantic_memory_extraction")
            except Exception as e:
                logger.warning(f"[{layer_name}] Semantic memory extraction error (non-fatal): {e}")

        # ARC citation replacement
        old_msgs = apply_arc_citations(old_msgs)
        metrics.inc("arc_citations_applied")

        # V3/V6: AFM adaptive fidelity classification — mark each message with fidelity level
        old_msgs = apply_afm_fidelity(old_msgs)

        # CCL commitment extraction
        commitments = extract_commitments(old_msgs)

        # Submodular selection
        # Use the COMPACTION_MODEL for context limit since that's what compaction runs on
        context_limit = get_model_context_limit(COMPACTION_MODEL)
        recent_tokens = estimate_tokens_accurate(recent_msgs)
        summary_reserve = 2000
        old_budget = context_limit - recent_tokens - RESPONSE_BUDGET - SAFETY_MARGIN - summary_reserve
        old_budget = max(500, old_budget)

        selected_old = submodular_select(old_msgs, old_budget, commitments, semantic_memory,
                                         selected_skills=selected_skills)
        if len(selected_old) < len(old_msgs):
            logger.info(
                f"[{layer_name}] Submodular selection: {len(old_msgs)} -> {len(selected_old)} messages "
                f"(budget={old_budget} tokens, recent={recent_tokens} tokens)"
            )
            metrics.inc("submodular_selection_applied")

        # Parallel block compaction or single compaction
        if len(selected_old) >= 6:
            summary = await compact_messages_parallel(
                selected_old, api_key, session, session_id=session_id,
                selected_skills=selected_skills
            )
        else:
            summary = await compact_messages(
                selected_old, api_key, session, session_id=session_id,
                selected_skills=selected_skills
            )

        if not summary:
            logger.error(f"[{layer_name}] Compaction failed — no summary generated")
            return None, None

        # Build compacted message list
        compacted = build_compacted_messages(summary, recent_msgs)

        # Orphan tool pair sanitization
        compacted = sanitize_tool_pairs(compacted)

        logger.info(
            f"[{layer_name}] Compacted: {len(old_msgs)} old -> summary + "
            f"{len(recent_msgs)} recent = {len(compacted)} total"
        )

        return compacted, summary

    async def compress(self, messages, api_key, session, session_id="default",
                       hook_manager=None, session_store=None, user_profile=None,
                       selected_skills=None):
        """
        Execute dual-layer compression.
        Layer 1: Aggressive gateway compression (keep only 15% of old messages).
        Layer 2: If still over threshold, moderate agent compression (keep 50% of L1 output).
        """
        original_model = COMPACTION_MODEL

        # V7: MemSkill skill selection for dual-layer path
        if selected_skills is None and MEMSKILL_ENABLED and skill_controller:
            try:
                compaction_ctx = CompactionContext.from_messages(
                    messages, original_model, session_id, is_preemptive=True
                )
                selected_skills = skill_controller.select_skills(compaction_ctx)
                if selected_skills:
                    logger.info(f"MemSkill: {len(selected_skills)} skills selected for dual-layer session {session_id}")
            except Exception as e:
                logger.warning(f"MemSkill skill selection failed in dual-layer (non-fatal): {e}")
                selected_skills = None

        # ── Layer 1: Gateway (aggressive) ──
        # Calculate keep_turns for aggressive compression: keep only gateway_ratio of turns
        # Minimum 2 turns to preserve some recent context
        total_turns = sum(1 for m in messages if m.get("role") == "user")
        l1_keep_turns = max(2, int(total_turns * DUAL_LAYER_GATEWAY_RATIO))

        logger.info(
            f"[dual-layer] Layer 1 (Gateway): aggressive compression, "
            f"keep_turns={l1_keep_turns} (ratio={DUAL_LAYER_GATEWAY_RATIO}, "
            f"total_user_turns={total_turns})"
        )

        # Pre-compact hook for Layer 1
        if hook_manager:
            messages, should_proceed = await hook_manager.call_pre_compact(
                messages, {
                    "session_id": session_id, "model": original_model,
                    "preemptive": True, "layer": 1,
                }
            )
            if not should_proceed:
                logger.info("[dual-layer] Layer 1 blocked by pre-compact hook")
                metrics.inc("compaction_blocked_by_hook")
                return None, None

        l1_compacted, l1_summary = await self._run_compaction_layer(
            messages, api_key, session, session_id,
            keep_turns=l1_keep_turns, layer_name="L1-Gateway",
            selected_skills=selected_skills
        )

        if l1_compacted is None:
            logger.warning("[dual-layer] Layer 1 failed, cannot proceed to Layer 2")
            metrics.inc("dual_layer_l1_failed")
            return None, None

        metrics.inc("dual_layer_l1_success")

        # Check if Layer 1 result fits within the threshold
        if not self._messages_still_over_threshold(l1_compacted, original_model):
            logger.info(
                "[dual-layer] Layer 1 succeeded and fits within threshold — skipping Layer 2"
            )
            # Save session
            if session_store and l1_summary:
                try:
                    sem_data = semantic_memory._knowledge if semantic_memory else {}
                    session_store.save_session(session_id, l1_summary, sem_data, len(messages))
                    metrics.inc("sessions_saved")
                except Exception as e:
                    logger.warning(f"[dual-layer] Session save error (non-fatal): {e}")

            # Post-compact hook
            if hook_manager:
                l1_summary = await hook_manager.call_post_compact(
                    l1_summary, messages, {"session_id": session_id, "layer": 1}
                )

            return l1_compacted, l1_summary

        # ── Layer 2: Agent (moderate) ──
        # Layer 1 output still exceeds threshold — apply second compression pass
        l2_keep_turns = max(2, int(total_turns * DUAL_LAYER_AGENT_RATIO))

        logger.info(
            f"[dual-layer] Layer 2 (Agent): moderate compression on L1 output, "
            f"keep_turns={l2_keep_turns} (ratio={DUAL_LAYER_AGENT_RATIO})"
        )
        metrics.inc("dual_layer_l2_used")

        # Pre-compact hook for Layer 2
        if hook_manager:
            l1_compacted, should_proceed = await hook_manager.call_pre_compact(
                l1_compacted, {
                    "session_id": session_id, "model": original_model,
                    "preemptive": True, "layer": 2,
                }
            )
            if not should_proceed:
                logger.info("[dual-layer] Layer 2 blocked by pre-compact hook — returning L1 result")
                # Still return L1 result since it's better than nothing
                if session_store and l1_summary:
                    try:
                        sem_data = semantic_memory._knowledge if semantic_memory else {}
                        session_store.save_session(session_id, l1_summary, sem_data, len(messages))
                        metrics.inc("sessions_saved")
                    except Exception as e:
                        logger.warning(f"[dual-layer] Session save error (non-fatal): {e}")
                return l1_compacted, l1_summary

        l2_compacted, l2_summary = await self._run_compaction_layer(
            l1_compacted, api_key, session, session_id,
            keep_turns=l2_keep_turns, layer_name="L2-Agent",
            selected_skills=selected_skills
        )

        if l2_compacted is None:
            # Layer 2 failed — return Layer 1 result as fallback
            logger.warning(
                "[dual-layer] Layer 2 failed — falling back to Layer 1 result"
            )
            metrics.inc("dual_layer_l2_failed")
            if session_store and l1_summary:
                try:
                    sem_data = semantic_memory._knowledge if semantic_memory else {}
                    session_store.save_session(session_id, l1_summary, sem_data, len(messages))
                    metrics.inc("sessions_saved")
                except Exception as e:
                    logger.warning(f"[dual-layer] Session save error (non-fatal): {e}")
            return l1_compacted, l1_summary

        metrics.inc("dual_layer_l2_success")

        # Safety verification: ensure dual-layer result is actually smaller than original
        if not verify_compaction_safety(
            {"messages": messages}, {"messages": l2_compacted}
        ):
            logger.warning(
                "[dual-layer] Dual-layer result failed safety check — "
                "falling back to aggressive truncation"
            )
            metrics.inc("dual_layer_safety_failed")
            context_limit = get_model_context_limit(original_model)
            target = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
            truncated = aggressive_truncate_messages(messages, target)
            return truncated, l2_summary

        # Save session with Layer 2 summary
        if session_store and l2_summary:
            try:
                sem_data = semantic_memory._knowledge if semantic_memory else {}
                session_store.save_session(session_id, l2_summary, sem_data, len(messages))
                metrics.inc("sessions_saved")
            except Exception as e:
                logger.warning(f"[dual-layer] Session save error (non-fatal): {e}")

        # Post-compact hook for Layer 2
        if hook_manager:
            l2_summary = await hook_manager.call_post_compact(
                l2_summary, messages, {"session_id": session_id, "layer": 2}
            )

        l1_tokens = estimate_tokens_accurate(l1_compacted)
        l2_tokens = estimate_tokens_accurate(l2_compacted)
        orig_tokens = estimate_tokens_accurate(messages)
        logger.info(
            f"[dual-layer] Complete: original={orig_tokens} -> L1={l1_tokens} -> L2={l2_tokens} tokens "
            f"(total reduction: {100*(1-l2_tokens/orig_tokens):.1f}%)"
        )

        return l2_compacted, l2_summary


def load_compression_engine():
    """V5: Load compression engine based on COMPRESSION_ENGINE env var"""
    engine_name = COMPRESSION_ENGINE.lower().strip()
    if engine_name == "default" or not engine_name:
        return DefaultCompressionEngine()
    if engine_name == "dual-layer":
        logger.info(
            f"Loading dual-layer compression engine "
            f"(gateway_ratio={DUAL_LAYER_GATEWAY_RATIO}, agent_ratio={DUAL_LAYER_AGENT_RATIO})"
        )
        return DualLayerCompressor()
    # Try to load custom engine module
    try:
        parts = engine_name.rsplit(".", 1)
        if len(parts) == 2:
            module_path, class_name = parts
            import importlib
            module = importlib.import_module(module_path)
            engine_cls = getattr(module, class_name)
            engine = engine_cls()
            logger.info(f"Loaded custom compression engine: {engine.name} from {engine_name}")
            return engine
        else:
            logger.warning(f"Invalid engine spec '{engine_name}', using default. Format: module.ClassName or 'dual-layer'")
            return DefaultCompressionEngine()
    except Exception as e:
        logger.error(f"Failed to load compression engine '{engine_name}': {e}, using default")
        return DefaultCompressionEngine()


# ── V7: MemSkill Controller + CompactionContext ───────────────────────────

@dataclass
class CompactionContext:
    """V7: Context snapshot for skill selection — built at start of do_compaction()"""
    session_id: str
    model: str
    message_count: int
    has_errors: bool
    has_code: bool
    has_tool_calls: bool
    token_pressure: float      # current_tokens / context_limit
    is_thrashing: bool
    is_preemptive: bool
    description: str           # Concatenated text for keyword matching
    relevant_stages: list      # Pipeline stages that will run

    @classmethod
    def from_messages(cls, messages, model, session_id, is_preemptive=False, is_thrashing=False):
        """Build context from the current message list."""
        content_parts = []
        has_errors = False
        has_code = False
        has_tool_calls = False
        error_indicators = ["error", "错误", "bug", "fix", "修复", "failed", "exception", "traceback"]

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            content_str = ""
            if isinstance(content, str):
                content_str = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content_str += block.get("text", "") + " "
                    elif isinstance(block, dict) and block.get("type") == "tool_use":
                        has_tool_calls = True
            content_lower = content_str.lower()
            if any(ind in content_lower for ind in error_indicators):
                has_errors = True
            if "```" in content_str or "def " in content_str or "class " in content_str:
                has_code = True
            # Truncate to avoid huge descriptions
            content_parts.append(content_str[:200])

        context_limit = get_model_context_limit(model)
        current_tokens = estimate_tokens_fast(messages)
        token_pressure = current_tokens / context_limit if context_limit > 0 else 0.0

        # Determine which pipeline stages will run
        relevant_stages = ["importance_scoring", "submodular_selection", "llm_compaction"]
        if not is_thrashing:
            relevant_stages.append("semantic_extraction")

        return cls(
            session_id=session_id,
            model=model,
            message_count=len(messages),
            has_errors=has_errors,
            has_code=has_code,
            has_tool_calls=has_tool_calls,
            token_pressure=token_pressure,
            is_thrashing=is_thrashing,
            is_preemptive=is_preemptive,
            description=" ".join(content_parts)[:2000],
            relevant_stages=relevant_stages,
        )


class SkillController:
    """V7: MemSkill Controller — selects skills for each compaction via keyword matching + Gumbel-Top-K"""

    def __init__(self, registry: SkillRegistry, exploration_tau: float = MEMSKILL_EXPLORATION_TAU):
        self._registry = registry
        self._tau = exploration_tau
        self._step_count = 0
        self._decay_steps = 50  # τ decays to 0 over this many steps

    def select_skills(self, context: CompactionContext) -> list[CompactionSkill]:
        """Select skills relevant to the current compaction context.
        Phase 1: keyword matching + historical success rate.
        Phase 2 (future): learned embeddings + Gumbel-Top-K sampling.
        """
        active_skills = self._registry.get_active_skills()
        if not active_skills:
            return []

        scored = []
        for skill in active_skills:
            score = self._compute_relevance(skill, context)
            scored.append((score, skill))

        # Sort by relevance score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Gumbel-Top-K sampling with exploration temperature decay
        # Phase 1: deterministic top-K when τ=0, stochastic when τ>0
        current_tau = self._tau * max(0, 1 - self._step_count / self._decay_steps)
        self._step_count += 1

        if current_tau > 0.01 and len(scored) > 1:
            # Add Gumbel noise for exploration
            import random
            gumbel_scores = []
            for score, skill in scored:
                gumbel_noise = -math.log(-math.log(random.random() + 1e-10) + 1e-10)
                gumbel_scores.append((score + current_tau * gumbel_noise, skill))
            gumbel_scores.sort(key=lambda x: x[0], reverse=True)
            scored = gumbel_scores

        # Select top-K (K=3) skills that are relevant (score > 0)
        K = 3
        selected = []
        for score, skill in scored[:K]:
            if score > 0:
                selected.append(skill)
            else:
                break

        if selected:
            skill_ids = [s.skill_id for s in selected]
            logger.info(f"MemSkill Controller: selected skills {skill_ids} (tau={current_tau:.3f}, step={self._step_count})")
            metrics.inc("memskill_skills_selected")

        return selected

    def _compute_relevance(self, skill: CompactionSkill, context: CompactionContext) -> float:
        """Compute relevance score for a skill given the compaction context.
        Phase 1: keyword overlap + pipeline stage match + historical success rate.
        """
        score = 0.0

        # 1. Pipeline stage match — skill must affect at least one stage that will run
        stage_overlap = set(skill.pipeline_stages) & set(context.relevant_stages)
        if not stage_overlap:
            return 0.0  # Skill doesn't affect any running stage
        score += 0.3 * len(stage_overlap) / max(1, len(skill.pipeline_stages))

        # 2. Keyword matching — skill's when_to_use vs context description
        when_lower = skill.when_to_use.lower()
        desc_lower = context.description.lower()
        # Extract key terms from when_to_use
        key_terms = [t.strip() for t in when_lower.replace(",", " ").replace("/", " ").split() if len(t) > 2]
        matches = sum(1 for t in key_terms if t in desc_lower)
        if key_terms:
            score += 0.4 * matches / len(key_terms)

        # 3. Context-specific boosts
        if context.has_errors and "error" in when_lower:
            score += 0.2
        if context.has_code and "code" in when_lower:
            score += 0.1
        if context.is_thrashing and "thrashing" in when_lower:
            score += 0.3
        if context.token_pressure > 0.8 and ("pressure" in when_lower or "aggressive" in when_lower):
            score += 0.2

        # 4. Historical success rate
        if skill.usage_count > 0:
            success_rate = skill.success_count / skill.usage_count
            score += 0.1 * success_rate

        return min(1.0, score)


# V7: MemSkill Controller global (initialized in main())
skill_controller: Optional[SkillController] = None


class MemSkillAwareEngine(CompressionEngine):
    """V7: Wrapper that injects MemSkill skill selection into any CompressionEngine.
    When MEMSKILL_ENABLED=0, delegates directly to inner engine (zero regression).
    """

    def __init__(self, inner_engine: CompressionEngine, skill_registry: SkillRegistry,
                 skill_controller: SkillController):
        self._inner = inner_engine
        self._registry = skill_registry
        self._controller = skill_controller

    @property
    def name(self) -> str:
        return f"memskill+{self._inner.name}"

    def should_compress(self, messages, model) -> bool:
        return self._inner.should_compress(messages, model)

    async def compress(self, messages, api_key, session, session_id="default",
                       hook_manager=None, session_store=None, user_profile=None,
                       selected_skills=None):
        """Compress with MemSkill skill selection. Falls back to inner engine on any error."""
        if not MEMSKILL_ENABLED:
            return await self._inner.compress(messages, api_key, session, session_id=session_id,
                                               hook_manager=hook_manager, session_store=session_store,
                                               user_profile=user_profile,
                                               selected_skills=None)
        try:
            # Build context for skill selection
            context = CompactionContext.from_messages(messages, COMPACTION_MODEL, session_id,
                                                       is_preemptive=True)
            # Select skills
            selected_skills = self._controller.select_skills(context)

            # Delegate to inner engine with skills attached
            # For DualLayerCompressor: skills are passed directly via selected_skills param
            # For DefaultCompressionEngine: do_compaction() has its own skill selection via body
            result, summary = await self._inner.compress(
                messages, api_key, session, session_id=session_id,
                hook_manager=hook_manager, session_store=session_store,
                user_profile=user_profile,
                selected_skills=selected_skills,
            )

            # Update skill stats
            if selected_skills and result is not None:
                for skill in selected_skills:
                    self._registry.update_skill_stats(skill.skill_id, reward=0.5, success=True)

            return result, summary

        except Exception as e:
            logger.error(f"MemSkill error, falling back to default: {e}")
            metrics.inc("memskill_fallback")
            return await self._inner.compress(messages, api_key, session, session_id=session_id,
                                               hook_manager=hook_manager, session_store=session_store,
                                               user_profile=user_profile,
                                               selected_skills=None)


# ── V4: Secret Redaction ──────────────────────────────────────────────

SECRET_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'api_key\s*[=:]\s*["\']?[\w-]{8,}["\']?', re.I), '[REDACTED_API_KEY]'),
    (re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}'), '[REDACTED_JWT]'),
    (re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END'), '[REDACTED_PEM]'),
    (re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']{4,}["\']?', re.I), '[REDACTED_PASSWORD]'),
    (re.compile(r'(?:token|secret|auth_token)\s*[=:]\s*["\']?[^\s"\']{8,}["\']?', re.I), '[REDACTED_TOKEN]'),
]


def redact_secrets(text: str) -> str:
    """V4: Redact sensitive patterns from text"""
    if not REDACT_SECRETS:
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── V4: Thought Masking ──────────────────────────────────────────────

def strip_thinking(msg: dict) -> dict:
    """V4: Strip reasoning_content from assistant messages (Thought Masking)"""
    if msg.get("role") == "assistant" and "reasoning_content" in msg:
        new_msg = dict(msg)
        del new_msg["reasoning_content"]
        return new_msg
    return msg


# ── 上下文溢出检测 ──────────────────────────────────────────────────

OVERFLOW_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"context\s+window",
        r"context\s+length\s+exceeded",
        r"maximum\s+context\s+length",
        r"exceeds\s+model\s+context\s+window",
        r"input\s+token\s+count\s+exceeds",
        r"input\s+is\s+too\s+long",
        r"input\s+exceeds\s+the\s+maximum",
        r"request\s+size\s+exceeds",
        r"model\s+token\s+limit",
        r"token\s+limit",
        r"prompt\s+is\s+too\s+long",
        r"prompt\s+too\s+long",
        r"request_too_large",
        r"context_window_exceeded",
        r"上下文过长",
        r"上下文超出",
        r"上下文长度超",
        r"超出最大上下文",
        r"请压缩上下文",
        r"total\s+tokens?.*exceeds?",
        # V6: Provider-specific overflow patterns
        r"max_tokens_too_large",           # Anthropic
        r"too\s+many\s+tokens",            # Anthropic
        r"exceeds\s+the\s+maximum\s+number\s+of\s+tokens",  # Gemini
        r"resource[\s._-]exhausted",       # Gemini (RESOURCE_EXHAUSTED / resource.exhausted / resource exhausted)
        r"token_count_exceeds",            # Generic
        r"input_length_exceeds",           # Generic
        r"prompt_tokens.*exceeds",         # OpenAI structured error
        r"content_length_exceeded",        # Some providers
    ]
]


def is_context_overflow(status_code: int, body: bytes, provider: ProviderAdapter = None) -> bool:
    """V6: Provider-aware context overflow detection."""
    if provider and hasattr(provider, 'detect_overflow'):
        return provider.detect_overflow(status_code, body)
    # Fallback to generic detection
    if status_code not in (400, 403, 413, 500, 503):
        return False
    try:
        text = body.decode("utf-8", errors="replace").lower()
    except Exception:
        return False
    non_overflow = [
        "model not found", "pathdomainerror", "invalid_api_key",
        "authentication", "rate_limit", "overloaded", "engine is overloaded",
        "quota", "billing", "insufficient_quota",
        "overloaded_error", "permission_error",  # V6: Anthropic/Gemini exclusions
    ]
    if any(n in text for n in non_overflow):
        return False
    for pattern in OVERFLOW_PATTERNS:
        if pattern.search(text):
            return True
    return False


def detect_provider(model: str, headers: dict, upstream_url: str) -> ProviderAdapter:
    """V6: Auto-detect provider from model name, headers, and upstream URL."""
    model_lower = model.lower()
    upstream_lower = upstream_url.lower()

    # Check headers first (most reliable)
    if headers.get("x-api-key") or headers.get("X-Api-Key"):
        return AnthropicProvider()

    # Check model name patterns
    if any(k in model_lower for k in ["claude", "anthropic"]):
        return AnthropicProvider()
    if any(k in model_lower for k in ["gemini", "gemma"]):
        return GeminiProvider()

    # Check upstream URL
    if "anthropic" in upstream_lower or "claude" in upstream_lower:
        return AnthropicProvider()
    if "generativelanguage.googleapis" in upstream_lower or "gemini" in upstream_lower:
        return GeminiProvider()

    # Default: OpenAI-compatible (covers xfyun, ollama, vllm, litellm, openrouter, etc.)
    return OpenAIProvider()


# ── V6: OpenAI ↔ Anthropic Format Conversion ──────────────────────────────

def openai_to_anthropic_request(body: dict) -> dict:
    """
    Convert OpenAI Chat Completions request to Anthropic Messages API format.

    OpenAI format:
      { "model": "...", "messages": [{"role": "system|user|assistant", "content": "..."}],
        "max_tokens": N, "temperature": T, "stream": bool, "tools": [...] }

    Anthropic format:
      { "model": "...", "system": "...", "messages": [{"role": "user|assistant", "content": [...]}],
        "max_tokens": N, "temperature": T, "stream": bool, "tools": [...] }
    """
    messages = body.get("messages", [])

    # Extract system messages → top-level "system" field
    system_parts = []
    non_system = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            c = msg.get("content", "")
            if isinstance(c, list):
                # OpenAI system content blocks
                for block in c:
                    if isinstance(block, dict) and block.get("type") == "text":
                        system_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        system_parts.append(block)
            else:
                system_parts.append(str(c))
        else:
            non_system.append(msg)

    # Convert message content to Anthropic content blocks
    anthropic_messages = []
    for msg in non_system:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Map roles: OpenAI "tool" → Anthropic "user" with tool_result
        if role == "tool":
            # OpenAI tool result → Anthropic tool_result content block
            tool_call_id = msg.get("tool_call_id", "")
            tool_content = content
            if isinstance(tool_content, list):
                # Convert OpenAI content parts to Anthropic content blocks
                text_parts = []
                for part in tool_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append({"type": "text", "text": part.get("text", "")})
                    elif isinstance(part, str):
                        text_parts.append({"type": "text", "text": part})
                tool_content_blocks = text_parts if text_parts else [{"type": "text", "text": ""}]
            else:
                tool_content_blocks = [{"type": "text", "text": str(tool_content)}]

            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": tool_content_blocks,
                }]
            })
            continue

        # Convert content to Anthropic content blocks
        if isinstance(content, str):
            # Simple text → Anthropic content block
            if not content and role == "assistant":
                # Empty assistant content — skip or use placeholder
                continue
            content_blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            content_blocks = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        content_blocks.append({"type": "text", "text": block.get("text", "")})
                    elif btype == "image_url":
                        # OpenAI image_url → Anthropic image block
                        url = block.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            # data:image/png;base64,xxx
                            media_type, _, data = url.partition(";base64,")
                            media_type = media_type.replace("data:", "") or "image/png"
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data,
                                }
                            })
                        else:
                            content_blocks.append({
                                "type": "image",
                                "source": {"type": "url", "url": url}
                            })
                    elif btype == "tool_use" or btype == "function_call":
                        # Already Anthropic-style or OpenAI function_call
                        if btype == "function_call":
                            content_blocks.append({
                                "type": "tool_use",
                                "id": block.get("id", f"call_{len(content_blocks)}"),
                                "name": block.get("name", block.get("function", {}).get("name", "")),
                                "input": block.get("function", {}).get("arguments", "{}")
                                if "function" in block else block.get("input", {}),
                            })
                        else:
                            content_blocks.append(block)
                    elif btype == "tool_result":
                        content_blocks.append(block)
                    else:
                        # Unknown block type — convert to text
                        content_blocks.append({"type": "text", "text": json.dumps(block)})
                elif isinstance(block, str):
                    content_blocks.append({"type": "text", "text": block})
            if not content_blocks:
                content_blocks = [{"type": "text", "text": ""}]
        else:
            content_blocks = [{"type": "text", "text": str(content)}]

        # Handle assistant tool_calls → Anthropic tool_use blocks
        tool_calls = msg.get("tool_calls", [])
        if role == "assistant" and tool_calls:
            for tc in tool_calls:
                tc_id = tc.get("id", f"call_{len(content_blocks)}")
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    arguments = json.loads(func.get("arguments", "{}")) if isinstance(func.get("arguments"), str) else func.get("arguments", {})
                except json.JSONDecodeError:
                    arguments = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc_id,
                    "name": name,
                    "input": arguments,
                })

        # Map role: OpenAI "assistant" → Anthropic "assistant"
        anthropic_role = role  # "user" and "assistant" are the same

        anthropic_messages.append({
            "role": anthropic_role,
            "content": content_blocks,
        })

    # Ensure alternating user/assistant (Anthropic requirement)
    # If first message is not "user", prepend empty user message
    if anthropic_messages and anthropic_messages[0]["role"] != "user":
        anthropic_messages.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuation)"}]})

    # Merge consecutive same-role messages (Anthropic doesn't allow them)
    merged = []
    for msg in anthropic_messages:
        if merged and merged[-1]["role"] == msg["role"]:
            # Merge content blocks
            merged[-1]["content"].extend(msg["content"])
        else:
            merged.append(msg)
    anthropic_messages = merged

    # Build Anthropic request
    anthropic_body = {
        "model": body.get("model", ""),
        "max_tokens": body.get("max_tokens", 4096),
        "messages": anthropic_messages,
    }

    if system_parts:
        system_text = "\n\n".join(system_parts)
        anthropic_body["system"] = system_text

    # Optional fields
    if "temperature" in body:
        anthropic_body["temperature"] = body["temperature"]
    if "top_p" in body:
        anthropic_body["top_p"] = body["top_p"]
    if "stop" in body:
        stops = body["stop"]
        if isinstance(stops, str):
            stops = [stops]
        anthropic_body["stop_sequences"] = stops
    if body.get("stream", False):
        anthropic_body["stream"] = True

    # Convert OpenAI tools format to Anthropic tools format
    openai_tools = body.get("tools", [])
    if openai_tools:
        anthropic_tools = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                # Already Anthropic-style or unknown — pass through
                anthropic_tools.append(tool)
        if anthropic_tools:
            anthropic_body["tools"] = anthropic_tools

    # Metadata
    if body.get("metadata"):
        anthropic_body["metadata"] = body["metadata"]

    return anthropic_body


def anthropic_to_openai_response(data: dict, model: str = "") -> dict:
    """
    Convert Anthropic Messages API response to OpenAI Chat Completions format.

    Anthropic response:
      { "id": "...", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", ...}],
        "model": "...", "stop_reason": "end_turn|tool_use|...", "usage": {...} }

    OpenAI response:
      { "id": "...", "object": "chat.completion", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "...", "tool_calls": [...]},
        "finish_reason": "stop|tool_calls|..."}], "usage": {...}, "model": "..." }
    """
    # Map stop reasons
    stop_reason_map = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }
    anthropic_stop = data.get("stop_reason", "end_turn")
    finish_reason = stop_reason_map.get(anthropic_stop, "stop")

    # Extract content
    content_blocks = data.get("content", [])
    text_parts = []
    thinking_parts = []
    tool_calls = []
    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "thinking":
            # Some models (e.g. xsparkx2agent) put all actual content in thinking
            # blocks with no text block. Collect thinking content as fallback.
            thinking_text = block.get("text", "")
            if thinking_text:
                thinking_parts.append(thinking_text)
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                }
            })

    # Use text content if available; fall back to thinking content if no text blocks
    # (some reasoning models only emit thinking blocks with the actual answer)
    if text_parts:
        text_content = "\n".join(text_parts)
    elif thinking_parts:
        text_content = "\n".join(thinking_parts)
    else:
        text_content = None

    # Build message
    message = {"role": "assistant", "content": text_content or ""}
    if tool_calls:
        message["tool_calls"] = tool_calls
        if finish_reason == "stop":
            finish_reason = "tool_calls"

    # Map usage
    anthropic_usage = data.get("usage", {})
    openai_usage = {
        "prompt_tokens": anthropic_usage.get("input_tokens", 0),
        "completion_tokens": anthropic_usage.get("output_tokens", 0),
        "total_tokens": anthropic_usage.get("input_tokens", 0) + anthropic_usage.get("output_tokens", 0),
    }

    return {
        "id": data.get("id", f"chatcmpl-{id(data)}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or data.get("model", ""),
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": openai_usage,
    }


def convert_anthropic_sse_to_openai(line: str, model: str = "") -> list:
    """
    Convert a single Anthropic SSE event line to OpenAI SSE format.
    Returns a list of SSE lines (may be 0, 1, or multiple).

    Anthropic SSE events:
      event: message_start\ndata: {"type":"message_start","message":{...}}
      event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}
      event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}
      event: content_block_stop\ndata: {"type":"content_block_stop","index":0}
      event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":15}}
      event: message_stop\ndata: {"type":"message_stop"}

    OpenAI SSE events:
      data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
      data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}
      data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
    """
    if not line.strip():
        return [line]  # Keep blank lines as-is

    # Parse SSE format
    if line.startswith("event:"):
        # We don't need the event type line for OpenAI format
        # But we need to track state — handled by the caller
        return []  # Skip event type lines, we'll emit data lines only

    if not line.startswith("data:"):
        return [line]  # Pass through non-data lines (comments, etc.)

    data_str = line[5:].strip()
    if data_str == "[DONE]":
        return ["data: [DONE]\n\n"]

    try:
        event = json.loads(data_str)
    except json.JSONDecodeError:
        return [line]  # Pass through unparseable data

    event_type = event.get("type", "")
    chunk_id = f"chatcmpl-{id(event)}"

    # message_start → first chunk with role
    if event_type == "message_start":
        msg = event.get("message", {})
        # Store message ID for subsequent chunks
        msg_id = msg.get("id", chunk_id)
        convert_anthropic_sse_to_openai._last_msg_id = msg_id
        convert_anthropic_sse_to_openai._last_model = msg.get("model", model)
        convert_anthropic_sse_to_openai._tool_call_index = 0
        convert_anthropic_sse_to_openai._tool_call_ids = {}
        convert_anthropic_sse_to_openai._has_text_block = False
        convert_anthropic_sse_to_openai._thinking_buffer = []
        chunk = {
            "id": msg_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": msg.get("model", model),
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }],
        }
        return [f"data: {json.dumps(chunk)}\n\n"]

    # content_block_start — may be text, thinking, or tool_use
    if event_type == "content_block_start":
        content_block = event.get("content_block", {})
        block_type = content_block.get("type", "")
        block_index = event.get("index", 0)

        if block_type == "text":
            # Text block start — mark that we have a text block
            convert_anthropic_sse_to_openai._has_text_block = True
            return []
        elif block_type == "thinking":
            # Thinking block start — if no text block follows, we'll convert
            # thinking_delta to content. Nothing to emit yet.
            return []
        elif block_type == "tool_use":
            # Tool use block start — emit tool_calls delta
            tc_id = content_block.get("id", f"call_{convert_anthropic_sse_to_openai._tool_call_index}")
            tc_name = content_block.get("name", "")
            convert_anthropic_sse_to_openai._tool_call_ids[block_index] = tc_id
            tc_idx = convert_anthropic_sse_to_openai._tool_call_index
            convert_anthropic_sse_to_openai._tool_call_index += 1
            chunk = {
                "id": getattr(convert_anthropic_sse_to_openai, "_last_msg_id", chunk_id),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": getattr(convert_anthropic_sse_to_openai, "_last_model", model),
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": tc_idx,
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": tc_name, "arguments": ""},
                        }]
                    },
                    "finish_reason": None,
                }],
            }
            return [f"data: {json.dumps(chunk)}\n\n"]
        return []

    # content_block_delta — text or tool input
    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")
        block_index = event.get("index", 0)

        if delta_type == "text_delta":
            # V6 fix: Mark that we have text content. If thinking was previously
            # buffered (emitted as content because we didn't know text was coming),
            # it's too late to retract it from the stream. But from this point on,
            # thinking_delta events will be suppressed.
            convert_anthropic_sse_to_openai._has_text_block = True
            text = delta.get("text", "")
            chunk = {
                "id": getattr(convert_anthropic_sse_to_openai, "_last_msg_id", chunk_id),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": getattr(convert_anthropic_sse_to_openai, "_last_model", model),
                "choices": [{
                    "index": 0,
                    "delta": {"content": text},
                    "finish_reason": None,
                }],
            }
            return [f"data: {json.dumps(chunk)}\n\n"]
        elif delta_type == "thinking_delta":
            # V6 fix: Buffer thinking content instead of emitting immediately.
            # If a text block follows, we discard the thinking (it's internal
            # reasoning). If no text block comes, we flush the buffer at
            # message_stop so the client receives the actual content.
            text = delta.get("thinking", "")
            if text:
                convert_anthropic_sse_to_openai._thinking_buffer.append(text)
            return []
        elif delta_type == "input_json_delta":
            # Tool input partial JSON
            partial = delta.get("partial_json", "")
            tc_idx = 0
            # Find the tool_call index for this block
            for bi, tid in convert_anthropic_sse_to_openai._tool_call_ids.items():
                if bi == block_index:
                    tc_idx = list(convert_anthropic_sse_to_openai._tool_call_ids.keys()).index(bi)
                    break
            chunk = {
                "id": getattr(convert_anthropic_sse_to_openai, "_last_msg_id", chunk_id),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": getattr(convert_anthropic_sse_to_openai, "_last_model", model),
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": tc_idx,
                            "function": {"arguments": partial},
                        }]
                    },
                    "finish_reason": None,
                }],
            }
            return [f"data: {json.dumps(chunk)}\n\n"]
        return []

    # content_block_stop — nothing to emit
    if event_type == "content_block_stop":
        return []

    # message_delta — stop reason + usage
    if event_type == "message_delta":
        delta = event.get("delta", {})
        stop_reason = delta.get("stop_reason", "")
        usage = event.get("usage", {})

        # Map stop reason
        stop_map = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length", "stop_sequence": "stop"}
        finish_reason = stop_map.get(stop_reason, "stop")

        chunk = {
            "id": getattr(convert_anthropic_sse_to_openai, "_last_msg_id", chunk_id),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": getattr(convert_anthropic_sse_to_openai, "_last_model", model),
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }],
        }
        if usage:
            chunk["usage"] = {
                "prompt_tokens": 0,
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("output_tokens", 0),
            }
        return [f"data: {json.dumps(chunk)}\n\n"]

    # message_stop — flush buffered thinking if no text block was seen, then emit [DONE]
    if event_type == "message_stop":
        result = []
        has_text = getattr(convert_anthropic_sse_to_openai, "_has_text_block", False)
        thinking_buf = getattr(convert_anthropic_sse_to_openai, "_thinking_buffer", [])
        if not has_text and thinking_buf:
            # Model only emitted thinking — convert to content so client gets text
            full_thinking = "".join(thinking_buf)
            if full_thinking:
                chunk = {
                    "id": getattr(convert_anthropic_sse_to_openai, "_last_msg_id", ""),
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": getattr(convert_anthropic_sse_to_openai, "_last_model", model),
                    "choices": [{
                        "index": 0,
                        "delta": {"content": full_thinking},
                        "finish_reason": None,
                    }],
                }
                result.append(f"data: {json.dumps(chunk)}\n\n")
        # Clear buffer
        convert_anthropic_sse_to_openai._thinking_buffer = []
        result.append("data: [DONE]\n\n")
        return result

    # ping — skip
    if event_type == "ping":
        return []

    # Unknown event type — pass through as-is
    return [line]


# Initialize static state for SSE conversion
convert_anthropic_sse_to_openai._last_msg_id = ""
convert_anthropic_sse_to_openai._last_model = ""
convert_anthropic_sse_to_openai._tool_call_index = 0
convert_anthropic_sse_to_openai._tool_call_ids = {}
convert_anthropic_sse_to_openai._has_text_block = False
convert_anthropic_sse_to_openai._thinking_buffer = []


def openai_to_anthropic_headers(headers: dict, api_key: str) -> dict:
    """Convert OpenAI-style headers to Anthropic-style headers for upstream."""
    clean = {}
    for k, v in headers.items():
        lower = k.lower()
        if lower in ("host", "content-length", "transfer-encoding", "connection"):
            continue
        # Convert Authorization: Bearer → x-api-key
        if lower == "authorization" and v.startswith("Bearer "):
            clean["x-api-key"] = v[7:]
            continue
        clean[k] = v
    # Ensure required Anthropic headers
    if "x-api-key" not in clean and "X-Api-Key" not in clean:
        clean["x-api-key"] = api_key
    clean["anthropic-version"] = "2023-06-01"
    clean["content-type"] = "application/json"
    return clean

# 语义承诺类型 (arXiv:2605.17304)
COMMITMENT_TYPES = {
    "goal": re.compile(
        r'(?:goal|目标|purpose|aim|task|任务|want\s+to|需要|trying\s+to|attempting)\s*[:：]?\s*(.{5,200})',
        re.IGNORECASE
    ),
    "constraint": re.compile(
        r'(?:constraint|约束|limit|限制|must|必须|requirement|需求|cannot|不能|never|绝不|rule|规则)\s*[:：]?\s*(.{5,200})',
        re.IGNORECASE
    ),
    "decision": re.compile(
        r'(?:decided|决定|chose|选择|will\s+use|使用|adopted|采用|switched|切换)\s*(?:to\s+)?(.{5,200})',
        re.IGNORECASE
    ),
    "error": re.compile(
        r'(?:error|错误|bug|fix|修复|exception|traceback|failed|失败)\s*[:：]?\s*(.{5,200})',
        re.IGNORECASE
    ),
    "file_op": re.compile(
        r'(?:created|modified|deleted|renamed|创建|修改|删除|重命名|wrote|read|编辑)\s+(.{5,200})',
        re.IGNORECASE
    ),
}


def extract_commitments(messages: list) -> list:
    """
    V3: CCL 承诺提取 — 从消息中提取语义承诺原子。
    返回 [(type, content)] 列表。
    """
    commitments = []
    all_text = ""

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            all_text += content + "\n"
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        all_text += block.get("text", "") + "\n"
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            # 从工具调用中提取文件操作承诺
                            for key in ("path", "file_path", "target_file", "filename"):
                                if key in inp:
                                    commitments.append(("file_op", f"{name}: {inp[key]}"))

    # 从文本中提取承诺
    for ctype, pattern in COMMITMENT_TYPES.items():
        for match in pattern.finditer(all_text):
            text = match.group(1).strip()
            if len(text) >= 5:
                commitments.append((ctype, text))

    # 去重
    seen = set()
    unique = []
    for ctype, text in commitments:
        key = (ctype, text[:80])
        if key not in seen:
            seen.add(key)
            unique.append((ctype, text))

    return unique[:40]  # 最多 40 个承诺


def format_commitments_for_prompt(commitments: list) -> str:
    """将承诺格式化为压缩提示词中的保留指令"""
    if not commitments:
        return ""
    lines = ["## SEMANTIC COMMITMENTS (MUST PRESERVE)"]
    by_type = defaultdict(list)
    for ctype, text in commitments:
        by_type[ctype].append(text)

    type_labels = {
        "goal": "Goals",
        "constraint": "Constraints",
        "decision": "Decisions",
        "error": "Errors",
        "file_op": "File Operations",
    }

    for ctype in ["goal", "constraint", "decision", "error", "file_op"]:
        items = by_type.get(ctype, [])
        if items:
            label = type_labels.get(ctype, ctype)
            lines.append(f"### {label}")
            for item in items[:8]:
                lines.append(f"- {item}")

    return "\n".join(lines)


# ── V5: LLM-Driven Memory Extraction ────────────────────────────────────

async def llm_extract_memory(
    messages: list,
    api_key: str,
    session: aiohttp.ClientSession,
    existing_memory: dict = None,
) -> dict:
    """
    V5: LLM-driven memory extraction — like Claude Code's auto MEMORY.md generation.
    Uses the compaction model to extract structured knowledge from conversation.
    Falls back to regex-based extraction if LLM fails.

    Returns dict with keys: goals, decisions, errors, files, constraints, insights
    """
    # Build extraction prompt from last 20 messages
    conversation_text = format_messages_for_compaction(messages[-20:])

    existing_context = ""
    if existing_memory:
        existing_context = f"\n\nEXISTING MEMORY (update/extend, don't duplicate):\n{json.dumps(existing_memory, ensure_ascii=False, indent=2)}"

    extraction_prompt = f"""Analyze this conversation and extract structured knowledge. Return a JSON object with these keys:
- "goals": List of active goals/tasks the user is working on
- "decisions": List of important decisions made
- "errors": List of errors encountered and their resolutions
- "files": List of important files mentioned with their paths
- "constraints": List of constraints or requirements
- "insights": List of key insights, patterns, or learnings

Rules:
1. Each entry should be a concise string (max 100 chars)
2. Only include things worth remembering across sessions
3. Don't include trivial or obvious information
4. Merge with existing memory where possible (avoid duplicates)
5. Return ONLY valid JSON, no markdown or explanation

CONVERSATION:
{conversation_text}
{existing_context}"""

    payload = {
        "model": COMPACTION_MODEL,
        "max_tokens": 1000,
        "temperature": 0.1,  # Low temperature for extraction
        "messages": [
            {"role": "system", "content": "You are a memory extraction system. Extract structured knowledge from conversations. Return only valid JSON."},
            {"role": "user", "content": extraction_prompt},
        ],
    }

    # V6: Provider-aware memory extraction call
    compaction_upstream = COMPACTION_UPSTREAM or UPSTREAM_BASE
    compaction_api_key = COMPACTION_API_KEY or api_key

    if COMPACTION_PROVIDER == "auto":
        mem_provider = detect_provider(COMPACTION_MODEL, {}, compaction_upstream)
    elif COMPACTION_PROVIDER == "anthropic":
        mem_provider = AnthropicProvider()
    elif COMPACTION_PROVIDER == "gemini":
        mem_provider = GeminiProvider()
    else:
        mem_provider = OpenAIProvider()

    headers = mem_provider.build_compaction_headers(compaction_api_key)
    provider_payload = mem_provider.build_compaction_payload(
        COMPACTION_MODEL, payload.get("messages", []),
        payload.get("max_tokens", 1000), payload.get("temperature", 0.1)
    )
    url = mem_provider.build_compaction_url(compaction_upstream)

    if isinstance(mem_provider, GeminiProvider):
        url = f"{compaction_upstream.rstrip('/')}/v1beta/models/{COMPACTION_MODEL}:generateContent"

    try:
        async with session.post(
            url, headers=headers, data=json.dumps(provider_payload).encode(),
            timeout=aiohttp.ClientTimeout(total=30),  # Short timeout for extraction
        ) as resp:
            if resp.status != 200:
                logger.warning(f"LLM memory extraction failed: {resp.status}")
                return None
            data = json.loads(await resp.read())
            content = mem_provider.extract_compaction_content(data)

            # V6 fix: streaming fallback for empty thinking blocks
            if not content and isinstance(mem_provider, AnthropicProvider):
                content_blocks = data.get("content", [])
                has_thinking_only = (
                    not any(b.get("type") == "text" and b.get("text", "").strip() for b in content_blocks) and
                    any(b.get("type") == "thinking" for b in content_blocks) and
                    not any(b.get("type") == "tool_use" for b in content_blocks)
                )
                if has_thinking_only:
                    logger.info("Memory extraction: non-streaming has only empty thinking block, retrying as streaming")
                    stream_payload = dict(provider_payload)
                    stream_payload["stream"] = True
                    try:
                        collected_text = []
                        async with session.post(
                            url, headers=headers, data=json.dumps(stream_payload).encode(),
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as stream_resp:
                            if stream_resp.status == 200:
                                async for line_bytes in stream_resp.content:
                                    line = line_bytes.decode("utf-8", errors="replace").strip()
                                    if not line.startswith("data: "):
                                        continue
                                    payload_str = line[6:]
                                    if payload_str == "[DONE]":
                                        break
                                    try:
                                        evt = json.loads(payload_str)
                                        evt_type = evt.get("type", "")
                                        if evt_type == "content_block_delta":
                                            delta = evt.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                collected_text.append(delta.get("text", ""))
                                            elif delta.get("type") == "thinking_delta":
                                                collected_text.append(delta.get("thinking", ""))
                                    except (json.JSONDecodeError, KeyError):
                                        continue
                        if collected_text:
                            content = "".join(collected_text)
                            logger.info(f"Memory extraction streaming fallback captured {len(content)} chars")
                    except Exception as e:
                        logger.warning(f"Memory extraction streaming fallback failed: {e}")

            if not content:
                return None
            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        logger.warning(f"LLM memory extraction error: {e}")
        return None


# ── 标识符提取 ──────────────────────────────────────────────────────────

IDENTIFIER_PATTERNS = [
    re.compile(r'/[\w/.-]+\.\w{1,10}'),
    re.compile(r'def\s+(\w+)\s*\('),
    re.compile(r'class\s+(\w+)'),
    re.compile(r'Error["\s:]+([^"\n]{5,100})'),
    re.compile(r'`([^`]+)`'),
]


def extract_identifiers(messages: list) -> list:
    identifiers = set()
    all_text = ""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            all_text += content + "\n"
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        all_text += block.get("text", "") + "\n"
                    elif block.get("type") == "tool_use":
                        all_text += block.get("name", "") + " "
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            all_text += json.dumps(inp, ensure_ascii=False) + "\n"

    for pattern in IDENTIFIER_PATTERNS:
        for match in pattern.finditer(all_text):
            identifiers.add(match.group(0).strip())

    result = sorted(identifiers)
    return result[:50]


# ── 智能截断 ──────────────────────────────────────────────────────────

def smart_truncate(text: str, budget: int, head_ratio: float = 0.6) -> str:
    if len(text) <= budget:
        return text

    head_budget = int(budget * head_ratio)
    tail_budget = budget - head_budget - 30

    head_end = head_budget
    for offset in range(0, min(200, len(text) - head_budget)):
        pos = head_budget + offset
        if pos < len(text) and text[pos] in ('\n', '.', '!', '?', '。', '！', '？'):
            head_end = pos + 1
            break

    tail_start = len(text) - tail_budget
    for offset in range(0, min(200, tail_budget)):
        pos = tail_start - offset
        if pos >= 0 and text[pos] in ('\n', '.', '!', '?', '。', '！', '？'):
            tail_start = pos + 1
            break

    head = text[:head_end]
    tail = text[tail_start:]
    omitted = len(text) - len(head) - len(tail)

    return f"{head}\n... [truncated, {omitted} chars omitted] ...\n{tail}"


# ── V4: Tag-based Selective Retention ────────────────────────────────

FIDELITY_FULL = "full"         # 完整保留
FIDELITY_COMPRESSED = "compressed"  # 压缩保留
FIDELITY_PLACEHOLDER = "placeholder"  # 仅保留占位符

VERBOSE_TOOLS = {"ls", "find", "cat", "head", "tail", "wc", "echo", "pwd", "type", "dir", "listdir", "readdir", "tree"}
CRITICAL_TOOLS = {"submit", "error", "diff", "apply_diff", "apply_patch", "write", "create", "delete", "rename", "move", "edit"}
INFO_TOOLS = {"read", "grep", "search", "glob", "which", "stat", "file_info", "get", "fetch"}
CODE_TOOLS = {"bash", "python", "python3", "node", "exec", "run", "shell", "sh"}


def classify_tool_fidelity(tool_name: str) -> str:
    """V4: Tag-based Selective Retention — classify tool output retention level"""
    name_lower = tool_name.lower().strip()
    for t in VERBOSE_TOOLS:
        if t in name_lower: return FIDELITY_PLACEHOLDER
    for t in CRITICAL_TOOLS:
        if t in name_lower: return FIDELITY_FULL
    for t in INFO_TOOLS:
        if t in name_lower: return FIDELITY_COMPRESSED
    for t in CODE_TOOLS:
        if t in name_lower: return FIDELITY_COMPRESSED
    return FIDELITY_COMPRESSED


# ── V4: Structure-Aware Compression ──────────────────────────────────

def compress_code_output(text: str, budget: int = 2000) -> str:
    """V4: AST-aware compression — keep signatures, strip bodies"""
    if len(text) <= budget: return text
    lines = text.split('\n')
    kept = []
    in_body = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('async def '):
            in_body = 0
            kept.append(line)
        elif stripped.startswith('@'):
            kept.append(line)
        elif in_body > 0:
            in_body -= 1
        elif stripped and not stripped.startswith('#') and not stripped.startswith('"""'):
            in_body = 2
        else:
            kept.append(line)
    result = '\n'.join(kept)
    if len(result) > budget:
        return smart_truncate(result, budget)
    return result


def compress_log_output(text: str, budget: int = 1500) -> str:
    """V4: Log-aware compression — keep ERROR/WARN, drop INFO/DEBUG"""
    if len(text) <= budget: return text
    lines = text.split('\n')
    important = [l for l in lines if any(kw in l.upper() for kw in ['ERROR', 'WARN', 'CRITICAL', 'FATAL', 'FAIL', 'EXCEPTION', 'TRACEBACK'])]
    if important:
        result = '\n'.join(important)
        if len(result) <= budget: return result
        return smart_truncate(result, budget)
    return smart_truncate(text, budget)


def compress_json_output(text: str, budget: int = 2000) -> str:
    """V4: JSON-aware compression — keep keys, truncate values"""
    if len(text) <= budget: return text
    try:
        data = json.loads(text)
        def truncate_values(obj, max_val=100):
            if isinstance(obj, dict):
                return {k: truncate_values(v, max_val) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [truncate_values(v, max_val) for v in obj[:10]]
            elif isinstance(obj, str) and len(obj) > max_val:
                return obj[:max_val] + "..."
            return obj
        truncated = truncate_values(data)
        result = json.dumps(truncated, ensure_ascii=False, indent=2)
        if len(result) <= budget: return result
    except Exception:
        pass
    return smart_truncate(text, budget)


def auto_compress_output(text: str, budget: int = 2000) -> str:
    """V4: Auto-detect output type and apply appropriate compressor"""
    if len(text) <= budget: return text
    stripped = text.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            json.loads(stripped)
            return compress_json_output(text, budget)
        except Exception:
            pass
    if any(kw in stripped[:500].upper() for kw in ['ERROR', 'WARN', 'INFO', 'DEBUG', 'TRACEBACK', 'LOG']):
        return compress_log_output(text, budget)
    if 'def ' in stripped or 'class ' in stripped or 'import ' in stripped:
        return compress_code_output(text, budget)
    return smart_truncate(text, budget)


# ── V3: AFM 自适应保真度 ──────────────────────────────────────────────

def classify_fidelity(msg: dict, position_from_end: int, total_msgs: int) -> str:
    """
    V3: AFM 自适应保真度分类。
    根据消息角色、位置、内容决定保真级别。
    (arXiv:2511.12712)

    规则:
    - system 消息: FULL
    - 最近 2 轮: FULL
    - 包含错误/决策的消息: COMPRESSED (重要但可压缩)
    - 纯工具调用结果: PLACEHOLDER (用 ARC 引用替代)
    - 其他: COMPRESSED

    V6 FIX: Now integrated into the compaction flow via:
    1. format_messages_for_compaction() — message-level AFM baseline + tool-level override
    2. submodular_select() — fidelity-aware importance boosting
    3. do_compaction() — AFM fidelity marking before submodular selection
    """
    role = msg.get("role", "")
    content = msg.get("content", "")

    # System 消息始终完整保留
    if role == "system":
        return FIDELITY_FULL

    # 最近的消息完整保留
    if position_from_end < 4:  # 最近 2 轮 (user+assistant)
        return FIDELITY_FULL

    # 检测内容类型
    content_str = ""
    if isinstance(content, str):
        content_str = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    content_str += block.get("text", "") + " "
                elif block.get("type") == "tool_result":
                    # 工具结果 → PLACEHOLDER (用 ARC 引用)
                    return FIDELITY_PLACEHOLDER

    # 包含错误/决策 → COMPRESSED (重要但可压缩，保留关键信息)
    error_indicators = ["error", "错误", "bug", "fix", "修复", "failed", "失败", "exception", "traceback", "crash"]
    decision_indicators = ["decided", "决定", "chose", "选择", "will use", "使用", "goal:", "plan:", "strategy"]

    content_lower = content_str.lower()
    if any(ind in content_lower for ind in error_indicators):
        # Errors are important — COMPRESSED to preserve key info but reduce verbosity
        return FIDELITY_COMPRESSED
    if any(ind in content_lower for ind in decision_indicators):
        # Decisions are important — COMPRESSED to preserve key info
        return FIDELITY_COMPRESSED

    # 默认: COMPRESSED
    return FIDELITY_COMPRESSED


def apply_afm_fidelity(messages: list) -> list:
    """
    V3/V6: Apply AFM adaptive fidelity classification to all messages.
    Marks each message with _afm_fidelity for downstream use by
    format_messages_for_compaction() and submodular_select().

    Returns a new list with _afm_fidelity metadata on each message.
    """
    total = len(messages)
    result = []
    full_count = 0
    compressed_count = 0
    placeholder_count = 0

    for i, msg in enumerate(messages):
        position_from_end = total - 1 - i
        fidelity = classify_fidelity(msg, position_from_end, total)
        new_msg = dict(msg)
        new_msg["_afm_fidelity"] = fidelity
        result.append(new_msg)

        if fidelity == FIDELITY_FULL:
            full_count += 1
        elif fidelity == FIDELITY_COMPRESSED:
            compressed_count += 1
        elif fidelity == FIDELITY_PLACEHOLDER:
            placeholder_count += 1

    if full_count + compressed_count + placeholder_count > 0:
        logger.info(
            f"AFM fidelity classification: {total} messages -> "
            f"FULL={full_count}, COMPRESSED={compressed_count}, PLACEHOLDER={placeholder_count}"
        )
        metrics.inc("afm_fidelity_classified")

    return result


# ── V3: 子模选择 (PACMS) ──────────────────────────────────────────────

def _message_importance(msg: dict, position_from_end: int, commitments: list) -> float:
    """
    计算消息的重要性分数 (0-1)。
    子模函数的边际收益近似。
    (V3 compat — V4 uses _message_importance_v4)
    """
    role = msg.get("role", "")
    content = msg.get("content", "")
    score = 0.0

    # 角色权重
    role_weights = {"system": 1.0, "user": 0.8, "assistant": 0.6, "tool": 0.3}
    score += role_weights.get(role, 0.4)

    # 位置衰减: 越近越重要
    decay = math.exp(-0.1 * position_from_end)
    score *= (0.3 + 0.7 * decay)

    # 内容重要性
    content_str = ""
    if isinstance(content, str):
        content_str = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                content_str += block.get("text", "") + " "

    # 承诺匹配加分
    for ctype, ctext in commitments:
        if ctext.lower() in content_str.lower():
            score += 0.3
            break

    # 错误/决策加分
    error_indicators = ["error", "错误", "bug", "fix", "修复", "failed", "exception"]
    if any(ind in content_str.lower() for ind in error_indicators):
        score += 0.4

    # 代码加分 (包含文件路径或代码块)
    if "```" in content_str or re.search(r'/[\w/.-]+\.\w{1,10}', content_str):
        score += 0.2

    return min(1.0, score)


def _message_importance_v4(msg: dict, position_from_end: int, commitments: list, semantic_memory=None,
                           selected_skills=None) -> float:
    """V4: Four-Signal Memory Scoring — Semantic(50%) + Recency(25%) + Kind(15%) + Quality(10%)
    V7: Weights overridable via selected_skills (MemSkill)."""
    role = msg.get("role", "")
    content = msg.get("content", "")
    content_str = ""
    if isinstance(content, str):
        content_str = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                content_str += block.get("text", "") + " "

    # V7: Start with default weights, allow skill overrides
    weights = {"semantic": 0.50, "recency": 0.25, "kind": 0.15, "quality": 0.10}
    if selected_skills:
        for skill in selected_skills:
            if "importance_scoring" in skill.pipeline_stages and "importance_weights" in skill.params:
                for k, v in skill.params["importance_weights"].items():
                    if k in weights:
                        weights[k] = v
    # Normalize weights
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {k: v / total_w for k, v in weights.items()}

    # Signal 1: Semantic relevance
    semantic_score = 0.0
    for ctype, ctext in commitments:
        if ctext.lower() in content_str.lower():
            semantic_score += 0.3
            break
    if semantic_memory:
        for category, items in semantic_memory._knowledge.items():
            for item in items:
                if item.lower() in content_str.lower():
                    semantic_score += 0.2
                    break
            if semantic_score >= 0.5:
                break
    semantic_score = min(1.0, semantic_score)

    # Signal 2: Recency
    recency_score = math.exp(-0.1 * position_from_end)

    # Signal 3: Kind
    kind_weights = {"system": 1.0, "user": 0.8, "assistant": 0.6, "tool": 0.3}
    kind_score = kind_weights.get(role, 0.4)
    error_indicators = ["error", "错误", "bug", "fix", "修复", "failed", "exception"]
    if any(ind in content_str.lower() for ind in error_indicators):
        kind_score = min(1.0, kind_score + 0.3)

    # Signal 4: Quality
    quality_score = 0.5
    if "```" in content_str or re.search(r'/[\w/.-]+\.\w{1,10}', content_str):
        quality_score = 0.8
    if len(content_str) < 20:
        quality_score = 0.2
    if any(ind in content_str.lower() for ind in error_indicators):
        quality_score = 0.9

    return min(1.0, semantic_score * weights["semantic"] + recency_score * weights["recency"] + kind_score * weights["kind"] + quality_score * weights["quality"])
def submodular_select(messages: list, token_budget: int, commitments: list,
                      semantic_memory=None, selected_skills=None) -> list:
    """
    V4: PACMS submodular selection — greedy selection under token budget.
    Enhanced to use _message_importance_v4 with semantic memory.
    V6: AFM fidelity-aware — FULL fidelity messages get importance boost,
    PLACEHOLDER fidelity messages get importance penalty (they can be replaced by ARC refs).
    V7: Fidelity multipliers overridable via selected_skills (MemSkill).

    Uses greedy algorithm to approximate submodular maximization:
    1. Calculate importance and token cost for each message
    2. Apply AFM fidelity multiplier to importance scores
    3. Greedy selection: pick message with highest marginal benefit/cost ratio
    4. Until budget is exhausted
    """
    if not messages:
        return []

    # V7: Start with default fidelity multipliers, allow skill overrides
    fidelity_multipliers = {FIDELITY_FULL: 1.5, FIDELITY_PLACEHOLDER: 0.5}
    if selected_skills:
        for skill in selected_skills:
            if "submodular_selection" in skill.pipeline_stages and "fidelity_multipliers" in skill.params:
                fm = skill.params["fidelity_multipliers"]
                if "full" in fm:
                    fidelity_multipliers[FIDELITY_FULL] = fm["full"]
                if "placeholder" in fm:
                    fidelity_multipliers[FIDELITY_PLACEHOLDER] = fm["placeholder"]

    # Pre-calculate token cost and importance for each message
    msg_data = []
    for i, msg in enumerate(messages):
        position_from_end = len(messages) - 1 - i
        importance = _message_importance_v4(msg, position_from_end, commitments, semantic_memory,
                                            selected_skills=selected_skills)

        # V6: AFM fidelity multiplier — boost FULL, penalize PLACEHOLDER
        afm_fidelity = msg.get("_afm_fidelity", "")
        if afm_fidelity in fidelity_multipliers:
            importance *= fidelity_multipliers[afm_fidelity]

        token_cost = estimate_messages_tokens([msg])
        msg_data.append({
            "msg": msg,
            "importance": importance,
            "cost": max(1, token_cost),
            "selected": False,
        })

    # System messages must be selected
    selected_indices = set()
    used_budget = 0

    for i, md in enumerate(msg_data):
        if md["msg"].get("role") == "system":
            selected_indices.add(i)
            used_budget += md["cost"]
            md["selected"] = True

    # Greedy selection
    while used_budget < token_budget:
        best_ratio = -1
        best_idx = -1

        for i, md in enumerate(msg_data):
            if md["selected"]:
                continue
            if used_budget + md["cost"] > token_budget:
                continue
            # Marginal benefit / cost ratio
            ratio = md["importance"] / md["cost"]
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i

        if best_idx < 0:
            break

        msg_data[best_idx]["selected"] = True
        selected_indices.add(best_idx)
        used_budget += msg_data[best_idx]["cost"]

    # Return selected messages in original order
    result = [msg_data[i]["msg"] for i in sorted(selected_indices)]
    return result


# ── Message Splitting ────────────────────────────────────────────────────

def split_messages(messages: list, keep_recent_turns: int) -> tuple:
    if not messages:
        return [], []

    system_msgs = []
    conversation_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            conversation_msgs.append(msg)

    if len(conversation_msgs) <= keep_recent_turns * 2:
        return [], messages

    turn_count = 0
    split_idx = len(conversation_msgs)

    for i in range(len(conversation_msgs) - 1, -1, -1):
        role = conversation_msgs[i].get("role", "")
        if role == "user":
            turn_count += 1
            if turn_count >= keep_recent_turns:
                split_idx = i
                break

    old_msgs = conversation_msgs[:split_idx]
    recent_msgs = conversation_msgs[split_idx:]

    recent_with_system = system_msgs + recent_msgs

    return old_msgs, recent_with_system


# ── V4: ARC Citation Replacement ─────────────────────────────────────────

def apply_arc_citations(messages: list) -> list:
    """
    V4: Addressable Recall Compaction — replace lengthy tool_results with short ID references.
    Original content stored in ARC log, LLM can recall via ID.
    (arXiv:2607.25066)
    """
    result = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            new_blocks = []
            modified = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result_text = ""
                    if isinstance(block.get("content"), str):
                        result_text = block["content"]
                    elif isinstance(block.get("content"), list):
                        for r in block["content"]:
                            if isinstance(r, dict) and r.get("type") == "text":
                                result_text += r.get("text", "")

                    # tool_results over 800 chars get ARC reference
                    if len(result_text) > 800:
                        arc_id = arc_log.store(result_text, source_msg_idx=i)
                        citation = arc_log.make_citation(arc_id, preview_len=100)
                        new_block = dict(block)
                        new_block["content"] = (
                            f"[ARC Reference: {citation}]\n"
                            f"Full content available via ID: {arc_id}"
                        )
                        new_blocks.append(new_block)
                        modified = True
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)

            if modified:
                new_msg = dict(msg)
                new_msg["content"] = new_blocks
                result.append(new_msg)
            else:
                result.append(msg)
        else:
            result.append(msg)

    return result


# ── Compaction Prompts ───────────────────────────────────────────────────

COMPACTION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Progress
### Done
- [x] [Completed tasks/changes with file paths]

### In Progress
- [ ] [Current work]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [File paths, function names, error messages, config values]
- [Any data or references needed to continue]
- [ARC references: if you see [ARC-XXXX] IDs, list them as available for recall]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve EXACT file paths, function names, and error messages.
Write the summary in the primary language used in the conversation."""

COMPACTION_USER_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Focus on factual content: what was discussed, decisions made, and current state.
Preserve exact file paths, function names, and error messages.
If you see [ARC-XXXX] references, note them as available for recall in the Critical Context section."""


# ── V4: Format Messages for Compaction (Enhanced) ────────────────────────

def format_messages_for_compaction(messages: list) -> str:
    """
    V4→V6: Format messages for compaction prompt text.
    Enhanced with:
    - Thought masking: strip reasoning_content from assistant messages
    - Secret redaction: redact_secrets on all text content
    - Tag-based retention: classify_tool_fidelity for tool_result blocks
    - Structure-aware compression: auto_compress_output for tool results
    - V6 AFM fidelity: message-level fidelity affects formatting budget
      - FULL fidelity → larger budget, preserve more detail
      - COMPRESSED fidelity → standard budget
      - PLACEHOLDER fidelity → minimal budget, ARC reference if long
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        afm_fidelity = msg.get("_afm_fidelity", FIDELITY_COMPRESSED)

        # V4: Thought masking — strip reasoning_content from assistant messages
        if role == "assistant" and isinstance(msg, dict):
            # We don't modify the original msg, just skip reasoning_content in output
            pass  # reasoning_content handled below in content extraction

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        raw_text = block.get("text", "")
                        # V4: Secret redaction on text content
                        text = redact_secrets(raw_text)
                        text_parts.append(text)
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            inp_json = json.dumps(inp, ensure_ascii=False)
                        else:
                            inp_json = str(inp)
                        # V4: Secret redaction on tool input
                        inp_json = redact_secrets(inp_json)
                        if len(inp_json) > 1000:
                            inp_json = inp_json[:500] + "\n... [truncated] ...\n" + inp_json[-400:]
                        text_parts.append(f"[Tool call: {name}({inp_json})]")
                    elif block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        result = block.get("content", "")

                        # V4: Tag-based retention — classify tool fidelity
                        # Find the tool name from the corresponding tool_use message
                        # We use a heuristic: check the tool_use_id against recent messages
                        tool_name = _find_tool_name_by_id(messages, tool_use_id)
                        tool_fidelity = classify_tool_fidelity(tool_name) if tool_name else FIDELITY_COMPRESSED

                        if tool_fidelity == FIDELITY_PLACEHOLDER or tool_fidelity == "verbose":
                            # VERBOSE tools -> just put ARC reference placeholder
                            if isinstance(result, str):
                                if len(result) > 200:
                                    arc_id = arc_log.store(result)
                                    citation = arc_log.make_citation(arc_id, preview_len=80)
                                    text_parts.append(f"[Tool result: [ARC Reference: {citation}]]")
                                else:
                                    text_parts.append(f"[Tool result: {redact_secrets(result)}]")
                            elif isinstance(result, list):
                                combined = ""
                                for r in result:
                                    if isinstance(r, dict) and r.get("type") == "text":
                                        combined += r.get("text", "")
                                if len(combined) > 200:
                                    arc_id = arc_log.store(combined)
                                    citation = arc_log.make_citation(arc_id, preview_len=80)
                                    text_parts.append(f"[Tool result: [ARC Reference: {citation}]]")
                                else:
                                    text_parts.append(f"[Tool result: {redact_secrets(combined)}]")
                        elif tool_fidelity == FIDELITY_FULL or tool_fidelity == "critical":
                            # CRITICAL tools -> full content (with secret redaction)
                            if isinstance(result, str):
                                text_parts.append(f"[Tool result: {redact_secrets(result)}]")
                            elif isinstance(result, list):
                                for r in result:
                                    if isinstance(r, dict) and r.get("type") == "text":
                                        text_parts.append(f"[Tool result: {redact_secrets(r.get('text', ''))}]")
                        else:
                            # INFO/CODE tools -> structure-aware compression
                            if isinstance(result, str):
                                compressed = auto_compress_output(result, budget=800)
                                text_parts.append(f"[Tool result: {redact_secrets(compressed)}]")
                            elif isinstance(result, list):
                                for r in result:
                                    if isinstance(r, dict) and r.get("type") == "text":
                                        compressed = auto_compress_output(r.get("text", ""), budget=800)
                                        text_parts.append(f"[Tool result: {redact_secrets(compressed)}]")
                    elif block.get("type") == "image":
                        text_parts.append("[Image attached]")
                else:
                    text_parts.append(str(block))
            content = "\n".join(text_parts)
        elif isinstance(content, str):
            # V4: Secret redaction on string content
            content = redact_secrets(content)
            # V4: Thought masking — skip reasoning_content
            if role == "assistant" and msg.get("reasoning_content"):
                # Don't include reasoning_content in compaction text
                pass
        else:
            content = str(content)

        # Smart truncation (role-based budget, V6: AFM fidelity-aware)
        if afm_fidelity == FIDELITY_FULL:
            # FULL fidelity → preserve more content
            if role == "system":
                budget = 8000
            elif role in ("user", "assistant"):
                budget = 6000
            else:
                budget = 4000
        elif afm_fidelity == FIDELITY_PLACEHOLDER:
            # PLACEHOLDER fidelity → minimal content, ARC reference if long
            if role == "system":
                budget = 2000
            elif role in ("user", "assistant"):
                budget = 500
            else:
                budget = 300
        else:
            # COMPRESSED fidelity → standard budget
            if role == "system":
                budget = 6000
            elif role in ("user", "assistant"):
                budget = 4000
            else:
                budget = 3000

        if len(content) > budget:
            # V6: For PLACEHOLDER fidelity, use ARC reference instead of truncation
            if afm_fidelity == FIDELITY_PLACEHOLDER and len(content) > 500:
                arc_id = arc_log.store(content)
                citation = arc_log.make_citation(arc_id, preview_len=120)
                content = f"[AFM-PLACEHOLDER: {citation}]"
            else:
                content = smart_truncate(content, budget)

        # V6: Add fidelity tag for compaction prompt awareness
        fidelity_tag = ""
        if afm_fidelity == FIDELITY_FULL:
            fidelity_tag = " [HIGH-FIDELITY]"
        elif afm_fidelity == FIDELITY_PLACEHOLDER:
            fidelity_tag = " [LOW-FIDELITY]"

        parts.append(f"[{role}{fidelity_tag}]: {content}")

    return "\n\n".join(parts)


def _find_tool_name_by_id(messages: list, tool_use_id: str) -> str:
    """V4: Helper to find the tool name corresponding to a tool_use_id."""
    if not tool_use_id:
        return ""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("id") == tool_use_id:
                        return block.get("name", "")
    return ""


# ── V4: Compact Messages (Enhanced with Incremental Compaction) ──────────

# V5: _prior_summary / _last_compaction_msg_count removed from globals.
# Per-session state is now stored in SessionStore (SQLite).
# compact_messages() receives session_id as parameter instead of using globals.


async def compact_messages(
    old_messages: list,
    api_key: str,
    session: aiohttp.ClientSession,
    session_id: str = "default",
    _save_state: bool = True,
    selected_skills: list = None,
    system_prompt_override: str = None,
) -> Optional[str]:
    """
    V4: Compress old messages into a structured summary using LLM.
    Enhanced with incremental compaction: if a prior summary exists and
    there are new messages since last compaction, only summarize the delta.
    V5: session_id parameter replaces global _current_session_id hack.
    _save_state: when False (parallel blocks), don't save prior_summary
    to avoid corrupting incremental state; caller saves after merge.
    V7: selected_skills — MemSkill skills that modify compaction prompts and behavior.
    """
    # V5: Load per-session prior_summary from SessionStore
    prior_summary = None
    last_compaction_msg_count = 0
    if session_store:
        ps, mc = session_store.get_prior_summary(session_id)
        if ps is not None:
            prior_summary = ps
            last_compaction_msg_count = mc

    if not old_messages:
        return None

    if not compaction_breaker.can_attempt():
        logger.warning("Compaction circuit breaker is OPEN, skipping compaction")
        metrics.inc("compaction_circuit_breaker_skip")
        return None

    cached = compaction_cache.get(old_messages, salt=system_prompt_override or "")
    if cached is not None:
        logger.info(f"Compaction cache hit ({len(cached)} chars)")
        metrics.inc("compaction_cache_hit")
        return cached

    metrics.inc("compaction_cache_miss")

    # V4: Incremental compaction — if prior summary exists and there are new messages
    if prior_summary and len(old_messages) > last_compaction_msg_count:
        new_msgs = old_messages[last_compaction_msg_count:]
        conversation_text = format_messages_for_compaction(new_msgs)

        if len(conversation_text) < 50:
            # Too little new content, just return prior summary
            return prior_summary

        # Build incremental compaction request
        system_content = system_prompt_override if system_prompt_override else COMPACTION_SYSTEM_PROMPT
        identifiers = extract_identifiers(new_msgs)
        identifier_note = ""
        if identifiers:
            id_list = "\n".join(f"- {id}" for id in identifiers[:30])
            identifier_note = (
                "\n\n## MANDATORY PRESERVATION LIST\n"
                "The following identifiers MUST appear verbatim in your summary:\n"
                f"{id_list}\n"
            )
        system_content += identifier_note

        # V7: Inject MemSkill prompt additions
        if selected_skills:
            for skill in selected_skills:
                append = skill.prompt_additions.get("system_prompt_append", "")
                if append:
                    system_content += "\n\n" + append

        commitments = extract_commitments(new_msgs)
        commitment_note = format_commitments_for_prompt(commitments)
        if commitment_note:
            system_content += "\n\n" + commitment_note

        # V4: Inject semantic memory into system prompt
        if semantic_memory:
            system_content += "\n\n" + semantic_memory.format_for_prompt()

        payload = {
            "model": COMPACTION_MODEL,
            "max_tokens": 2000,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"PRIOR SUMMARY:\n{prior_summary}\n\nNEW MESSAGES SINCE LAST COMPACTION:\n\n{conversation_text}\n\nUpdate the prior summary with these new messages."},
            ],
        }
    else:
        # Full compaction (no prior summary or message count reset)
        conversation_text = format_messages_for_compaction(old_messages)

        if len(conversation_text) < 200:
            # V6 fix: Return a minimal structured summary instead of None,
            # so callers (do_compaction, handle_summarize) don't fail on short conversations.
            logger.info(f"Compaction: conversation too short ({len(conversation_text)} chars), returning minimal summary")
            return f"## Brief Exchange\n{conversation_text}"

        identifiers = extract_identifiers(old_messages)
        identifier_note = ""
        if identifiers:
            id_list = "\n".join(f"- {id}" for id in identifiers[:30])
            identifier_note = (
                "\n\n## MANDATORY PRESERVATION LIST\n"
                "The following identifiers MUST appear verbatim in your summary:\n"
                f"{id_list}\n"
            )

        commitments = extract_commitments(old_messages)
        commitment_note = format_commitments_for_prompt(commitments)

        system_content = (system_prompt_override if system_prompt_override else COMPACTION_SYSTEM_PROMPT) + identifier_note
        if commitment_note:
            system_content += "\n\n" + commitment_note

        # V7: Inject MemSkill prompt additions
        if selected_skills:
            for skill in selected_skills:
                append = skill.prompt_additions.get("system_prompt_append", "")
                if append:
                    system_content += "\n\n" + append

        # V4: Inject semantic memory into system prompt
        if semantic_memory:
            system_content += "\n\n" + semantic_memory.format_for_prompt()

        payload = {
            "model": COMPACTION_MODEL,
            "max_tokens": 2000,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"CONVERSATION TO SUMMARIZE:\n\n{conversation_text}"},
                {"role": "assistant", "content": "I will create a structured summary of this conversation."},
                {"role": "user", "content": COMPACTION_USER_PROMPT},
            ],
        }

    # V6: Provider-aware compaction LLM call
    compaction_upstream = COMPACTION_UPSTREAM or UPSTREAM_BASE
    compaction_api_key = COMPACTION_API_KEY or api_key

    # Determine provider for compaction
    if COMPACTION_PROVIDER == "auto":
        compaction_provider = detect_provider(COMPACTION_MODEL, {}, compaction_upstream)
    elif COMPACTION_PROVIDER == "anthropic":
        compaction_provider = AnthropicProvider()
    elif COMPACTION_PROVIDER == "gemini":
        compaction_provider = GeminiProvider()
    else:
        compaction_provider = OpenAIProvider()

    # Build request using provider
    headers = compaction_provider.build_compaction_headers(compaction_api_key)
    provider_payload = compaction_provider.build_compaction_payload(
        COMPACTION_MODEL, payload.get("messages", []),
        payload.get("max_tokens", 2000), payload.get("temperature", 0.3)
    )
    url = compaction_provider.build_compaction_url(compaction_upstream)

    # V6: Gemini special handling — model in URL, key via x-goog-api-key header
    if isinstance(compaction_provider, GeminiProvider):
        url = f"{compaction_upstream.rstrip('/')}/v1beta/models/{COMPACTION_MODEL}:generateContent"

    try:
        async with session.post(
            url, headers=headers, data=json.dumps(provider_payload).encode(),
            timeout=aiohttp.ClientTimeout(total=COMPACTION_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                error_body = await resp.read()
                error_text = error_body.decode("utf-8", errors="replace")[:300]
                logger.error(f"Compaction model error {resp.status}: {error_text}")
                compaction_breaker.record_failure()
                metrics.inc("compaction_model_error")
                return None

            result = await resp.read()
            data = json.loads(result)

            # V6: Provider-aware content extraction
            content = compaction_provider.extract_compaction_content(data)

            # V6 fix: If non-streaming response has only empty thinking blocks
            # (xsparkx2agent bug), retry as streaming to capture content
            if not content and isinstance(compaction_provider, AnthropicProvider):
                content_blocks = data.get("content", [])
                has_thinking_only = (
                    not any(b.get("type") == "text" and b.get("text", "").strip() for b in content_blocks) and
                    any(b.get("type") == "thinking" for b in content_blocks) and
                    not any(b.get("type") == "tool_use" for b in content_blocks)
                )
                if has_thinking_only:
                    logger.info("Compaction: non-streaming response has only empty thinking block, retrying as streaming")
                    stream_payload = dict(provider_payload)
                    stream_payload["stream"] = True
                    try:
                        collected_text = []
                        async with session.post(
                            url, headers=headers, data=json.dumps(stream_payload).encode(),
                            timeout=aiohttp.ClientTimeout(total=COMPACTION_TIMEOUT),
                        ) as stream_resp:
                            if stream_resp.status == 200:
                                async for line_bytes in stream_resp.content:
                                    line = line_bytes.decode("utf-8", errors="replace").strip()
                                    if not line.startswith("data: "):
                                        continue
                                    payload_str = line[6:]
                                    if payload_str == "[DONE]":
                                        break
                                    try:
                                        evt = json.loads(payload_str)
                                        evt_type = evt.get("type", "")
                                        if evt_type == "content_block_delta":
                                            delta = evt.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                collected_text.append(delta.get("text", ""))
                                            elif delta.get("type") == "thinking_delta":
                                                collected_text.append(delta.get("thinking", ""))
                                    except (json.JSONDecodeError, KeyError):
                                        continue
                        if collected_text:
                            content = "".join(collected_text)
                            logger.info(f"Compaction streaming fallback captured {len(content)} chars")
                    except Exception as e:
                        logger.warning(f"Compaction streaming fallback failed: {e}")

            if not content:
                logger.error("Compaction model returned empty content")
                compaction_breaker.record_failure()
                metrics.inc("compaction_model_error")
                return None

            compaction_breaker.record_success()
            metrics.inc("compaction_success")
            logger.info(f"Compaction summary generated: {len(content)} chars (provider={compaction_provider.name})")

            compaction_cache.put(old_messages, content, salt=system_prompt_override or "")

            # V5: Persist per-session prior summary (no more global state)
            # Skip saving during parallel block compaction to avoid corruption
            if _save_state and session_store:
                session_store.save_prior_summary(session_id, content, len(old_messages))

            return content

    except asyncio.TimeoutError:
        logger.error(f"Compaction model timeout ({COMPACTION_TIMEOUT}s)")
        compaction_breaker.record_failure()
        metrics.inc("compaction_timeout")
        return None
    except Exception as e:
        logger.error(f"Compaction model error: {e}")
        compaction_breaker.record_failure()
        metrics.inc("compaction_error")
        return None


# ── V4: Parallel Compaction ──────────────────────────────────────────────

async def compact_messages_parallel(
    old_messages: list,
    api_key: str,
    session: aiohttp.ClientSession,
    num_blocks: int = PARALLEL_COMPACTION_BLOCKS,
    session_id: str = "default",
    selected_skills: list = None,
) -> Optional[str]:
    """
    V4: Parallel block compaction — split old messages into N blocks,
    compact each in parallel, then merge results.
    (arXiv:2605.23296)
    V5: session_id parameter for per-session state; incremental state
    is saved AFTER merge (not per-block) to avoid corruption.
    V7: selected_skills passed through to compact_messages.
    """
    if not old_messages:
        return None

    if not compaction_breaker.can_attempt():
        logger.warning("Compaction circuit breaker is OPEN, skipping")
        metrics.inc("compaction_circuit_breaker_skip")
        return None

    # Too few messages, no need for parallel
    if len(old_messages) < 6:
        return await compact_messages(old_messages, api_key, session, session_id=session_id,
                                      selected_skills=selected_skills)

    # Split into blocks
    block_size = max(2, len(old_messages) // num_blocks)
    blocks = []
    for i in range(0, len(old_messages), block_size):
        blocks.append(old_messages[i:i + block_size])

    # Ensure we don't exceed num_blocks
    if len(blocks) > num_blocks:
        while len(blocks) > num_blocks:
            last = blocks.pop()
            blocks[-1].extend(last)

    logger.info(f"Parallel compaction: {len(old_messages)} messages -> {len(blocks)} blocks")

    # Compact each block in parallel (V5: _save_state=False to avoid corrupting incremental state)
    tasks = []
    for block in blocks:
        tasks.append(compact_messages(block, api_key, session, session_id=session_id, _save_state=False,
                                      selected_skills=selected_skills))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect successful summaries
    summaries = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Block {i} compaction error: {result}")
            metrics.inc("compaction_block_error")
        elif result is not None:
            summaries.append(result)
        else:
            logger.warning(f"Block {i} compaction returned None")
            metrics.inc("compaction_block_empty")

    if not summaries:
        logger.error("All parallel compaction blocks failed")
        return None

    if len(summaries) == 1:
        return summaries[0]

    # Merge multiple summaries
    merged = await _merge_summaries(summaries, api_key, session)

    # V5: Save merged summary as the prior_summary for this session
    # (only save after successful merge, not per-block)
    if merged and session_store:
        session_store.save_prior_summary(session_id, merged, len(old_messages))

    return merged


# ── V4: Merge Summaries ──────────────────────────────────────────────────

async def _merge_summaries(
    summaries: list,
    api_key: str,
    session: aiohttp.ClientSession,
) -> Optional[str]:
    """Merge multiple parallel compaction summaries into one unified summary"""
    if not summaries:
        return None

    if len(summaries) == 1:
        return summaries[0]

    combined = "\n\n---\n\n".join(
        f"### Part {i+1}\n{s}" for i, s in enumerate(summaries)
    )

    # If combined is short enough, just concatenate
    if len(combined) < 3000:
        return combined

    # Otherwise use LLM to merge
    merge_prompt = (
        "Merge the following partial summaries into a single coherent summary. "
        "Preserve all goals, decisions, errors, file paths, and ARC references. "
        "Remove duplicates. Use the same structured format (Goal, Progress, Key Decisions, Next Steps, Critical Context)."
    )

    payload = {
        "model": COMPACTION_MODEL,
        "max_tokens": 2000,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a summary merging assistant. Merge partial summaries into one coherent summary."},
            {"role": "user", "content": f"{merge_prompt}\n\n{combined}"},
        ],
    }

    # V6: Provider-aware merge LLM call
    compaction_upstream = COMPACTION_UPSTREAM or UPSTREAM_BASE
    compaction_api_key = COMPACTION_API_KEY or api_key

    if COMPACTION_PROVIDER == "auto":
        merge_provider = detect_provider(COMPACTION_MODEL, {}, compaction_upstream)
    elif COMPACTION_PROVIDER == "anthropic":
        merge_provider = AnthropicProvider()
    elif COMPACTION_PROVIDER == "gemini":
        merge_provider = GeminiProvider()
    else:
        merge_provider = OpenAIProvider()

    headers = merge_provider.build_compaction_headers(compaction_api_key)
    provider_payload = merge_provider.build_compaction_payload(
        COMPACTION_MODEL, payload.get("messages", []),
        payload.get("max_tokens", 2000), payload.get("temperature", 0.2)
    )
    url = merge_provider.build_compaction_url(compaction_upstream)

    if isinstance(merge_provider, GeminiProvider):
        url = f"{compaction_upstream.rstrip('/')}/v1beta/models/{COMPACTION_MODEL}:generateContent"

    try:
        async with session.post(
            url, headers=headers, data=json.dumps(provider_payload).encode(),
            timeout=aiohttp.ClientTimeout(total=COMPACTION_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                error_body = await resp.read()
                logger.error(f"Summary merge error {resp.status}: {error_body.decode('utf-8', errors='replace')[:200]}")
                # Fallback: just concatenate
                return combined

            result = await resp.read()
            data = json.loads(result)

            # V6: Provider-aware content extraction
            content = merge_provider.extract_compaction_content(data)

            # V6 fix: streaming fallback for empty thinking blocks (same as compact_messages)
            if not content and isinstance(merge_provider, AnthropicProvider):
                content_blocks = data.get("content", [])
                has_thinking_only = (
                    not any(b.get("type") == "text" and b.get("text", "").strip() for b in content_blocks) and
                    any(b.get("type") == "thinking" for b in content_blocks) and
                    not any(b.get("type") == "tool_use" for b in content_blocks)
                )
                if has_thinking_only:
                    logger.info("Merge: non-streaming response has only empty thinking block, retrying as streaming")
                    stream_payload = dict(provider_payload)
                    stream_payload["stream"] = True
                    try:
                        collected_text = []
                        async with session.post(
                            url, headers=headers, data=json.dumps(stream_payload).encode(),
                            timeout=aiohttp.ClientTimeout(total=COMPACTION_TIMEOUT),
                        ) as stream_resp:
                            if stream_resp.status == 200:
                                async for line_bytes in stream_resp.content:
                                    line = line_bytes.decode("utf-8", errors="replace").strip()
                                    if not line.startswith("data: "):
                                        continue
                                    payload_str = line[6:]
                                    if payload_str == "[DONE]":
                                        break
                                    try:
                                        evt = json.loads(payload_str)
                                        evt_type = evt.get("type", "")
                                        if evt_type == "content_block_delta":
                                            delta = evt.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                collected_text.append(delta.get("text", ""))
                                            elif delta.get("type") == "thinking_delta":
                                                collected_text.append(delta.get("thinking", ""))
                                    except (json.JSONDecodeError, KeyError):
                                        continue
                        if collected_text:
                            content = "".join(collected_text)
                            logger.info(f"Merge streaming fallback captured {len(content)} chars")
                    except Exception as e:
                        logger.warning(f"Merge streaming fallback failed: {e}")

            return content if content else combined

    except Exception as e:
        logger.error(f"Summary merge error: {e}")
        return combined


# ── V4: Build Compacted Messages (Enhanced) ──────────────────────────────

def prune_old_compaction_artifacts(messages: list) -> list:
    """V4: Remove old compaction summaries/placeholders from before most recent compaction"""
    # Find the most recent compaction summary
    latest_compaction_idx = -1
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, str):
            if "[Context Compaction Summary]" in content or "[AGGRESSIVE TRUNCATION" in content:
                latest_compaction_idx = i
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if "[Context Compaction Summary]" in text or "[AGGRESSIVE TRUNCATION" in text:
                        latest_compaction_idx = i

    if latest_compaction_idx < 0:
        return messages

    # Remove any compaction artifacts BEFORE the latest one
    result = []
    for i, msg in enumerate(messages):
        if i < latest_compaction_idx:
            content = msg.get("content", "")
            is_artifact = False
            if isinstance(content, str):
                if "[Context Compaction Summary]" in content or "[AGGRESSIVE TRUNCATION" in content or "[ARC Reference:" in content:
                    is_artifact = True
            if not is_artifact:
                result.append(msg)
        else:
            result.append(msg)
    return result


def ensure_query_at_end(messages: list) -> list:
    """
    V4→V6: Query Placement Optimization — multi-rule placement framework.
    Rules:
    1. Latest user query must be the last non-system message
    2. tool_call + tool_result pairs must stay adjacent (no interleaving)
    3. System instructions come before tool results
    4. Recent context window: last N messages stay in original order at the tail
    """
    if not messages:
        return messages

    # Rule 4: Protect the recent context window (last 4 messages stay in order at tail)
    RECENT_WINDOW = 4
    if len(messages) <= RECENT_WINDOW:
        return messages  # Too short to reorder meaningfully

    recent_tail = messages[-RECENT_WINDOW:]
    head = messages[:-RECENT_WINDOW]

    # Rule 2: Fix broken tool_call/tool_result pairs in head
    head = _fix_tool_pair_adjacency(head)

    # Rule 3: System messages first in head
    system_msgs = [m for m in head if m.get("role") == "system"]
    non_system = [m for m in head if m.get("role") != "system"]

    # Rule 1: Find last user message in non_system and move to end of non_system
    last_user_idx = -1
    for i in range(len(non_system) - 1, -1, -1):
        if non_system[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx >= 0 and last_user_idx < len(non_system) - 1:
        user_msg = non_system.pop(last_user_idx)
        non_system.append(user_msg)

    result = system_msgs + non_system + recent_tail

    # Log placement decisions
    moved_count = sum(1 for a, b in zip(messages, result) if a is not b)
    if moved_count > 0:
        logger.info(f"Query placement: {moved_count} messages reordered (system-first, query-at-end, pairs-adjacent, recent-window-protected)")
        metrics.inc("query_placement_reorders")

    return result


def _fix_tool_pair_adjacency(messages: list) -> list:
    """V6: Ensure tool_call and tool_result pairs stay adjacent.
    Orphan tool_results (no matching tool_call) are removed.
    tool_calls without tool_results get a placeholder inserted after them.
    """
    if not messages:
        return messages

    # Build index of tool_use_ids
    tool_call_ids = set()
    tool_result_ids = set()
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        tool_call_ids.add(block.get("id", ""))
                    elif block.get("type") == "tool_result":
                        tool_result_ids.add(block.get("tool_use_id", ""))

    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Check if this is an orphan tool_result (no matching tool_call in messages)
        is_orphan_result = False
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid not in tool_call_ids:
                        is_orphan_result = True
                        break
        elif role == "tool":
            # Legacy format: role=tool with tool_call_id
            tid = msg.get("tool_call_id", "")
            if tid and tid not in tool_call_ids:
                is_orphan_result = True

        if is_orphan_result:
            continue  # Drop orphan tool results

        result.append(msg)

        # Check if this message has tool_calls without following tool_results
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    call_id = block.get("id", "")
                    if call_id and call_id not in tool_result_ids:
                        # Insert placeholder tool_result after this message
                        result.append({
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": "[Tool result not available — compacted]",
                            }],
                        })

    return result


def reorder_for_cache_efficiency(messages: list) -> list:
    """
    V5→V6: Cache-Optimized Message Ordering — real cache-aware ordering.
    Uses CachedSystemPrompt layer hashes to group messages by cache stability:
    1. Static layers (unchanged hash) → first for prefix cache hits
    2. Volatile layers (changed hash) → after static content
    3. Non-system messages grouped by volatility:
       - Compaction summaries / CCL commitments (semi-stable) → before recent
       - Recent conversation (volatile) → last
    4. Inject cache_control breakpoints at layer boundaries
    5. Latest user query at end (via ensure_query_at_end)
    """
    if not messages:
        return messages

    # Phase 1: Separate system messages and analyze their layer membership
    system_msgs = []
    non_system_msgs = []

    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            non_system_msgs.append(msg)

    # Phase 2: Order system messages by CachedSystemPrompt layer stability
    if system_msgs and cached_system_prompt._hashes:
        # Classify system messages by which layer they belong to
        static_system = []    # Layers whose hash hasn't changed
        volatile_system = []  # Layers whose hash changed

        for msg in system_msgs:
            content = msg.get("content", "")
            content_str = ""
            if isinstance(content, str):
                content_str = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content_str += block.get("text", "")

            # Check if this content matches any cached layer
            content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]
            is_static = False
            for layer_name in LAYER_ORDER:
                prev_hash = cached_system_prompt._prev_hashes.get(layer_name, "")
                curr_hash = cached_system_prompt._hashes.get(layer_name, "")
                if curr_hash == content_hash:
                    # This message matches a current layer
                    if prev_hash == curr_hash and prev_hash:
                        # Hash unchanged → static layer
                        is_static = True
                    break

            if is_static:
                static_system.append(msg)
            else:
                volatile_system.append(msg)

        system_msgs = static_system + volatile_system

        if static_system or volatile_system:
            logger.info(f"Cache ordering: system messages split into static={len(static_system)}, volatile={len(volatile_system)}")
            metrics.inc("cache_ordering_system_split")

    # Phase 3: Group non-system messages by volatility
    # Semi-stable: compaction summaries, CCL commitments, semantic memory
    semi_stable = []
    volatile = []

    for msg in non_system_msgs:
        content = msg.get("content", "")
        content_str = ""
        if isinstance(content, str):
            content_str = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    content_str += block.get("text", "")

        # Semi-stable indicators: compaction summaries, CCL, semantic memory
        semi_stable_markers = [
            "[Context Compaction Summary]",
            "[CCL Commitments]",
            "[Semantic Memory]",
            "[Compaction Summary]",
        ]
        is_semi_stable = any(marker in content_str for marker in semi_stable_markers)

        if is_semi_stable:
            semi_stable.append(msg)
        else:
            volatile.append(msg)

    # Phase 4: Assemble in cache-optimal order
    # static system → volatile system → semi-stable → volatile → query at end
    result = system_msgs + semi_stable + volatile

    # Phase 5: Apply query placement optimization (multi-rule)
    result = ensure_query_at_end(result)

    # Phase 6: Inject cache_control breakpoints at layer boundaries
    result = _inject_cache_breakpoints(result)

    # Log final ordering
    n_system = sum(1 for m in result if m.get("role") == "system")
    n_semi = sum(1 for m in result if any(
        marker in _msg_text(m) for marker in [
            "[Context Compaction Summary]", "[CCL Commitments]",
            "[Semantic Memory]", "[Compaction Summary]",
        ]
    ))
    n_volatile = len(result) - n_system - n_semi
    logger.info(f"Cache ordering result: system={n_system}, semi-stable={n_semi}, volatile={n_volatile}, total={len(result)}")
    metrics.inc("cache_ordering_applied")

    return result


def _msg_text(msg: dict) -> str:
    """Helper: extract text content from a message for analysis."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return ""


def _inject_cache_breakpoints(messages: list) -> list:
    """V6: Inject Anthropic cache_control breakpoints at layer boundaries.
    Places cache_control markers at transitions between:
    - System messages → non-system messages
    - Semi-stable → volatile messages
    This enables Anthropic's prompt caching to reuse prefix tokens.
    """
    if not messages:
        return messages

    result = []
    prev_category = None  # "system", "semi_stable", "volatile"

    semi_stable_markers = [
        "[Context Compaction Summary]", "[CCL Commitments]",
        "[Semantic Memory]", "[Compaction Summary]",
    ]

    for msg in messages:
        # Determine category
        if msg.get("role") == "system":
            category = "system"
        elif any(marker in _msg_text(msg) for marker in semi_stable_markers):
            category = "semi_stable"
        else:
            category = "volatile"

        # Inject cache_control at boundary transitions
        if prev_category is not None and category != prev_category:
            # Add cache_control breakpoint to the LAST message of the previous category
            if result:
                last_msg = result[-1]
                _add_cache_control_to_msg(last_msg)

        result.append(msg)
        prev_category = category

    # Always add cache_control at the very end (last message)
    if result:
        _add_cache_control_to_msg(result[-1])

    return result


def _add_cache_control_to_msg(msg: dict) -> None:
    """V6: Add cache_control breakpoint to a message's content blocks.
    Mutates the message in place. Only adds if not already present.
    """
    content = msg.get("content", "")

    if isinstance(content, list):
        # Find the last text block and add cache_control
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") in ("text", "tool_result"):
                if "cache_control" not in block:
                    block["cache_control"] = {"type": "ephemeral"}
                    return
        # No text block found — add one
        content.append({
            "type": "text",
            "text": "",
            "cache_control": {"type": "ephemeral"},
        })
    elif isinstance(content, str):
        # Convert to block format with cache_control
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}},
        ]


def _build_layer_content(summary: str, recent_msgs: list) -> dict:
    """
    V5: Build content for each CachedSystemPrompt layer from compaction data.
    Returns a dict of {layer_name: content_string}.

    Layers (from most stable to most volatile):
    1. core_system          — Original system instructions (never changes)
    2. user_profile         — User profile text (changes rarely)
    3. semantic_memory      — Semantic memory (changes on compaction)
    4. background_sessions  — Background session knowledge (changes per session)
    5. compaction_summary   — Compaction summary (changes on each compaction)
    6. ccl_commitments      — CCL commitments (changes on each compaction)
    7. identifier_preservation — Identifier preservation list (changes on each compaction)
    8. tool_retention       — Tool retention policy (static)
    9. safety_instructions  — Safety instructions (static)
    10. session_context     — Session-specific context (changes per request)
    """
    layers = {}

    # Layer 1: Core system instructions — extract from existing system messages
    core_parts = []
    for msg in recent_msgs:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                core_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        core_parts.append(block.get("text", ""))
    if core_parts:
        layers["core_system"] = "\n".join(core_parts)

    # Layer 2: User profile
    if user_profile and user_profile.has_profile:
        profile_text = user_profile.format_for_prompt()
        if profile_text:
            layers["user_profile"] = profile_text

    # Layer 3: Semantic memory
    if semantic_memory:
        sem_mem_text = semantic_memory.format_for_prompt()
        if sem_mem_text:
            layers["semantic_memory"] = f"\n\n---\n**[Semantic Memory]**\n{sem_mem_text}"

    # Layer 4: Background session knowledge
    if session_store:
        try:
            bg_summaries = session_store.get_background_knowledge(BACKGROUND_SESSIONS)
            if bg_summaries:
                bg_text = "\n\n---\n**[Background Knowledge from Recent Sessions]**\n"
                for i, bs in enumerate(bg_summaries):
                    bg_text += f"\n### Previous Session {i+1}\n{bs[:800]}\n"
                layers["background_sessions"] = bg_text
        except Exception as e:
            logger.warning(f"Background knowledge layer error (non-fatal): {e}")

    # Layer 5: Compaction summary
    if summary:
        layers["compaction_summary"] = (
            "\n\n---\n**[Context Compaction Summary]**\n"
            "The conversation history before this point was compacted into the following summary:\n\n"
            f"{summary}\n\n"
            "Recent messages are preserved verbatim below. Continue the conversation from where it left off."
        )

    # Layer 6: CCL commitments
    commitments = extract_commitments(recent_msgs)
    if commitments:
        ccl_text = format_commitments_for_prompt(commitments)
        if ccl_text:
            layers["ccl_commitments"] = ccl_text

    # Layer 7: Identifier preservation list
    identifiers = extract_identifiers(recent_msgs)
    if identifiers:
        id_lines = ["## IDENTIFIERS TO PRESERVE (do not modify or forget these)"]
        for ident in identifiers[:30]:
            id_lines.append(f"- {ident}")
        layers["identifier_preservation"] = "\n".join(id_lines)

    # Layer 8: Tool retention policy (static)
    layers["tool_retention"] = (
        "## TOOL OUTPUT RETENTION POLICY\n"
        "- Tool results with status codes, error messages: RETAIN in full\n"
        "- Tool results with file contents: COMPRESS to key excerpts\n"
        "- Tool results with search/list output: KEEP first 5 items, summarize rest\n"
        "- Tool results with confirmation messages: PLACEHOLDER only"
    )

    # Layer 9: Safety instructions (static)
    layers["safety_instructions"] = (
        "## SAFETY INSTRUCTIONS\n"
        "- Never reveal API keys, passwords, or authentication tokens\n"
        "- Preserve all file paths and function names exactly as given\n"
        "- Maintain all user-specified constraints and requirements\n"
        "- If uncertain about any preserved context, ask for clarification"
    )

    # Layer 10: Session-specific context (volatile — changes per request)
    # This layer is set per-request, not during compaction
    # It will be populated by inject_session_context() before forwarding

    return layers


def inject_session_context(body: dict, session_id: str = "default") -> dict:
    """
    V5: Inject session-specific context into the CachedSystemPrompt's
    most volatile layer (session_context) before forwarding a request.
    This is called on every request, not just during compaction.
    """
    # Build session context from current request state
    session_parts = []
    messages = body.get("messages", [])
    msg_count = len(messages)

    # Current session metadata
    session_parts.append(f"Current session: {session_id}")
    session_parts.append(f"Message count: {msg_count}")

    # Latest user intent (from last user message)
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                # First 200 chars of latest user message as intent hint
                session_parts.append(f"Latest user intent: {content[:200]}")
            break

    session_text = f"## SESSION CONTEXT\n" + "\n".join(f"- {p}" for p in session_parts)
    cached_system_prompt.set_layer("session_context", session_text)

    return body


def build_compacted_messages(summary: str, recent_msgs: list) -> list:
    """
    V5: Build compacted message list using CachedSystemPrompt layers.
    Each layer is independently tracked for cache efficiency.
    Layers are assembled into the system message with prefix boundaries.

    Enhancement over V4:
    - Instead of concatenating all context into one string, we build
      10 independently cacheable layers
    - Each layer has a content hash; unchanged layers get cache hits
    - Anthropic-style cache_control breakpoints are injected at layer boundaries
    - Metrics track cache hit rates and estimated token savings
    """
    # Build layer content from compaction data
    layer_content = _build_layer_content(summary, recent_msgs)

    # Set all layers in the cached system prompt
    for name, content in layer_content.items():
        cached_system_prompt.set_layer(name, content)

    # Assemble the system prompt from layers (tracks cache hits)
    assembled_prompt = cached_system_prompt.assemble()

    # Log cache efficiency
    boundary = cached_system_prompt.get_cache_boundary()
    hit_rate = cached_system_prompt.get_last_hit_rate()
    logger.info(
        f"CachedSystemPrompt: cache_boundary=layer_{boundary}, "
        f"hit_rate={hit_rate:.2%}, "
        f"active_layers={sum(1 for n in LAYER_ORDER if cached_system_prompt.get_layer(n))}"
    )

    # Update metrics
    metrics.inc("system_prompt_cache_layers_total", len(LAYER_ORDER))
    active_layers = sum(1 for n in LAYER_ORDER if cached_system_prompt.get_layer(n))
    hit_layers = max(0, boundary + 1) if boundary >= 0 else 0
    metrics.inc("system_prompt_cache_layers_hit", hit_layers)
    metrics.inc("system_prompt_cache_layers_miss", active_layers - hit_layers)

    # Build the result message list
    result = []
    system_merged = False

    for msg in recent_msgs:
        if msg.get("role") == "system" and not system_merged:
            original_content = msg.get("content", "")

            # Try to use Anthropic-style cache_control blocks if the content
            # is already in block format (Anthropic API)
            if isinstance(original_content, list):
                # Use format_for_anthropic_api to inject cache breakpoints
                layer_blocks = cached_system_prompt.format_for_anthropic_api(original_content)
                merged = {"role": "system", "content": layer_blocks}
            elif isinstance(original_content, str):
                # For string content, append assembled layers
                # Check if we should use block format for cache_control
                # (only if the upstream API supports it)
                merged = {"role": "system", "content": original_content + assembled_prompt}
            else:
                merged = {"role": "system", "content": str(original_content) + assembled_prompt}

            result.append(merged)
            system_merged = True
        else:
            result.append(msg)

    if not system_merged:
        # No system message found — create one with assembled layers
        # Use Anthropic-style blocks for cache_control support
        layer_blocks = cached_system_prompt.format_for_anthropic_api("")
        if layer_blocks and any(b.get("text", "") for b in layer_blocks if isinstance(b, dict)):
            result.insert(0, {"role": "system", "content": layer_blocks})
        else:
            # Fallback to simple string
            result.insert(0, {
                "role": "system",
                "content": (
                    "The conversation history before this point was compacted into the following summary:\n\n"
                    f"{summary}\n\n"
                    "Recent messages are preserved verbatim below. Continue the conversation from where it left off."
                ),
            })

    # V4: Prune old compaction artifacts
    result = prune_old_compaction_artifacts(result)

    # V5: Cache-optimized ordering (uses layer-aware ordering)
    result = reorder_for_cache_efficiency(result)

    return result


# ── HTTP Handling Functions ──────────────────────────────────────────────

_session: Optional[aiohttp.ClientSession] = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=600, connect=10)
        _session = aiohttp.ClientSession(timeout=timeout)
    return _session


def extract_api_key(headers: dict, provider: "ProviderAdapter" = None) -> str:
    """V6: Provider-aware API key extraction."""
    if provider:
        return provider.extract_api_key(headers)
    # Fallback: try Bearer, then x-api-key
    auth = headers.get("Authorization", headers.get("authorization", ""))
    if auth.startswith("Bearer "):
        return auth[7:]
    key = headers.get("x-api-key", headers.get("X-Api-Key", ""))
    if key:
        return key
    return ""


def clean_request_headers(headers: dict, extra_skip=()) -> dict:
    skip = {"host", "content-length", "transfer-encoding", "accept-encoding"}
    skip.update(h.lower() for h in extra_skip)
    clean = {}
    for key, value in headers.items():
        if key.lower() not in skip:
            clean[key] = value
    return clean


async def forward_request(
    session: aiohttp.ClientSession,
    path: str,
    method: str,
    headers: dict,
    body: bytes,
    provider: "ProviderAdapter" = None,
) -> tuple:
    """V6: Provider-aware request forwarding."""
    # Determine upstream URL
    upstream_url = UPSTREAM_BASE.rstrip("/") + path

    # V6: Provider-aware header cleaning
    clean_headers = clean_request_headers(headers)

    # V6: If provider specified, let it adjust headers
    if provider:
        api_key = provider.extract_api_key(headers)
        clean_headers = provider.build_forward_headers(clean_headers, api_key)

    try:
        async with session.request(
            method=method,
            url=upstream_url,
            headers=clean_headers,
            data=body,
        ) as resp:
            resp_body = await resp.read()
            resp_headers = {}
            for key, value in resp.headers.items():
                lower = key.lower()
                if lower not in ("transfer-encoding", "content-encoding", "content-length", "connection"):
                    resp_headers[key] = value
            return resp.status, resp_body, resp_headers
    except aiohttp.ClientError as e:
        logger.error(f"Upstream connection error: {e}")
        return 502, f'{{"error":{{"message":"Upstream error: {e}","type":"proxy_error"}}}}'.encode(), {}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Unexpected upstream error: {e}")
        return 500, f'{{"error":{{"message":"Internal proxy error","type":"proxy_error"}}}}'.encode(), {}


# ── Preemptive Compaction Check ──────────────────────────────────────────

def should_compact_preemptively(body: dict) -> bool:
    """
    V4: Two-stage token estimation pre-screening.
    Stage 1: Fast heuristic estimation (chars/3)
    Stage 2: If Stage 1 is near threshold, use CJK-aware accurate estimation
    """
    model = body.get("model", "")
    messages = body.get("messages", [])
    context_limit = get_model_context_limit(model)

    # Deduct response budget and safety margin
    usable_limit = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
    threshold = int(usable_limit * PREEMPTIVE_THRESHOLD)

    # Stage 1: Fast estimation
    fast_est = estimate_tokens_fast(messages)
    if fast_est < threshold * 0.7:
        # Clearly won't overflow, skip
        return False

    # Stage 2: Accurate estimation (near threshold)
    accurate_est = estimate_tokens_accurate(messages)

    if accurate_est > threshold:
        logger.info(
            f"Preemptive compaction: estimated {accurate_est} tokens > "
            f"{PREEMPTIVE_THRESHOLD*100:.0f}% of {usable_limit} (threshold={threshold}) "
            f"for model={model} (fast_est={fast_est})"
        )
        return True
    return False


# ── V4: Preflight Safety Verification ────────────────────────────────────

def verify_compaction_safety(original_body: dict, compacted_body: dict) -> bool:
    """
    V4: Preflight safety verification — compacted token count must not exceed original.
    If it does, degrade to more aggressive truncation.
    (arXiv:2606.03618 safety pattern)
    """
    orig_tokens = estimate_tokens_accurate(original_body.get("messages", []))
    comp_tokens = estimate_tokens_accurate(compacted_body.get("messages", []))

    if comp_tokens >= orig_tokens:
        logger.warning(
            f"Compaction safety check FAILED: compacted {comp_tokens} >= original {orig_tokens} tokens. "
            f"Will apply aggressive truncation instead."
        )
        return False
    return True


def aggressive_truncate_messages(messages: list, target_tokens: int) -> list:
    """
    V4: Aggressive truncation fallback — when compaction still exceeds original token count.
    Keep system + last 2 turns, discard everything else.
    """
    system_msgs = []
    conversation_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            conversation_msgs.append(msg)

    # Only keep last 2 turns (4 messages)
    recent = conversation_msgs[-4:] if len(conversation_msgs) > 4 else conversation_msgs

    # Add truncation notice
    truncation_notice = {
        "role": "system",
        "content": (
            "[AGGRESSIVE TRUNCATION APPLIED]\n"
            "The conversation was too long and could not be compressed effectively. "
            "Only the most recent messages are preserved. "
            "Earlier context has been lost. Please re-state any important context if needed."
        ),
    }

    return system_msgs + [truncation_notice] + recent


# (semantic_memory instance already created above near SemanticMemory class)


# ── V4: Do Compaction (Enhanced with all V4 features) ────────────────────

async def do_compaction(
    body: dict,
    api_key: str,
    session: aiohttp.ClientSession,
    is_preemptive: bool = False,
    session_id: str = "default",
) -> Optional[dict]:
    """
    V4: Execute compaction flow, integrating all V4 enhancements.
    1. Thrashing detection -> if thrashing, aggressive truncation
    2. Message splitting -> old messages + recent N turns
    3. Knowledge extraction into semantic memory (V4)
    4. ARC citation replacement -> lengthy tool_results replaced with IDs
    5. AFM fidelity classification -> mark fidelity level per message
    6. Submodular selection -> select most important old messages under token budget
    7. Parallel block compaction -> block-parallel compaction of old messages
    8. Preflight safety verification -> compacted tokens must not exceed original
    9. Prune old compaction artifacts (V4)
    10. Cache-optimized ordering (V4)
    11. Incremental compaction (V4)
    """
    messages = body.get("messages", [])
    original_model = body.get("model", "?")

    # V7: MemSkill Step 0 — Build CompactionContext and select skills
    selected_skills = None
    if MEMSKILL_ENABLED and skill_registry and skill_controller:
        is_thrashing_now = thrashing_detector.is_thrashing()
        compaction_ctx = CompactionContext.from_messages(
            messages, original_model, session_id,
            is_preemptive=is_preemptive, is_thrashing=is_thrashing_now,
        )
        selected_skills = skill_controller.select_skills(compaction_ctx)
        if selected_skills:
            logger.info(f"MemSkill: {len(selected_skills)} skills selected for session {session_id}")
    # V7: Consume skills pre-set by MemSkillAwareEngine. Always pop so the
    # internal "_memskill_selected" field never leaks to the upstream API.
    if "_memskill_selected" in body:
        pre_set_skills = body.pop("_memskill_selected")
        if selected_skills is None:
            selected_skills = pre_set_skills

    # V5 FIX: Save original messages BEFORE any modification for safety verification
    original_messages = list(messages)  # shallow copy for safety check

    # Thrashing detection
    thrashing_detector.record_compaction()
    if thrashing_detector.is_thrashing():
        logger.warning(
            "THRASHING DETECTED: compaction triggered too frequently. "
            "Applying aggressive truncation instead of LLM compaction."
        )
        metrics.inc("thrashing_detected")
        context_limit = get_model_context_limit(original_model)
        target = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
        truncated = aggressive_truncate_messages(messages, target)
        body["messages"] = truncated
        return body

    # V5: Pre-compact hook
    if hook_manager:
        messages, should_proceed = await hook_manager.call_pre_compact(
            messages, {"session_id": session_id, "model": original_model, "preemptive": is_preemptive}
        )
        if not should_proceed:
            logger.info("Compaction blocked by pre-compact hook")
            metrics.inc("compaction_blocked_by_hook")
            return None
        body["messages"] = messages

    # V5: session_id is now passed as parameter to compact_messages (no more function attribute hack)

    # V5: Check if pluggable compression engine wants to handle this
    use_custom_engine = (
        compression_engine is not None
        and compression_engine.name != "default"
        and compression_engine.should_compress(messages, original_model)
    )

    # V6: Adaptive keep_turns — when messages are few, reduce keep_turns so
    # split_messages() actually produces old_messages to compact.
    # Callers (e.g. handle_manual_compact) may pass body["keep_recent_turns"].
    user_msg_count = sum(1 for m in messages if m.get("role") == "user")
    requested_keep = body.get("keep_recent_turns", KEEP_RECENT_TURNS)
    base_keep = requested_keep if isinstance(requested_keep, int) and requested_keep >= 2 else KEEP_RECENT_TURNS
    adaptive_max_keep = max(2, user_msg_count // 2) if user_msg_count > 0 else base_keep

    for attempt in range(1, MAX_COMPACTION_RETRIES + 1):
        keep_turns = min(base_keep - (attempt - 1) * 2, adaptive_max_keep)
        keep_turns = max(2, keep_turns)

        logger.info(
            f"Compaction attempt {attempt}/{MAX_COMPACTION_RETRIES} (keep_recent={keep_turns})"
            + (" [preemptive]" if is_preemptive else "")
        )

        # Split messages
        old_msgs, recent_msgs = split_messages(messages, keep_turns)

        if not old_msgs:
            logger.warning("No messages to compact, giving up")
            break

        # V5: Use custom compression engine if configured and applicable
        if use_custom_engine:
            try:
                custom_result, custom_summary = await compression_engine.compress(
                    messages, api_key, session, session_id=session_id,
                    hook_manager=hook_manager, session_store=session_store,
                    user_profile=user_profile,
                )
                if custom_result is not None:
                    body["messages"] = custom_result
                    # V5: Orphan tool pair sanitization
                    body["messages"] = sanitize_tool_pairs(body["messages"])
                    # V5: Save session
                    if session_store and custom_summary:
                        try:
                            sem_data = semantic_memory._knowledge if semantic_memory else {}
                            session_store.save_session(session_id, custom_summary, sem_data, len(messages))
                            # V5: Save original transcript for session resume
                            session_store.save_transcript(session_id, original_messages)
                            metrics.inc("sessions_saved")
                        except Exception as e:
                            logger.warning(f"Session save error (non-fatal): {e}")
                    logger.info(f"Custom compression engine '{compression_engine.name}' succeeded")
                    metrics.inc("custom_engine_success")
                    return body
                else:
                    logger.warning(f"Custom compression engine '{compression_engine.name}' returned None, falling back to default")
                    metrics.inc("custom_engine_fallback")
            except Exception as e:
                logger.error(f"Custom compression engine error: {e}, falling back to default")
                metrics.inc("custom_engine_error")
            # Fall through to default flow

        # V5: LLM-driven memory extraction (if enabled)
        # V7: DELETE-type skills can skip semantic extraction
        skip_semantic_extraction = False
        skip_arc = False
        if selected_skills:
            for skill in selected_skills:
                if skill.action_type == "DELETE":
                    if "semantic_extraction" in skill.pipeline_stages:
                        skip_semantic_extraction = True
                        logger.info(f"MemSkill: skipping semantic extraction (DELETE skill: {skill.skill_id})")
                    if "arc_citation" in skill.pipeline_stages:
                        skip_arc = True
                        logger.info(f"MemSkill: skipping ARC citation (DELETE skill: {skill.skill_id})")

        llm_memory = None
        if LLM_MEMORY_EXTRACTION and semantic_memory and not skip_semantic_extraction:
            try:
                llm_memory = await llm_extract_memory(
                    old_msgs, api_key, session,
                    existing_memory=semantic_memory._knowledge if semantic_memory else None,
                )
                if llm_memory:
                    metrics.inc("llm_memory_extraction_success")
            except Exception as e:
                logger.debug(f"LLM memory extraction failed (non-fatal): {e}")
                metrics.inc("llm_memory_extraction_fallback")

        # V4: Extract knowledge into semantic memory before compaction
        if semantic_memory and not skip_semantic_extraction:
            try:
                semantic_memory.extract_from_messages(old_msgs, llm_result=llm_memory)
                metrics.inc("semantic_memory_extraction")
            except Exception as e:
                logger.warning(f"Semantic memory extraction error (non-fatal): {e}")

        # ARC citation replacement
        if not skip_arc:
            old_msgs = apply_arc_citations(old_msgs)
            metrics.inc("arc_citations_applied")

        # V3/V6: AFM adaptive fidelity classification — mark each message with fidelity level
        old_msgs = apply_afm_fidelity(old_msgs)

        # CCL commitment extraction (for submodular selection and compaction prompts)
        commitments = extract_commitments(old_msgs)

        # Submodular selection — select most important old messages under token budget
        context_limit = get_model_context_limit(original_model)
        # Old message token budget = total budget - recent messages - response - safety margin - summary reserve
        recent_tokens = estimate_tokens_accurate(recent_msgs)
        summary_reserve = 2000  # Tokens reserved for summary
        old_budget = context_limit - recent_tokens - RESPONSE_BUDGET - SAFETY_MARGIN - summary_reserve
        old_budget = max(500, old_budget)

        # V4: Pass semantic_memory to submodular_select
        # V7: Pass selected_skills for parameter overrides
        selected_old = submodular_select(old_msgs, old_budget, commitments, semantic_memory,
                                         selected_skills=selected_skills)
        if len(selected_old) < len(old_msgs):
            logger.info(
                f"Submodular selection: {len(old_msgs)} -> {len(selected_old)} messages "
                f"(budget={old_budget} tokens, recent={recent_tokens} tokens)"
            )
            metrics.inc("submodular_selection_applied")

        # Parallel block compaction
        if len(selected_old) >= 6:
            summary = await compact_messages_parallel(selected_old, api_key, session, session_id=session_id,
                                                       selected_skills=selected_skills)
        else:
            summary = await compact_messages(selected_old, api_key, session, session_id=session_id,
                                             selected_skills=selected_skills)

        if not summary:
            logger.error(f"Compaction failed on attempt {attempt}, trying with fewer turns")
            metrics.inc("compaction_attempt_failed")
            continue

        # V5: Post-compact hook — allow hook to modify summary
        if hook_manager:
            summary = await hook_manager.call_post_compact(
                summary, messages, {"session_id": session_id, "attempt": attempt}
            )

        # Build compacted message list (V4: includes semantic memory, pruning, cache ordering)
        body["messages"] = build_compacted_messages(summary, recent_msgs)

        # V5: Orphan tool pair sanitization
        body["messages"] = sanitize_tool_pairs(body["messages"])

        # V5: Save session to SessionStore
        if session_store:
            try:
                sem_data = semantic_memory._knowledge if semantic_memory else {}
                session_store.save_session(session_id, summary, sem_data, len(messages))
                # V5: Save original transcript for session resume
                session_store.save_transcript(session_id, original_messages)
                metrics.inc("sessions_saved")
            except Exception as e:
                logger.warning(f"Session save error (non-fatal): {e}")
        messages = body["messages"]

        new_msg_count = len(body["messages"])
        logger.info(
            f"Compacted: {len(old_msgs)} old messages -> summary + "
            f"{len(recent_msgs)} recent = {new_msg_count} total"
        )
        metrics.inc("compaction_attempt_success")

        # Preflight safety verification (V5 FIX: use saved original_messages, not body which is already compacted)
        if not verify_compaction_safety(
            {"messages": original_messages},
            {"messages": body.get("messages", [])}
        ):
            # Compaction made it larger, degrade to aggressive truncation
            metrics.inc("compaction_safety_failed")
            context_limit = get_model_context_limit(original_model)
            target = context_limit - RESPONSE_BUDGET - SAFETY_MARGIN
            truncated = aggressive_truncate_messages(messages, target)
            body["messages"] = truncated
            return body

        if is_preemptive:
            return body

        # Non-streaming: retry verification
        # V6 FIX: Convert to Anthropic format if upstream requires it
        if is_upstream_anthropic():
            verify_body = openai_to_anthropic_request(body)
            verify_body_bytes = json.dumps(verify_body).encode("utf-8")
            verify_headers = openai_to_anthropic_headers(
                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                api_key
            )
            verify_path = "/v1/messages"
        else:
            verify_body = body
            verify_body_bytes = json.dumps(verify_body).encode("utf-8")
            verify_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            verify_path = "/chat/completions"

        status, resp_body, resp_headers = await forward_request(
            session, verify_path, "POST", verify_headers, verify_body_bytes, provider=None
        )

        # V6 FIX: Convert Anthropic response back to OpenAI format for overflow detection
        if is_upstream_anthropic() and status == 200:
            try:
                anthropic_data = json.loads(resp_body)
                openai_data = anthropic_to_openai_response(anthropic_data, original_model)
                resp_body = json.dumps(openai_data).encode("utf-8")
            except Exception as e:
                logger.warning(f"Anthropic→OpenAI response conversion in compaction verify failed: {e}")

        if not is_context_overflow(status, resp_body, provider=None):
            logger.info(f"Compaction succeeded on attempt {attempt}! (HTTP {status})")
            metrics.inc("compaction_retry_success")
            return body

        logger.warning(f"Still overflow after attempt {attempt}, reducing context further...")
        metrics.inc("compaction_retry_still_overflow")

    logger.error(f"All {MAX_COMPACTION_RETRIES} compaction attempts failed for model={original_model}")
    metrics.inc("compaction_all_attempts_failed")
    return None
def apply_thought_masking(messages: list) -> list:
    """V4: Strip reasoning_content from assistant messages before forwarding"""
    result = []
    saved_tokens = 0
    for msg in messages:
        if msg.get("role") == "assistant" and "reasoning_content" in msg:
            rc = msg.get("reasoning_content", "")
            saved_tokens += estimate_tokens_v3(rc)
            new_msg = dict(msg)
            del new_msg["reasoning_content"]
            result.append(new_msg)
        else:
            result.append(msg)
    if saved_tokens > 0:
        metrics.inc("thought_masking_tokens_saved", saved_tokens)
        logger.info(f"Thought masking: stripped {saved_tokens} estimated tokens from reasoning_content")
    return result


# ── V4: Secret Redaction ──────────────────────────────────────────────

def apply_secret_redaction(messages: list) -> list:
    """V4: Redact secrets from messages before forwarding"""
    result = []
    redactions = 0
    for msg in messages:
        new_msg = dict(msg)
        content = new_msg.get("content", "")
        if isinstance(content, str):
            redacted = redact_secrets(content)
            if redacted != content:
                new_msg["content"] = redacted
                redactions += 1
        elif isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    redacted = redact_secrets(block.get("text", ""))
                    if redacted != block.get("text", ""):
                        new_blocks.append({**block, "text": redacted})
                        redactions += 1
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            new_msg["content"] = new_blocks
        result.append(new_msg)
    if redactions > 0:
        metrics.inc("secret_redactions", redactions)
        logger.info(f"Secret redaction: {redactions} messages had secrets redacted")
    return result


# ── 非流式处理 ──────────────────────────────────────────────────────

async def handle_non_streaming(
    body: dict,
    headers: dict,
    session: aiohttp.ClientSession,
    session_id: str = "default",
    provider: "ProviderAdapter" = None,
) -> web.Response:
    api_key = extract_api_key(headers, provider)

    # V4: Thought masking
    messages = body.get("messages", [])
    if messages:
        masked = apply_thought_masking(messages)
        if masked is not messages:
            body["messages"] = masked

    # V4: Secret redaction
    messages = body.get("messages", [])
    if messages and REDACT_SECRETS:
        redacted = apply_secret_redaction(messages)
        if redacted is not messages:
            body["messages"] = redacted

    # V5: Inject session context into cached system prompt
    inject_session_context(body, session_id)

    body_bytes = json.dumps(body).encode("utf-8")

    # 预防性压缩检查
    if should_compact_preemptively(body):
        metrics.inc("preemptive_compaction_triggered")
        compacted = await do_compaction(body, api_key, session, is_preemptive=True, session_id=session_id)
        if compacted is not None:
            body = compacted
            body_bytes = json.dumps(body).encode("utf-8")

    # V6: Provider-aware forward path + format conversion for Anthropic upstream
    forward_path = "/chat/completions"
    need_anthropic_conversion = False
    original_model = body.get("model", "")

    if provider:
        forward_path = provider.get_forward_path("/chat/completions")
        if isinstance(provider, AnthropicProvider):
            forward_path = "/v1/messages"

    # V6: If upstream is Anthropic format, we need to convert OpenAI→Anthropic
    if is_upstream_anthropic():
        need_anthropic_conversion = True
        forward_path = "/v1/messages"
        # Convert request body
        body = openai_to_anthropic_request(body)
        body_bytes = json.dumps(body).encode("utf-8")
        # Convert headers: Bearer → x-api-key
        fwd_headers = openai_to_anthropic_headers(headers, api_key)
    else:
        fwd_headers = headers

    status, resp_body, resp_headers = await forward_request(
        session, forward_path, "POST", fwd_headers, body_bytes, provider=provider
    )

    metrics.inc("request_total")
    metrics.inc("request_non_streaming")
    thrashing_detector.record_message()

    if is_context_overflow(status, resp_body, provider=provider):
        error_text = resp_body.decode("utf-8", errors="replace")[:200]
        logger.warning(f"Context overflow detected (HTTP {status}): {error_text}")
        logger.info(
            f"Starting auto-compaction (model={original_model}, "
            f"messages={len(body.get('messages', []))})"
        )
        metrics.inc("overflow_detected")

        compacted = await do_compaction(body, api_key, session, is_preemptive=False, session_id=session_id)
        if compacted is not None:
            if need_anthropic_conversion:
                compacted = openai_to_anthropic_request(compacted)
            body_bytes = json.dumps(compacted).encode("utf-8")
            status, resp_body, resp_headers = await forward_request(
                session, forward_path, "POST", fwd_headers, body_bytes, provider=provider
            )
            if not is_context_overflow(status, resp_body, provider=provider):
                metrics.inc("compaction_recovery_success")
                # Convert Anthropic response back to OpenAI format
                if need_anthropic_conversion and status == 200:
                    try:
                        anthropic_data = json.loads(resp_body)
                        openai_data = anthropic_to_openai_response(anthropic_data, original_model)
                        resp_body = json.dumps(openai_data).encode("utf-8")
                    except Exception as e:
                        logger.warning(f"Failed to convert Anthropic response to OpenAI: {e}")
                return web.Response(status=status, body=resp_body, headers=resp_headers)
            else:
                metrics.inc("compaction_recovery_failed")
        # Convert error response if needed
        return web.Response(status=status, body=resp_body, headers=resp_headers)

    if status == 200:
        metrics.inc("request_success")
        # Convert Anthropic response back to OpenAI format
        if need_anthropic_conversion:
            try:
                anthropic_data = json.loads(resp_body)
                # V6 FIX: Some upstream APIs (e.g. iFlytek MaaS xsparkx2agent) return
                # only a thinking block with empty text in non-streaming mode, losing
                # all content. Detect this and fall back to streaming to get the actual text.
                content_blocks = anthropic_data.get("content", [])
                has_text = any(b.get("type") == "text" and b.get("text", "").strip() for b in content_blocks)
                has_thinking_only = (not has_text and
                    any(b.get("type") == "thinking" for b in content_blocks) and
                    not any(b.get("type") == "tool_use" for b in content_blocks))
                if has_thinking_only:
                    logger.info("Non-streaming response has only empty thinking block, retrying as streaming to capture content")
                    # Re-send the same request in streaming mode
                    stream_body = dict(body)
                    stream_body["stream"] = True
                    stream_body_bytes = json.dumps(stream_body).encode("utf-8")
                    # Collect streaming content
                    collected_text = []
                    stream_status, stream_resp, _ = await forward_request(
                        session, forward_path, "POST", fwd_headers, stream_body_bytes, provider=provider
                    )
                    if stream_status == 200:
                        for line in stream_resp.decode("utf-8", errors="replace").split("\n"):
                            line = line.strip()
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload == "[DONE]":
                                break
                            try:
                                evt = json.loads(payload)
                                evt_type = evt.get("type", "")
                                if evt_type == "content_block_delta":
                                    delta = evt.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        collected_text.append(delta.get("text", ""))
                                    elif delta.get("type") == "thinking_delta":
                                        collected_text.append(delta.get("thinking", ""))
                            except (json.JSONDecodeError, KeyError):
                                continue
                    if collected_text:
                        full_text = "".join(collected_text)
                        # Build a synthetic OpenAI response with the collected text
                        openai_data = anthropic_to_openai_response(anthropic_data, original_model)
                        openai_data["choices"][0]["message"]["content"] = full_text
                        resp_body = json.dumps(openai_data).encode("utf-8")
                        logger.info(f"Streaming fallback captured {len(full_text)} chars of content")
                    else:
                        # Streaming also failed, just convert as-is
                        openai_data = anthropic_to_openai_response(anthropic_data, original_model)
                        resp_body = json.dumps(openai_data).encode("utf-8")
                else:
                    openai_data = anthropic_to_openai_response(anthropic_data, original_model)
                    resp_body = json.dumps(openai_data).encode("utf-8")
                logger.debug(f"Converted Anthropic response to OpenAI format")
            except Exception as e:
                logger.warning(f"Failed to convert Anthropic response to OpenAI: {e}")
    else:
        metrics.inc("request_error")
    return web.Response(status=status, body=resp_body, headers=resp_headers)


# ── 流式处理 ──────────────────────────────────────────────────────────

async def handle_streaming(
    request: web.Request,
    body: dict,
    headers: dict,
    session: aiohttp.ClientSession,
    session_id: str = "default",
    provider: "ProviderAdapter" = None,
) -> web.StreamResponse:
    api_key = extract_api_key(headers, provider)

    # V4: Thought masking
    messages = body.get("messages", [])
    if messages:
        masked = apply_thought_masking(messages)
        if masked is not messages:
            body["messages"] = masked

    # V4: Secret redaction
    messages = body.get("messages", [])
    if messages and REDACT_SECRETS:
        redacted = apply_secret_redaction(messages)
        if redacted is not messages:
            body["messages"] = redacted

    # V5: Inject session context into cached system prompt
    inject_session_context(body, session_id)

    # 预防性压缩检查
    if should_compact_preemptively(body):
        metrics.inc("preemptive_compaction_triggered")
        compacted = await do_compaction(body, api_key, session, is_preemptive=True, session_id=session_id)
        if compacted is not None:
            body = compacted

    body["stream"] = True
    original_model = body.get("model", "")

    # V6: Format conversion for Anthropic upstream
    need_anthropic_conversion = False
    if is_upstream_anthropic():
        need_anthropic_conversion = True
        body = openai_to_anthropic_request(body)
        body["stream"] = True
        clean_headers = openai_to_anthropic_headers(headers, api_key)
        upstream_url = UPSTREAM_BASE.rstrip("/") + "/v1/messages"
    else:
        clean_headers = clean_request_headers(headers, extra_skip=("accept-encoding",))
        if provider:
            upstream_path = provider.get_forward_path("/chat/completions")
            upstream_url = UPSTREAM_BASE.rstrip("/") + upstream_path
            fwd_key = provider.extract_api_key(headers)
            clean_headers = provider.build_forward_headers(clean_headers, fwd_key)
        else:
            upstream_url = UPSTREAM_BASE.rstrip("/") + "/chat/completions"

    body_bytes = json.dumps(body).encode("utf-8")

    metrics.inc("request_total")
    metrics.inc("request_streaming")
    thrashing_detector.record_message()

    try:
        upstream_resp = await session.post(
            upstream_url, headers=clean_headers, data=body_bytes
        )
    except aiohttp.ClientError as e:
        logger.error(f"Streaming upstream connection error: {e}")
        metrics.inc("request_error")
        return web.Response(
            status=502,
            text=json.dumps({"error": {"message": f"Upstream error: {e}", "type": "proxy_error"}}),
            content_type="application/json",
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Unexpected streaming error: {e}")
        metrics.inc("request_error")
        return web.Response(
            status=500,
            text=json.dumps({"error": {"message": "Internal proxy error", "type": "proxy_error"}}),
            content_type="application/json",
        )

    if upstream_resp.status != 200:
        error_body = await upstream_resp.read()
        error_text = error_body.decode("utf-8", errors="replace")[:500]

        if is_context_overflow(upstream_resp.status, error_body, provider=provider):
            logger.warning(f"Streaming context overflow detected (HTTP {upstream_resp.status}): {error_text}")
            logger.info(
                f"Starting auto-compaction for streaming request "
                f"(model={body.get('model', '?')}, messages={len(body.get('messages', []))})"
            )
            metrics.inc("overflow_detected")

            upstream_resp.close()

            compacted = await do_compaction(body, api_key, session, is_preemptive=False, session_id=session_id)
            if compacted is not None:
                compacted["stream"] = True
                if need_anthropic_conversion:
                    compacted = openai_to_anthropic_request(compacted)
                    compacted["stream"] = True
                compacted_bytes = json.dumps(compacted).encode("utf-8")
                try:
                    retry_resp = await session.post(
                        upstream_url, headers=clean_headers, data=compacted_bytes
                    )
                    if retry_resp.status == 200:
                        metrics.inc("compaction_recovery_success")
                        return await _stream_sse_response(request, retry_resp, need_anthropic_conversion=need_anthropic_conversion, model=original_model)
                    else:
                        retry_error = await retry_resp.read()
                        retry_resp.close()
                        logger.error(f"Streaming retry after compaction failed: {retry_resp.status}")
                        metrics.inc("compaction_recovery_failed")
                        return web.Response(
                            status=retry_resp.status, body=retry_error,
                            content_type="application/json",
                        )
                except (aiohttp.ClientError, Exception) as e:
                    logger.error(f"Streaming retry error: {e}")
                    metrics.inc("request_error")
                    return web.Response(
                        status=502,
                        text=json.dumps({"error": {"message": f"Upstream error: {e}", "type": "proxy_error"}}),
                        content_type="application/json",
                    )

            metrics.inc("compaction_recovery_failed")
            return web.Response(status=upstream_resp.status, body=error_body, content_type="application/json")

        logger.error(f"Streaming upstream error {upstream_resp.status}: {error_text}")
        metrics.inc("request_error")
        return web.Response(status=upstream_resp.status, body=error_body, content_type="application/json")

    metrics.inc("request_success")
    return await _stream_sse_response(request, upstream_resp, need_anthropic_conversion=need_anthropic_conversion, model=original_model)


async def _stream_sse_response(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    need_anthropic_conversion: bool = False,
    model: str = "",
) -> web.StreamResponse:
    stream_resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    try:
        await stream_resp.prepare(request)
    except (ConnectionResetError, asyncio.CancelledError, Exception) as e:
        # V6 FIX: Client disconnected before we could start streaming
        logger.warning(f"Cannot prepare stream response (client disconnected): {e}")
        try:
            upstream_resp.close()
        except Exception:
            pass
        return stream_resp

    try:
        if need_anthropic_conversion:
            # V6: Convert Anthropic SSE events to OpenAI SSE format line by line
            buffer = b""
            async for chunk in upstream_resp.content.iter_any():
                buffer += chunk
                # Process complete lines from buffer
                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace")
                    converted_lines = convert_anthropic_sse_to_openai(line, model)
                    for converted in converted_lines:
                        await stream_resp.write(converted.encode("utf-8"))
            # Process remaining buffer
            if buffer.strip():
                line = buffer.decode("utf-8", errors="replace")
                converted_lines = convert_anthropic_sse_to_openai(line, model)
                for converted in converted_lines:
                    await stream_resp.write(converted.encode("utf-8"))
        else:
            # Pass through SSE as-is
            async for chunk in upstream_resp.content.iter_any():
                await stream_resp.write(chunk)

    except asyncio.CancelledError:
        logger.info("Streaming request cancelled by client")
        raise

    except Exception as e:
        logger.error(f"Streaming error after prepare(): {e}")
        metrics.inc("streaming_error_after_prepare")
        try:
            error_event = (
                f"event: error\ndata: {json.dumps({'error': {'message': f'Streaming error: {e}', 'type': 'proxy_error'}})}\n\n"
            )
            await stream_resp.write(error_event.encode("utf-8"))
        except Exception:
            pass

    finally:
        try:
            upstream_resp.close()
        except Exception:
            pass
        try:
            await stream_resp.write_eof()
        except Exception:
            pass

    return stream_resp


# ── V5: 认证装饰器 ──────────────────────────────────────────────────────

def _is_loopback_peer(request) -> bool:
    """True if the request came from a loopback address (127.0.0.1 / ::1)."""
    try:
        peer = request.transport.get_extra_info("peername")
    except Exception:
        return False
    if not peer:
        return False
    host = peer[0] if isinstance(peer, tuple) else peer
    return host in ("127.0.0.1", "::1", "localhost")


def require_auth(handler):
    """V5: Require API secret for sensitive endpoints.
    If COMPACTION_PROXY_API_SECRET is not set, only loopback peers are
    allowed (defense in depth — the proxy still binds 127.0.0.1, but a
    non-loopback client can never bypass auth).
    Supports Authorization: Bearer <secret> and X-API-Key: <secret> headers.
    """
    async def wrapper(request):
        if not API_SECRET:
            # No secret configured — restrict to loopback only.
            if not _is_loopback_peer(request):
                logger.warning(
                    f"Rejected {request.method} {request.path} from non-loopback peer "
                    f"— set COMPACTION_PROXY_API_SECRET to allow remote clients"
                )
                return web.json_response({"error": "Unauthorized"}, status=401)
            return await handler(request)
        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == API_SECRET:
            return await handler(request)
        # Check X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        if api_key == API_SECRET:
            return await handler(request)
        return web.json_response({"error": "Unauthorized"}, status=401)
    return wrapper


# ── V3: ARC 回查端点 ──────────────────────────────────────────────────

@require_auth
async def handle_arc_retrieve(request: web.Request) -> web.Response:
    """
    V3: ARC 回查端点 — 通过 ID 检索被引用的原始 tool_result 内容。
    """
    arc_id = request.match_info.get("arc_id", "")
    content = arc_log.retrieve(arc_id)
    if content is None:
        return web.json_response(
            {"error": f"ARC reference {arc_id} not found"},
            status=404
        )
    return web.json_response({
        "arc_id": arc_id,
        "content": content,
        "size": len(content),
    })


# ── 主处理函数 ──────────────────────────────────────────────────────────

async def handle_chat_completions(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    session = await get_session()
    headers = dict(request.headers)
    is_stream = body.get("stream", False)

    # V5: Extract session ID for per-session state
    session_id = extract_session_id(body, headers)

    # V6: Detect provider for this request
    model = body.get("model", "")
    if REQUEST_PROVIDER == "auto":
        request_provider = detect_provider(model, headers, UPSTREAM_BASE)
    elif REQUEST_PROVIDER == "anthropic":
        request_provider = AnthropicProvider()
    elif REQUEST_PROVIDER == "gemini":
        request_provider = GeminiProvider()
    else:
        request_provider = OpenAIProvider()

    if is_stream:
        return await handle_streaming(request, body, headers, session, session_id=session_id, provider=request_provider)
    else:
        return await handle_non_streaming(body, headers, session, session_id=session_id, provider=request_provider)


# ── V6: Anthropic Messages API ──────────────────────────────────────────

async def handle_anthropic_messages(request: web.Request) -> web.Response:
    """V6: Handle Anthropic Messages API requests (/v1/messages).
    Since upstream is already Anthropic format, forward directly without
    OpenAI↔Anthropic conversion. Response stays in Anthropic format.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    session = await get_session()
    headers = dict(request.headers)
    is_stream = body.get("stream", False)

    # Add model field if missing
    if "model" not in body:
        body["model"] = "claude-sonnet-4"

    session_id = extract_session_id(body, headers)

    # Extract API key from Anthropic headers
    api_key = headers.get("x-api-key", headers.get("X-Api-Key", ""))
    if not api_key:
        auth = headers.get("Authorization", headers.get("authorization", ""))
        if auth.startswith("Bearer "):
            api_key = auth[7:]

    # Build forward headers — keep Anthropic format
    fwd_headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": headers.get("anthropic-version", "2023-06-01"),
    }
    # Preserve Authorization if present
    if "Authorization" in headers or "authorization" in headers:
        fwd_headers["Authorization"] = headers.get("Authorization", headers.get("authorization", ""))

    # Convert Anthropic messages to OpenAI format just for compaction checks
    openai_body = dict(body)
    anthropic_system = body.get("system")
    if anthropic_system:
        if isinstance(anthropic_system, str):
            system_msg = {"role": "system", "content": anthropic_system}
        elif isinstance(anthropic_system, list):
            system_text = " ".join(
                b.get("text", "") for b in anthropic_system
                if isinstance(b, dict) and b.get("type") == "text"
            )
            system_msg = {"role": "system", "content": system_text}
        else:
            system_msg = None
        if system_msg:
            openai_body["messages"] = [system_msg] + body.get("messages", [])

    # V4: Thought masking + secret redaction on openai_body
    messages = openai_body.get("messages", [])
    if messages:
        masked = apply_thought_masking(messages)
        if masked is not messages:
            openai_body["messages"] = masked
    messages = openai_body.get("messages", [])
    if messages and REDACT_SECRETS:
        redacted = apply_secret_redaction(messages)
        if redacted is not messages:
            openai_body["messages"] = redacted

    # V5: Inject session context
    inject_session_context(openai_body, session_id)

    # Check if compaction is needed before forwarding
    should_compact = should_compact_preemptively(openai_body)
    if should_compact and not is_stream:
        logger.info("Anthropic /v1/messages: preemptive compaction needed")
        compacted_body = await do_compaction(openai_body, api_key, session, session_id=session_id)
        if compacted_body:
            compacted_anthropic = openai_to_anthropic_request(compacted_body)
            body = compacted_anthropic

    upstream_url = UPSTREAM_BASE.rstrip("/") + "/v1/messages"
    body_bytes = json.dumps(body).encode("utf-8")

    metrics.inc("request_total")
    thrashing_detector.record_message()

    # ── Streaming path ──
    if is_stream:
        metrics.inc("request_streaming")
        try:
            upstream_resp = await session.post(
                upstream_url, headers=fwd_headers, data=body_bytes
            )
        except aiohttp.ClientError as e:
            logger.error(f"Anthropic streaming upstream error: {e}")
            metrics.inc("request_error")
            return web.Response(
                status=502,
                text=json.dumps({"error": {"message": f"Upstream error: {e}", "type": "proxy_error"}}),
                content_type="application/json",
            )

        if upstream_resp.status != 200:
            error_body = await upstream_resp.read()
            error_text = error_body.decode("utf-8", errors="replace")[:300]
            logger.error(f"Anthropic streaming upstream error {upstream_resp.status}: {error_text}")
            metrics.inc("request_error")
            upstream_resp.close()
            return web.Response(
                status=upstream_resp.status,
                body=error_body,
                content_type="application/json",
            )

        metrics.inc("request_success")
        # Stream Anthropic SSE events directly (no conversion needed)
        stream_resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        try:
            await stream_resp.prepare(request)
        except (ConnectionResetError, asyncio.CancelledError, Exception) as e:
            logger.warning(f"Cannot prepare Anthropic stream response: {e}")
            upstream_resp.close()
            return stream_resp

        try:
            async for chunk in upstream_resp.content.iter_any():
                await stream_resp.write(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Anthropic streaming error: {e}")
        finally:
            try:
                upstream_resp.close()
            except Exception:
                pass
            try:
                await stream_resp.write_eof()
            except Exception:
                pass
        return stream_resp

    # ── Non-streaming path ──
    metrics.inc("request_non_streaming")

    try:
        async with session.post(
            upstream_url, headers=fwd_headers, data=body_bytes,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            status = resp.status
            resp_body = await resp.read()
            resp_headers = resp.headers
    except aiohttp.ClientError as e:
        logger.error(f"Anthropic non-streaming upstream error: {e}")
        metrics.inc("request_error")
        return web.Response(
            status=502,
            text=json.dumps({"error": {"message": f"Upstream error: {e}", "type": "proxy_error"}}),
            content_type="application/json",
        )

    # Handle errors
    if status != 200:
        metrics.inc("request_error")
        # Check for context overflow
        if is_context_overflow(status, resp_body):
            metrics.inc("context_overflow")
            logger.info("Anthropic /v1/messages: context overflow detected, attempting compaction")
            compacted_body = await do_compaction(openai_body, api_key, session, session_id=session_id)
            if compacted_body:
                compacted_anthropic = openai_to_anthropic_request(compacted_body)
                retry_body = json.dumps(compacted_anthropic).encode("utf-8")
                try:
                    async with session.post(
                        upstream_url, headers=fwd_headers, data=retry_body,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as retry_resp:
                        if retry_resp.status == 200:
                            resp_body = await retry_resp.read()
                            status = 200
                            metrics.inc("request_success")
                            metrics.inc("compaction_recovery")
                        else:
                            metrics.inc("request_error")
                except Exception:
                    metrics.inc("request_error")

        if status != 200:
            return web.Response(
                status=status,
                body=resp_body,
                content_type="application/json",
            )

    # Success — return Anthropic response directly (no conversion)
    metrics.inc("request_success")

    # V6 fix: Check for empty thinking blocks in non-streaming response
    try:
        anthropic_data = json.loads(resp_body)
        content_blocks = anthropic_data.get("content", [])
        has_text = any(b.get("type") == "text" and b.get("text", "").strip() for b in content_blocks)
        has_thinking_only = (not has_text and
            any(b.get("type") == "thinking" for b in content_blocks) and
            not any(b.get("type") == "tool_use" for b in content_blocks))
        if has_thinking_only:
            logger.info("Anthropic /v1/messages: non-streaming has only empty thinking block, retrying as streaming")
            stream_body = dict(body)
            stream_body["stream"] = True
            stream_body_bytes = json.dumps(stream_body).encode("utf-8")
            collected_text = []
            try:
                async with session.post(
                    upstream_url, headers=fwd_headers, data=stream_body_bytes,
                ) as stream_resp:
                    if stream_resp.status == 200:
                        async for line_bytes in stream_resp.content:
                            line = line_bytes.decode("utf-8", errors="replace").strip()
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload == "[DONE]":
                                break
                            try:
                                evt = json.loads(payload)
                                evt_type = evt.get("type", "")
                                if evt_type == "content_block_delta":
                                    delta = evt.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        collected_text.append(delta.get("text", ""))
                                    elif delta.get("type") == "thinking_delta":
                                        collected_text.append(delta.get("thinking", ""))
                            except (json.JSONDecodeError, KeyError):
                                continue
            except Exception as e:
                logger.warning(f"Anthropic /v1/messages streaming fallback failed: {e}")

            if collected_text:
                full_text = "".join(collected_text)
                # Replace empty thinking block with text block
                anthropic_data["content"] = [{"type": "text", "text": full_text}]
                resp_body = json.dumps(anthropic_data).encode("utf-8")
                logger.info(f"Anthropic /v1/messages streaming fallback captured {len(full_text)} chars")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Error checking Anthropic response for thinking blocks: {e}")

    return web.Response(
        status=200,
        body=resp_body,
        content_type="application/json",
    )


# ── 其他路由 ──────────────────────────────────────────────────────────

async def handle_passthrough(request: web.Request) -> web.Response:
    session = await get_session()
    body = await request.read()
    headers = dict(request.headers)

    path = request.path
    method = request.method

    logger.debug(f"passthrough {method} {path}")

    status, resp_body, resp_headers = await forward_request(
        session, path, method, headers, body
    )

    if status >= 400:
        error_text = resp_body.decode("utf-8", errors="replace")[:300]
        logger.debug(f"passthrough error {status} for {method} {path}: {error_text}")

    return web.Response(status=status, body=resp_body, headers=resp_headers)


async def handle_health(request: web.Request) -> web.Response:
    snap = metrics.snapshot()
    sem_mem_stats = {}
    if semantic_memory:
        sem_mem_stats = {k: len(v) for k, v in semantic_memory._knowledge.items()}
    return web.json_response({
        "status": "ok",
        "version": "V6",
        "compaction_model": COMPACTION_MODEL,
        "keep_recent_turns": KEEP_RECENT_TURNS,
        "max_retries": MAX_COMPACTION_RETRIES,
        "upstream": UPSTREAM_BASE,
        "upstream_is_anthropic": is_upstream_anthropic(),
        "uptime": snap["uptime_seconds"],
        "circuit_breaker": {"state": compaction_breaker.state, "failure_count": compaction_breaker.failure_count},
        "cache_size": compaction_cache.size,
        "arc_log_size": arc_log.size,
        "thrashing": thrashing_detector.status,
        "semantic_memory": sem_mem_stats,
        "session_store": {
            "session_count": session_store.session_count if session_store else 0,
            "db_path": SESSION_DB_PATH,
        } if session_store else {"session_count": 0},
        "user_profile": {
            "has_profile": user_profile.has_profile if user_profile else False,
            "size": user_profile.size if user_profile else 0,
        } if user_profile else {"has_profile": False, "size": 0},
        "hooks": {
            "pre_compact_url": PRE_COMPACT_HOOK_URL or "(not configured)",
            "post_compact_url": POST_COMPACT_HOOK_URL or "(not configured)",
        },
        "compression_engine": compression_engine.name if compression_engine else "default",
        "api_secret_configured": bool(API_SECRET),
        "system_prompt_cache": cached_system_prompt.stats,
        "metrics": snap["counters"],
        "v6_features": {
            "provider_support": True,
            "compaction_upstream": COMPACTION_UPSTREAM or UPSTREAM_BASE,
            "compaction_provider": COMPACTION_PROVIDER,
            "request_provider": REQUEST_PROVIDER,
            "supported_providers": ["openai", "anthropic", "gemini"],
            "model_registry_size": len(MODEL_CONTEXT_LIMITS),
        },
        "memskill": {
            "enabled": MEMSKILL_ENABLED,
            "skills_total": len(skill_registry.get_all_skills()) if skill_registry else 0,
            "skills_active": len(skill_registry.get_active_skills()) if skill_registry else 0,
            "designer_rounds": metrics.get("memskill_designer_rounds"),
            "avg_reward": metrics.get("memskill_avg_reward"),
            "controller_step": skill_controller._step_count if skill_controller else 0,
            "exploration_tau": skill_controller._tau if skill_controller else 0,
        } if MEMSKILL_ENABLED else {"enabled": False},
    })


async def handle_metrics(request: web.Request) -> web.Response:
    snap = metrics.snapshot()
    lines = []
    lines.append(f"# TYPE compaction_proxy_uptime_seconds gauge")
    lines.append(f"compaction_proxy_uptime_seconds {snap['uptime_seconds']}")
    for name, value in sorted(snap["counters"].items()):
        lines.append(f"# TYPE compaction_proxy_{name} counter")
        lines.append(f"compaction_proxy_{name} {value}")
    cb_state_val = {"closed": 0, "open": 1, "half_open": 0.5}.get(compaction_breaker.state, -1)
    lines.append(f"# TYPE compaction_proxy_circuit_breaker_state gauge")
    lines.append(f"compaction_proxy_circuit_breaker_state {cb_state_val}")
    lines.append(f"# TYPE compaction_proxy_cache_size gauge")
    lines.append(f"compaction_proxy_cache_size {compaction_cache.size}")
    lines.append(f"# TYPE compaction_proxy_arc_log_size gauge")
    lines.append(f"compaction_proxy_arc_log_size {arc_log.size}")
    lines.append(f"# TYPE compaction_proxy_thrashing gauge")
    lines.append(f"compaction_proxy_thrashing {1 if thrashing_detector.is_thrashing() else 0}")
    # V4: semantic_memory_size metric
    sem_mem_size = 0
    if semantic_memory:
        sem_mem_size = sum(len(v) for v in semantic_memory._knowledge.values())
    lines.append(f"# TYPE compaction_proxy_semantic_memory_size gauge")
    lines.append(f"compaction_proxy_semantic_memory_size {sem_mem_size}")
    # V4: thought_masking_tokens_saved metric
    lines.append(f"# TYPE compaction_proxy_thought_masking_tokens_saved counter")
    lines.append(f"compaction_proxy_thought_masking_tokens_saved {metrics.get('thought_masking_tokens_saved')}")
    # V4: secret_redactions metric
    lines.append(f"# TYPE compaction_proxy_secret_redactions counter")
    lines.append(f"compaction_proxy_secret_redactions {metrics.get('secret_redactions')}")
    # V5: new metrics
    lines.append(f"# TYPE compaction_proxy_hooks_called counter")
    lines.append(f"compaction_proxy_hooks_called {metrics.get('hooks_pre_compact_called') + metrics.get('hooks_post_compact_called')}")
    lines.append(f"# TYPE compaction_proxy_sessions_saved counter")
    lines.append(f"compaction_proxy_sessions_saved {metrics.get('sessions_saved')}")
    lines.append(f"# TYPE compaction_proxy_tool_pairs_sanitized counter")
    lines.append(f"compaction_proxy_tool_pairs_sanitized {metrics.get('tool_pairs_sanitized')}")
    lines.append(f"# TYPE compaction_proxy_session_count gauge")
    lines.append(f"compaction_proxy_session_count {session_store.session_count if session_store else 0}")
    lines.append(f"# TYPE compaction_proxy_user_profile_size gauge")
    lines.append(f"compaction_proxy_user_profile_size {user_profile.size if user_profile else 0}")
    # V5: System prompt cache metrics
    csp_stats = cached_system_prompt.stats
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_layers_total gauge")
    lines.append(f"compaction_proxy_system_prompt_cache_layers_total {csp_stats['layers_total']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_layers_active gauge")
    lines.append(f"compaction_proxy_system_prompt_cache_layers_active {csp_stats['layers_active']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_hit_rate gauge")
    lines.append(f"compaction_proxy_system_prompt_cache_hit_rate {csp_stats['cumulative_hit_rate']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_last_hit_rate gauge")
    lines.append(f"compaction_proxy_system_prompt_cache_last_hit_rate {csp_stats['last_hit_rate']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_tokens_saved counter")
    lines.append(f"compaction_proxy_system_prompt_cache_tokens_saved {csp_stats['estimated_tokens_saved']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_assemblies counter")
    lines.append(f"compaction_proxy_system_prompt_cache_assemblies {csp_stats['total_assemblies']}")
    lines.append(f"# TYPE compaction_proxy_system_prompt_cache_boundary gauge")
    lines.append(f"compaction_proxy_system_prompt_cache_boundary {csp_stats['cache_boundary']}")

    # V7: MemSkill metrics
    if MEMSKILL_ENABLED:
        lines.append(f"# TYPE compaction_proxy_memskill_enabled gauge")
        lines.append(f"compaction_proxy_memskill_enabled {1 if MEMSKILL_ENABLED else 0}")
        lines.append(f"# TYPE compaction_proxy_memskill_skills_total gauge")
        lines.append(f"compaction_proxy_memskill_skills_total {len(skill_registry.get_all_skills()) if skill_registry else 0}")
        lines.append(f"# TYPE compaction_proxy_memskill_skills_active gauge")
        lines.append(f"compaction_proxy_memskill_skills_active {len(skill_registry.get_active_skills()) if skill_registry else 0}")
        lines.append(f"# TYPE compaction_proxy_memskill_skills_selected counter")
        lines.append(f"compaction_proxy_memskill_skills_selected {metrics.get('memskill_skills_selected')}")
        lines.append(f"# TYPE compaction_proxy_memskill_avg_reward gauge")
        lines.append(f"compaction_proxy_memskill_avg_reward {metrics.get('memskill_avg_reward')}")
        lines.append(f"# TYPE compaction_proxy_memskill_designer_rounds counter")
        lines.append(f"compaction_proxy_memskill_designer_rounds {metrics.get('memskill_designer_rounds')}")
        lines.append(f"# TYPE compaction_proxy_memskill_designer_edits_applied counter")
        lines.append(f"compaction_proxy_memskill_designer_edits_applied {metrics.get('memskill_designer_edits_applied')}")
        lines.append(f"# TYPE compaction_proxy_memskill_rollbacks counter")
        lines.append(f"compaction_proxy_memskill_rollbacks {metrics.get('memskill_rollbacks')}")
        lines.append(f"# TYPE compaction_proxy_memskill_fallback counter")
        lines.append(f"compaction_proxy_memskill_fallback {metrics.get('memskill_fallback')}")

    return web.Response(
        text="\n".join(lines) + "\n",
        content_type="text/plain; version=0.0.4",
    )


# ── V5: Session & Profile Endpoints ──────────────────────────────────────

@require_auth
async def handle_sessions_search(request: web.Request) -> web.Response:
    """V5: FTS5 session search endpoint"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    query = request.query.get("q", "")
    if not query:
        return web.json_response({"error": "Missing query parameter 'q'"}, status=400)
    try:
        limit = int(request.query.get("limit", "5"))
        limit = max(1, min(limit, 50))  # Clamp to reasonable range
    except ValueError:
        return web.json_response({"error": "Invalid 'limit' parameter, must be integer"}, status=400)
    results = session_store.search_sessions(query, limit=limit)
    return web.json_response({"query": query, "results": results, "count": len(results)})


@require_auth
async def handle_sessions_recent(request: web.Request) -> web.Response:
    """V5: Recent sessions endpoint"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    try:
        n = int(request.query.get("n", "5"))
        n = max(1, min(n, 50))  # Clamp to reasonable range
    except ValueError:
        return web.json_response({"error": "Invalid 'n' parameter, must be integer"}, status=400)
    results = session_store.get_recent_sessions(n)
    return web.json_response({"sessions": results, "count": len(results)})


@require_auth
async def handle_session_detail(request: web.Request) -> web.Response:
    """V5: Single session detail endpoint"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    session_id = request.match_info.get("session_id", "")
    result = session_store.get_session(session_id)
    if not result:
        return web.json_response({"error": f"Session {session_id} not found"}, status=404)
    return web.json_response(result)


@require_auth
async def handle_session_create(request: web.Request) -> web.Response:
    """
    POST /session — Generate a stable session ID the client can reuse.
    Accepts an optional JSON body with messages/metadata to derive a content-based ID.
    Returns the session_id and instructs the client to echo it back via X-Session-Id header.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    headers = dict(request.headers)
    session_id = extract_session_id(body, headers)

    resp = web.json_response({
        "session_id": session_id,
        "usage": "Include this session_id in the X-Session-Id header of subsequent requests for session continuity",
    })
    # Also set a cookie so the client auto-echoes it back
    resp.set_cookie("X-Session-Id", session_id, max_age=86400 * 30, httponly=False)
    return resp


@require_auth
async def handle_profile_get(request: web.Request) -> web.Response:
    """V5: Get user profile"""
    if not user_profile:
        return web.json_response({"error": "UserProfile not initialized"}, status=503)
    return web.json_response({
        "profile": user_profile.load(),
        "size": user_profile.size,
        "has_profile": user_profile.has_profile,
    })


@require_auth
async def handle_profile_post(request: web.Request) -> web.Response:
    """V5: Update user profile"""
    if not user_profile:
        return web.json_response({"error": "UserProfile not initialized"}, status=503)
    try:
        body = await request.json()
        content = body.get("content", "")
        user_profile.save(content)
        return web.json_response({"status": "ok", "size": user_profile.size})
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)


@require_auth
async def handle_memory_get(request: web.Request) -> web.Response:
    """V5: Get semantic memory"""
    if not semantic_memory:
        return web.json_response({"error": "SemanticMemory not initialized"}, status=503)
    return web.json_response(semantic_memory._knowledge)


@require_auth
async def handle_memory_delete(request: web.Request) -> web.Response:
    """V5: Clear semantic memory"""
    if not semantic_memory:
        return web.json_response({"error": "SemanticMemory not initialized"}, status=503)
    semantic_memory.clear()
    return web.json_response({"status": "ok", "message": "Semantic memory cleared"})


# ── V5: Manual Compaction & Session Resume Endpoints ──────────────────────

@require_auth
async def handle_manual_compact(request: web.Request) -> web.Response:
    """
    V5: Manual compaction trigger — like Claude Code's /compact command.
    Accepts a messages array and optional parameters, returns compacted messages.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    messages = body.get("messages", [])
    model = body.get("model", COMPACTION_MODEL)
    session_id = body.get("session_id", "manual")
    keep_turns = body.get("keep_recent_turns", KEEP_RECENT_TURNS)

    if not messages:
        return web.json_response({"error": "No messages provided"}, status=400)

    # Extract API key from request
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        return web.json_response({"error": "Missing API key"}, status=401)

    # Get the shared HTTP session
    http_session = await get_session()

    # Override KEEP_RECENT_TURNS for this request if specified
    global_keep = KEEP_RECENT_TURNS
    if keep_turns != global_keep:
        # Temporarily patch for this compaction run
        # do_compaction reads KEEP_RECENT_TURNS internally, so we pass it via body metadata
        body["keep_recent_turns"] = keep_turns

    result = await do_compaction(
        body,
        api_key,
        http_session,
        is_preemptive=False,
        session_id=session_id,
    )

    if result is None:
        # Not enough content to compact — return original messages unchanged
        return web.json_response({
            "messages": messages,
            "original_count": len(messages),
            "compacted_count": len(messages),
            "note": "Not enough content to compact — messages returned unchanged",
        })

    compacted_messages = result.get("messages", messages)
    return web.json_response({
        "messages": compacted_messages,
        "original_count": len(messages),
        "compacted_count": len(compacted_messages),
    })


@require_auth
async def handle_summarize(request: web.Request) -> web.Response:
    """
    V6: Summarize endpoint for CompactionProvider plugin.
    Returns a summary string instead of compacted messages.
    This is the bridge between OpenClaw's CompactionProvider interface
    (which requires summarize() to return a string) and the V6 proxy's
    compaction engine (which produces structured summaries).
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    messages = body.get("messages", [])
    model = body.get("model", COMPACTION_MODEL)
    session_id = body.get("session_id", "plugin")
    custom_instructions = body.get("customInstructions", "")
    previous_summary = body.get("previousSummary", "")
    compression_ratio = body.get("compressionRatio")

    if not messages:
        # No messages to summarize — return previous summary or empty
        return web.json_response({"summary": previous_summary or ""})

    # Extract API key from request (check body first since OpenClaw sandbox strips headers)
    api_key = body.get("apiKey", "")
    if not api_key:
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return web.json_response({"error": "Missing API key"}, status=401)

    # Get the shared HTTP session
    http_session = await get_session()

    # Split messages into old (to summarize) and recent (to keep)
    # When called from CompactionProvider, messages may be few (e.g. 6 msgs = 3 turns).
    # Adapt keep_recent_turns: keep at most half the turns, minimum 1 turn kept.
    conv_msg_count = sum(1 for m in messages if m.get("role") != "system")
    user_msg_count = sum(1 for m in messages if m.get("role") == "user")
    adaptive_keep = max(1, min(KEEP_RECENT_TURNS, user_msg_count // 2))
    old_messages, recent_messages = split_messages(messages, adaptive_keep)

    # If custom instructions provided, augment the compaction system prompt
    # WITHOUT mutating the module-level constant (avoids cross-request races).
    effective_prompt = COMPACTION_SYSTEM_PROMPT
    if custom_instructions:
        effective_prompt = COMPACTION_SYSTEM_PROMPT + "\n\nAdditional instructions from user: " + custom_instructions

    if not old_messages:
        # Still not enough to split — summarize ALL conversation messages as a last resort
        conv_only = [m for m in messages if m.get("role") != "system"]
        if conv_only and user_msg_count >= 1:
            # Check if there's enough content to summarize
            conv_text = "\n".join(f"{m.get('role','?')}: {m.get('content','')}" for m in conv_only)
            if len(conv_text) < 200:
                logger.info(f"/summarize: conversation too short ({len(conv_text)} chars), returning as-is")
                # Return the conversation content as a minimal summary
                return web.json_response({"summary": f"## Brief Exchange\n{conv_text}"})
            logger.info(f"/summarize: too few messages to split ({user_msg_count} user msgs), summarizing all conversation")
            summary = await compact_messages(
                conv_only, api_key, http_session,
                session_id=session_id,
                system_prompt_override=effective_prompt,
            )
            if summary is None:
                return web.json_response({"error": "Summarization failed"}, status=500)
            return web.json_response({"summary": summary})
        # No conversation at all — return previous summary or empty
        return web.json_response({"summary": previous_summary or ""})

    # If a previous_summary is provided, store it in the session for incremental compaction
    if previous_summary and session_store:
        session_store.save_prior_summary(session_id, previous_summary, 0)

    # Use compact_messages to generate the summary
    summary = await compact_messages(
        old_messages, api_key, http_session,
        session_id=session_id,
        system_prompt_override=effective_prompt,
    )

    if summary is None:
        return web.json_response({"error": "Summarization failed"}, status=500)

    return web.json_response({"summary": summary})


@require_auth
async def handle_session_transcript(request: web.Request) -> web.Response:
    """V5: Get full conversation transcript for session resume"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    session_id = request.match_info["session_id"]
    transcript = session_store.get_transcript(session_id)
    if transcript is None:
        return web.json_response({"error": "Transcript not found"}, status=404)
    return web.json_response({
        "session_id": session_id,
        "messages": transcript,
        "count": len(transcript),
    })


@require_auth
async def handle_session_resume(request: web.Request) -> web.Response:
    """V5: Resume a session with transcript + summary"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    session_id = request.match_info["session_id"]
    session_data = session_store.get_session(session_id)
    transcript = session_store.get_transcript(session_id)
    prior_summary, msg_count = session_store.get_prior_summary(session_id)
    return web.json_response({
        "session_id": session_id,
        "transcript": transcript,
        "summary": prior_summary,
        "last_msg_count": msg_count,
        "session_data": session_data,
    })


# ── V7: MemSkill API Endpoints ────────────────────────────────────────────

@require_auth
async def handle_skills_list(request: web.Request) -> web.Response:
    """V7: List all skills and their status"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skills = skill_registry.get_all_skills()
    return web.json_response({
        "skills": [s.to_dict() for s in skills],
        "total": len(skills),
        "active": len([s for s in skills if s.status == "active"]),
        "enabled": MEMSKILL_ENABLED,
    })


@require_auth
async def handle_skills_detail(request: web.Request) -> web.Response:
    """V7: Get skill detail + version history"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skill_id = request.match_info["skill_id"]
    skill = skill_registry.get_skill(skill_id)
    if not skill:
        return web.json_response({"error": f"Skill '{skill_id}' not found"}, status=404)
    # Load snapshots for version history
    snapshots = []
    if session_store:
        try:
            rows = session_store._conn.execute(
                "SELECT snapshot_id, version, snapshot_reason, created_at FROM skill_snapshots "
                "WHERE skill_id=? ORDER BY created_at DESC LIMIT 10",
                (skill_id,),
            ).fetchall()
            snapshots = [{"snapshot_id": r[0], "version": r[1], "reason": r[2], "created_at": r[3]} for r in rows]
        except Exception:
            pass
    return web.json_response({"skill": skill.to_dict(), "snapshots": snapshots})


@require_auth
async def handle_skills_create(request: web.Request) -> web.Response:
    """V7: Create a new skill (→ draft status)"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    required = ["skill_id", "description", "purpose", "when_to_use", "how_to_apply", "action_type"]
    for field in required:
        if field not in data:
            return web.json_response({"error": f"Missing required field: {field}"}, status=400)
    skill = CompactionSkill(
        skill_id=data["skill_id"],
        description=data["description"],
        purpose=data["purpose"],
        when_to_use=data["when_to_use"],
        how_to_apply=data["how_to_apply"],
        action_type=data.get("action_type", "UPDATE"),
        constraints=data.get("constraints", []),
        pipeline_stages=data.get("pipeline_stages", []),
        params=data.get("params", {}),
        prompt_additions=data.get("prompt_additions", {}),
    )
    # Validate params
    violations = _validate_skill_params(skill)
    if violations:
        return web.json_response({"error": f"Parameter validation failed: {violations}"}, status=400)
    skill_id = skill_registry.register_skill(skill)
    return web.json_response({"skill_id": skill_id, "status": "draft"}, status=201)


@require_auth
async def handle_skills_activate(request: web.Request) -> web.Response:
    """V7: Activate a draft skill"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skill_id = request.match_info["skill_id"]
    success = skill_registry.activate_skill(skill_id)
    if success:
        return web.json_response({"skill_id": skill_id, "status": "active"})
    return web.json_response({"error": f"Cannot activate '{skill_id}' — check status and params"}, status=400)


@require_auth
async def handle_skills_deprecate(request: web.Request) -> web.Response:
    """V7: Deprecate an active skill"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skill_id = request.match_info["skill_id"]
    success = skill_registry.deprecate_skill(skill_id)
    if success:
        return web.json_response({"skill_id": skill_id, "status": "deprecated"})
    return web.json_response({"error": f"Cannot deprecate '{skill_id}'"}, status=400)


@require_auth
async def handle_skills_rollback(request: web.Request) -> web.Response:
    """V7: Rollback a skill to a specific version"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skill_id = request.match_info["skill_id"]
    try:
        data = await request.json()
        target_version = int(data.get("version", 0))
    except Exception:
        return web.json_response({"error": "Invalid version"}, status=400)
    if target_version < 1:
        return web.json_response({"error": "version must be >= 1"}, status=400)
    success = skill_registry.rollback_skill(skill_id, target_version)
    if success:
        metrics.inc("memskill_rollbacks")
        return web.json_response({"skill_id": skill_id, "rolled_back_to": target_version})
    return web.json_response({"error": f"Rollback failed for '{skill_id}' v{target_version}"}, status=400)


@require_auth
async def handle_skills_performance(request: web.Request) -> web.Response:
    """V7: Get aggregated performance stats for a skill"""
    if not skill_registry:
        return web.json_response({"error": "MemSkill not initialized"}, status=503)
    skill_id = request.match_info["skill_id"]
    skill = skill_registry.get_skill(skill_id)
    if not skill:
        return web.json_response({"error": f"Skill '{skill_id}' not found"}, status=404)
    return web.json_response({
        "skill_id": skill_id,
        "usage_count": skill.usage_count,
        "success_count": skill.success_count,
        "success_rate": skill.success_count / max(1, skill.usage_count),
        "avg_reward": skill.avg_reward,
        "version": skill.version,
        "status": skill.status,
    })


@require_auth
async def handle_skills_trajectories(request: web.Request) -> web.Response:
    """V7: Get recent compaction trajectories"""
    if not session_store:
        return web.json_response({"error": "SessionStore not initialized"}, status=503)
    try:
        limit = int(request.query.get("limit", "20"))
        limit = max(1, min(limit, 100))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)
    try:
        rows = session_store._conn.execute(
            "SELECT trajectory_id, session_id, timestamp, message_count, has_errors, has_code, "
            "token_pressure, skills_activated, reward, token_savings_ratio, safety_passed, "
            "compaction_succeeded, llm_calls, total_tokens_used "
            "FROM trajectories ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        trajectories = [{
            "trajectory_id": r[0], "session_id": r[1], "timestamp": r[2],
            "message_count": r[3], "has_errors": bool(r[4]), "has_code": bool(r[5]),
            "token_pressure": r[6], "skills_activated": r[7], "reward": r[8],
            "token_savings_ratio": r[9], "safety_passed": bool(r[10]),
            "compaction_succeeded": bool(r[11]), "llm_calls": r[12], "total_tokens_used": r[13],
        } for r in rows]
        return web.json_response({"trajectories": trajectories, "count": len(trajectories)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@require_auth
async def handle_skills_designer_trigger(request: web.Request) -> web.Response:
    """V7: Manually trigger the Skill Designer (Phase 4 placeholder)"""
    if not MEMSKILL_ENABLED:
        return web.json_response({"error": "MemSkill not enabled (set COMPACTION_PROXY_MEMSKILL=1)"}, status=403)
    # Phase 4: Will invoke SkillDesigner.design_round()
    return web.json_response({"status": "designer_not_yet_implemented", "phase": 4})


# ── 应用 ──────────────────────────────────────────────────────────────

START_TIME = time.time()

app = web.Application()

# 核心路由
app.router.add_post("/chat/completions", handle_chat_completions)
app.router.add_post("/chat/completions/", handle_chat_completions)
app.router.add_post("/v1/chat/completions", handle_chat_completions)
app.router.add_post("/v1/chat/completions/", handle_chat_completions)

# V6: Anthropic Messages API
app.router.add_post("/v1/messages", handle_anthropic_messages)
app.router.add_post("/v1/messages/", handle_anthropic_messages)

# V3: ARC 回查端点
app.router.add_get("/arc/{arc_id}", handle_arc_retrieve)

# V5: Session & Profile endpoints
app.router.add_post("/session", handle_session_create)
app.router.add_get("/sessions/search", handle_sessions_search)
app.router.add_get("/sessions/recent", handle_sessions_recent)
app.router.add_get("/sessions/{session_id}", handle_session_detail)
app.router.add_get("/sessions/{session_id}/transcript", handle_session_transcript)
app.router.add_post("/sessions/{session_id}/resume", handle_session_resume)
app.router.add_get("/profile", handle_profile_get)
app.router.add_post("/profile", handle_profile_post)
app.router.add_get("/memory", handle_memory_get)
app.router.add_delete("/memory", handle_memory_delete)

# V5: Manual compaction endpoint (like Claude Code's /compact)
app.router.add_post("/compact", handle_manual_compact)

# V6: Summarize endpoint for CompactionProvider plugin
app.router.add_post("/summarize", handle_summarize)

# V7: MemSkill endpoints
app.router.add_get("/skills", handle_skills_list)
app.router.add_get("/skills/{skill_id}", handle_skills_detail)
app.router.add_post("/skills", handle_skills_create)
app.router.add_post("/skills/{skill_id}/activate", handle_skills_activate)
app.router.add_post("/skills/{skill_id}/deprecate", handle_skills_deprecate)
app.router.add_post("/skills/{skill_id}/rollback", handle_skills_rollback)
app.router.add_get("/skills/{skill_id}/performance", handle_skills_performance)
app.router.add_get("/skills/trajectories", handle_skills_trajectories)
app.router.add_post("/skills/designer/trigger", handle_skills_designer_trigger)

# 健康检查和指标
app.router.add_get("/health", handle_health)
app.router.add_get("/metrics", handle_metrics)
app.router.add_get("/", handle_health)

# 透传其他所有请求
app.router.add_route("*", "/{path:.*}", handle_passthrough)


async def on_cleanup(app):
    global _session
    if _session and not _session.closed:
        await _session.close()
    # V5: Close V5 resources
    if hook_manager:
        await hook_manager.close()
    if session_store:
        session_store.close()

app.on_cleanup.append(on_cleanup)


# ── 启动 ──────────────────────────────────────────────────────────────

def main():
    global session_store, user_profile, hook_manager, compression_engine
    global skill_registry, skill_controller

    # V5: Initialize new components
    session_store = SessionStore()
    user_profile = UserProfile()
    hook_manager = HookManager()
    compression_engine = load_compression_engine()

    # V7: Initialize MemSkill components
    if MEMSKILL_ENABLED:
        skill_registry = SkillRegistry(session_store=session_store)
        skill_controller = SkillController(skill_registry, exploration_tau=MEMSKILL_EXPLORATION_TAU)
        # Wrap compression engine with MemSkill awareness
        compression_engine = MemSkillAwareEngine(compression_engine, skill_registry, skill_controller)
        logger.info(f"MemSkill V7: ENABLED — {len(skill_registry.get_active_skills())} active skills, "
                     f"tau={MEMSKILL_EXPLORATION_TAU}, designer_interval={MEMSKILL_DESIGNER_INTERVAL}")
    else:
        skill_registry = SkillRegistry(session_store=session_store)  # Still init for /skills API
        logger.info("MemSkill V7: DISABLED (set COMPACTION_PROXY_MEMSKILL=1 to enable)")

    # V5: Restore ARC log from SQLite
    try:
        arc_entries = session_store.load_arc_entries()
        if arc_entries:
            arc_log._log = arc_entries
            # V5 FIX: Robust counter restoration — handle non-standard key formats
            max_counter = 0
            for k in arc_entries.keys():
                try:
                    parts = k.split("-")
                    if len(parts) >= 2:
                        max_counter = max(max_counter, int(parts[-1]))
                except (ValueError, IndexError):
                    continue
            arc_log._counter = max_counter
            logger.info(f"Restored {len(arc_entries)} ARC entries from SQLite (counter={max_counter})")
    except Exception as e:
        logger.warning(f"ARC log restoration error (non-fatal): {e}")

    logger.info(f"OpenClaw Compaction Proxy V6 starting on {LISTEN_HOST}:{LISTEN_PORT}")
    logger.info(f"   Upstream: {UPSTREAM_BASE}")
    logger.info(f"   Compaction model: {COMPACTION_MODEL}")
    logger.info(f"   Keep recent turns: {KEEP_RECENT_TURNS}")
    logger.info(f"   Max retries: {MAX_COMPACTION_RETRIES}")
    logger.info(f"   Preemptive threshold: {PREEMPTIVE_THRESHOLD*100:.0f}%")
    logger.info(f"   Circuit breaker: 3 failures / 60s cooldown")
    logger.info(f"   Compaction cache: 30 min TTL")
    logger.info(f"   === 35 Compression Techniques ===")
    logger.info(f"   V3 Core (8):")
    logger.info(f"     1. ARC Addressable References (max {arc_log._max} entries)")
    logger.info(f"     2. AFM Adaptive Fidelity — message-level (Full/Compressed/Placeholder) + submodular boost")
    logger.info(f"     3. PACMS Submodular Selection (greedy, fidelity-aware)")
    logger.info(f"     4. CCL Commitment Extraction")
    logger.info(f"     5. Thrashing Detection ({THRASHING_COMPACTS} compacts / {THRASHING_WINDOW_MSGS} msgs)")
    logger.info(f"     6. Two-Stage Token Estimation (CJK-aware)")
    logger.info(f"     7. Preflight Safety Verification")
    logger.info(f"     8. Parallel Compaction ({PARALLEL_COMPACTION_BLOCKS} blocks)")
    logger.info(f"   V4 Enhanced (10):")
    logger.info(f"     9. Episodic-Semantic Dual-Layer Memory (arXiv:2605.17625)")
    logger.info(f"     10. Thought Masking (Kevin-32B/Devin pattern)")
    logger.info(f"     11. Tag-based Selective Retention (SWE-agent/memor-ai)")
    logger.info(f"     12. Structure-Aware Tool Output Compression (memor-ai)")
    logger.info(f"     13. Secret Redaction (Cursor/memor-ai)")
    logger.info(f"     14. Cache-Optimized Message Ordering — layer-hash-aware + cache_control breakpoints")
    logger.info(f"     15. Incremental Compaction (CoMem arXiv:2605.30842)")
    logger.info(f"     16. Four-Signal Memory Scoring (semantic 50%, recency 25%, kind 15%, quality 10%)")
    logger.info(f"     17. Query Placement Optimization — multi-rule (query-at-end, pair-adjacency, recent-window)")
    logger.info(f"     18. Compaction-Item Pruning (OpenAI)")
    logger.info(f"   V5 Session (5):")
    logger.info(f"     19. Cross-Session Memory + FTS5 Search (SQLite)")
    logger.info(f"     20. Pre/Post Compaction Hooks (HTTP webhooks)")
    logger.info(f"     21. Orphan Tool Pair Sanitization")
    logger.info(f"     22. User Profile Memory (USER.md)")
    logger.info(f"     23. Pluggable Compression Engine ({compression_engine.name})")
    if compression_engine.name == "dual-layer":
        logger.info(f"        Dual-Layer: gateway_ratio={DUAL_LAYER_GATEWAY_RATIO} (L1), agent_ratio={DUAL_LAYER_AGENT_RATIO} (L2)")
    logger.info(f"   V6 Provider (5):")
    logger.info(f"     24. Provider Abstraction Layer (OpenAI/Anthropic/Gemini)")
    logger.info(f"     25. Compaction Provider (separate upstream/key)")
    logger.info(f"     26. Provider-Specific Overflow Detection")
    logger.info(f"     27. Dual-Layer Compression (gateway + agent)")
    logger.info(f"     28. CachedSystemPrompt (10-layer prefix cache + cache_control)")
    logger.info(f"   Additional (7):")
    logger.info(f"     29. Smart Truncation (role-based budget)")
    logger.info(f"     30. Compaction Cache (30-min TTL)")
    logger.info(f"     31. Circuit Breaker (3 failures / 60s cooldown)")
    logger.info(f"     32. Preemptive Compaction")
    logger.info(f"     33. Overflow Recovery (compress + retry)")
    logger.info(f"     34. Identifier Preservation")
    logger.info(f"     35. Summary Merge (parallel block merging)")
    logger.info(f"   V7 MemSkill (5 seed skills):")
    logger.info(f"     36. Self-evolving Memory Skills (arXiv:2602.02474)")
    logger.info(f"     37. Skill Controller (keyword matching + Gumbel-Top-K)")
    logger.info(f"     38. Skill Registry (CRUD + activation lifecycle + snapshot rollback)")
    logger.info(f"     39. Parameter Override Pipeline (importance weights + fidelity multipliers)")
    logger.info(f"     40. DELETE-type Skill Pipeline Skip (semantic extraction / ARC)")
    logger.info(f"   === Infrastructure (not counted as techniques) ===")
    logger.info(f"     - Auto Provider Detection: {REQUEST_PROVIDER}")
    logger.info(f"     - Compaction Upstream: {COMPACTION_UPSTREAM or UPSTREAM_BASE}")
    logger.info(f"     - Compaction API Key: {'configured' if COMPACTION_API_KEY else 'same as request key'}")
    logger.info(f"     - Model Registry: {len(MODEL_CONTEXT_LIMITS)} models")
    logger.info(f"   === Components ===")
    logger.info(f"     SessionStore: {session_store.session_count} sessions in {SESSION_DB_PATH}")
    logger.info(f"     UserProfile: {'loaded' if user_profile.has_profile else 'empty'} ({USER_PROFILE_PATH})")
    logger.info(f"     Hooks: pre={PRE_COMPACT_HOOK_URL or 'none'}, post={POST_COMPACT_HOOK_URL or 'none'}")
    logger.info(f"     Compression engine: {compression_engine.name}")
    logger.info(f"     API Secret auth: {'enabled' if API_SECRET else 'disabled (set COMPACTION_PROXY_API_SECRET to enable)'}")
    if MEMSKILL_ENABLED:
        logger.info(f"     MemSkill: ENABLED — {len(skill_registry.get_active_skills())} active skills, "
                     f"tau={MEMSKILL_EXPLORATION_TAU}, auto_activate={MEMSKILL_AUTO_ACTIVATE}")
        for s in skill_registry.get_active_skills():
            logger.info(f"       - {s.skill_id} (v{s.version}, {s.action_type}, stages={s.pipeline_stages})")
    else:
        logger.info(f"     MemSkill: DISABLED (set COMPACTION_PROXY_MEMSKILL=1 to enable)")
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None, handle_signals=True)

if __name__ == "__main__":
    main()
