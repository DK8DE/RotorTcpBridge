"""
Rotor-Konfiguration als XML sichern und wiederherstellen.

Nur Befehle mit GET und SET werden berücksichtigt.
Steuerbefehle (STOP, NSTOP, SETREF, SETPOSDG, etc.) werden ausgeschlossen.

Lokale App-Einstellungen (config.json-Bereiche) werden mitgespeichert.
Hardware-Parameter gehen an Rotor-Slaves (AZ/EL) bzw. an den Display-Controller
(``SETCON*`` / ``SETLSL`` → ``controller_hw.cont_id``). Antennen-Namen:
``SETANTNAME*`` am Rotor-Slave.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional

from .command_catalog import command_specs, CommandSpec

# Alle Top-Level-Bereiche der lokalen App-Config (siehe app_config.DEFAULT_CONFIG).
_GUI_CONFIG_KEYS = (
    "pst_server",
    "rotctld_server",
    "gs232_server",
    "easycomm_server",
    "dcu1_server",
    "n1mm_rotor",
    "map_webserver",
    "pst_serial",
    "rotor_bus",
    "hardware_link",
    "controller_link",
    "network_modules",
    "network_scan",
    "ui",
    "polling_ms",
    "spid",
    "pwm",
    "behavior",
    "controller_hw",
    "rig_bridge",
)

# SET-Befehle ohne GET oder Steuerbefehle – nicht backupbar
_EXCLUDED_SET = frozenset(
    {
        "STOP",
        "NSTOP",
        "SETREF",
        "SETPOSDG",
        "SETPOSCC",
        "SETCAL",
        "ABORTCAL",
        "DELCAL",
        "RESET",
        "CLRSTAT",
        "DELWARN",
        "JOG",
    }
)

# Spezielle Zuordnung SET -> GET (abweichende Namensgebung)
_SET_TO_GET_SPECIAL = {
    "SETSWAPTEMP": "GETSWAPTMP",
    "SETHOMERETURN": "GETHOMRETURN",
    "SETISFILTERLEN": "GETFILTERLEN",
    "SETISGRACEMS": "GETGRACEMS",
    "SETTEMPA": "GETTEMPAW",
    "SETTEMPM": "GETTEMPMW",
    "SETANTOFF1": "GETANTOFF1",
    "SETANTOFF2": "GETANTOFF2",
    "SETANTOFF3": "GETANTOFF3",
    "SETANGLE1": "GETANGLE1",
    "SETANGLE2": "GETANGLE2",
    "SETANGLE3": "GETANGLE3",
}

# Display-/Hardware-Controller (nicht Rotor-Slave)
_CONTROLLER_SET_CMDS = frozenset(
    {
        "SETLSL",  # Piep-Lautstärke am Controller
    }
)

# Absolut-Encoder Typ 3: kein Windsensor am Rotor / kein Anemo am Controller
_TYPE3_UNSUPPORTED_SET = frozenset(
    {
        "SETWINDENABLE",
        "SETWINDDIROF",
        "SETWINDCOH",
        "SETWINDPEAK",
        "SETANEMOOF",
        "SETCONANO",
    }
)


def is_controller_set_cmd(set_cmd: str) -> bool:
    """True für Befehle, die an ``controller_hw.cont_id`` gehen (nicht AZ/EL-Slave)."""
    name = str(set_cmd or "").strip().upper()
    return name.startswith("SETCON") or name in _CONTROLLER_SET_CMDS


def is_type3_unsupported_set_cmd(set_cmd: str) -> bool:
    """True für SET-Befehle, die bei Encoder-Typ 3 nicht existieren / NAK liefern."""
    return str(set_cmd or "").strip().upper() in _TYPE3_UNSUPPORTED_SET


def parse_encoder_type_value(params: Any) -> Optional[int]:
    """Parst GETENCTYPE/SETENCTYPE-Parameter zu 1…3."""
    try:
        raw = str(params or "").strip().split(";")[0].replace(",", ".")
        v = int(float(raw))
    except Exception:
        return None
    return v if v in (1, 2, 3) else None


def encoder_type_from_ctrl(ctrl: Any) -> Optional[int]:
    """Bekannter Encoder-Typ vom laufenden Controller, sonst None."""
    if ctrl is None:
        return None
    try:
        if not bool(getattr(ctrl, "encoder_type_known", False)):
            return None
        return parse_encoder_type_value(getattr(ctrl, "encoder_type", None))
    except Exception:
        return None


def encoder_type_from_backup_entries(entries: list[dict]) -> Optional[int]:
    """Encoder-Typ aus Backup-``SETENCTYPE``-Einträgen (letzter Treffer gewinnt)."""
    found: Optional[int] = None
    for e in entries or []:
        try:
            cmd = str(e.get("cmd", "") or "").strip().upper()
        except Exception:
            continue
        if cmd != "SETENCTYPE":
            continue
        v = parse_encoder_type_value(e.get("params"))
        if v is not None:
            found = v
    return found


def should_skip_set_for_encoder(set_cmd: str, encoder_type: Optional[int]) -> bool:
    """Wind-/Anemo-SETs bei Absolut-Encoder Typ 3 überspringen."""
    try:
        et = int(encoder_type) if encoder_type is not None else None
    except Exception:
        et = None
    return et == 3 and is_type3_unsupported_set_cmd(set_cmd)


def backupable_pairs() -> list[tuple[str, str]]:
    """
    Liste aller (set_cmd, get_cmd) Paare, die backupbar sind.
    Nur Einträge mit sowohl SET als auch GET werden zurückgegeben.
    """
    specs = {s.name: s for s in command_specs()}
    pairs: list[tuple[str, str]] = []

    for name, spec in specs.items():
        if not name.startswith("SET") or name in _EXCLUDED_SET:
            continue
        get_cmd = _SET_TO_GET_SPECIAL.get(name)
        if get_cmd is None:
            get_cmd = f"GET{name[3:]}"
        if get_cmd in specs:
            pairs.append((name, get_cmd))

    pairs.sort(key=lambda p: (p[0], p[1]))
    return pairs


def rotor_backupable_pairs() -> list[tuple[str, str]]:
    """SET/GET-Paare für Rotor-Slaves (ohne Controller-Befehle)."""
    return [(s, g) for s, g in backupable_pairs() if not is_controller_set_cmd(s)]


def controller_backupable_pairs() -> list[tuple[str, str]]:
    """SET/GET-Paare für den Display-Controller."""
    return [(s, g) for s, g in backupable_pairs() if is_controller_set_cmd(s)]


def controller_hw_enabled(cfg: Dict[str, Any]) -> bool:
    """True wenn in den Einstellungen „Hardware-Controller verwenden“ aktiv ist."""
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    return bool(chw.get("enabled", True))


def backup_hw_destinations(cfg: Dict[str, Any]) -> list[tuple[int, str]]:
    """Bus-Ziele für Hardware-Backup: ``(dst, 'rotor'|'controller')``."""
    out: list[tuple[int, str]] = []
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    seen: set[int] = set()

    def _add(dst: int, role: str) -> None:
        if dst in seen:
            return
        seen.add(dst)
        out.append((dst, role))

    try:
        if bool(rb.get("enable_az", True)):
            _add(int(rb.get("slave_az", 20)), "rotor")
    except Exception:
        pass
    try:
        if bool(rb.get("enable_el", False)):
            _add(int(rb.get("slave_el", 21)), "rotor")
    except Exception:
        pass
    if not any(role == "rotor" for _, role in out):
        try:
            _add(int(rb.get("slave_az", 0) or 0), "rotor")
        except Exception:
            _add(0, "rotor")

    if controller_hw_enabled(cfg):
        chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
        try:
            cid = int(chw.get("cont_id", 2) or 0)
        except Exception:
            cid = 0
        try:
            master = int(rb.get("master_id", 0) or 0)
        except Exception:
            master = 0
        if cid > 0 and cid != master:
            _add(cid, "controller")
    return out


def build_backup_work(
    cfg: Dict[str, Any],
    *,
    encoder_type: Optional[int] = None,
) -> list[tuple[int, str, str]]:
    """Arbeitsschritte: ``(dst, set_cmd, get_cmd)`` für Rotor + Controller.

    - Ohne Hardware-Controller: keine ``SETCON*`` / ``SETLSL`` (kein Zielgerät).
    - Bei ``encoder_type == 3``: keine Wind-/Anemo-Befehle.
    """
    work: list[tuple[int, str, str]] = []
    rotor_pairs = rotor_backupable_pairs()
    include_controller = controller_hw_enabled(cfg)
    ctrl_pairs = controller_backupable_pairs() if include_controller else []
    for dst, role in backup_hw_destinations(cfg):
        if role == "controller":
            if not include_controller:
                continue
            pairs = ctrl_pairs
        else:
            pairs = rotor_pairs
        for set_cmd, get_cmd in pairs:
            if should_skip_set_for_encoder(set_cmd, encoder_type):
                continue
            # Verteidigung: Controller-Befehle nie an Rotor-DST hängen
            if is_controller_set_cmd(set_cmd) and role != "controller":
                continue
            work.append((int(dst), set_cmd, get_cmd))
    return work


def filter_restore_entries_for_encoder(
    entries: list[dict],
    *,
    encoder_type: Optional[int] = None,
    include_controller: bool = True,
) -> list[dict]:
    """Filtert Restore-Einträge (Typ-3-Wind und optional ohne Hardware-Controller)."""
    out: list[dict] = []
    for e in entries or []:
        try:
            cmd = str(e.get("cmd", "") or "").strip().upper()
        except Exception:
            continue
        if should_skip_set_for_encoder(cmd, encoder_type):
            continue
        if not include_controller and is_controller_set_cmd(cmd):
            continue
        out.append(e)
    return out


def get_params_for_get(spec: Optional[CommandSpec]) -> str:
    """Parameter-String für einen GET-Befehl ermitteln."""
    if spec is None:
        return "0"
    if spec.kind == "none" and spec.params_literal is not None:
        return str(spec.params_literal)
    return "0"


def _clone_json(val: Any) -> Any:
    return json.loads(json.dumps(val))


def _extract_gui_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Extrahiert die lokalen App-Config-Bereiche fürs Backup."""
    out: Dict[str, Any] = {}
    for key in _GUI_CONFIG_KEYS:
        if key not in cfg:
            continue
        val = cfg[key]
        if isinstance(val, (dict, list)):
            out[key] = _clone_json(val)
        elif val is not None:
            out[key] = val
    return out


