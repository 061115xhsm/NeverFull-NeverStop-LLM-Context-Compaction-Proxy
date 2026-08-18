"""
专用压缩小模型接口与加载器(model_hub.py)
==========================================
可插拔的专用压缩摘要模型层,供压缩代理调用:

- SummaryModel(ABC):压缩摘要模型统一接口
- LLMBackedModel:通用 LLM 摘要实现(默认,走 HTTP 上游)
- LocalModelAdapter:本地小模型(Qwen2-7B / Llama 3-8B 等)适配接口
  (实际推理由外部进程/推理服务器提供,此处为客户端封装)
- ModelHub:模型注册与按名加载
- get_summary_model():按配置返回摘要模型

目标:用开源小模型在高质量对话压缩数据集上微调后,替代通用 LLM 做摘要,
提速 5-10 倍、降本 90%(路线图 #5)。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


# ── 摘要模型统一接口 ────────────────────────────────────────────────

class SummaryModel(ABC):
    """压缩摘要模型统一接口。"""

    @abstractmethod
    def summarize(self, messages: List[dict], max_tokens: int = 2000,
                  instructions: str = "") -> Optional[str]:
        """对消息生成摘要,返回摘要文本或 None。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """模型标识。"""


# ── 通用 LLM 摘要实现(默认) ────────────────────────────────────────

class LLMBackedModel(SummaryModel):
    """
    通用 LLM 摘要实现:支持 OpenAI 兼容 /chat/completions 与 Anthropic /v1/messages。

    环境变量(自动探测,无需全部设置):
      SUMMARY_MODEL_URL: 上游地址(默认探测 ANTHROPIC_BASE_URL → 127.0.0.1:8198)
      SUMMARY_MODEL_NAME: 模型名(默认探测 ANTHROPIC_MODEL → gpt-4o-mini)
      SUMMARY_API_KEY: 上游密钥(默认探测 ANTHROPIC_API_KEY / OPENAI_API_KEY)
      SUMMARY_API_STYLE: "openai" | "anthropic"(默认按 URL 自动判断)
    """

    def __init__(
        self,
        url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_style: Optional[str] = None,
    ) -> None:
        # URL:显式 > SUMMARY_MODEL_URL > ANTHROPIC_BASE_URL > 默认
        self.url = url or os.environ.get("SUMMARY_MODEL_URL") \
            or os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/") \
            or "http://127.0.0.1:8198"
        # 模型:显式 > SUMMARY_MODEL_NAME > ANTHROPIC_MODEL > 默认
        self.model = model or os.environ.get("SUMMARY_MODEL_NAME") \
            or os.environ.get("ANTHROPIC_MODEL", "") \
            or "gpt-4o-mini"
        # Key:显式 > SUMMARY_API_KEY > ANTHROPIC_API_KEY > OPENAI_API_KEY > 空
        self.api_key = api_key or os.environ.get("SUMMARY_API_KEY") \
            or os.environ.get("ANTHROPIC_API_KEY") \
            or os.environ.get("OPENAI_API_KEY") \
            or ""
        # 风格:显式 > SUMMARY_API_STYLE > URL 自动判断
        if api_style:
            self.api_style = api_style.lower()
        else:
            self.api_style = (os.environ.get("SUMMARY_API_STYLE") or "").lower()
        if not self.api_style:
            # 含 anthropic/claude/coding-api 视为 Anthropic 格式
            self.api_style = "anthropic" if re.search(
                r"anthropic|claude|coding-api", self.url
            ) else "openai"

    @property
    def name(self) -> str:
        return f"llm:{self.api_style}:{self.model}"

    def summarize(self, messages: List[dict], max_tokens: int = 2000,
                  instructions: str = "") -> Optional[str]:
        sys_prompt = (
            "You are a context summarization assistant. Produce a concise structured summary. "
            "Preserve all identifiers, decisions, and active tasks verbatim."
        )
        if instructions:
            sys_prompt += f"\n\nAdditional focus: {instructions}"
        conversation = "\n".join(f"{m.get('role','?')}: {m.get('content','')}" for m in messages)

        if self.api_style == "anthropic":
            return self._summarize_anthropic(conversation, sys_prompt, max_tokens)
        return self._summarize_openai(conversation, sys_prompt, max_tokens)

    def _summarize_openai(self, conversation: str, sys_prompt: str, max_tokens: int) -> Optional[str]:
        url = self.url.rstrip("/") + "/v1/chat/completions" \
            if not self.url.endswith("/chat/completions") else self.url
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": "CONVERSATION TO SUMMARIZE:\n\n" + conversation},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data.get("choices", [{}])[0].get("message", {}).get("content") or None
        except Exception:
            return None

    def _summarize_anthropic(self, conversation: str, sys_prompt: str, max_tokens: int) -> Optional[str]:
        url = self.url.rstrip("/") + "/v1/messages" \
            if not self.url.endswith("/v1/messages") else self.url
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "system": sys_prompt,
            "messages": [{"role": "user", "content": "CONVERSATION TO SUMMARIZE:\n\n" + conversation}],
        }
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
            return text or None
        except Exception:
            return None


