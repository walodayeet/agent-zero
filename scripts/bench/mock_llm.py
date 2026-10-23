#!/usr/bin/env python3
"""Deterministic OpenAI-compatible mock for A0 benchmarks.

  python scripts/bench/mock_llm.py 18099

Returns an A0-shaped response tool call so a turn needs exactly one model call.
Use it to measure Agent Zero overhead without gateway variance.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except Exception:
            req = {}
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json({"error": "not found"}, 404)
        content = json.dumps(
            {"thoughts": ["mock"], "tool_name": "response", "tool_args": {"text": "MOCK_OK"}}
        )
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            base = {"id": "mock", "object": "chat.completion.chunk", "created": int(time.time()), "model": "mock"}
            for delta in ({"role": "assistant"}, {"content": content}, {}):
                chunk = dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}])
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            done = dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
            self.wfile.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
            self.close_connection = True
            return
        self._json({
            "id": "mock", "object": "chat.completion", "created": int(time.time()), "model": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 18099), Handler).serve_forever()
