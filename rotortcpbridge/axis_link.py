"""Hardware-Link pro Achse (AZ/EL) — getrennte TCP-IPs im gleichen Profil."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional, Tuple


def _ui_endpoints(cfg: Optional[dict]) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    _ui = cfg.get("ui")
    ui: dict = _ui if isinstance(_ui, dict) else {}
    _eps = ui.get("setup_axis_endpoints")
    return _eps if isinstance(_eps, dict) else {}


def axis_endpoint_from_cfg(cfg: Optional[dict], axis: str) -> Tuple[str, int]:
    """Host/Port aus ui.setup_axis_endpoints[axis], sonst hardware_link."""
    cfg = cfg if isinstance(cfg, dict) else {}
    axis = str(axis or "az").strip().lower()
    eps = _ui_endpoints(cfg)
    _ep = eps.get(axis)
    ep: dict = _ep if isinstance(_ep, dict) else {}
    host = str(ep.get("host") or "").strip()
    try:
        port = int(ep.get("port") or 8886)
    except Exception:
        port = 8886
    if not host:
        _hl = cfg.get("hardware_link")
        hl: dict = _hl if isinstance(_hl, dict) else {}
        host = str(hl.get("tcp_ip") or "").strip()
        try:
            port = int(hl.get("tcp_port") or 8886)
        except Exception:
            port = 8886
    return host, max(1, min(65535, int(port)))


def store_axis_endpoint(cfg: dict, axis: str, host: str, port: int) -> None:
    """Persistiert Host/Port für eine Achse unter ui.setup_axis_endpoints."""
    axis = str(axis or "az").strip().lower()
    if axis not in ("az", "el"):
        axis = "az"
    ui = cfg.setdefault("ui", {})
    if not isinstance(ui, dict):
        cfg["ui"] = {}
        ui = cfg["ui"]
    eps = ui.setdefault("setup_axis_endpoints", {})
    if not isinstance(eps, dict):
        eps = {}
        ui["setup_axis_endpoints"] = eps
    try:
        port_i = max(1, min(65535, int(port)))
    except Exception:
        port_i = 8886
    eps[axis] = {"host": str(host or "").strip(), "port": port_i}


def sync_hardware_link_from_az(cfg: dict) -> None:
    """hardware_link.tcp_* aus AZ-Endpunkt (Primärlink / Anzeige)."""
    host, port = axis_endpoint_from_cfg(cfg, "az")
    hl = cfg.setdefault("hardware_link", {})
    if not isinstance(hl, dict):
        cfg["hardware_link"] = {}
        hl = cfg["hardware_link"]
    if host:
        mode = str(hl.get("mode") or "tcp").strip().lower()
        if mode not in ("tcp", "udp", "com"):
            mode = "tcp"
        if mode == "com":
            # TCP-Endpunkt gesetzt → Betriebsmodus TCP
            mode = "tcp"
        hl["mode"] = mode if mode in ("tcp", "udp") else "tcp"
        hl["tcp_ip"] = host
        hl["tcp_port"] = int(port)


def dual_axis_tcp_active(cfg: Optional[dict]) -> bool:
    """True wenn AZ und EL unterschiedliche TCP-Endpunkte haben und EL aktiv ist."""
    cfg = cfg if isinstance(cfg, dict) else {}
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    if not bool(rb.get("enable_el", False)):
        return False
    hl = cfg.get("hardware_link") if isinstance(cfg.get("hardware_link"), dict) else {}
    mode = str(hl.get("mode") or "tcp").strip().lower()
    if mode not in ("tcp", "udp"):
        return False
    az_h, az_p = axis_endpoint_from_cfg(cfg, "az")
    el_h, el_p = axis_endpoint_from_cfg(cfg, "el")
    if not az_h or not el_h:
        return False
    return (az_h, int(az_p)) != (el_h, int(el_p))


def link_cfg_for_axis(cfg: Optional[dict], axis: str) -> dict:
    """HardwareClient-Config für eine Achse (TCP/UDP aus Endpunkt, sonst hardware_link)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    _hl = cfg.get("hardware_link")
    hl: dict = dict(_hl) if isinstance(_hl, dict) else {}
    mode = str(hl.get("mode") or "tcp").strip().lower()
    if mode not in ("tcp", "udp", "com"):
        mode = "tcp"
    host, port = axis_endpoint_from_cfg(cfg, axis)
    out = dict(hl)
    if mode in ("tcp", "udp") and host:
        out["mode"] = mode
        out["tcp_ip"] = host
        out["tcp_port"] = int(port)
    return out


