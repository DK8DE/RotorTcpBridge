"""Rotor-Profile: volle Config-JSON pro Setup unter %APPDATA%/RotorTcpBridge/profiles/."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "Default"
_INDEX_NAME = "profiles.json"
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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
            out.append(
                {
                    "id": pid,
                    "name": name,
                    "file": fname,
                    "is_default": is_def,
                }
            )
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


def load_index() -> Dict[str, Any]:
    p = profiles_index_path()
    if not p.exists():
        idx = _empty_index()
        save_index(idx)
        return idx
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = None
    idx = _normalize_index(raw)
    save_index(idx)
    return idx


def save_index(index: Dict[str, Any]) -> None:
    idx = _normalize_index(index)
    p = profiles_index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=2, ensure_ascii=False)


def list_profiles() -> List[Dict[str, Any]]:
    return list(load_index().get("profiles") or [])


def get_active_profile_id() -> str:
    return str(load_index().get("active_id") or DEFAULT_PROFILE_ID)


def get_profile_meta(profile_id: str) -> Optional[Dict[str, Any]]:
    pid = str(profile_id or "").strip()
    for p in list_profiles():
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    _write_json_file(profile_file_path(new_id, filename=fname), data)
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
