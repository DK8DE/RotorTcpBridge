"""HTTP-Pull fremder RotorTcpBridge-Instanzen für die Rotorübersicht (kein RS485)."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .rotor_overview_config import normalize_overview_config
from .rotor_overview_node import apply_node_snapshot_to_state

LocalProvider = Callable[[], Optional[Dict[str, Any]]]


@dataclass
class SiteLiveState:
    site_id: str
    online: bool = False
    referenced: Optional[bool] = None
    az_deg: Optional[float] = None
    az_smooth_deg: Optional[float] = None
    aselect: Optional[int] = None
    moving: bool = False
    profile_name: str = ""
    locator: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    antennas: Optional[List[Dict[str, Any]]] = None
    temp_motor_c: Optional[float] = None
    temp_ambient_c: Optional[float] = None
    wind_kmh: Optional[float] = None
    wind_dir_deg: Optional[float] = None
    wind_visible: bool = False
    last_rx_ts: float = 0.0
    last_error: str = ""


class _SiteWorker:
    def __init__(
        self,
        site: Dict[str, Any],
        on_update: Callable[[SiteLiveState], None],
        stop_event: threading.Event,
        local_provider: Optional[LocalProvider] = None,
        interval_s: float = 0.75,
    ) -> None:
        self.site = dict(site)
        self.on_update = on_update
        self.stop_event = stop_event
        self.local_provider = local_provider
        self.interval_s = max(0.4, float(interval_s))
        self.thread = threading.Thread(target=self._run, name=f"ov-http-{site.get('id')}", daemon=True)
        self.state = SiteLiveState(site_id=str(site.get("id") or ""))

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            kind = str(self.site.get("kind") or "app")
            try:
                if kind == "local":
                    self._poll_local()
                else:
                    self._poll_http()
            except Exception as e:
                self.state.online = False
                self.state.last_error = str(e)
            try:
                self.on_update(self.state)
            except Exception:
                pass
            self.stop_event.wait(self.interval_s)

    def _poll_local(self) -> None:
        fn = self.local_provider
        data = fn() if callable(fn) else None
        if not isinstance(data, dict):
            self.state.online = False
            self.state.last_error = "local"
            return
        apply_node_snapshot_to_state(self.state, data)

    def _poll_http(self) -> None:
        host = str(self.site.get("host") or "").strip()
        port = int(self.site.get("port") or 80)
        if not host:
            self.state.online = False
            self.state.last_error = "no_host"
            return
        pw = str(self.site.get("password") or "").strip()
        password = pw if pw else "rotor"
        url = f"http://{host}:{port}/api/overview/node"
        req = urllib.request.Request(url, method="GET")
        token = base64.b64encode(f"rotor:{password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            self.state.online = False
            self.state.last_error = f"http_{e.code}"
            return
        except Exception as e:
            self.state.online = False
            self.state.last_error = str(e) or "http"
            return
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            self.state.online = False
            self.state.last_error = "json"
            return
        data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        if not isinstance(data, dict):
            self.state.online = False
            self.state.last_error = "payload"
            return
        apply_node_snapshot_to_state(self.state, data)


class RotorOverviewMonitor:
    def __init__(
        self,
        on_update: Callable[[SiteLiveState], None],
        local_provider: Optional[LocalProvider] = None,
    ) -> None:
        self._on_update = on_update
        self._local_provider = local_provider
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._workers: List[_SiteWorker] = []
        self._cfg: Dict[str, Any] = normalize_overview_config(None)

    def set_local_provider(self, fn: Optional[LocalProvider]) -> None:
        self._local_provider = fn

    def start(self, cfg: Any) -> None:
        self.stop()
        self._stop = threading.Event()
        if isinstance(cfg, list):
            cfg = {"sites": cfg}
        self._cfg = normalize_overview_config(cfg)
        workers: List[_SiteWorker] = []
        for site in self._cfg.get("sites") or []:
            if not site.get("enabled"):
                continue
            kind = str(site.get("kind") or "")
            if kind == "local":
                w = _SiteWorker(site, self._on_update, self._stop, local_provider=self._local_provider)
            elif kind == "app":
                if not str(site.get("host") or "").strip():
                    continue
                w = _SiteWorker(site, self._on_update, self._stop, local_provider=None)
            else:
                continue
            workers.append(w)
            w.start()
        with self._lock:
            self._workers = workers

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._workers = []
