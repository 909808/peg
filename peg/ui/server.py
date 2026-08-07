"""The graphical client's back end: a small local HTTP server.

PEG's GUI runs in a browser rather than in a desktop toolkit. That is a
deliberate trade. Tk is in the standard library but is absent from a
surprising number of Linux installs and looks like 1995; a browser is on every
machine that exists, draws to a canvas at sixty frames a second, and costs
this project exactly zero dependencies because ``http.server`` is stdlib too.

The server binds to localhost only, serves one page and five JSON endpoints,
and holds the single :class:`~peg.game.Game` in memory. It is not a web
application -- it is a rendering surface that happens to speak HTTP.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import viewdata

#: The page is package data so it survives being installed as a wheel or
#: bundled into a zipapp, exactly like the Earth dataset.
PAGE_PACKAGE = __package__ + ".web"
PAGE_NAME = "index.html"


def page_html() -> bytes:
    return resources.files(PAGE_PACKAGE).joinpath(PAGE_NAME).read_bytes()


class _Handler(BaseHTTPRequestHandler):
    #: Set by :func:`serve`.
    game = None
    lock = threading.Lock()
    #: The planet thumbnail never changes; build it once.
    _cached_minimap = None

    protocol_version = "HTTP/1.1"
    server_version = "PEG"

    # ---- plumbing ----

    def log_message(self, fmt, *args):        # noqa: N802 - stdlib signature
        """Silence the default request log. The terminal belongs to the game."""

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                              # browser navigated away mid-send

    def _json(self, payload, code: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _query(self) -> dict[str, str]:
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    def _int(self, q: dict, key: str, default: int) -> int:
        try:
            return int(q.get(key, default))
        except (TypeError, ValueError):
            return default

    # ---- routes ----

    def do_GET(self) -> None:                 # noqa: N802 - stdlib signature
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html"):
                self._send(page_html(), "text/html; charset=utf-8")
            elif route == "/api/legend":
                self._json(viewdata.legend())
            elif route == "/api/status":
                self._json(self._status())
            elif route == "/api/view":
                self._json(self._view())
            elif route == "/api/tile":
                self._json(self._tile())
            elif route == "/api/site":
                self._json(self._site())
            elif route == "/api/minimap":
                self._json(self._minimap())
            elif route == "/api/health":
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:              # a GUI bug must not kill the game
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:                # noqa: N802 - stdlib signature
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if route == "/api/advance":
                self._json(self._advance(body))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---- handlers ----

    def _status(self) -> dict:
        g = self.game
        with self.lock:
            return {
                "status": viewdata.status(g),
                "actors": viewdata.actors(g.colony),
                "structures": viewdata.structures(g.colony),
                "fields": viewdata.fields(g.colony),
            }

    def _view(self) -> dict:
        q = self._query()
        g = self.game
        vp = viewdata.Viewport(
            x0=self._int(q, "x0", 0), z0=self._int(q, "z0", 0),
            w=self._int(q, "w", 64), h=self._int(q, "h", 64))
        with self.lock:
            return viewdata.tiles(g.colony.map, vp)

    def _site(self) -> dict:
        """The whole site in one response.

        A 160 m site is 167 KiB packed. Fetching viewports on every pan was
        the single largest source of interface lag, and entirely unnecessary
        at that size -- the client now loads once and pans locally.
        """
        m = self.game.colony.map
        with self.lock:
            m.ensure_all()
            return viewdata.tiles(m, viewdata.Viewport(0, 0, m.size, m.size))

    def _minimap(self):
        if self._cached_minimap is None:
            with self.lock:
                _Handler._cached_minimap = viewdata.world_minimap(self.game)
        return self._cached_minimap

    def _tile(self) -> dict:
        q = self._query()
        with self.lock:
            return viewdata.tile_info(self.game.colony.map,
                                      self._int(q, "x", 0),
                                      self._int(q, "z", 0),
                                      self.game.colony)

    def _advance(self, body: dict) -> dict:
        minutes = max(0, min(int(body.get("minutes", 60)), 1440 * 30))
        g = self.game
        with self.lock:
            steps = max(1, minutes // g.minutes_per_step)
            reports = []
            for _ in range(steps):
                g.step()
                if g.over:
                    break
                r = g.resolve_incoming()
                if r:
                    reports.append(r)
                    g.colony.note(r)
            return {"ok": True, "over": g.over, "battles": reports}


def serve(game, host: str = "127.0.0.1", port: int = 8770,
          open_browser: bool = True, quiet: bool = False):
    """Start the GUI server and block until interrupted.

    Binds to localhost. This is a single-player game with a browser for a
    window; there is nothing here that should be reachable from a network.
    """
    _Handler.game = game
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{httpd.server_address[1]}/"

    if not quiet:
        print()
        print(f"  PEG is running at  {url}")
        print("  Press Ctrl-C here to stop.")
        print()

    if open_browser:
        # Opening a browser is best-effort: on a headless box or over SSH there
        # may not be one, and that is not an error worth stopping for.
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\n  stopped.")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return httpd


def start_background(game, host: str = "127.0.0.1", port: int = 0):
    """Start the server on a thread and return ``(httpd, url)``.

    Used by the tests, which need the endpoints without blocking.
    """
    _Handler.game = game
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://{host}:{httpd.server_address[1]}/"
