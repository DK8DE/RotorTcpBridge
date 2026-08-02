"""
Rotor-Konfiguration als XML sichern und wiederherstellen.

Nur Befehle mit GET und SET werden berücksichtigt.
Steuerbefehle (STOP, NSTOP, SETREF, SETPOSDG, etc.) werden ausgeschlossen.

Lokale App-Einstellungen (config.json-Bereiche) werden mitgespeichert.
Hardware-Parameter gehen an Rotor-Slaves (AZ/EL) bzw. an den Display-Controller
(``SETCON*`` / ``SETLSL`` → ``controller_hw.cont_id``).
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
    "pst_serial",
    "rotor_bus",
    "hardware_link",
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
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    try:
        cid = int(chw.get("cont_id", 2) or 0)
        if hasattr(ctrl, "setposcc_controller_src_id"):
            ctrl.setposcc_controller_src_id = cid
    except Exception:
        pass


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
