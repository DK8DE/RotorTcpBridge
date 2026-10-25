"""Rotor-Profile: volle Config-JSON pro Setup unter %APPDATA%/RotorTcpBridge/profiles/."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "Default"
_INDEX_NAME = "profiles.json"
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_INDEX_LOCK = threading.RLock()
_INDEX_CACHE: Optional[Dict[str, Any]] = None
_INDEX_CACHE_PATH: Optional[str] = None


def profiles_dir() -> Path:
    from .app_config import appdata_dir

    p = appdata_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def profiles_index_path() -> Path:
    return profiles_dir() / _INDEX_NAME


def profile_file_path(profile_id: str, *, filename: Optional[str] = None) -> Path:
    name = filename or f"{profile_id}.json"
    return profiles_dir() / name


def _empty_index() -> Dict[str, Any]:
    return {
        "active_id": DEFAULT_PROFILE_ID,
        "profiles": [
            {
                "id": DEFAULT_PROFILE_ID,
                "name": DEFAULT_PROFILE_NAME,
                "file": f"{DEFAULT_PROFILE_ID}.json",
                "is_default": True,
            }
        ],
    }


def _normalize_index(raw: Any) -> Dict[str, Any]:
    idx = _empty_index()
    if not isinstance(raw, dict):
        return idx
    profiles_in = raw.get("profiles")
    out: List[Dict[str, Any]] = []
    if isinstance(profiles_in, list):
        seen: Dict[str, int] = {}
        for item in profiles_in:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id", "") or "").strip()
            if not pid or not _SAFE_ID_RE.match(pid):
                continue
            name = str(item.get("name", "") or "").strip() or pid
            fname = str(item.get("file", "") or "").strip() or f"{pid}.json"
            # Pfad-Traversal verhindern
            if "/" in fname or "\\" in fname or fname.startswith("."):
                fname = f"{pid}.json"
            is_def = bool(item.get("is_default", False)) or pid == DEFAULT_PROFILE_ID
            entry = {
                "id": pid,
                "name": name,
                "file": fname,
                "is_default": is_def,
            }
            if pid in seen:
                prev = out[seen[pid]]
                # Doppelter Index-Eintrag (Anlegen + Orphan-Recover): Anzeigename behalten
                if name not in (pid, ""):
                    prev["name"] = name
                if fname and prev.get("file") in (f"{pid}.json", ""):
                    prev["file"] = fname
                continue
            seen[pid] = len(out)
            out.append(entry)
    if not out:
        out = list(idx["profiles"])
    # Genau ein Default-Flag am Default-ID
    has_default = False
    for p in out:
        if p["id"] == DEFAULT_PROFILE_ID:
            p["is_default"] = True
            has_default = True
        elif p.get("is_default"):
            p["is_default"] = False
    if not has_default:
        out.insert(
            0,
            {
                "id": DEFAULT_PROFILE_ID,
                "name": DEFAULT_PROFILE_NAME,
                "file": f"{DEFAULT_PROFILE_ID}.json",
                "is_default": True,
            },
        )
    active = str(raw.get("active_id", "") or "").strip() or DEFAULT_PROFILE_ID
    ids = {p["id"] for p in out}
    if active not in ids:
        active = DEFAULT_PROFILE_ID
    return {"active_id": active, "profiles": out}


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _index_signature(idx: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "active_id": idx.get("active_id"),
            "profiles": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "file": p.get("file"),
                    "is_default": bool(p.get("is_default")),
                }
                for p in (idx.get("profiles") or [])
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _recover_orphan_profile_files(idx: Dict[str, Any]) -> Dict[str, Any]:
    """Profildateien, die nicht mehr im Index stehen, wieder einhängen (nach Index-Verlust)."""
    profiles = list(idx.get("profiles") or [])
    known_ids = {str(p.get("id") or "") for p in profiles}
    known_files = {str(p.get("file") or "") for p in profiles}
    try:
        paths = sorted(profiles_dir().glob("*.json"))
    except Exception:
        return idx
    for path in paths:
        name = path.name
        if name == _INDEX_NAME or name.endswith(".tmp"):
            continue
        pid = path.stem
        if not _SAFE_ID_RE.match(pid):
            continue
        if pid in known_ids or name in known_files:
            continue
        if pid == DEFAULT_PROFILE_ID:
            continue
        display = pid
        profiles.append(
            {
                "id": pid,
                "name": display,
                "file": name,
                "is_default": False,
            }
        )
        known_ids.add(pid)
        known_files.add(name)
    idx = dict(idx)
    idx["profiles"] = profiles
    return _normalize_index(idx)


def load_index() -> Dict[str, Any]:
    with _INDEX_LOCK:
        return _load_index_unlocked()


def _load_index_unlocked() -> Dict[str, Any]:
    global _INDEX_CACHE, _INDEX_CACHE_PATH
    path = str(profiles_index_path())
    if _INDEX_CACHE is not None and _INDEX_CACHE_PATH == path:
        return copy.deepcopy(_INDEX_CACHE)
    p = profiles_index_path()
    persist = False
    do_recover = False
    if not p.exists():
        idx = _empty_index()
        persist = True
        do_recover = True
    else:
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = None
            persist = True
            do_recover = True
        idx = _normalize_index(raw)
        # Einmal pro Prozess vom Datenträger: verwaiste Dateien nach Index-Verlust.
        do_recover = True
    if do_recover:
        before = _index_signature(idx)
        idx = _recover_orphan_profile_files(idx)
        if _index_signature(idx) != before:
            persist = True
    if persist:
        _save_index_unlocked(idx)
    else:
        _INDEX_CACHE = copy.deepcopy(idx)
        _INDEX_CACHE_PATH = path
    return copy.deepcopy(idx)


def save_index(index: Dict[str, Any]) -> None:
    with _INDEX_LOCK:
        _save_index_unlocked(index)


def _save_index_unlocked(index: Dict[str, Any]) -> None:
    global _INDEX_CACHE, _INDEX_CACHE_PATH
    idx = _normalize_index(index)
    _atomic_write_json(profiles_index_path(), idx)
    _INDEX_CACHE = copy.deepcopy(idx)
    _INDEX_CACHE_PATH = str(profiles_index_path())


def list_profiles() -> List[Dict[str, Any]]:
    return list(load_index().get("profiles") or [])


def get_active_profile_id() -> str:
    return str(load_index().get("active_id") or DEFAULT_PROFILE_ID)


def get_active_profile_meta() -> Dict[str, Any]:
    """Aktives Profil in einem Index-Lesevorgang (ohne Extra-Schreibzugriff)."""
    idx = load_index()
    pid = str(idx.get("active_id") or DEFAULT_PROFILE_ID)
    for p in idx.get("profiles") or []:
        if p.get("id") == pid:
            return dict(p)
    return {"id": pid, "name": pid, "file": f"{pid}.json", "is_default": pid == DEFAULT_PROFILE_ID}


def get_profile_meta(profile_id: str) -> Optional[Dict[str, Any]]:
    pid = str(profile_id or "").strip()
    for p in load_index().get("profiles") or []:
        if p.get("id") == pid:
            return dict(p)
    return None


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    _atomic_write_json(path, data)


def ensure_profiles_migrated() -> None:
    """Einmalig: profiles/ anlegen, Legacy config.json → default.json."""
    from .app_config import config_path

    profiles_dir()
    idx_path = profiles_index_path()
    default_path = profile_file_path(DEFAULT_PROFILE_ID)
    legacy = config_path()

    if not idx_path.exists():
        # Migration von Legacy-config.json
        if legacy.exists():
            raw = _read_json_file(legacy)
            if raw is not None:
                _write_json_file(default_path, raw)
        if not default_path.exists():
            from .app_config import migrate_and_merge_config

            initial = migrate_and_merge_config(None)
            _write_json_file(default_path, initial)
        save_index(_empty_index())
        return

    # Index existiert: Default-Datei absichern
    idx = load_index()
    meta = get_profile_meta(DEFAULT_PROFILE_ID)
    if meta is None:
        idx["profiles"].insert(
            0,
            {
                "id": DEFAULT_PROFILE_ID,
                "name": DEFAULT_PROFILE_NAME,
                "file": f"{DEFAULT_PROFILE_ID}.json",
                "is_default": True,
            },
        )
        save_index(idx)
        meta = get_profile_meta(DEFAULT_PROFILE_ID)
    dpath = profile_file_path(DEFAULT_PROFILE_ID, filename=(meta or {}).get("file"))
    if not dpath.exists():
        if legacy.exists():
            raw = _read_json_file(legacy)
            if raw is not None:
                _write_json_file(dpath, raw)
                return
        from .app_config import migrate_and_merge_config

        _write_json_file(dpath, migrate_and_merge_config(None))


def _normalize_loaded_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    from .app_config import migrate_and_merge_config

    return migrate_and_merge_config(cfg)


def load_profile_config(profile_id: str) -> Dict[str, Any]:
    ensure_profiles_migrated()
    meta = get_profile_meta(profile_id)
    if meta is None:
        meta = get_profile_meta(DEFAULT_PROFILE_ID)
    assert meta is not None
    path = profile_file_path(str(meta["id"]), filename=str(meta.get("file")))
    raw = _read_json_file(path)
    if raw is None:
        from .app_config import migrate_and_merge_config

        raw = migrate_and_merge_config(None)
        _write_json_file(path, raw)
        return json.loads(json.dumps(raw))
    return _normalize_loaded_config(raw)


def load_active_config() -> Dict[str, Any]:
    ensure_profiles_migrated()
    return load_profile_config(get_active_profile_id())


def save_profile_config(profile_id: str, cfg: Dict[str, Any]) -> None:
    ensure_profiles_migrated()
    meta = get_profile_meta(profile_id)
    if meta is None:
        raise ValueError(f"unknown profile: {profile_id}")
    path = profile_file_path(str(meta["id"]), filename=str(meta.get("file")))
    _write_json_file(path, cfg)


def save_active_config(cfg: Dict[str, Any]) -> None:
    save_profile_config(get_active_profile_id(), cfg)


def create_profile(name: str, *, clone_active: bool = False) -> str:
    """Neues Profil anlegen.

    Standard: jungfräuliche Config wie bei Neuinstallation (DEFAULT_CONFIG + Migration).
    ``clone_active=True`` kopiert die aktuelle aktive Config (optional).
    """
    ensure_profiles_migrated()
    display = str(name or "").strip() or "Profil"
    if clone_active:
        return copy_profile(get_active_profile_id(), display)
    from .app_config import migrate_and_merge_config

    data = migrate_and_merge_config(None)
    return _append_profile(display, data)


def seed_commissioning_runtime_link(profile_id: str, source_cfg: Dict[str, Any]) -> None:
    """Übernimmt PC↔Hardware/Controller-Link in ein jungfräuliches Profil.

    Rotor-Bus und Kalibrierung bleiben Werkswerte. Ohne Seed würde die
    Inbetriebnahme beim ersten „Weiter“ auf DEFAULT (COM1, Link-Mode 0)
    umschalten und TCP/USB Remote abrupt trennen — Absturzrisiko.
    """
    pid = str(profile_id or "").strip()
    if not pid or get_profile_meta(pid) is None:
        raise ValueError(f"unknown profile: {profile_id}")
    src = source_cfg if isinstance(source_cfg, dict) else {}
    data = load_profile_config(pid)

    for key in ("hardware_link", "controller_link", "network_modules"):
        if key in src:
            data[key] = copy.deepcopy(src[key])

    src_chw = src.get("controller_hw") if isinstance(src.get("controller_hw"), dict) else {}
    dst_chw = data.get("controller_hw")
    if not isinstance(dst_chw, dict):
        dst_chw = {}
        data["controller_hw"] = dst_chw
    for k in ("enabled", "cont_id", "usb_remote", "link_mode"):
        if k in src_chw:
            dst_chw[k] = copy.deepcopy(src_chw[k])

    src_ui = src.get("ui") if isinstance(src.get("ui"), dict) else {}
    dst_ui = data.get("ui")
    if not isinstance(dst_ui, dict):
        dst_ui = {}
        data["ui"] = dst_ui
    if "setup_axis_endpoints" in src_ui:
        dst_ui["setup_axis_endpoints"] = copy.deepcopy(src_ui["setup_axis_endpoints"])
    if "language" in src_ui:
        dst_ui["language"] = copy.deepcopy(src_ui["language"])

    save_profile_config(pid, data)


def copy_profile(source_id: str, name: str) -> str:
    """Neues Profil als Kopie eines bestehenden (beliebige Listen-Zeile, nicht nur aktiv)."""
    ensure_profiles_migrated()
    src = str(source_id or "").strip()
    if not src or get_profile_meta(src) is None:
        raise ValueError(f"unknown profile: {source_id}")
    display = str(name or "").strip() or "Profil"
    data = json.loads(json.dumps(load_profile_config(src)))
    return _append_profile(display, data)


def _append_profile(display: str, data: Dict[str, Any]) -> str:
    new_id = f"p_{uuid.uuid4().hex[:10]}"
    fname = f"{new_id}.json"
    idx = load_index()
    idx["profiles"].append(
        {
            "id": new_id,
            "name": display,
            "file": fname,
            "is_default": False,
        }
    )
    save_index(idx)
    _write_json_file(profile_file_path(new_id, filename=fname), data)
    return new_id


def rename_profile(profile_id: str, name: str) -> None:
    display = str(name or "").strip()
    if not display:
        raise ValueError("empty name")
    idx = load_index()
    found = False
    for p in idx["profiles"]:
        if p.get("id") == profile_id:
            p["name"] = display
            found = True
            break
    if not found:
        raise ValueError(f"unknown profile: {profile_id}")
    save_index(idx)


def delete_profile(profile_id: str) -> None:
    pid = str(profile_id or "").strip()
    meta = get_profile_meta(pid)
    if meta is None:
        raise ValueError(f"unknown profile: {pid}")
    if bool(meta.get("is_default")) or pid == DEFAULT_PROFILE_ID:
        raise ValueError("default profile cannot be deleted")
    idx = load_index()
    if len(idx["profiles"]) <= 1:
        raise ValueError("cannot delete last profile")
    was_active = str(idx.get("active_id")) == pid
    path = profile_file_path(pid, filename=str(meta.get("file")))
    idx["profiles"] = [p for p in idx["profiles"] if p.get("id") != pid]
    if was_active:
        idx["active_id"] = DEFAULT_PROFILE_ID
    save_index(idx)
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def switch_profile(profile_id: str) -> Dict[str, Any]:
    """Aktives Profil wechseln und Config laden (ohne vorheriges Speichern)."""
    ensure_profiles_migrated()
    pid = str(profile_id or "").strip()
    meta = get_profile_meta(pid)
    if meta is None:
        raise ValueError(f"unknown profile: {pid}")
    idx = load_index()
    idx["active_id"] = pid
    save_index(idx)
    return load_profile_config(pid)


PROFILE_EXPORT_FORMAT = "RotorTcpBridge-profile"
PROFILE_EXPORT_VERSION = 1


def export_profile(profile_id: str, path: Path | str) -> None:
    """Profil als JSON-Datei exportieren (inkl. Anzeigename)."""
    ensure_profiles_migrated()
    pid = str(profile_id or "").strip()
    meta = get_profile_meta(pid)
    if meta is None:
        raise ValueError(f"unknown profile: {profile_id}")
    cfg = load_profile_config(pid)
    payload = {
        "format": PROFILE_EXPORT_FORMAT,
        "version": PROFILE_EXPORT_VERSION,
        "name": str(meta.get("name") or pid),
        "config": cfg,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def import_profile(path: Path | str, *, name: Optional[str] = None) -> str:
    """Profil aus JSON importieren → neues Profil (nie Default überschreiben).

    Akzeptiert Export-Wrapper oder reine Config-JSON (wie profiles/*.json).
    """
    ensure_profiles_migrated()
    raw = _read_json_file(Path(path))
    if raw is None:
        raise ValueError("invalid or unreadable JSON")
    display = str(name or "").strip()
    if (
        str(raw.get("format") or "") == PROFILE_EXPORT_FORMAT
        and isinstance(raw.get("config"), dict)
    ):
        cfg_raw = raw["config"]
        if not display:
            display = str(raw.get("name") or "").strip()
    else:
        # Reine Config-Datei
        cfg_raw = raw
    if not isinstance(cfg_raw, dict):
        raise ValueError("invalid profile config")
    # Heuristik: echte App-Config hat typische Top-Level-Keys
    if not any(k in cfg_raw for k in ("rotor_bus", "hardware_link", "ui", "pst_server")):
        raise ValueError("file does not look like a RotorTcpBridge profile")
    cfg = _normalize_loaded_config(cfg_raw)
    if not display:
        stem = Path(path).stem.strip() or "Import"
        display = stem
    return _append_profile(display, cfg)
