"""
安全增强模块(security_enhanced.py)
====================================
精细数据安全 + 细粒度权限与多租户,供压缩代理调用:

- PIIRedactor:敏感信息脱敏升级(API Key + PII:身份证/手机号/邮箱/自定义词)
- EncryptedStore:落盘数据加密(可选 cryptography,降级 XOR+base64 防明文)
- TenantManager:多 API key 隔离,独立会话/记忆/配额
- PermissionGate:只读/读写/管理权限分级 + token 限额

依赖:优先 cryptography;未安装则降级内置轻量加密,保证可用。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# ── 可选依赖:加密库 ────────────────────────────────────────────────
try:
    from cryptography.fernet import Fernet  # type: ignore
    _HAS_CRYPTOGRAPHY = True
except Exception:
    _HAS_CRYPTOGRAPHY = False


# ── PII 脱敏 ────────────────────────────────────────────────────────

class PIIRedactor:
    """
    敏感信息脱敏:

    - 内置:API Key、身份证、手机号、邮箱、IP、URL token
    - 自定义:敏感词列表(正则 + 词面双识别)
    """

    _PATTERNS: List[tuple] = [
        (r"sk-[A-Za-z0-9]{16,}", "<API_KEY>"),
        (r"(?<!\d)\d{17}[\dXx](?!\d)", "<ID_CARD>"),
        (r"(?<!\d)1[3-9]\d{9}(?!\d)", "<PHONE>"),
        (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<EMAIL>"),
        (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<IP>"),
        (r"(?i)(bearer\s+)[a-z0-9._~+/=-]+", r"\1<TOKEN>"),
        (r"(?i)(x-api-key:\s*)[a-z0-9._~+/=-]+", r"\1<KEY>"),
    ]

    def __init__(self, custom_words: Optional[List[str]] = None) -> None:
        self.custom_words = custom_words or []
        self._custom_re = None
        if self.custom_words:
            escaped = [re.escape(w) for w in self.custom_words if w]
            if escaped:
                self._custom_re = re.compile("|".join(escaped), re.IGNORECASE)

    def redact(self, text: str) -> str:
        out = text
        for pat, repl in self._PATTERNS:
            out = re.sub(pat, repl, out)
        if self._custom_re is not None:
            out = self._custom_re.sub("<CUSTOM_SENSITIVE>", out)
        return out

    def redact_messages(self, messages: List[dict]) -> List[dict]:
        out: List[dict] = []
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, str):
                nm = dict(m)
                nm["content"] = self.redact(c)
                out.append(nm)
            else:
                out.append(m)
        return out


# ── 落盘加密 ────────────────────────────────────────────────────────

class EncryptedStore:
    """
    落盘数据加密存储。优先 Fernet;无 cryptography 时用内置 XOR+base64
    (防明文,非强加密——生产请安装 cryptography)。

    支持"纯内存模式"(memory_only=True 时不落盘,数据不落地)。
    """

    def __init__(self, key: Optional[str] = None, memory_only: bool = False) -> None:
        self.memory_only = memory_only
        self._memory: Dict[str, str] = {}
        if _HAS_CRYPTOGRAPHY:
            derived = self._derive_key(key or "default-key")
            self._fernet = Fernet(derived)
            self._mode = "fernet"
        else:
            self._key = hashlib.sha256((key or "default-key").encode()).digest()
            self._mode = "xor"

    @staticmethod
    def _derive_key(secret: str) -> bytes:
        return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())

    def _xor_crypt(self, data: bytes) -> bytes:
        return bytes(b ^ self._key[i % len(self._key)] for i, b in enumerate(data))

    def encrypt(self, plaintext: str) -> str:
        if self._mode == "fernet":
            return self._fernet.encrypt(plaintext.encode()).decode()
        enc = self._xor_crypt(plaintext.encode())
        return "xor:" + base64.urlsafe_b64encode(enc).decode()

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext.startswith("xor:"):
            raw = base64.urlsafe_b64decode(ciphertext[4:])
            return self._xor_crypt(raw).decode()
        if self._mode == "fernet":
            return self._fernet.decrypt(ciphertext.encode()).decode()
        return ciphertext

    def put(self, key: str, value: str, path: Optional[str] = None) -> None:
        enc = self.encrypt(value)
        if self.memory_only:
            self._memory[key] = enc
            return
        if path:
            data = self._load_file(path)
            data[key] = enc
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

    def get(self, key: str, path: Optional[str] = None) -> Optional[str]:
        if self.memory_only:
            enc = self._memory.get(key)
        else:
            data = self._load_file(path) if path else {}
            enc = data.get(key)
        if enc is None:
            return None
        try:
            return self.decrypt(enc)
        except Exception:
            return None

    @staticmethod
    def _load_file(path: str) -> Dict[str, str]:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


# ── 多租户与权限 ────────────────────────────────────────────────────

class TenantManager:
    """多 API key 隔离:每个租户独立的会话/记忆命名空间与配额。"""

    def __init__(self) -> None:
        self.tenants: Dict[str, Dict[str, Any]] = {}

    def register(self, api_key_hash: str, quota_daily_tokens: int = 1_000_000,
                 permissions: str = "read-write") -> None:
        self.tenants[api_key_hash] = {
            "quota_daily_tokens": quota_daily_tokens,
            "used_today": 0,
            "day": time.strftime("%Y-%m-%d"),
            "permissions": permissions,
            "created_at": time.time(),
        }

    def authenticate(self, api_key: str) -> Optional[str]:
        """返回租户 id(api_key 的 sha256),未注册返回 None。"""
        h = hashlib.sha256(api_key.encode()).hexdigest()
        return h if h in self.tenants else None

    def authorize(self, tenant_id: str, required: str = "read") -> bool:
        t = self.tenants.get(tenant_id)
        if not t:
            return False
        perm = t["permissions"]
        order = {"read": 1, "read-write": 2, "admin": 3}
        return order.get(perm, 0) >= order.get(required, 1)

    def consume_tokens(self, tenant_id: str, tokens: int) -> bool:
        """消耗配额;超限返回 False。"""
        t = self.tenants.get(tenant_id)
        if not t:
            return False
        today = time.strftime("%Y-%m-%d")
        if t["day"] != today:
            t["day"] = today
            t["used_today"] = 0
        if t["used_today"] + tokens > t["quota_daily_tokens"]:
            return False
        t["used_today"] += tokens
        return True

    def usage(self, tenant_id: str) -> Dict[str, Any]:
        t = self.tenants.get(tenant_id, {})
        return {
            "used_today": t.get("used_today", 0),
            "quota": t.get("quota_daily_tokens", 0),
            "permissions": t.get("permissions", ""),
        }
