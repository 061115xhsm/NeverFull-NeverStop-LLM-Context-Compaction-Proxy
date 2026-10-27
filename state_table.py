"""State table for long-horizon consistency (V11 P1b).

Research basis
--------------
- MemTrace (arXiv:2610.04838): repository changes invalidate earlier execution
  evidence; compaction must be state-consistent.
- A-TMA (arXiv:2607.01935): "ghost memory" — old, current and transition facts
  coexist and mislead retrieval. Track which fact is current.
- Can Agent Memory Systems Track Evolving State? (arXiv:2608.19652): answers must
  reflect the current state, not a superseded one.

What this module does
---------------------
Keeps a tiny per-session state table:
  files      path -> last operation, timestamp, edit/read counts
  decisions  typed text -> active / superseded

At compaction time it renders a compact <state_snapshot> block so the model knows
which file versions are current and which earlier reads are stale. Pure stdlib.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

DEFAULT_DB = os.environ.get(
    "COMPACTION_PROXY_STATE_DB",
    os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "state.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    session_id TEXT NOT NULL,
    path       TEXT NOT NULL,
    last_op    TEXT NOT NULL,
    ts         REAL NOT NULL,
    edits      INTEGER NOT NULL DEFAULT 0,
    reads      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, path)
);
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    norm       TEXT NOT NULL,
    ts         REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id, status);
CREATE TABLE IF NOT EXISTS segments (
    session_id TEXT NOT NULL,
    path       TEXT NOT NULL,
    chunk_idx  INTEGER NOT NULL,
    line_start INTEGER NOT NULL,
    line_end   INTEGER NOT NULL,
    hash       TEXT NOT NULL,
    ts         REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'FRESH',
    PRIMARY KEY (session_id, path, chunk_idx)
);
"""

EDIT_TOOLS = {
    "write", "edit", "apply_patch", "create", "delete", "rename",
    "str_replace_editor", "notebook_edit", "multiedit", "apply_diff", "patch",
}
READ_TOOLS = {"read", "cat", "view", "open_file", "head", "tail", "grep", "search", "glob"}

