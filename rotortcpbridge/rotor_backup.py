"""
Rotor-Konfiguration als XML sichern und wiederherstellen.

Nur Befehle mit GET und SET werden berücksichtigt.
Steuerbefehle (STOP, NSTOP, SETREF, SETPOSDG, etc.) werden ausgeschlossen.
GUI-Einstellungen (rotor_bus, hardware_link, ui, etc.) werden mit gespeichert.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional

from .command_catalog import command_specs, CommandSpec

# Config-Bereiche, die als GUI-Einstellungen mit gesichert werden
_GUI_CONFIG_KEYS = (
    "pst_server",
    "rotor_bus",
    "hardware_link",
    "ui",
    "polling_ms",
    "spid",
    "pwm",
    "behavior",
    "controller_hw",
)

# SET-Befehle ohne GET oder Steuerbefehle – nicht backupbar
_EXCLUDED_SET = frozenset(
    {
        "STOP",
        "NSTOP",
        "SETREF",
        "SETPOSDG",
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


def get_params_for_get(spec: Optional[CommandSpec]) -> str:
    """Parameter-String für einen GET-Befehl ermitteln."""
    if spec is None:
        return "0"
    if spec.kind == "none" and spec.params_literal is not None:
        return str(spec.params_literal)
    return "0"


def _extract_gui_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Extrahiert die GUI-relevanten Bereiche aus der Konfiguration."""
    out: Dict[str, Any] = {}
    for key in _GUI_CONFIG_KEYS:
        if key in cfg and isinstance(cfg[key], dict):
            out[key] = json.loads(json.dumps(cfg[key]))
    return out


def _apply_gui_config(cfg: Dict[str, Any], gui: Dict[str, Any]) -> None:
    """Überschreibt cfg mit den geladenen GUI-Einstellungen (in-place merge)."""
    for key, val in gui.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            _deep_merge(cfg[key], val)
        else:
            cfg[key] = json.loads(json.dumps(val)) if isinstance(val, (dict, list)) else val


def extract_gui_config_for_backup(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """GUI-relevante Bereiche für Backup extrahieren."""
    return _extract_gui_config(cfg)


def apply_gui_config_from_backup(cfg: Dict[str, Any], gui: Dict[str, Any]) -> None:
    """Geladene GUI-Einstellungen in cfg eintragen."""
    _apply_gui_config(cfg, gui)


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def save_rotor_config_xml(
    path: Path, entries: list[dict], gui_config: Optional[Dict[str, Any]] = None
) -> None:
    """
    Speichert Einträge und optional GUI-Einstellungen als XML.
    Jeder Eintrag: {"dst": int, "cmd": str, "params": str}
    gui_config: Dict mit pst_server, rotor_bus, hardware_link, ui, etc.
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
