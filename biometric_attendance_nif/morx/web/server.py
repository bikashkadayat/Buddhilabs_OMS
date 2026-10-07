"""The dashboard's HTTP server.

Standard library only, and deliberately small: a static file handler plus two
JSON endpoints. The collector already earns its keep running unattended on a
small box; adding a web framework to look at its output would be a poor trade.

The files under `static/` are the built React app (source in `frontend/`,
`npm run build`). The build output is committed, so deploying needs no Node --
only editing the UI does.

The whole dataset goes to the browser in one response and every filter, chart,
and table is computed there. At a door terminal's volume (~11k punches after
five years) that is a ~250KB response and instant filtering, with no query
layer to keep in sync with the aggregations.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..config import Settings
from . import dataset

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# Below this a gzip round-trip costs more than it saves.
GZIP_THRESHOLD = 1024


class DataCache:
    """Parse once, serve many.

    A browser polls `/api/meta` to notice new punches, so the common request is
    "has anything changed?" -- answered with a `stat`, not a reparse. The
    dataset is rebuilt only when the file's size or mtime moves.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._fingerprint: tuple | None = None
        self._payload: bytes = b""
        self._meta: dict = {}

    def _paths(self) -> tuple[str, Path, Path]:
        return self._settings.storage_backend, self._settings.data_dir, self._settings.sqlite_path

    def refresh(self) -> None:
        backend, data_dir, sqlite_path = self._paths()
        current = dataset.fingerprint(backend, data_dir, sqlite_path)
        with self._lock:
            if current == self._fingerprint and self._payload:
                return
            data = dataset.load(backend, data_dir, sqlite_path)
            payload = data.to_payload()
            self._payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._fingerprint = current
            self._meta = {
                "generated_at": payload["generated_at"],
                "source": payload["source"],
                "punches": len(payload["records"]["t"]),
                "employees": len(payload["employees"]),
                # The browser compares this to decide whether to refetch the
                # (much larger) dataset -- any value that moves on change works.
                "revision": str(hash(current) & 0xFFFFFFFF),
                "notes": payload["notes"],
            }
            logger.info(
                "Dataset loaded: %d punches, %d employees from %s",
                self._meta["punches"],
                self._meta["employees"],
                payload["source"]["location"],
            )

    def data(self) -> bytes:
        self.refresh()
        with self._lock:
            return self._payload

    def meta(self) -> bytes:
        self.refresh()
        with self._lock:
            return json.dumps(self._meta).encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "morx-dashboard"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, cache: DataCache, **kwargs) -> None:
        self._cache = cache
        super().__init__(*args, **kwargs)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        path = urlparse(self.path).path
        try:
            if path == "/api/data":
                self._send_bytes(self._cache.data(), "application/json; charset=utf-8", cache=False)
            elif path == "/api/meta":
                self._send_bytes(self._cache.meta(), "application/json; charset=utf-8", cache=False)
            else:
                self._send_static(path)
        except BrokenPipeError:
            pass  # reader navigated away mid-response; nothing to report
        except Exception:
            logger.exception("Error serving %s", path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _send_static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC_DIR / name).resolve()
        # Confine to STATIC_DIR: the request path is attacker-controlled, and
        # `..` segments would otherwise walk out of it.
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        # Vite fingerprints everything under assets/ with a content hash, so those
        # are safe to cache forever; index.html points at the current hashes and
        # must not be, or a rebuilt UI would never reach an open browser.
        immutable = target.parent.name == "assets"
        self._send_bytes(target.read_bytes(), content_type, cache=immutable)

    # -- responses ----------------------------------------------------------

    def _send_bytes(self, body: bytes, content_type: str, *, cache: bool) -> None:
        encoding = None
        if len(body) > GZIP_THRESHOLD and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, 6)
            encoding = "gzip"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header(
            "Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store"
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Route access logs through logging instead of stderr, so they obey
        # --log-level like everything else the CLI prints.
        logger.debug("%s %s", self.address_string(), fmt % args)


def build_server(settings: Settings, host: str, port: int) -> ThreadingHTTPServer:
    cache = DataCache(settings)
    handler = partial(DashboardHandler, cache=cache)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> int:
    """Run the dashboard until interrupted. Returns a process exit code."""
    server = build_server(settings, host, port)
    shown = host if host not in ("0.0.0.0", "::") else "localhost"
    url = f"http://{shown}:{server.server_address[1]}/"

    print(f"Dashboard on {url}  (Ctrl-C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()
    return 0