def ensure_setup_axis_endpoints(cfg: dict) -> None:
    """Migration: fehlende Achsen-Endpunkte aus hardware_link ableiten."""
    if not isinstance(cfg, dict):
        return
    hl = cfg.get("hardware_link") if isinstance(cfg.get("hardware_link"), dict) else {}
    host = str(hl.get("tcp_ip") or "").strip()
    try:
        port = int(hl.get("tcp_port") or 8886)
    except Exception:
        port = 8886
    eps = _ui_endpoints(cfg)
    for axis in ("az", "el"):
        ep = eps.get(axis) if isinstance(eps.get(axis), dict) else {}
        if not str(ep.get("host") or "").strip() and host:
            store_axis_endpoint(cfg, axis, host, port)


def wait_hw_ready(hw: Any, *, timeout_s: float = 4.0) -> bool:
    """Safe-Reconnect abwarten und is_connected()."""
    if hw is None:
        return False
    deadline = time.time() + max(0.5, float(timeout_s))
    app = None
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    except Exception:
        app = None
    while time.time() < deadline:
        try:
            in_safe = bool(getattr(hw, "_in_safe_reconnect_mode", lambda: False)())
        except Exception:
            in_safe = False
        try:
            online = bool(hw.is_connected())
        except Exception:
            online = False
        if online and not in_safe:
            return True
        if app is not None:
            try:
                app.processEvents()
            except Exception:
                pass
        time.sleep(0.05)
    try:
        return bool(hw.is_connected()) and not bool(
            getattr(hw, "_in_safe_reconnect_mode", lambda: False)()
        )
    except Exception:
        return False


def ensure_axis_tcp_link(
    ctrl: Any,
    cfg: dict,
    axis: str,
    *,
    save_cfg_cb: Optional[Callable[[dict], None]] = None,
    timeout_s: float = 5.0,
) -> Tuple[bool, str]:
    """Achsen-Link herstellen.

    Dual-IP: passenden Client (hw / el_hw) reconnecten, ohne den anderen zu überschreiben.
    Single-IP: wie bisher hardware_link auf die Achsen-IP stellen.
    """
    axis = str(axis or "az").strip().lower()
    host, port = axis_endpoint_from_cfg(cfg, axis)
    dual = dual_axis_tcp_active(cfg)
    hl = cfg.setdefault("hardware_link", {})
    if not isinstance(hl, dict):
        cfg["hardware_link"] = {}
        hl = cfg["hardware_link"]

    if dual and ctrl is not None:
        # Zuerst Speichern → Caller kann el_hw anlegen, bevor wir den Client wählen.
        # (Sonst fällt EL auf hw zurück und der AZ-TCP-Client landet auf der EL-IP.)
        if axis == "az":
            sync_hardware_link_from_az(cfg)
        if callable(save_cfg_cb):
            try:
                save_cfg_cb(cfg)
            except Exception:
                pass
        if axis == "el":
            hw = getattr(ctrl, "el_hw", None)
            if hw is None:
                return False, "no el_hw"
        else:
            hw = getattr(ctrl, "hw", None)
        link_cfg = link_cfg_for_axis(cfg, axis)
        if hw is None:
            return False, "no hw"
        if not host:
            return False, "no axis endpoint"
        try:
            hw.update_cfg(dict(link_cfg))
        except Exception as e:
            return False, str(e)
        if not wait_hw_ready(hw, timeout_s=timeout_s):
            return False, f"no link {host}:{int(port)}"
        time.sleep(0.15)
        return True, f"{host}:{int(port)}"

    # Single-Link: hardware_link umschalten (bisheriges Verhalten)
    # Bei getrennten Endpunkten trotzdem AZ-Primär in hardware_link halten,
    # wenn wir nur die EL-IP anfassen würden — Dual-Pfad oben greift aber zuerst.
    cur_mode = str(hl.get("mode", "com") or "com").strip().lower()
    hw = getattr(ctrl, "hw", None) if ctrl is not None else None

    if host:
        changed = (
            cur_mode != "tcp"
            or str(hl.get("tcp_ip") or "").strip() != host
            or int(hl.get("tcp_port") or 0) != int(port)
        )
        hl["mode"] = "tcp"
        hl["tcp_ip"] = host
        hl["tcp_port"] = int(port)
        store_axis_endpoint(cfg, axis, host, port)
        if callable(save_cfg_cb):
            try:
                save_cfg_cb(cfg)
            except Exception:
                pass
        if hw is None:
            return False, "no hw"
        need_reconnect = changed
        try:
            if not bool(hw.is_connected()):
                need_reconnect = True
        except Exception:
            need_reconnect = True
        if need_reconnect:
            try:
                hw.update_cfg(dict(hl))
            except Exception as e:
                return False, str(e)
            if not wait_hw_ready(hw, timeout_s=timeout_s):
                return False, f"no link {host}:{int(port)}"
            time.sleep(0.15)
        return True, f"{host}:{int(port)}"

    if cur_mode == "com":
        if hw is not None and not wait_hw_ready(hw, timeout_s=min(2.0, timeout_s)):
            return False, "offline"
        return True, "COM"
    return False, "no axis endpoint"