def _apply_gui_config(cfg: Dict[str, Any], gui: Dict[str, Any]) -> None:
    """Ersetzt cfg-Bereiche mit den geladenen GUI-Einstellungen (vollständige Wiederherstellung)."""
    for key, val in gui.items():
        if key not in _GUI_CONFIG_KEYS and key not in cfg:
            # Unbekannte Keys aus älteren/neueren Backups trotzdem übernehmen
            pass
        if isinstance(val, (dict, list)):
            cfg[key] = _clone_json(val)
        else:
            cfg[key] = val


def extract_gui_config_for_backup(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """GUI-relevante Bereiche für Backup extrahieren."""
    return _extract_gui_config(cfg)


def apply_gui_config_from_backup(cfg: Dict[str, Any], gui: Dict[str, Any]) -> None:
    """Geladene GUI-Einstellungen in cfg eintragen."""
    _apply_gui_config(cfg, gui)


def apply_live_ids_from_cfg(ctrl: Any, cfg: Dict[str, Any]) -> None:
    """``rotor_bus`` / ``controller_hw`` aus cfg auf den laufenden Controller anwenden."""
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    try:
        mid = int(rb.get("master_id", 0))
        saz = int(rb.get("slave_az", 20))
        sel = int(rb.get("slave_el", 21))
        en_az = bool(rb.get("enable_az", True))
        en_el = bool(rb.get("enable_el", False))
    except Exception:
        return
    try:
        if hasattr(ctrl, "update_ids"):
            ctrl.update_ids(mid, saz, sel, enable_az=en_az, enable_el=en_el)
    except Exception:
        pass
    try:
        ignore = rb.get("setposcc_ignore_src_master_ids", [])
        if hasattr(ctrl, "setposcc_ignore_src_master_ids"):
            norm = getattr(ctrl, "_normalize_master_id_list", None)
            if callable(norm):
                ctrl.setposcc_ignore_src_master_ids = norm(ignore)
            else:
                ctrl.setposcc_ignore_src_master_ids = list(ignore or [])
    except Exception:
        pass
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    try:
        cid = int(chw.get("cont_id", 2) or 0)
        if hasattr(ctrl, "set_controller_cont_id"):
            ctrl.set_controller_cont_id(cid)
        elif hasattr(ctrl, "setposcc_controller_src_id"):
            ctrl.setposcc_controller_src_id = max(0, min(254, cid))
    except Exception:
        pass


def _clamp_cont_id(v: Any, default: int = 0) -> int:
    try:
        return max(0, min(254, int(v)))
    except (TypeError, ValueError):
        return default


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def controller_hw_norm(chw: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Normierte Controller-HW-Werte für Diff/Push (inkl. AZ/EL-Rotor-IDs)."""
    ch = chw if isinstance(chw, dict) else {}
    try:
        ed = int(ch.get("encoder_delta", 10))
    except (TypeError, ValueError):
        ed = 10
    if ed not in (1, 10):
        ed = 10
    freq = _clamp_int(ch.get("speaker_freq_hz", 1100), 200, 4000, 1100)
    vol = _clamp_int(ch.get("speaker_volume", 50), 0, 50, 50)
    return {
        "cont_id": _clamp_cont_id(ch.get("cont_id", 2), 2),
        "slow_pwm": _clamp_int(ch.get("slow_pwm", 30), 0, 100, 30),
        "fast_pwm": _clamp_int(ch.get("fast_pwm", 80), 0, 100, 80),
        # Nicht über _clamp_cont_id (max 254): SETCONFRQ ist 200…4000 Hz.
        "speaker_freq_hz": freq,
        "speaker_volume": vol,
        "display_brightness_pct": _clamp_int(
            ch.get("display_brightness_pct", 100), 0, 100, 100
        ),
        "wind_anemometer": 1 if bool(ch.get("wind_anemometer", False)) else 0,
        "encoder_delta": ed,
        "antenna_realign_on_switch": 1 if bool(ch.get("antenna_realign_on_switch", False)) else 0,
        "az_rotor_id": _clamp_cont_id(ch.get("az_rotor_id", 20), 20),
        "el_rotor_id": _clamp_cont_id(ch.get("el_rotor_id", 0), 0),
        "usb_remote": 1 if bool(ch.get("usb_remote", False)) else 0,
    }


def controller_get_cmd_for_set(set_cmd: str) -> Optional[str]:
    """``SETCONxxx`` → ``GETCONxxx``; ``SETLSL`` → ``GETLSL``."""
    name = str(set_cmd or "").strip().upper()
    if name.startswith("SET"):
        return "GET" + name[3:]
    return None


def read_controller_int(ctrl: Any, dst: int, set_cmd: str) -> Optional[int]:
    """Aktuell im Controller gespeicherten Wert lesen; None bei Timeout/NAK/Parsefehler."""
    from .rotor_controller import SYNC_UI_NAK_PREFIX

    get_cmd = controller_get_cmd_for_set(set_cmd)
    if not get_cmd:
        return None
    try:
        r = ctrl.sync_ui_command_response(
            int(dst), get_cmd, "0", "ACK_" + get_cmd, timeout_s=1.2
        )
    except Exception:
        return None
    if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
        return None
    try:
        return int(float(str(r).strip().split(";")[0].replace(",", ".")))
    except (TypeError, ValueError):
        return None


def controller_rotor_ids_from_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Rotor-IDs, die der Display-Controller ansprechen muss (= Slave-IDs des aktiven Profils)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    az = _clamp_cont_id(rb.get("slave_az", chw.get("az_rotor_id", 20)), 20)
    el = _clamp_cont_id(rb.get("slave_el", chw.get("el_rotor_id", 0)), 0)
    if not bool(rb.get("enable_az", True)):
        az = 0
    if not bool(rb.get("enable_el", False)):
        el = 0
    return {
        "cont_id": _clamp_cont_id(chw.get("cont_id", 2), 2),
        "az_rotor_id": az,
        "el_rotor_id": el,
    }


def push_controller_rotor_ids(ctrl: Any, cfg: Optional[Dict[str, Any]]) -> bool:
    """AZ/EL-Rotor-ID im Display-Controller auf das aktive Profil stellen (nur bei Abweichung).

    Wird für Controller im Modus WEB gebraucht: der Display spricht über den Browser
    direkt den Rotor an — hat das Profil eine andere Slave-ID, antwortet sonst niemand.
    """
    if not hasattr(ctrl, "sync_ui_command_response"):
        return True
    ids = controller_rotor_ids_from_cfg(cfg)
    dst = int(ids["cont_id"])
    if dst <= 0:
        return True
    from .rotor_controller import SYNC_UI_NAK_PREFIX

    ok_all = True
    for key, cmd in (("az_rotor_id", "SETCONTAZID"), ("el_rotor_id", "SETCONTELID")):
        want = int(ids[key])
        cur = read_controller_int(ctrl, dst, cmd)
        if cur is not None and int(cur) == want:
            continue
        r = ctrl.sync_ui_command_response(dst, cmd, str(want), "ACK_" + cmd, timeout_s=1.5)
        if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
            ok_all = False
    return ok_all


def push_controller_hw_diff(
    ctrl: Any,
    *,
    old_chw: Optional[Dict[str, Any]],
    new_chw: Optional[Dict[str, Any]],
    connected: bool,
    encoder_type3: bool = False,
    force: bool = False,
    skip_remote: bool = False,
) -> bool:
    """``controller_hw``-Werte per SETCON* an den Controller schreiben.

    ``skip_remote=True``: ``SETCONREMOTE`` nicht anfassen und den Controller-Link nicht
    umschalten (Display hängt per Web Serial am Browser-Relay — der Link muss über den
    Profilwechsel hinweg stehen bleiben).

    - ``force=False`` (Default): nur Differenzen old→new.
    - ``force=True``: alle SETCON*-Werte aus ``new_chw`` schreiben (Profilwechsel),
      damit der physische Controller dem neuen Profil entspricht — auch wenn die
      beiden Profil-JSONs nach Normalisierung gleich aussehen.

    Nur wenn ``new_chw.enabled`` und ``connected``. Rückgabe True wenn alles OK / nichts zu tun.
    """
    from .rotor_controller import SYNC_UI_NAK_PREFIX

    if not connected or not hasattr(ctrl, "sync_ui_command_response"):
        return True
    new_ch = new_chw if isinstance(new_chw, dict) else {}
    if not bool(new_ch.get("enabled", True)):
        return True
    old_n = controller_hw_norm(old_chw)
    new_n = controller_hw_norm(new_ch)
    if (not force) and old_n == new_n:
        return True

    def _ok(r: Optional[str]) -> bool:
        if r is None:
            return False
        return not str(r).startswith(SYNC_UI_NAK_PREFIX)

    def _changed(key: str) -> bool:
        return force or old_n[key] != new_n[key]

    all_ok = True
    dst = int(new_n["cont_id"])
    if dst <= 0:
        return True

    def _write_if_differs(set_cmd: str, value: int) -> bool:
        """Controller-Wert lesen und nur bei Abweichung schreiben (Flash schonen)."""
        cur = read_controller_int(ctrl, dst, set_cmd)
        if cur is not None and int(cur) == int(value):
            return True
        r = ctrl.sync_ui_command_response(
            dst, set_cmd, str(int(value)), "ACK_" + set_cmd, timeout_s=1.5
        )
        return _ok(r)

    if _changed("cont_id"):
        id_dst = int(new_n["cont_id"]) if int(old_n["cont_id"]) == 0 else int(old_n["cont_id"])
        # Bei force und gleicher ID: SETCONTID überspringen (Adresse unverändert).
        if force and old_n["cont_id"] == new_n["cont_id"]:
            pass
        else:
            r = ctrl.sync_ui_command_response(
                id_dst, "SETCONTID", str(int(new_n["cont_id"])), "ACK_SETCONTID", timeout_s=1.5
            )
            if not _ok(r):
                all_ok = False

    pairs = (
        ("slow_pwm", "SETCONSPWM"),
        ("fast_pwm", "SETCONFPWM"),
        ("speaker_freq_hz", "SETCONFRQ"),
        ("speaker_volume", "SETLSL"),
        ("display_brightness_pct", "SETCONLEDP"),
        ("encoder_delta", "SETCONDELTA"),
        ("antenna_realign_on_switch", "SETCONCHA"),
        ("az_rotor_id", "SETCONTAZID"),
        ("el_rotor_id", "SETCONTELID"),
    )
    # Remote-USB: immer sicherstellen (nicht nur bei Toggle) — Flag kann auf dem
    # Controller nach Reset/Flash fehlen, während die Software-Config schon „an“ ist.
    if (not skip_remote) and int(new_n["usb_remote"]) == 1:
        if not _write_if_differs("SETCONREMOTE", 1):
            all_ok = False
        try:
            setter = getattr(ctrl, "set_controller_link", None)
            if callable(setter):
                setter(getattr(ctrl, "ctrl_hw", None), True)
        except Exception:
            pass

    for key, cmd in pairs:
        if not _changed(key):
            continue
        if not _write_if_differs(cmd, int(new_n[key])):
            all_ok = False

    if _changed("wind_anemometer") and not encoder_type3:
        if not _write_if_differs("SETCONANO", int(new_n["wind_anemometer"])):
            all_ok = False

    # Remote-USB ausschalten zuletzt (vorherige SETs noch über USB).
    if (not skip_remote) and _changed("usb_remote") and int(new_n["usb_remote"]) == 0:
        if not _write_if_differs("SETCONREMOTE", 0):
            all_ok = False
        try:
            setter = getattr(ctrl, "set_controller_link", None)
            if callable(setter):
                setter(getattr(ctrl, "ctrl_hw", None), False)
        except Exception:
            pass

    return all_ok


def _ui_list3(ui: Dict[str, Any], key: str, default: Any) -> list:
    raw = ui.get(key) if isinstance(ui, dict) else None
    out: list = []
    for i in range(3):
        try:
            out.append(raw[i])
        except Exception:
            out.append(default)
    return out


def seed_antenna_offsets_from_cfg(ctrl: Any, cfg: Optional[Dict[str, Any]]) -> None:
    """Profil-Versätze sofort in den Achsen-Cache (Anzeige nicht auf alten GETANTOFF warten)."""
    ui = (cfg or {}).get("ui") if isinstance(cfg, dict) else {}
    if not isinstance(ui, dict):
        ui = {}
    offs = _ui_list3(ui, "antenna_offsets_az", 0.0)
    try:
        az = ctrl.az
        for i, slot in enumerate((1, 2, 3)):
            try:
                setattr(az, f"antoff{slot}", max(0.0, min(360.0, float(offs[i]))))
            except Exception:
                setattr(az, f"antoff{slot}", 0.0)
    except Exception:
        return
    cb = getattr(ctrl, "on_antenna_offsets_changed", None)
    if callable(cb):
        try:
            cb()
        except Exception:
            pass


def seed_antenna_dipoles_from_cfg(ctrl: Any, cfg: Optional[Dict[str, Any]]) -> None:
    """Profil-Dipolflags sofort in den Achsen-Cache (Display braucht SETANTDP, nicht nur UI)."""
    from .angle_utils import antenna_dipole_enabled

    try:
        az = ctrl.az
    except Exception:
        return
    for i, slot in enumerate((1, 2, 3)):
        try:
            en = bool(antenna_dipole_enabled(az, cfg, i))
        except Exception:
            en = False
        try:
            setattr(az, f"antdp{slot}", en)
        except Exception:
            pass
    cb = getattr(ctrl, "on_antenna_dipoles_changed", None)
    if callable(cb):
        try:
            cb()
        except Exception:
            pass


def push_antenna_dipoles_async(ctrl: Any, cfg: Optional[Dict[str, Any]]) -> None:
    """SETANTDP1–3 asynchron (Antennenwechsel) — Display snifft/spiegelt Doppelkeule."""
    if not bool(getattr(ctrl, "enable_az", True)):
        return
    if not hasattr(ctrl, "set_antenna_dipole"):
        return
    seed_antenna_dipoles_from_cfg(ctrl, cfg)
    from .angle_utils import antenna_dipole_enabled

    for i, slot in enumerate((1, 2, 3)):
        try:
            en = bool(antenna_dipole_enabled(getattr(ctrl, "az", None), cfg, i))
        except Exception:
            en = False
        try:
            ctrl.set_antenna_dipole("az", slot, en, on_done=None)
        except Exception:
            pass


def push_profile_antenna_offsets(
    ctrl: Any,
    cfg: Optional[Dict[str, Any]],
    *,
    connected: bool,
) -> bool:
    """Profil-Versätze + Dipolflags auf den AZ-Rotor schreiben (SETANTOFF / SETANTDP).

    Nach Profilwechsel sonst: alter Cache, GET* erst beim USB-Reconnect, Display ohne SET-Spiegel.
    """
    seed_antenna_offsets_from_cfg(ctrl, cfg)
    seed_antenna_dipoles_from_cfg(ctrl, cfg)
    if not connected or not bool(getattr(ctrl, "enable_az", True)):
        return True
    if not hasattr(ctrl, "sync_ui_command_response"):
        return True
    from .angle_utils import antenna_dipole_enabled
    from .rotor_controller import SYNC_UI_NAK_PREFIX

    try:
        dst = int(getattr(ctrl, "slave_az", 0) or 0)
    except Exception:
        dst = 0
    if dst <= 0:
        return True
    ui = (cfg or {}).get("ui") if isinstance(cfg, dict) else {}
    if not isinstance(ui, dict):
        ui = {}
    offs = _ui_list3(ui, "antenna_offsets_az", 0.0)
    all_ok = True
    az = getattr(ctrl, "az", None)
    for slot in (1, 2, 3):
        try:
            v = max(0.0, min(360.0, float(offs[slot - 1])))
        except Exception:
            v = 0.0
        param = str(round(v, 1))
        r = ctrl.sync_ui_command_response(
            dst, f"SETANTOFF{slot}", param, f"ACK_SETANTOFF{slot}", timeout_s=1.5
        )
        if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
            all_ok = False
            continue
        if az is not None:
            try:
                setattr(az, f"antoff{slot}", v)
            except Exception:
                pass
    for slot in (1, 2, 3):
        try:
            en = bool(antenna_dipole_enabled(az, cfg, slot - 1))
        except Exception:
            en = False
        r = ctrl.sync_ui_command_response(
            dst, f"SETANTDP{slot}", "1" if en else "0", f"ACK_SETANTDP{slot}", timeout_s=1.5
        )
        if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
            all_ok = False
            continue
        if az is not None:
            try:
                setattr(az, f"antdp{slot}", en)
            except Exception:
                pass
    cb = getattr(ctrl, "on_antenna_offsets_changed", None)
    if callable(cb):
        try:
            cb()
        except Exception:
            pass
    cb_dp = getattr(ctrl, "on_antenna_dipoles_changed", None)
    if callable(cb_dp):
        try:
            cb_dp()
        except Exception:
            pass
    if all_ok:
        try:
            ctrl._antenna_bootstrap_requested = True
        except Exception:
            pass
    return all_ok


def save_rotor_config_xml(
    path: Path, entries: list[dict], gui_config: Optional[Dict[str, Any]] = None
) -> None:
    """
    Speichert Einträge und optional GUI-Einstellungen als XML.
    Jeder Eintrag: {"dst": int, "cmd": str, "params": str}
    gui_config: Dict mit lokalen App-Config-Bereichen.
    """
    root = ET.Element("rotor_config")
    if gui_config:
        gui_el = ET.SubElement(root, "gui_config")
        gui_el.text = json.dumps(gui_config, ensure_ascii=False, indent=0)
    for e in entries:
        item = ET.SubElement(root, "item")
        item.set("dst", str(int(e["dst"])))
        item.set("cmd", str(e["cmd"]))
        item.set("params", str(e.get("params", "")))
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True, default_namespace="")


def load_rotor_config_xml(path: Path) -> tuple[list[dict], Optional[Dict[str, Any]]]:
    """
    Lädt Backup-XML.
    Gibt (entries, gui_config) zurück.
    entries: Liste von {"dst": int, "cmd": str, "params": str}
    gui_config: Dict mit GUI-Einstellungen oder None wenn nicht vorhanden.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    entries: list[dict] = []
    gui_config: Optional[Dict[str, Any]] = None
    gui_el = root.find("gui_config")
    if gui_el is not None and gui_el.text:
        try:
            gui_config = json.loads(gui_el.text)
        except json.JSONDecodeError:
            pass
    for item in root.findall("item"):
        try:
            dst = int(item.get("dst", 0))
            cmd = str(item.get("cmd", "")).strip().upper()
            params = str(item.get("params", "")).strip()
            if cmd and cmd.startswith("SET"):
                entries.append({"dst": dst, "cmd": cmd, "params": params})
        except (ValueError, TypeError):
            continue
    return entries, gui_config
