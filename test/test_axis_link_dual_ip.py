"""Unit-Tests: getrennte AZ/EL-TCP-Endpunkte im gleichen Profil."""

from __future__ import annotations

from rotortcpbridge.axis_link import (
    axis_endpoint_from_cfg,
    dual_axis_tcp_active,
    ensure_setup_axis_endpoints,
    link_cfg_for_axis,
    store_axis_endpoint,
    sync_hardware_link_from_az,
)


def test_store_and_read_separate_endpoints() -> None:
    cfg: dict = {
        "hardware_link": {"mode": "tcp", "tcp_ip": "10.0.0.1", "tcp_port": 8886},
        "rotor_bus": {"enable_el": True},
        "ui": {},
    }
    store_axis_endpoint(cfg, "az", "10.0.0.1", 8886)
    store_axis_endpoint(cfg, "el", "10.0.0.2", 8886)
    assert axis_endpoint_from_cfg(cfg, "az") == ("10.0.0.1", 8886)
    assert axis_endpoint_from_cfg(cfg, "el") == ("10.0.0.2", 8886)
    assert dual_axis_tcp_active(cfg) is True


def test_dual_inactive_when_same_ip() -> None:
    cfg: dict = {
        "hardware_link": {"mode": "tcp", "tcp_ip": "10.0.0.1", "tcp_port": 8886},
        "rotor_bus": {"enable_el": True},
        "ui": {},
    }
    store_axis_endpoint(cfg, "az", "10.0.0.1", 8886)
    store_axis_endpoint(cfg, "el", "10.0.0.1", 8886)
    assert dual_axis_tcp_active(cfg) is False


def test_dual_inactive_when_el_disabled() -> None:
    cfg: dict = {
        "hardware_link": {"mode": "tcp", "tcp_ip": "10.0.0.1", "tcp_port": 8886},
        "rotor_bus": {"enable_el": False},
        "ui": {},
    }
    store_axis_endpoint(cfg, "az", "10.0.0.1", 8886)
    store_axis_endpoint(cfg, "el", "10.0.0.2", 8886)
    assert dual_axis_tcp_active(cfg) is False


def test_ensure_migrates_from_hardware_link() -> None:
    cfg: dict = {
        "hardware_link": {"mode": "tcp", "tcp_ip": "192.168.1.50", "tcp_port": 8887},
        "ui": {},
    }
    ensure_setup_axis_endpoints(cfg)
    assert axis_endpoint_from_cfg(cfg, "az") == ("192.168.1.50", 8887)
    assert axis_endpoint_from_cfg(cfg, "el") == ("192.168.1.50", 8887)


def test_link_cfg_for_axis_uses_endpoint() -> None:
    cfg: dict = {
        "hardware_link": {
            "mode": "tcp",
            "tcp_ip": "10.0.0.1",
            "tcp_port": 8886,
            "baudrate": 115200,
        },
        "ui": {},
    }
    store_axis_endpoint(cfg, "el", "10.0.0.9", 9000)
    el = link_cfg_for_axis(cfg, "el")
    assert el["tcp_ip"] == "10.0.0.9"
    assert int(el["tcp_port"]) == 9000
    assert el["mode"] == "tcp"


def test_sync_hardware_link_from_az() -> None:
    cfg: dict = {
        "hardware_link": {"mode": "com", "tcp_ip": "", "tcp_port": 8886, "com_port": "COM3"},
        "ui": {},
    }
    store_axis_endpoint(cfg, "az", "10.1.2.3", 8886)
    sync_hardware_link_from_az(cfg)
    assert cfg["hardware_link"]["mode"] == "tcp"
    assert cfg["hardware_link"]["tcp_ip"] == "10.1.2.3"


def test_ensure_axis_tcp_link_dual_does_not_stomp_az_client() -> None:
    """EL-Link bei Dual-IP darf den AZ-HardwareClient nicht auf die EL-IP umbiegen."""
    from rotortcpbridge.axis_link import ensure_axis_tcp_link

    class _Hw:
        def __init__(self, name: str) -> None:
            self.name = name
            self.cfg: dict = {}
            self._connected = True

        def is_connected(self) -> bool:
            return self._connected

        def update_cfg(self, cfg: dict) -> None:
            self.cfg = dict(cfg or {})

        def _in_safe_reconnect_mode(self) -> bool:
            return False

    class _Ctrl:
        def __init__(self) -> None:
            self.hw = _Hw("az")
            self.el_hw = None

    cfg: dict = {
        "hardware_link": {"mode": "tcp", "tcp_ip": "10.0.0.1", "tcp_port": 8886},
        "rotor_bus": {"enable_el": True},
        "ui": {},
    }
    store_axis_endpoint(cfg, "az", "10.0.0.1", 8886)
    store_axis_endpoint(cfg, "el", "10.0.0.2", 8886)
    ctrl = _Ctrl()

    def save_cb(c: dict) -> None:
        # Simuliert main._sync_el_hw
        if ctrl.el_hw is None:
            ctrl.el_hw = _Hw("el")

    ok, detail = ensure_axis_tcp_link(ctrl, cfg, "el", save_cfg_cb=save_cb, timeout_s=0.5)
    assert ok is True, detail
    assert ctrl.el_hw is not None
    assert ctrl.el_hw.cfg.get("tcp_ip") == "10.0.0.2"
    # AZ-Client darf nicht auf die EL-IP umgebogen worden sein
    assert ctrl.hw.cfg.get("tcp_ip") != "10.0.0.2"
