"""Web-Kompass: EL-Sollzeiger folgt SETPOSCC (compass_target_d10) vom Controller."""

from types import SimpleNamespace

from rotortcpbridge.ui import web_compass_state as wcs


def _mw(el_cc, el_target=0, moving=False):
    el = SimpleNamespace(compass_target_d10=el_cc, target_d10=el_target, moving=moving)
    ctrl = SimpleNamespace(el=el, az=SimpleNamespace(), cc_poll_hold_active=lambda now: False)
    return SimpleNamespace(ctrl=ctrl)


def test_el_soll_prefers_setposcc():
    mw = _mw(el_cc=456, el_target=100)
    assert wcs.web_effective_el_target_d10(mw) == 456


def test_el_soll_latch_survives_short_cc_gap_while_moving():
    mw = _mw(el_cc=300, el_target=0, moving=True)
    assert wcs.web_effective_el_target_d10(mw) == 300
    mw.ctrl.el.compass_target_d10 = None  # CC kurz weg, Rotor fährt noch
    assert wcs.web_effective_el_target_d10(mw) == 300


def test_el_soll_falls_back_to_motor_target():
    mw = _mw(el_cc=None, el_target=250)
    assert wcs.web_effective_el_target_d10(mw) == 250


def test_az_and_el_latches_are_independent():
    mw = _mw(el_cc=300)
    wcs.web_effective_el_target_d10(mw)
    assert getattr(mw, "_web_cc_latch_el_d10") == 300
    assert getattr(mw, "_web_cc_latch_az_d10", None) is None
