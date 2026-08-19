"""Hermetic fake llama-server for the Server Verification test suite.

Behaves like the documented llama-server legacy HTTP contract: /health flips
503->200, /v1/models returns model data, /completion returns content plus
timings, /embedding and /rerank serve their own shapes, Bearer auth and
--api-prefix are honored, and a set of env knobs drives failure modes
(slow loading, exit-early, malformed JSON, unsupported-endpoint 404s).

argv mirrors real llama-server flags so CommandRunner can launch it unchanged;
knobs come from environment variables because QProcess inherits the parent env.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SENTINEL_FAKE_MODEL = "fake-model"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class FakeLlamaServer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.host = args.host
        self.port = args.port
        self.prefix = (args.api_prefix or "").rstrip("/")
        if self.prefix and not self.prefix.startswith("/"):
            self.prefix = "/" + self.prefix
        alias = args.alias.split(",")[0].strip() if (args.alias or "").strip() else ""
        self.model_id = os.environ.get("FAKE_MODEL_ID") or alias or SENTINEL_FAKE_MODEL
        keys: list[str] = []
        if args.api_key:
            keys.extend(part.strip() for part in args.api_key.split(",") if part.strip())
        if args.api_key_file:
            for line in open(args.api_key_file, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    keys.append(line)
                    break
        env_key = os.environ.get("FAKE_KEY") or os.environ.get("LLAMA_API_KEY")
        if env_key and not keys:
            keys.append(env_key)
        self.keys = keys
        self.slow_503s = _env_int("FAKE_SLOW_503S", 0)
        self.malformed = os.environ.get("FAKE_MALFORMED", "")
        self.unsupported = os.environ.get("FAKE_MODE", "") == "unsupported"
        self.no_timings = os.environ.get("FAKE_NO_TIMINGS", "0") == "1"
        self.trace_file = os.environ.get("FAKE_TRACE", "")
        self.hits_health = 0

    def trace(self, path: str) -> None:
        if self.trace_file:
            with open(self.trace_file, "a", encoding="utf-8") as handle:
                handle.write(f"{path}\n")

    def check_auth(self, handler) -> bool:
        if not self.keys:
            return True
        header = handler.headers.get("Authorization", "")
        return header == f"Bearer {self.keys[0]}"

    def route(self, handler, path: str) -> None:
        if self.prefix:
            if not path.startswith(self.prefix):
                handler.send_error(404, "not found")
                return
            path = path[len(self.prefix):] or "/"
        if path == "/health":
            self._health(handler)
        elif path == "/v1/models":
            self._models(handler)
        elif path == "/completion":
            self._completion(handler)
        elif path == "/embedding":
            self._embedding(handler)
        elif path == "/rerank":
            self._rerank(handler)
        else:
            handler.send_error(404, "not found")

    def _health(self, handler) -> None:
        self.trace("/health")
        self.hits_health += 1
        if self.hits_health <= self.slow_503s:
            body = json.dumps({"status": "loading", "error": "model not loaded yet"}).encode()
            handler.send_response(503)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return
        body = json.dumps({"status": "ok"}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _models(self, handler) -> None:
        self.trace("/v1/models")
        if not self.check_auth(handler):
            self._unauthorized(handler)
            return
        if self.malformed == "models":
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(b"not-json")))
            handler.end_headers()
            handler.wfile.write(b"not-json")
            return
        body = json.dumps({"object": "list", "data": [{"id": self.model_id, "object": "model"}]}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _completion(self, handler) -> None:
        self.trace("/completion")
        if not self.check_auth(handler):
            self._unauthorized(handler)
            return
        if self.unsupported:
            handler.send_error(404, "not found")
            return
        if self.malformed == "completion":
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(b"garbage")))
            handler.end_headers()
            handler.wfile.write(b"garbage")
            return
        payload = {"content": "OK the word is OK", "stop": True}
        if not self.no_timings:
            payload["timings"] = {
                "prompt_n": 9,
                "predicted_n": 2,
                "prompt_per_second": 5.0,
                "predicted_per_second": 34.8,
            }
        body = json.dumps(payload).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _embedding(self, handler) -> None:
        self.trace("/embedding")
        if not self.check_auth(handler):
            self._unauthorized(handler)
            return
        if self.unsupported == 1 or self.unsupported:
            handler.send_error(404, "not found")
            return
        body = json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _rerank(self, handler) -> None:
        self.trace("/rerank")
        if not self.check_auth(handler):
            self._unauthorized(handler)
            return
        if self.unsupported:
            handler.send_error(404, "not found")
            return
        body = json.dumps({"results": [{"index": 0, "score": 0.95}]}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _unauthorized(self, handler) -> None:
        json_body = json.dumps({"error": {"message": "Unauthorized"}}).encode()
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(json_body)))
        handler.end_headers()
        handler.wfile.write(json_body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llama-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--api-prefix", default="")
    parser.add_argument("--alias")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-file", default="")
    parser.add_argument("--embedding", "--embeddings", action="store_true")
    parser.add_argument("--rerank", "--reranking", nargs="?", const=True, default=False)
    parser.add_argument("-m", "--model", default="")
    return parser


def main() -> int:
    pid_file = os.environ.get("FAKE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
    args, _unknown = build_parser().parse_known_args()
    print(f"llama-test-tool fake server: loading model {args.model or '<none>'}", flush=True)
    if os.environ.get("FAKE_EXIT_EARLY") == "1":
        print("llama-test-tool fake server: model load failed", flush=True)
        return 3
    server = FakeLlamaServer(args)
    print(f"llama-server: server is listening on http://{args.host}:{args.port}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            try:
                server.route(self, self.path)
            except BrokenPipeError:
                pass

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
            self._handle()

        def log_message(self, _format: str, *args) -> None:  # keep test output clean
            pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())