DECISION_PATTERNS = {
    "goal": re.compile(r"(?:goal|目标|purpose|aim|task|任务|want\s+to|需要|trying\s+to)\s*[:：]?\s*(.{5,160})", re.I),
    "constraint": re.compile(r"(?:constraint|约束|limit|限制|must|必须|requirement|需求|cannot|不能|never|绝不|rule|规则)\s*[:：]?\s*(.{5,160})", re.I),
    "decision": re.compile(r"(?:decided|决定|chose|选择|will\s+use|使用|adopted|采用|switched|切换)\s*(?:to\s+)?(.{5,160})", re.I),
    "task": re.compile(r"(?:todo|pending|next\s+step|待办|下一步|还需要|remaining)\s*[:：]?\s*(.{5,160})", re.I),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


class StateTable:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or DEFAULT_DB).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── ingest ────────────────────────────────────────────────────────
    def update(self, session_id: str, messages: list, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        edits_seen = set()
        with self._lock:
            # V12 Phase 3: pair read-type calls with their results for chunk hashing
            call_map = {}
            for msg in messages or []:
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_call"):
                            name = str(block.get("name") or "")
                            payload = block.get("input") or {}
                            if isinstance(payload, dict):
                                for k in ("path", "file_path", "target_file", "filename"):
                                    if isinstance(payload.get(k), str) and payload[k].strip():
                                        call_map[block.get("id")] = (name.lower(), payload[k].strip())
                                        break
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") if isinstance(tc, dict) else None
                    if isinstance(fn, dict):
                        try:
                            payload = json.loads(fn.get("arguments") or "{}")
                        except Exception:
                            payload = {}
                        if isinstance(payload, dict):
                            for k in ("path", "file_path", "target_file", "filename"):
                                if isinstance(payload.get(k), str) and payload[k].strip():
                                    call_map[tc.get("id")] = (str(fn.get("name", "")).lower(), payload[k].strip())
                                    break
            for msg in messages or []:
                self._ingest_tool_calls(session_id, msg, now, edits_seen)
                self._ingest_read_results(session_id, msg, call_map, now)
                self._ingest_decisions(session_id, msg, now)
            self._conn.commit()

    def _ingest_read_results(self, session_id: str, msg: dict, call_map: dict, now: float) -> None:
        content = None
        path = None
        tc_id = msg.get("tool_call_id")
        if tc_id and tc_id in call_map:
            name, path = call_map[tc_id]
        c = msg.get("content")
        if isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tc_id = block.get("tool_use_id")
                    if tc_id in call_map:
                        name, path = call_map[tc_id]
                        inner = block.get("content")
                        if isinstance(inner, str):
                            content = inner
                        elif isinstance(inner, list):
                            content = "\n".join(
                                b.get("text", "") for b in inner
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
        if content is None or not path:
            return
        if any(t in name for t in ("read", "cat", "view", "open_file", "head", "tail")):
            self._record_read(session_id, path, content, now)

    def _record_read(self, session_id: str, path: str, content: str, now: float) -> None:
        lines = content.splitlines()
        chunk_size = 50
        stored = {r[0]: r[1] for r in self._conn.execute(
            "SELECT chunk_idx, hash FROM segments WHERE session_id=? AND path=?",
            (session_id, path),
        ).fetchall()}
        total = max(1, (len(lines) + chunk_size - 1) // chunk_size) or 1
        for ci in range(total):
            part = "\n".join(lines[ci * chunk_size:(ci + 1) * chunk_size])
            h = hashlib.sha256(part.encode("utf-8")).hexdigest()[:16]
            status = "FRESH" if stored.get(ci) == h else "STALE"
            self._conn.execute(
                "INSERT INTO segments(session_id, path, chunk_idx, line_start, line_end, hash, ts, status) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(session_id, path, chunk_idx) DO UPDATE SET "
                "hash=excluded.hash, ts=excluded.ts, status=excluded.status",
                (session_id, path, ci, ci * chunk_size + 1,
                 min(len(lines), (ci + 1) * chunk_size) or chunk_size, h, now, status),
            )

    def _ingest_tool_calls(self, session_id: str, msg: dict, now: float, edits_seen: set) -> None:
        calls = []
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_call", "function_call"):
                    calls.append((block.get("name") or "", block.get("input") or {}))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if isinstance(fn, dict):
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        import json as _json
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                calls.append((fn.get("name") or "", args if isinstance(args, dict) else {}))

        for name, payload in calls:
            name_l = str(name).lower()
            path = None
            if isinstance(payload, dict):
                for k in ("path", "file_path", "target_file", "filename"):
                    v = payload.get(k)
                    if isinstance(v, str) and v.strip():
                        path = v.strip()
                        break
            if not path:
                continue
            op = None
            if any(t in name_l for t in EDIT_TOOLS):
                op = "edit"
            elif any(t in name_l for t in READ_TOOLS):
                op = "read"
            if op is None:
                continue
            # a later read of a file edited in this same block is not "stale"
            if op == "read" and path in edits_seen:
                continue
            self._conn.execute(
                "INSERT INTO files(session_id, path, last_op, ts, edits, reads) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(session_id, path) DO UPDATE SET "
                "last_op=excluded.last_op, ts=excluded.ts, "
                "edits=files.edits+excluded.edits, reads=files.reads+excluded.reads",
                (session_id, path, op, now, 1 if op == "edit" else 0, 1 if op == "read" else 0),
            )
            if op == "edit":
                edits_seen.add(path)

    def _ingest_decisions(self, session_id: str, msg: dict, now: float) -> None:
        text = self._text_of(msg)
        if not text:
            return
        for kind, pat in DECISION_PATTERNS.items():
            for m in pat.finditer(text):
                value = m.group(1).strip().rstrip("。.!！")
                if len(value) < 5:
                    continue
                self._add_decision(session_id, kind, value, now)

    @staticmethod
    def _text_of(msg: dict) -> str:
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""

    def _add_decision(self, session_id: str, kind: str, value: str, now: float) -> None:
        norm = _norm(value)
        rows = self._conn.execute(
            "SELECT id, text FROM decisions WHERE session_id=? AND kind=? AND status='active'",
            (session_id, kind),
        ).fetchall()
        for rid, old_text in rows:
            if _similar(old_text, value) >= 0.70:
                # same decision restated: keep the newest wording, supersede the old row
                self._conn.execute("UPDATE decisions SET status='superseded' WHERE id=?", (rid,))
        dup = self._conn.execute(
            "SELECT id FROM decisions WHERE session_id=? AND norm=? AND status='active'",
            (session_id, norm),
        ).fetchone()
        if dup:
            return
        self._conn.execute(
            "INSERT INTO decisions(session_id, kind, text, norm, ts, status) VALUES (?,?,?,?,?,'active')",
            (session_id, kind, value, norm, now),
        )

    # ── render ────────────────────────────────────────────────────────
    def render(self, session_id: str, max_files: int = 8, max_decisions: int = 6) -> str:
        with self._lock:
            return self._render_locked(session_id, max_files, max_decisions)

    def _segment_stats(self, session_id: str, path: str) -> dict:
        rows = self._conn.execute(
            "SELECT chunk_idx, line_start, line_end, status FROM segments "
            "WHERE session_id=? AND path=? ORDER BY chunk_idx",
            (session_id, path),
        ).fetchall()
        total = len(rows)
        stale = [(r[1], r[2]) for r in rows if r[3] == "STALE"]
        return {"total": total, "stale": stale}

    def _render_locked(self, session_id: str, max_files: int, max_decisions: int) -> str:
        files = self._conn.execute(
            "SELECT path, last_op, ts, edits, reads FROM files WHERE session_id=? "
            "ORDER BY ts DESC LIMIT ?",
            (session_id, max_files),
        ).fetchall()
        active = self._conn.execute(
            "SELECT kind, text FROM decisions WHERE session_id=? AND status='active' "
            "ORDER BY ts DESC LIMIT ?",
            (session_id, max_decisions),
        ).fetchall()
        superseded = self._conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE session_id=? AND status='superseded'",
            (session_id,),
        ).fetchone()[0]

        if not files and not active and not superseded:
            return ""

        lines = ["<state_snapshot>"]
        if files:
            parts = []
            for path, op, ts, edits, reads in files:
                clock = time.strftime("%H:%M", time.localtime(ts))
                seg = self._segment_stats(session_id, path)
                if seg["total"] and seg["stale"]:
                    ranges = ", ".join(f"{a}-{b}" for a, b in seg["stale"][:3])
                    more = f" (+{len(seg['stale']) - 3} more)" if len(seg["stale"]) > 3 else ""
                    note = f"; {len(seg['stale'])}/{seg['total']} chunks stale: {ranges}{more}"
                elif edits:
                    note = "; earlier reads stale"
                else:
                    note = "; chunks fresh"
                parts.append(f"{path} ({op} {clock}{note})")
            lines.append("[FILES] " + " | ".join(parts))
        if active:
            items = [f"({kind}) {text[:120]}" for kind, text in active]
            line = "[DECISIONS] active: " + " ; ".join(items)
            if superseded:
                line += f" ; superseded: {superseded}"
            lines.append(line)
        elif superseded:
            lines.append(f"[DECISIONS] all {superseded} previous decisions superseded")
        lines.append("[NOTE] Treat earlier reads of edited files as stale; re-read before relying on them.")
        lines.append("</state_snapshot>")
        return "\n".join(lines)

    def get(self, session_id: str) -> dict:
        with self._lock:
            return self._get_locked(session_id)

    def _get_locked(self, session_id: str) -> dict:
        files = self._conn.execute(
            "SELECT path, last_op, ts, edits, reads FROM files WHERE session_id=? ORDER BY ts DESC",
            (session_id,),
        ).fetchall()
        dec = self._conn.execute(
            "SELECT kind, text, status, ts FROM decisions WHERE session_id=? ORDER BY ts DESC",
            (session_id,),
        ).fetchall()
        return {
            "files": [
                {"path": p, "last_op": o, "ts": t, "edits": e, "reads": r}
                for p, o, t, e, r in files
            ],
            "decisions": [
                {"kind": k, "text": x, "status": s, "ts": t} for k, x, s, t in dec
            ],
        }

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM decisions WHERE session_id=?", (session_id,))
            self._conn.commit()

    def stats(self) -> dict:
        try:
            with self._lock:
                files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                dec = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        except sqlite3.Error:
            files = dec = 0
        return {"files": files, "decisions": dec, "path": str(self.path)}

    def prune(self, max_age_days: float = 30.0) -> int:
        """Retention: drop old file rows and superseded decisions; keep active decisions."""
        removed = 0
        with self._lock:
            if max_age_days and max_age_days > 0:
                cutoff = time.time() - max_age_days * 86400
                cur = self._conn.execute("DELETE FROM files WHERE ts < ?", (cutoff,))
                removed += cur.rowcount or 0
                cur = self._conn.execute(
                    "DELETE FROM decisions WHERE ts < ? AND status = 'superseded'", (cutoff,)
                )
                removed += cur.rowcount or 0
            self._conn.commit()
            if removed:
                try:
                    self._conn.execute("VACUUM")
                except sqlite3.Error:
                    pass
        return removed

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error:
            pass
