"""HTTP-Webserver für die Antennenkarte (Anzeige + Steuerung) im LAN.

Liefert dieselbe Leaflet-Karte wie das Desktop-Fenster und steuert den Rotor
über ``POST /api/setaz``. Live-Updates (Beams/Ist) per Server-Sent Events
unter ``GET /api/events``.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse

DEFAULT_MAP_WEBSERVER_PORT = 80
DEFAULT_MAP_WEBSERVER_HOST = "0.0.0.0"
DEFAULT_MAP_WEBSERVER_PASSWORD = "rotor"


def resolve_map_webserver_password(password: Optional[str]) -> str:
    """Konfiguriertes Passwort; leer / None → Default ``rotor``."""
    s = str(password or "").strip()
    return s if s else DEFAULT_MAP_WEBSERVER_PASSWORD


class MapWebServer:
    """Threading-HTTP-Server für die Browser-Antennenkarte."""

    def __init__(
        self,
        host: str,
        port: int,
        log: Any,
        *,
        password: Optional[str] = None,
        html_provider: Optional[Callable[[], str]] = None,
        compass_html_provider: Optional[Callable[[], str]] = None,
        live_provider: Optional[Callable[[], dict]] = None,
        compass_provider: Optional[Callable[[], dict]] = None,
        setaz_handler: Optional[Callable[..., None]] = None,
        ui_action_handler: Optional[Callable[..., None]] = None,
        aswatch_provider: Optional[Callable[[], tuple]] = None,
        aircraft_provider: Optional[Callable[[], list]] = None,
        asnearest_provider: Optional[Callable[[], list]] = None,
    ) -> None:
        self.host = str(host or DEFAULT_MAP_WEBSERVER_HOST).strip() or DEFAULT_MAP_WEBSERVER_HOST
        self.port = int(port or DEFAULT_MAP_WEBSERVER_PORT)
        self.log = log
        self._password_cfg = str(password if password is not None else DEFAULT_MAP_WEBSERVER_PASSWORD)
        self.html_provider = html_provider
        self.compass_html_provider = compass_html_provider
        self.live_provider = live_provider
        self.compass_provider = compass_provider
        self.setaz_handler = setaz_handler
        self.ui_action_handler = ui_action_handler
        self.aswatch_provider = aswatch_provider
        self.aircraft_provider = aircraft_provider
        self.asnearest_provider = asnearest_provider
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._last_error: str = ""
        self.last_rx_ts: float = 0.0

    @property
    def running(self) -> bool:
        return bool(self._running)

    @property
    def last_error(self) -> str:
        return str(self._last_error or "")

    @property
    def password(self) -> str:
        """Effektives HTTP-Basic-Passwort (leer → Default ``rotor``)."""
        return resolve_map_webserver_password(self._password_cfg)

    def set_password(self, password: Optional[str]) -> None:
        """Passwort zur Laufzeit setzen (ohne Server-Neustart)."""
        self._password_cfg = str(password if password is not None else "")

    def configure(
        self,
        *,
        password: Optional[str] = None,
        html_provider: Optional[Callable[[], str]] = None,
        compass_html_provider: Optional[Callable[[], str]] = None,
        live_provider: Optional[Callable[[], dict]] = None,
        compass_provider: Optional[Callable[[], dict]] = None,
        setaz_handler: Optional[Callable[..., None]] = None,
        ui_action_handler: Optional[Callable[..., None]] = None,
        aswatch_provider: Optional[Callable[[], tuple]] = None,
        aircraft_provider: Optional[Callable[[], list]] = None,
        asnearest_provider: Optional[Callable[[], list]] = None,
    ) -> None:
        if password is not None:
            self.set_password(password)
        if html_provider is not None:
            self.html_provider = html_provider
        if compass_html_provider is not None:
            self.compass_html_provider = compass_html_provider
        if live_provider is not None:
            self.live_provider = live_provider
        if compass_provider is not None:
            self.compass_provider = compass_provider
        if setaz_handler is not None:
            self.setaz_handler = setaz_handler
        if ui_action_handler is not None:
            self.ui_action_handler = ui_action_handler
        if aswatch_provider is not None:
            self.aswatch_provider = aswatch_provider
        if aircraft_provider is not None:
            self.aircraft_provider = aircraft_provider
        if asnearest_provider is not None:
            self.asnearest_provider = asnearest_provider

    def start(self) -> tuple[bool, str]:
        """Server starten. Bei Fehler: ``(False, meldung)``."""
        with self._lock:
            if self._running:
                return True, ""
            self._last_error = ""
            try:
                # Bind-Probe (klare Fehlermeldung bei belegtem Port / fehlenden Rechten).
                # Unter Windows: SO_EXCLUSIVEADDRUSE, sonst kann SO_REUSEADDR einen
                # bereits belegten Port fälschlich als frei melden.
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    else:
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind((self.host, int(self.port)))
                finally:
                    try:
                        probe.close()
                    except Exception:
                        pass
            except OSError as exc:
                err = self._format_bind_error(exc)
                self._last_error = err
                try:
                    self.log.write("WARN", f"Map-Webserver: {err}")
                except Exception:
                    pass
                return False, err

            try:
                handler = self._make_handler()

                class _MapHTTPServer(ThreadingHTTPServer):
                    # Kein SO_REUSEADDR: unter Windows unverträglich mit SO_EXCLUSIVEADDRUSE.
                    allow_reuse_address = False

                    def server_bind(self) -> None:  # noqa: N802
                        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                            try:
                                self.socket.setsockopt(
                                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
                                )
                            except OSError:
                                pass
                        super().server_bind()

                    def handle_error(self, request, client_address) -> None:  # noqa: N802
                        import sys

                        exc = sys.exc_info()[1]
                        if isinstance(
                            exc,
                            (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
                        ):
                            return
                        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (
                            10053,
                            10054,
                        ):
                            return
                        super().handle_error(request, client_address)

                httpd = _MapHTTPServer((self.host, int(self.port)), handler)
                httpd.daemon_threads = True
                self._httpd = httpd
                self._running = True
                self._thread = threading.Thread(
                    target=self._serve, name="map-webserver", daemon=True
                )
                self._thread.start()
                try:
                    self.log.write(
                        "INFO",
                        f"Map-Webserver aktiv auf http://{self.host}:{int(self.port)}/",
                    )
                except Exception:
                    pass
                return True, ""
            except OSError as exc:
                self._running = False
                self._httpd = None
                err = self._format_bind_error(exc)
                self._last_error = err
                try:
                    self.log.write("WARN", f"Map-Webserver: {err}")
                except Exception:
                    pass
                return False, err

    def stop(self) -> None:
        """Server stoppen; blockiert nicht endlos (SSE/Windows-shutdown-Hänger)."""
        with self._lock:
            self._running = False
            httpd = self._httpd
            self._httpd = None
        if httpd is not None:
            # serve_forever aufwecken: Socket schließen + shutdown im Hintergrund.
            # httpd.shutdown() kann sonst ewig auf __is_shut_down warten (offene SSE).
            try:
                httpd.socket.close()
            except Exception:
                pass

            def _shutdown_httpd() -> None:
                try:
                    httpd.shutdown()
                except Exception:
                    pass
                try:
                    httpd.server_close()
                except Exception:
                    pass

            sh = threading.Thread(
                target=_shutdown_httpd, name="map-webserver-shutdown", daemon=True
            )
            sh.start()
            sh.join(timeout=1.5)
        th = self._thread
        self._thread = None
        if th is not None and th.is_alive():
            try:
                th.join(timeout=1.5)
            except Exception:
                pass
        try:
            self.log.write("INFO", "Map-Webserver gestoppt")
        except Exception:
            pass

    def restart(self, host: str, port: int) -> tuple[bool, str]:
        self.stop()
        self.host = str(host or DEFAULT_MAP_WEBSERVER_HOST).strip() or DEFAULT_MAP_WEBSERVER_HOST
        try:
            self.port = int(port)
        except Exception:
            self.port = DEFAULT_MAP_WEBSERVER_PORT
        return self.start()

    def _serve(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        try:
            httpd.serve_forever(poll_interval=0.5)
        except Exception as exc:
            try:
                self.log.write("WARN", f"Map-Webserver serve: {exc}")
            except Exception:
                pass
        finally:
            self._running = False

    def _format_bind_error(self, exc: BaseException) -> str:
        from .net_bind_error import format_bind_error

        return format_bind_error(
            self.host, int(self.port), exc, proto_name="Karten-Webserver"
        )

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                return

            def handle(self) -> None:
                """Browser-Abbrechen (Reload/Tab zu) nicht als Traceback loggen."""
                try:
                    super().handle()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    return
                except OSError as exc:
                    # WinError 10053/10054 u.ä.
                    if getattr(exc, "winerror", None) in (10053, 10054) or getattr(
                        exc, "errno", None
                    ) in (104, 32, 54):
                        return
                    raise

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Content-Type, Authorization"
                )

            def _unauthorized(self) -> None:
                body = b'{"ok":false,"error":"unauthorized"}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header('WWW-Authenticate', 'Basic realm="RotorTcpBridge"')
                self.send_header("Cache-Control", "no-store")
                self._cors()
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _check_auth(self) -> bool:
                """HTTP Basic Auth: Benutzername beliebig, Passwort aus Config (Default rotor)."""
                want = server.password
                hdr = str(self.headers.get("Authorization") or "").strip()
                if not hdr.lower().startswith("basic "):
                    self._unauthorized()
                    return False
                try:
                    raw = base64.b64decode(hdr[6:].strip().encode("ascii"), validate=False)
                    decoded = raw.decode("utf-8", errors="replace")
                except Exception:
                    self._unauthorized()
                    return False
                # user:password — nur Passwort prüfen
                if ":" in decoded:
                    _user, got = decoded.split(":", 1)
                else:
                    got = decoded
                if got != want:
                    self._unauthorized()
                    return False
                return True

            def _send(
                self,
                code: int,
                body: bytes,
                content_type: str,
                *,
                extra_headers: Optional[dict] = None,
            ) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors()
                if extra_headers:
                    for k, v in extra_headers.items():
                        self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _send_json(self, code: int, obj: Any) -> None:
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self._send(code, raw, "application/json; charset=utf-8")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if not self._check_auth():
                    return
                path = urlparse(self.path).path or "/"
                if path in ("/", "/index.html", "/map"):
                    self._handle_map()
                    return
                if path == "/compass":
                    self._handle_compass()
                    return
                if path == "/api/state":
                    self._handle_state()
                    return
                if path == "/api/compass":
                    self._handle_compass_state()
                    return
                if path == "/api/events":
                    self._handle_sse()
                    return
                self._send_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._check_auth():
                    return
                path = urlparse(self.path).path or "/"
                if path == "/api/setaz":
                    self._handle_setaz()
                    return
                if path == "/api/ui":
                    self._handle_ui()
                    return
                if path == "/api/geocode":
                    self._handle_geocode()
                    return
                self._send_json(404, {"ok": False, "error": "not_found"})

            def _handle_map(self) -> None:
                try:
                    html = ""
                    if callable(server.html_provider):
                        html = str(server.html_provider() or "")
                    if not html:
                        html = (
                            "<!DOCTYPE html><html><body><p>Map unavailable</p></body></html>"
                        )
                    self._send(
                        200,
                        html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_compass(self) -> None:
                try:
                    html = ""
                    if callable(server.compass_html_provider):
                        html = str(server.compass_html_provider() or "")
                    if not html:
                        html = (
                            "<!DOCTYPE html><html><body><p>Compass unavailable</p></body></html>"
                        )
                    self._send(
                        200,
                        html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_state(self) -> None:
                try:
                    data = {}
                    if callable(server.live_provider):
                        data = server.live_provider() or {}
                    self._send_json(200, {"ok": True, "data": data})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_compass_state(self) -> None:
                try:
                    data = {}
                    if callable(server.compass_provider):
                        data = server.compass_provider() or {}
                    self._send_json(200, {"ok": True, "data": data})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_setaz(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except Exception:
                    length = 0
                raw = self.rfile.read(max(0, min(length, 65536))) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                except Exception:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                try:
                    lat = float(payload.get("lat"))
                    lon = float(payload.get("lon"))
                except Exception:
                    self._send_json(400, {"ok": False, "error": "lat_lon_required"})
                    return
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    self._send_json(400, {"ok": False, "error": "lat_lon_range"})
                    return
                dest = payload.get("asnearest_dest")
                dest_s = str(dest).strip() if dest is not None and str(dest).strip() else None
                try:
                    server.last_rx_ts = time.time()
                    if callable(server.setaz_handler):
                        server.setaz_handler(lat, lon, dest_s)
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_ui(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except Exception:
                    length = 0
                raw = self.rfile.read(max(0, min(length, 65536))) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                except Exception:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                action = str(payload.get("action") or "").strip()
                if not action:
                    self._send_json(400, {"ok": False, "error": "action_required"})
                    return
                try:
                    server.last_rx_ts = time.time()
                    if callable(server.ui_action_handler):
                        server.ui_action_handler(action, payload)
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_geocode(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except Exception:
                    length = 0
                raw = self.rfile.read(max(0, min(length, 65536))) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                except Exception:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                query = str(payload.get("query") or "").strip()
                if not query:
                    self._send_json(400, {"ok": False, "error": "query_required"})
                    return
                try:
                    from .geocode import geocode_places

                    results = geocode_places(query, limit=8, timeout=12.0)
                    items = [
                        {
                            "lat": float(r.lat),
                            "lon": float(r.lon),
                            "display_name": str(r.display_name or ""),
                        }
                        for r in (results or [])
                    ]
                    self._send_json(200, {"ok": True, "results": items})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _sse_write(self, event: str, obj: Any) -> bool:
                try:
                    data = json.dumps(obj, ensure_ascii=False)
                    chunk = f"event: {event}\ndata: {data}\n\n".encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    return True
                except Exception:
                    return False

            def _handle_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._cors()
                self.end_headers()
                # Initial + periodische Updates
                while server._running:
                    try:
                        live = {}
                        if callable(server.live_provider):
                            live = server.live_provider() or {}
                        if not self._sse_write("beam", live):
                            break
                        if callable(server.compass_provider):
                            try:
                                comp = server.compass_provider() or {}
                                if not self._sse_write("compass", comp):
                                    break
                            except Exception:
                                pass
                        if callable(server.aswatch_provider):
                            try:
                                items, total = server.aswatch_provider()
                                if not self._sse_write(
                                    "aswatch",
                                    {"items": items or [], "total": int(total or 0)},
                                ):
                                    break
                            except Exception:
                                pass
                        if callable(server.aircraft_provider):
                            try:
                                air = server.aircraft_provider() or []
                                if not self._sse_write("aircraft", {"items": air}):
                                    break
                            except Exception:
                                pass
                        if callable(server.asnearest_provider):
                            try:
                                rows = server.asnearest_provider() or []
                                if not self._sse_write("asnearest", {"items": rows}):
                                    break
                            except Exception:
                                pass
                    except Exception:
                        break
                    # ~30 Hz — gleiche Rate wie Web-Live-Cache / Karten-Refresh
                    time.sleep(0.033)

        return Handler