# ── 本地小模型适配器(推理服务器客户端) ─────────────────────────────

class LocalModelAdapter(SummaryModel):
    """
    本地微调小模型适配:对接 Ollama / vLLM / llama.cpp 等推理服务器。

    环境变量:
      LOCAL_MODEL_URL: 推理服务器地址(默认 http://127.0.0.1:11434/api/chat, Ollama)
      LOCAL_MODEL_NAME: 模型名(默认 qwen2:7b)

    说明:将开源小模型(Qwen2-7B / Llama 3-8B)在高质量对话压缩数据集上
    微调后,把服务地址指向本适配器即可替代通用 LLM 摘要。
    """

    def __init__(
        self,
        url: Optional[str] = None,
        model: Optional[str] = None,
        backend: str = "ollama",
    ) -> None:
        self.backend = backend.lower()
        if self.backend == "openai":
            self.url = url or os.environ.get("LOCAL_MODEL_URL", "http://127.0.0.1:8000/v1/chat/completions")
        else:  # ollama
            self.url = url or os.environ.get("LOCAL_MODEL_URL", "http://127.0.0.1:11434/api/chat")
        self.model = model or os.environ.get("LOCAL_MODEL_NAME", "qwen2:7b")

    @property
    def name(self) -> str:
        return f"local:{self.backend}:{self.model}"

    def summarize(self, messages: List[dict], max_tokens: int = 2000,
                  instructions: str = "") -> Optional[str]:
        user_text = "\n".join(f"{m.get('role','?')}: {m.get('content','')}" for m in messages)
        try:
            if self.backend == "openai":
                payload = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": "Summarize this conversation concisely."},
                        {"role": "user", "content": user_text},
                    ],
                }
                req = urllib.request.Request(
                    self.url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read())
                return data.get("choices", [{}])[0].get("message", {}).get("content") or None
            # ollama
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": "Summarize this conversation concisely."},
                    {"role": "user", "content": user_text},
                ],
            }
            req = urllib.request.Request(
                self.url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            return data.get("message", {}).get("content") or None
        except Exception:
            return None


# ── 模型注册与加载 ──────────────────────────────────────────────────

class ModelHub:
    """模型注册表:按名获取摘要模型。"""

    _REGISTRY: Dict[str, Any] = {
        "llm": LLMBackedModel,
        "local": LocalModelAdapter,
    }

    @classmethod
    def register(cls, name: str, factory: Any) -> None:
        cls._REGISTRY[name] = factory

    @classmethod
    def create(cls, kind: str = "llm", **kwargs) -> SummaryModel:
        factory = cls._REGISTRY.get(kind)
        if factory is None:
            raise ValueError(f"未知摘要模型类型: {kind}(可选: {list(cls._REGISTRY)})")
        return factory(**kwargs)


def get_summary_model() -> SummaryModel:
    """
    按配置返回摘要模型(路线图 #5 的入口)。

    环境变量:
      SUMMARY_BACKEND: llm(默认) | local
      其余配置见各实现的环境变量。
    """
    kind = os.environ.get("SUMMARY_BACKEND", "llm").lower()
    try:
        return ModelHub.create(kind)
    except Exception:
        return LLMBackedModel()
