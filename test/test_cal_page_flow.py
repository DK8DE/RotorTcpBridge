"""Offscreen-Test: gefÃ¼hrtes Einmessen ohne Endschalter (CalPage) gegen simulierten Rotor."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from rotortcpbridge.i18n import load_lang
from rotortcpbridge.ui import rotor_setup_wizard as rsw

load_lang("de")


class FakeRotor:
    """Minimaler Rotor mit Setup-Modus, SETPOSHERE/BADCPR, Counts und Reboot-Verhalten."""

    def __init__(self, cpr: int = 0) -> None:
        self.cpr = cpr
        self.setup = 0
        self.ref = 0
        self.phys = 0  # rohe Encoder-Position
        self.offset = 0
        self.maxdg = 0.0
        self.begindg = 0.0
        self.dgcal = 0.0
        self.log: list[tuple[str, str]] = []

    @property
    def counts(self) -> int:
        return self.phys + self.offset

    def handle(self, cmd: str, par: str) -> tuple[str, str]:
        self.log.append((cmd, par))
        if cmd == "GETENCCAX":
            return "ACK_GETENCCAX", str(self.cpr)
        if cmd == "SETENCCAX":
            new = int(par)
            if new != self.cpr:  # Reboot: Setup/Referenz weg
                self.cpr = new
                self.setup = 0
                self.ref = 0
            return "ACK_SETENCCAX", par
        if cmd == "SETSETUP":
            self.setup = int(par)
            return "ACK_SETSETUP", par
        if cmd == "GETSETUP":
            return "ACK_GETSETUP", str(self.setup)
        if cmd == "SETPOSHERE":
            if self.cpr <= 0:
                return "NAK_SETPOSHERE", "BADCPR"
            ang = float(par.replace(",", "."))
            self.offset = int(round(ang * self.cpr / (self.maxdg or 360.0))) - self.phys
            self.ref = 1
            return "ACK_SETPOSHERE", par
        if cmd == "GETBEGINDG":
            return "ACK_GETBEGINDG", f"{self.begindg:.2f}".replace(".", ",")
        if cmd == "GETMAXDG":
            return "ACK_GETMAXDG", f"{self.maxdg:.2f}".replace(".", ",")
        if cmd == "GETDGCAL":
            return "ACK_GETDGCAL", f"{self.dgcal:.1f}".replace(".", ",")
        if cmd == "SETDGCAL":
            self.dgcal = float(par.replace(",", "."))
            return "ACK_SETDGCAL", par
        if cmd == "SETENCCOUNT":
            if self.cpr <= 0:
                return "NAK_SETENCCOUNT", "BADCPR"
            self.offset = int(par) - self.phys
            return "ACK_SETENCCOUNT", par
        if cmd == "GETENCCOUNT":
            return "ACK_GETENCCOUNT", str(self.counts)
        if cmd == "GETREF":
            return "ACK_GETREF", str(self.ref)
        if cmd == "SETMAXDG":
            self.maxdg = float(par.replace(",", "."))
            return "ACK_SETMAXDG", par
        if cmd == "SETBEGINDG":
            self.begindg = float(par.replace(",", "."))
            return "ACK_SETBEGINDG", par
        if cmd == "GETPOSDG":
            deg = self.counts * (self.maxdg or 360.0) / self.cpr if self.cpr else 0.0
            return "ACK_GETPOSDG", f"{deg:.2f}".replace(".", ",")
        if cmd == "JOG":
            return "ACK_JOG", par
        return "NAK_" + cmd, "UNKNOWN"


class FakeCtrl:
    def __init__(self, rotor: FakeRotor) -> None:
        self.rotor = rotor

    def send_ui_command(self, dst, cmd, params, expect_prefix=None, timeout_s=0.8, priority=0, on_done=None, **kw):
        c, p = self.rotor.handle(str(cmd), str(params))
        if on_done is not None:
            on_done(SimpleNamespace(cmd=c, params=p, src=dst, dst=0), None)


def _pump(app: QApplication, ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_page(rotor: FakeRotor):
    wiz = SimpleNamespace(
        ctrl=FakeCtrl(rotor),
        enc_type=1,
        endstc=0,
        axis_role="el",
        axis_dst=21,
        _setup_dst=None,
    )
    wiz.slave_dst = lambda: 21

    def force_off():
        if wiz._setup_dst is not None:
            rotor.handle("SETSETUP", "0")
        wiz._setup_dst = None

    wiz.force_setup_off = force_off
    page = rsw.CalPage(wiz)  # type: ignore[arg-type]
    page._dst = lambda: 21  # type: ignore[method-assign]
    return wiz, page


def test_flow_without_endswitch(app, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    rotor = FakeRotor(cpr=0)
    rotor.phys = -4000  # mechanische Null liegt "unter" dem Startpunkt
    rotor.dgcal = 3.5  # alter Korrekturwinkel soll am Ende weg sein
    wiz, page = _make_page(rotor)
    page.initializePage()
    _pump(app, 100)
    assert not page.g2.isEnabled()

    # Schritt 1: CPR fehlt -> Startwert, Reboot-Wartezeit, dann Setup an + vorläufiges Max
    page._start_setup()
    _pump(app, 4500)
    assert rotor.cpr == 28600
    assert rotor.setup == 1 and page._setup_on
    assert rotor.maxdg >= 360.0
    assert wiz._setup_dst == 21
    assert page.g2.isEnabled() and page.g_jog.isEnabled()

    # Schritt 2: Null setzen (SETPOSHERE:0,00)
    page._set_zero()
    _pump(app, 300)
    assert ("SETPOSHERE", "0") in rotor.log or ("SETPOSHERE", "0,00") in rotor.log
    assert rotor.counts == 0 and page._zero_done
    assert page.g3.isEnabled()

    # Schritt 3: Max bei 180 deg -> 15000 Counts => Counts fuer 0..180 = 15000 (nicht hochgerechnet)
    rotor.phys += 15000
    page.sp_max.setValue(180.0)
    page._measure()
    _pump(app, 300)
    assert page._new_cpr == 15000
    assert page.g4.isEnabled()

    # Schritt 4: CPR geändert -> Reboot -> Setup wird wieder eingeschaltet
    page._apply_all()
    _pump(app, 4500)
    assert rotor.cpr == 15000
    assert abs(rotor.maxdg - 180.0) < 1e-6
    assert rotor.setup == 1 and page._setup_on and page._cpr_applied
    assert page.g5.isEnabled()

    # Schritt 5: Null nochmals anfahren, setzen, kontrollieren, Setup beenden
    rotor.phys = 40  # etwas daneben
    page._finish()
    _pump(app, 600)
    assert rotor.setup == 0 and rotor.ref == 1
    assert not page._setup_on
    assert "0,00" in page.lbl_s5.text()
    assert "referenziert" in page.lbl_s5.text()
    assert rotor.dgcal == 0.0
    assert ("SETDGCAL", "0,0") in rotor.log
    assert "Counts" in page.lbl_s5.text() and "15000" in page.lbl_s5.text()
    assert ("SETBEGINDG", "0") in rotor.log or ("SETBEGINDG", "0,00") in rotor.log


def test_zero_badcpr_message(app):
    rotor = FakeRotor(cpr=0)
    wiz, page = _make_page(rotor)
    page.initializePage()
    page._setup_on = True
    page._refresh_enabled()
    page._set_zero()
    _pump(app, 300)
    assert not page._zero_done
    assert "BADCPR" in page.lbl_s2.text()


def test_measure_rejects_negative_counts(app):
    rotor = FakeRotor(cpr=28600)
    wiz, page = _make_page(rotor)
    page.initializePage()
    page._setup_on = True
    page._zero_done = True
    page._zero_count = 0
    page._refresh_enabled()
    rotor.phys = -500
    page._measure()
    _pump(app, 300)
    assert page._new_cpr is None


def test_validate_page_ends_setup(app, monkeypatch):
    answers = []

    def fake_q(*a, **k):
        answers.append(1)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_q))
    rotor = FakeRotor(cpr=28600)
    rotor.setup = 1
    wiz, page = _make_page(rotor)
    page.initializePage()
    wiz._setup_dst = 21
    page._setup_on = True
    assert page.validatePage()
    assert answers  # Rückfrage bei laufendem Setup
    assert rotor.setup == 0


def test_validate_page_can_stay(app, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    rotor = FakeRotor(cpr=28600)
    rotor.setup = 1
    wiz, page = _make_page(rotor)
    page.initializePage()
    wiz._setup_dst = 21
    page._setup_on = True
    assert not page.validatePage()
    assert rotor.setup == 1


def test_locale_keys_present():
    root = Path(rsw.__file__).resolve().parents[1] / "locales"
    src = Path(rsw.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(setup\.cal_[a-z0-9_]+)"', src))
    for lang in ("de", "en"):
        data = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, (lang, missing)
        for k in ("catalog.help.JOG", "catalog.help.SETSETUP", "catalog.help.GETSETUP",
                  "catalog.help.SETSRVJOG", "catalog.help.GETENCCOUNT"):
            assert k in data



def _alerts_page(wind_choice, ctrl=None, enc_type=1):
    wiz = SimpleNamespace(
        cfg={"ui": {"weather_alert_thresholds": {"wind_alert_enabled": True}}},
        enc_type=enc_type,
        ctrl=ctrl,
        wind_choice=wind_choice,
        save_cfg_cb=None,
    )
    page = rsw.AlertsPage(wiz)  # type: ignore[arg-type]
    page.initializePage()
    return wiz, page


def test_alerts_hide_wind_when_not_activated(app):
    wiz, page = _alerts_page(False)
    assert page._tab._gb_wind.isHidden()  # Windmesser nicht aktiv → keine Wind-Schwellen
    assert page._tab._gb_wind_enable.isHidden()  # Aktivierung gehört in den Wetter-Schritt
    assert page.validatePage()
    assert wiz.cfg["ui"]["weather_alert_thresholds"]["wind_alert_enabled"] is False


def test_alerts_show_wind_when_activated(app):
    wiz, page = _alerts_page(True)
    assert not page._tab._gb_wind.isHidden()
    assert page._tab._gb_wind_enable.isHidden()


def test_alerts_wind_follows_rotor_state_when_unknown_choice(app):
    ctrl = SimpleNamespace(wind_enabled_known=True, wind_enabled=True)
    wiz, page = _alerts_page(None, ctrl)
    assert not page._tab._gb_wind.isHidden()
    ctrl2 = SimpleNamespace(wind_enabled_known=False, wind_enabled=False)
    wiz2, page2 = _alerts_page(None, ctrl2)
    assert page2._tab._gb_wind.isHidden()
