"""Antennenauswahl: Speichern/Lesen am AZ-Rotor, Mitschnitt, ohne AZ deaktiviert."""

from __future__ import annotations

from rotortcpbridge.rotor_controller import RotorController
from rotortcpbridge.rs485_protocol import Telegram, build, parse


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.requests: list = []

    def send_line_fire_and_forget(self, line: str) -> None:
        self.lines.append(str(line))

    def send_request(self, req) -> None:
        self.requests.append(req)


def test_set_aselect_goes_to_az_slave() -> None:
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.broadcast_set_aselect(2)
    assert len(hw.lines) == 1
    tel = parse(hw.lines[0])
    assert tel is not None
    assert tel.dst == 20
    assert tel.cmd == "SETASELECT"
    assert str(tel.params).startswith("2")


def test_set_aselect_skipped_without_az() -> None:
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = False
    c.broadcast_set_aselect(1)
    assert hw.lines == []


def test_get_aselect_goes_to_az_slave() -> None:
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.request_antenna_selection()
    assert len(hw.requests) == 1
    line = str(hw.requests[0].line)
    tel = parse(line)
    assert tel is not None
    assert tel.dst == 20
    assert tel.cmd == "GETASELECT"


def test_get_aselect_skipped_without_az() -> None:
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = False
    c.request_antenna_selection()
    assert hw.requests == []


def test_sniff_setaselect_to_az_slave() -> None:
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=7, dst=20, cmd="SETASELECT", params="3", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert seen == [3]


def test_sniff_setaselect_broadcast_still_works() -> None:
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=2, dst=255, cmd="SETASELECT", params="1", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert seen == [1]


def test_sniff_setaselect_any_master_to_our_software_id() -> None:
    """Fremder Master (z. B. 7) an unsere Master-ID — muss trotzdem greifen."""
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=7, dst=1, cmd="SETASELECT", params="2", cs=0.0, ok=True)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert seen == [2]


def test_sniff_setaselect_any_master_to_foreign_master_dst() -> None:
    """Mitschnitt: Master 7 → Master 5 (nicht wir) — trotzdem UI aktualisieren."""
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=7, dst=5, cmd="SETASELECT", params="3,00", cs=0.0, ok=False)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert seen == [3]


def test_sniff_ack_setaselect_to_foreign_master() -> None:
    """ACK vom AZ an fremden Master 7 — Auswahl übernehmen."""
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=20, dst=7, cmd="ACK_SETASELECT", params="2", cs=0.0, ok=True)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert seen == [2]


def test_sniff_ignored_without_az() -> None:
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = False
    c.on_setaselect_from_bus = lambda n: seen.append(int(n))
    tel = Telegram(src=7, dst=20, cmd="SETASELECT", params="2", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert seen == []


def test_ack_getaselect_from_az() -> None:
    seen: list[int] = []
    hw = _Hw()
    c = RotorController(hw, master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.enable_az = True
    c.on_aselect_query_result = lambda n: seen.append(int(n))
    tel = Telegram(src=20, dst=1, cmd="ACK_GETASELECT", params="2", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert seen == [2]


def test_build_line_matches_protocol() -> None:
    line = build(1, 20, "SETASELECT", "2")
    tel = parse(line)
    assert tel is not None and tel.cmd == "SETASELECT" and tel.dst == 20
