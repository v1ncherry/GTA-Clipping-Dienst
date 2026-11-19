"""Kleiner Ollama-Ersatz für Tests: /api/tags, /api/chat (JSON-Antwort) und /api/pull (Fortschritt)."""

from __future__ import annotations

import http.server
import json
import threading


class FakeOllama:
    def __init__(self, models: list[str] | None = None, score: int = 8, port: int = 0):
        self.models = list(models or [])
        self.score = score
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _json(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/api/tags":
                    self._json({"models": [{"name": m} for m in outer.models]})
                else:
                    self._json({}, 404)

            def do_POST(self):  # noqa: N802
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append({"path": self.path, **payload})
                if self.path == "/api/chat":
                    content = json.dumps({"score": outer.score, "title": "Er fährt direkt in den Pool 😂",
                                          "hook": "WARTE AUF DAS ENDE", "hashtags": ["#pool", "#fail"],
                                          "reason": "Unerwartet und lustig"})
                    self._json({"message": {"role": "assistant", "content": content}})
                elif self.path == "/api/pull":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for line in ({"status": "pulling manifest"}, {"status": "downloading", "total": 100, "completed": 50},
                                 {"status": "downloading", "total": 100, "completed": 100}, {"status": "success"}):
                        self.wfile.write((json.dumps(line) + "\n").encode())
                    outer.models.append(payload.get("model", "x"))
                else:
                    self._json({}, 404)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
