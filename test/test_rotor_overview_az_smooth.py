from __future__ import annotations

from rotortcpbridge.rotor_overview_monitor import _SiteWorker
from rotortcpbridge.rs485_protocol import build


def _worker() -> _SiteWorker:
    import threading

    return _SiteWorker(
        {"id": "t", "slave_az": 20, "smooth_alpha": 0.5},
        on_update=lambda *_a, **_k: None,
        stop_event=threading.Event(),
    )


def test_getposdg_decimal_degrees_not_d10() -> None:
    w = _worker()
    line = build(20, 0, "ACK_GETPOSDG", "123,4")
    w._handle_telegram(line)
    assert w.state.az_deg == 123.4


def test_smooth_uses_shortest_path_around_wrap() -> None:
    w = _worker()
    w._apply_az(10.0)
    w._apply_az(350.0)
    # Kürzester Weg: 10 → 0 → 350 (nicht linear über 180°).
    assert w.state.az_smooth_deg is not None
    assert w.state.az_smooth_deg < 10.0 or w.state.az_smooth_deg > 340.0


def test_setposdg_does_not_move_overlay() -> None:
    w = _worker()
    w._apply_az(40.0)
    before = w.state.az_smooth_deg
    # Sollbefehl darf Ist-Overlay nicht vorziehen
    w._handle_telegram(build(0, 20, "SETPOSDG", "180,0"))
    w._handle_telegram(build(20, 0, "ACK_SETPOSDG", "180,0"))
    assert w.state.az_deg == 40.0
    assert w.state.az_smooth_deg == before
