"""Streaming integration test: compaction hook + OpenAI SSE pass-through.

Starts a fake upstream, imports the proxy with that upstream, forces the
preemptive-compaction branch with a stub, and verifies the streamed response
still reaches the client.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="nf-stream-")


async def fake_upstream(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    if body.get("stream"):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for token in ("he", "llo"):
            chunk = {"id": "x", "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"content": token}}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response({"choices": [{"message": {"content": "ok"}}]})


class TestStreamingCompaction(unittest.TestCase):
    def test_stream_passes_through_after_compaction(self):
        import asyncio

        async def scenario():
            up_app = web.Application()
            up_app.router.add_post("/v1/chat/completions", fake_upstream)
            up = TestServer(up_app)
            await up.start_server()
            port = up.port

            os.environ["COMPACTION_PROXY_UPSTREAM"] = f"http://127.0.0.1:{port}/v1"
            for key, val in {
                "COMPACTION_PROXY_SESSION_DB": str(Path(TMP) / "sessions.db"),
                "COMPACTION_PROXY_SEMANTIC_MEMORY": str(Path(TMP) / "sem.json"),
                "COMPACTION_PROXY_USER_PROFILE": str(Path(TMP) / "profile.md"),
                "COMPACTION_PROXY_FOLD_DB": str(Path(TMP) / "folds.db"),
                "COMPACTION_PROXY_STATE_DB": str(Path(TMP) / "state.db"),
                "COMPACTION_PROXY_CALIBRATION_DB": str(Path(TMP) / "cal.json"),
                "COMPACTION_PROXY_LOG_LEVEL": "ERROR",
            }.items():
                os.environ[key] = val

            spec = importlib.util.spec_from_file_location("compaction_proxy_stream", ROOT / "compaction-proxy.py")
            cp = importlib.util.module_from_spec(spec)
            sys.modules["compaction_proxy_stream"] = cp
            spec.loader.exec_module(cp)

            called = {"n": 0}

            cp.should_compact_preemptively = lambda body: True

            async def fake_do(body, api_key, session, is_preemptive=False, session_id="default"):
                called["n"] += 1
                body["messages"] = [{"role": "user", "content": "compacted"}]
                return body

            cp.do_compaction = fake_do

            proxy = TestServer(cp.app)
            await proxy.start_server()
            client = TestClient(proxy)
            await client.start_server()
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o-mini", "stream": True,
                          "messages": [{"role": "user", "content": "hi"}]},
                )
                text = await resp.text()
                return resp.status, text, called["n"]
            finally:
                await client.close()
                await proxy.close()
                await up.close()

        status, text, n = asyncio.run(scenario())
        self.assertEqual(status, 200)
        self.assertIn('"content": "he"', text)
        self.assertIn("[DONE]", text)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
