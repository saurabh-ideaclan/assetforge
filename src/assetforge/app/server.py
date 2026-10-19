"""Local HTTP server behind the desktop app: static UI, a JSON API and media files.

Security model: it binds to 127.0.0.1 only, on a random port, and every request must carry a random
session token (query `token=` on the first page load, then an HttpOnly SameSite=Strict cookie, or an
`X-AssetForge-Token` header). It also checks the Host header so DNS-rebinding pages can't reach it.
Only files AssetForge cataloged are served; there is no general file access.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue as stdqueue
import re
import secrets
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from assetforge.core.catalog import Library
from assetforge.errors import AssetForgeError, ExitCode, InternalError, InvalidInputError

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "af_token"
POOL_SIZE = 8
STATUS_BY_EXIT = {
    ExitCode.NOT_FOUND: 404, ExitCode.INVALID_INPUT: 400, ExitCode.USAGE: 400, ExitCode.CONFLICT: 409,
    ExitCode.POLICY: 403, ExitCode.PROVIDER: 502, ExitCode.STORAGE: 500,
}  # fmt: skip


@dataclass
class FileResponse:
    path: Path
    mime: str | None = None
    cache: str = "no-cache"


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    match: re.Match[str]
    server: AppServer

    def arg(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values and values[0] != "" else default

    def args(self, name: str) -> list[str]:
        return [v for raw in self.query.get(name, []) for v in raw.split(",") if v]

    def int_arg(self, name: str, default: int) -> int:
        try:
            return int(self.arg(name, str(default)))  # type: ignore[arg-type]
        except ValueError as exc:
            raise InvalidInputError(f"{name} must be a whole number") from exc


Handler = Callable[[Request, Library], Any]


@dataclass
class Router:
    routes: list[tuple[str, re.Pattern[str], Handler]] = field(default_factory=list)

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            self.routes.append((method, re.compile(f"^{pattern}$"), fn))
            return fn

        return register

    def find(self, method: str, path: str) -> tuple[Handler, re.Match[str]] | None:
        for m, pattern, fn in self.routes:
            if m == method and (match := pattern.match(path)):
                return fn, match
        return None


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, home: str | Path | None, router: Router, *, port: int = 0, token: str | None = None,
                 verbose: bool = False) -> None:  # fmt: skip
        super().__init__(("127.0.0.1", port), RequestHandler)
        self.router = router
        self.token = token or secrets.token_urlsafe(24)
        self.verbose = verbose
        first = Library(home, pooled=True)
        self.home = first.paths.home
        self._pool: stdqueue.LifoQueue[Library] = stdqueue.LifoQueue()
        self._pool.put(first)
        self._created = 1
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    @contextmanager
    def library(self) -> Iterator[Library]:
        """Borrow a pooled catalog connection (each is used by one request thread at a time)."""
        try:
            lib = self._pool.get_nowait()
        except stdqueue.Empty:
            with self._lock:
                grow = self._created < POOL_SIZE
                if grow:
                    self._created += 1
            lib = Library(self.home, pooled=True) if grow else self._pool.get()
        try:
            yield lib
        finally:
            self._pool.put(lib)

    def close_pool(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
            except stdqueue.Empty:
                return


class RequestHandler(BaseHTTPRequestHandler):
    server: AppServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.server.verbose:
            sys.stderr.write(f"[assetforge ui] {self.address_string()} {format % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    # ------------------------------------------------------------------ plumbing

    def _token_ok(self, query: dict[str, list[str]]) -> tuple[bool, bool]:
        """Returns (authorized, token_came_from_query)."""
        expected = self.server.token
        if secrets.compare_digest(query.get("token", [""])[0], expected):
            return True, True
        if secrets.compare_digest(self.headers.get("X-AssetForge-Token", ""), expected):
            return True, False
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        value = cookie[COOKIE].value if COOKIE in cookie else ""
        return secrets.compare_digest(value, expected), False

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        if not self._host_ok():
            return self._send_json({"status": "error", "error": {"code": "FORBIDDEN", "message": "Bad host"}}, 403)
        authorized, from_query = self._token_ok(query)
        if not authorized:
            return self._send_json({"status": "error", "error": {"code": "FORBIDDEN", "message":
                                    "Missing or wrong session token; open the URL printed by `assetforge ui`"}}, 403)  # fmt: skip
        extra_headers = {}
        if from_query:
            extra_headers["Set-Cookie"] = f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"
        path = url.path
        if method in ("GET", "HEAD") and (path == "/" or path.startswith("/static/")):
            return self._send_static(path, method, extra_headers)
        found = self.server.router.find("GET" if method == "HEAD" else method, path)
        if found is None:
            return self._send_json({"status": "error", "error": {"code": "NOT_FOUND", "message": f"No route {path}"}}, 404)
        handler, match = found
        try:
            body = self._read_body() if method == "POST" else None
            with self.server.library() as lib:
                result = handler(Request(method, path, query, body, match, self.server), lib)
            if isinstance(result, FileResponse):
                return self._send_file(result, method)
            self._send_json({"status": "ok", **result} if isinstance(result, dict) else {"status": "ok", "data": result},
                            200, extra_headers)  # fmt: skip
        except AssetForgeError as exc:
            self._send_json({"status": "error", "error": exc.to_dict()}, STATUS_BY_EXIT.get(exc.exit_code, 500))
        except ValidationError as exc:
            from assetforge.cli_common import invalid_input

            self._send_json({"status": "error", "error": invalid_input(exc).to_dict()}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            error = InternalError(f"{type(exc).__name__}: {exc}")
            self._send_json({"status": "error", "error": error.to_dict()}, 500)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 5_000_000:
            raise InvalidInputError("Request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise InvalidInputError(f"Body is not valid JSON: {exc}") from exc

    def _send_json(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, default=str, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_static(self, path: str, method: str, headers: dict[str, str]) -> None:
        rel = "index.html" if path == "/" else path.removeprefix("/static/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._send_json({"status": "error", "error": {"code": "NOT_FOUND", "message": rel}}, 404)
        data = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            mime = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")  # fmt: skip
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(data)

    def _send_file(self, response: FileResponse, method: str) -> None:
        """Serve a file with single-range support (audio/video seeking needs 206 responses)."""
        size = response.path.stat().st_size
        start, end, status = 0, size - 1, 200
        if rng := self.headers.get("Range"):
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if m.group(1) == "":
                start = max(0, size - int(m.group(2)))
            else:
                start = int(m.group(1))
                end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", response.mime or mimetypes.guess_type(response.path.name)[0]
                         or "application/octet-stream")  # fmt: skip
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", response.cache)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if method == "HEAD":
            return
        with response.path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def watch_parent(parent_pid: int, server: AppServer, interval: float = 2.0) -> threading.Thread:
    """Shut down when the process that started us (the Mac app) goes away."""

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                server.shutdown()
                return
            except PermissionError:
                continue

    thread = threading.Thread(target=loop, name="parent-watchdog", daemon=True)
    thread.start()
    return thread
