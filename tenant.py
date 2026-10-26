"""Tenant scoping for the compaction proxy (V11 P6)."""

from __future__ import annotations

import contextvars
import re

TENANT = contextvars.ContextVar("tenant", default="default")
SESSION_CTX = contextvars.ContextVar("session_id", default=None)


def normalize_tenant(value: str | None) -> str:
    v = re.sub(r"[^A-Za-z0-9_.-]", "-", (value or "").strip())[:48].strip("-")
    return v or "default"


def current_tenant() -> str:
    return TENANT.get()


def scoped_id(session_id: str | None) -> str:
    sid = str(session_id or "default")
    tenant = current_tenant()
    return sid if sid.startswith(tenant + ":") else f"{tenant}:{sid}"


def unscoped_id(session_id: str | None) -> str:
    sid = str(session_id or "")
    return sid.split(":", 1)[1] if ":" in sid else sid
