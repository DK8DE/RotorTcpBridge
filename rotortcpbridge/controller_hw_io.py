"""Controller-Link + GET/SET für Settings und Inbetriebnahme (RS485 / PC-USB / WEB)."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from .rotor_controller import SYNC_UI_NAK_PREFIX


def parse_hw_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(float(str(s).strip().split(";")[0].replace(",", ".")))
    except Exception:
        return None


def sync_got_ack(r: Any) -> bool:
    if r is None:
        return False
    return not str(r).startswith(SYNC_UI_NAK_PREFIX)


def sync_nak_notimpl(r: Any) -> bool:
    if r is None or not str(r).startswith(SYNC_UI_NAK_PREFIX):
        return False
    return "NOTIMPL" in str(r).upper()


def _process_events() -> None:
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def ensure_controller_link(
    ctrl: Any,
    *,
    mode: int,
    cfg: dict,
    proxy: Any = None,
) -> Tuple[bool, str]:
    """Link für SETCON*/GETCON* herstellen.

    mode: 0=RS485 (Rotor-Bus), 1=PC-USB (Remote), 2=WEB (Browser-Relay).
    Returns: (ok, error_code) — error_code leer bei Erfolg.
    """
    if ctrl is None:
        return False, "no_ctrl"
    _chw = cfg.get("controller_hw")
    chw: Dict[str, Any] = _chw if isinstance(_chw, dict) else {}
    try:
        cid = int(chw.get("cont_id", 2) or 2)
    except Exception:
        cid = 2
    try:
        if hasattr(ctrl, "set_controller_cont_id"):
            ctrl.set_controller_cont_id(cid)
    except Exception:
        pass

    mode = int(mode)
    if mode == 2:
        # WEB: Browser muss Relay schon gesetzt haben
        hw = getattr(ctrl, "ctrl_hw", None)
        try:
            ok = bool(
                getattr(ctrl, "usb_remote", False)
                and hw is not None
                and hw.is_connected()
            )
        except Exception:
            ok = False
        return (ok, "" if ok else "web_offline")

    if mode == 1:
        if proxy is None:
            return False, "usb_offline"
        try:
            proxy.update_cfg(cfg)
        except Exception:
            pass
        try:
            already = bool(getattr(proxy, "is_active", lambda: False)())
            hw = getattr(proxy, "ctrl_hw", None) or getattr(ctrl, "ctrl_hw", None)
            if already and hw is not None and bool(hw.is_connected()):
                try:
                    ctrl.set_controller_link(hw, True)
                except Exception:
                    pass
                return True, ""
        except Exception:
            pass
        try:
            if hasattr(proxy, "apply_mode"):
                _process_events()
                applied = bool(proxy.apply_mode(True, timeout_s=8.0))
                if not applied:
                    # COM offen, aber Probe nicht bestätigt → der eigentliche GET-Test entscheidet.
                    hw0 = getattr(proxy, "ctrl_hw", None) or getattr(ctrl, "ctrl_hw", None)
                    try:
                        com_ok = bool(hw0 is not None and hw0.is_connected())
                    except Exception:
                        com_ok = False
                    if not com_ok:
                        return False, "usb_offline"
            else:
                return False, "usb_offline"
        except Exception:
            return False, "usb_offline"
        hw = getattr(proxy, "ctrl_hw", None) or getattr(ctrl, "ctrl_hw", None)
        try:
            if hw is not None and hasattr(ctrl, "set_controller_link"):
                ctrl.set_controller_link(hw, True)
        except Exception:
            pass
        # apply_mode enthält bereits wait_ready/GETCONTID — Erfolg zählt
        return True, ""

    # RS485 am Rotor-Bus
    try:
        if hasattr(ctrl, "set_controller_link"):
            ctrl.set_controller_link(getattr(ctrl, "ctrl_hw", None), False)
    except Exception:
        pass
    hw = getattr(ctrl, "hw", None)
    try:
        ok = bool(hw is not None and hw.is_connected())
    except Exception:
        ok = False
    return (ok, "" if ok else "bus_offline")


def read_controller_settings(
    ctrl: Any,
    dst: int,
    *,
    timeout_s: float = 1.2,
) -> Tuple[bool, Dict[str, Any]]:
    """GETCON*-Werte vom Controller lesen. (ok, values).

    ok=True wenn GETCONTID antwortet; einzelne Felder können fehlen.
    """
    out: Dict[str, Any] = {}
    if ctrl is None or not hasattr(ctrl, "sync_ui_command_response"):
        return False, out
    dst = int(dst)
    _to = float(timeout_s)
    _process_events()

    # Controller kann nach SETCONREMOTE kurz im "Initialisiere"-Zustand sein → mehrere Versuche.
    p = None
    for attempt in range(4):
        p = ctrl.sync_ui_command_response(dst, "GETCONTID", "0", "ACK_GETCONTID", timeout_s=_to)
        if sync_got_ack(p):
            break
        _process_events()
        time.sleep(0.25)
    if not sync_got_ack(p):
        return False, out
    v = parse_hw_int(p)
    if v is not None:
        out["cont_id"] = max(0, min(245, int(v)))

    def _get(cmd: str, key: str, lo: int, hi: int) -> None:
        _process_events()
        rp = ctrl.sync_ui_command_response(dst, cmd, "0", "ACK_" + cmd, timeout_s=_to)
        if sync_nak_notimpl(rp):
            return
        if not sync_got_ack(rp):
            return
        w = parse_hw_int(rp)
        if w is None:
            return
        out[key] = max(lo, min(hi, int(w)))

    _get("GETCONSPWM", "slow_pwm", 0, 100)
    _get("GETCONFPWM", "fast_pwm", 0, 100)
    _get("GETCONFRQ", "speaker_freq_hz", 200, 4000)
    _get("GETLSL", "speaker_volume", 0, 50)
    _get("GETCONLEDP", "display_brightness_pct", 0, 100)
    _get("GETCONANO", "wind_anemometer", 0, 1)
    _get("GETCONDELTA", "encoder_delta", 1, 10)
    _get("GETCONCHA", "antenna_realign_on_switch", 0, 1)
    _get("GETCONTAZID", "az_rotor_id", 0, 254)
    _get("GETCONTELID", "el_rotor_id", 0, 254)
    # GETCONREMOTE nur als Erreichbarkeits-Check, Wert nicht übernehmen
    _process_events()
    ctrl.sync_ui_command_response(dst, "GETCONREMOTE", "0", "ACK_GETCONREMOTE", timeout_s=_to)
    return True, out


def apply_settings_to_controller_tab(tab: Any, values: Dict[str, Any]) -> None:
    """Gelesene Werte in ControllerHwTab-Felder schreiben."""
    if not values:
        return
    try:
        if "cont_id" in values and hasattr(tab, "sp_controller_id"):
            tab.sp_controller_id.setValue(int(values["cont_id"]))
        if "az_rotor_id" in values and hasattr(tab, "sp_cont_az_rotor_id"):
            tab.sp_cont_az_rotor_id.setValue(int(values["az_rotor_id"]))
        if "el_rotor_id" in values and hasattr(tab, "sp_cont_el_rotor_id"):
            tab.sp_cont_el_rotor_id.setValue(int(values["el_rotor_id"]))
        if "slow_pwm" in values and hasattr(tab, "sp_cont_pwm_slow"):
            tab.sp_cont_pwm_slow.setValue(int(values["slow_pwm"]))
        if "fast_pwm" in values and hasattr(tab, "sp_cont_pwm_fast"):
            tab.sp_cont_pwm_fast.setValue(int(values["fast_pwm"]))
        if "speaker_freq_hz" in values and hasattr(tab, "sp_cont_beep_freq"):
            tab.sp_cont_beep_freq.setValue(int(values["speaker_freq_hz"]))
        if "speaker_volume" in values and hasattr(tab, "sp_cont_beep_vol"):
            tab.sp_cont_beep_vol.setValue(int(values["speaker_volume"]))
        if "display_brightness_pct" in values and hasattr(tab, "sl_cont_display_brightness"):
            tab.sl_cont_display_brightness.setValue(int(values["display_brightness_pct"]))
            if hasattr(tab, "_update_brightness_label"):
                tab._update_brightness_label()
        if "wind_anemometer" in values and hasattr(tab, "chk_cont_wind_anemo"):
            tab.chk_cont_wind_anemo.setChecked(bool(int(values["wind_anemometer"])))
        if "encoder_delta" in values and hasattr(tab, "cb_cont_encoder_delta"):
            ed = int(values["encoder_delta"])
            tab.cb_cont_encoder_delta.setCurrentIndex(0 if ed == 1 else 1)
    except Exception:
        pass


def profile_rotor_ids(cfg: dict) -> Tuple[int, int, int]:
    """(cont_id, az_rotor_id, el_rotor_id) aus Profil / rotor_bus."""
    from .rotor_backup import controller_rotor_ids_from_cfg

    ids = controller_rotor_ids_from_cfg(cfg)
    return int(ids["cont_id"]), int(ids["az_rotor_id"]), int(ids["el_rotor_id"])


def write_controller_rotor_ids(
    ctrl: Any,
    dst: int,
    az_id: int,
    el_id: int,
    *,
    timeout_s: float = 1.5,
) -> bool:
    """SETCONTAZID / SETCONTELID schreiben (nur bei Abweichung)."""
    if ctrl is None or not hasattr(ctrl, "sync_ui_command_response"):
        return False
    from .rotor_backup import read_controller_int

    ok_all = True
    _to = float(timeout_s)
    for want, cmd in ((int(az_id), "SETCONTAZID"), (int(el_id), "SETCONTELID")):
        _process_events()
        cur = read_controller_int(ctrl, int(dst), cmd)
        if cur is not None and int(cur) == int(want):
            continue
        try:
            r = ctrl.sync_ui_command_response(
                int(dst), cmd, str(int(want)), "ACK_" + cmd, timeout_s=_to
            )
        except Exception:
            ok_all = False
            continue
        if not sync_got_ack(r):
            ok_all = False
    return ok_all
