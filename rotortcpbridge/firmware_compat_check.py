"""Live-Versionscheck gegen gepinnte Firmware aus version.py."""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .version import (
    CONTROLLER_FW_VERSION,
    INCLINOMETER_FW_VERSION,
    LAN_MODULE_FW_VERSION,
    ROTOR_FW_VERSION,
    WLAN_MODULE_FW_VERSION,
)

_VER_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def normalize_version(raw: Optional[str]) -> str:
    """Erste SemVer-ähnliche Zahl aus Antwort extrahieren."""
    s = str(raw or "").strip()
    if not s:
        return ""
    # erster Token vor ; oder Leerzeichen
    token = s.split(";")[0].strip().split()[0] if s else ""
    m = _VER_RE.search(token) or _VER_RE.search(s)
    if not m:
        return token.lstrip("vV")
    major, minor, patch = m.group(1), m.group(2), m.group(3) or "0"
    return f"{int(major)}.{int(minor)}.{int(patch)}"


def version_tuple(raw: Optional[str]) -> Optional[tuple[int, int, int]]:
    """SemVer als (major, minor, patch) oder None."""
    n = normalize_version(raw)
    if not n:
        return None
    m = _VER_RE.match(n)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def versions_equal(a: Optional[str], b: Optional[str]) -> bool:
    ta, tb = version_tuple(a), version_tuple(b)
    if ta is None or tb is None:
        return False
    return ta == tb


def version_at_least(found: Optional[str], minimum: Optional[str]) -> bool:
    """True wenn found >= minimum (neuere Firmware ist OK)."""
    tf, tm = version_tuple(found), version_tuple(minimum)
    if tf is None or tm is None:
        return False
    return tf >= tm


@dataclass
class AxisVersionResult:
    axis: str
    expected: str
    found: Optional[str]
    ok: bool
    offline: bool = False
    query_failed: bool = False


@dataclass
class ModuleVersionResult:
    """Ein konfiguriertes DK8DE WLAN- oder LAN-Modul."""

    kind: str  # "wlan" | "lan"
    label: str
    uid: str
    host: str
    expected: str
    found: Optional[str]
    ok: bool
    offline: bool = False


@dataclass
class CompatCheckResult:
    rotor_ok: bool = True
    rotor_axes: List[AxisVersionResult] = field(default_factory=list)
    controller_found: Optional[str] = None
    controller_expected: str = CONTROLLER_FW_VERSION
    controller_ok: Optional[bool] = None  # None = nicht geprüft
    inclinometer_found: Optional[str] = None
    inclinometer_expected: str = INCLINOMETER_FW_VERSION
    inclinometer_ok: Optional[bool] = None
    inclinometer_axis: Optional[str] = None  # "AZ" | "EL" wenn gefunden
    modules: List[ModuleVersionResult] = field(default_factory=list)
    modules_ok: bool = True  # False wenn mind. ein erreichbares Modul zu alt
    wlan_note: Optional[str] = None  # Abwärtskompatibel
    info_lines: List[str] = field(default_factory=list)


def _sync_get(ctrl: Any, dst: int, cmd: str, expect: str, *, timeout_s: float = 1.5) -> Optional[str]:
    if ctrl is None or not hasattr(ctrl, "sync_ui_command_response"):
        return None
    try:
        r = ctrl.sync_ui_command_response(
            int(dst), str(cmd), "0", str(expect), timeout_s=float(timeout_s)
        )
    except Exception:
        return None
    if r is None:
        return None
    s = str(r).strip()
    if not s or s.upper().startswith("NAK"):
        return None
    return s.split(";")[0].strip() or None


def _bus_connected(ctrl: Any) -> bool:
    try:
        hw = getattr(ctrl, "hw", None)
        if hw is not None and hasattr(hw, "is_connected"):
            return bool(hw.is_connected())
    except Exception:
        pass
    return False


def _inclinometer_configured(ctrl: Any, cfg: Optional[dict]) -> bool:
    """Encoder-Typ 4 = Inklinometer."""
    try:
        et = getattr(ctrl, "encoder_type", None)
        if et is not None and int(et) == 4:
            return True
    except Exception:
        pass
    cfg = cfg if isinstance(cfg, dict) else {}
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    try:
        if int(ui.get("encoder_type", 0) or 0) == 4:
            return True
    except Exception:
        pass
    return False


def _module_kind(vendor: str) -> str:
    v = str(vendor or "").strip().lower()
    if v in ("dk8de_lan", "lan_to_rs485"):
        return "lan"
    return "wlan"


def _module_label(name: str, uid: str, host: str, kind: str) -> str:
    n = str(name or "").strip()
    if n:
        return n
    u = str(uid or "").strip().upper()
    if u:
        return f"{'LAN' if kind == 'lan' else 'WLAN'}-{u}"
    h = str(host or "").strip()
    return h or ("LAN" if kind == "lan" else "WLAN")


def _read_dk8de_module_fw(
    host: str,
    *,
    web_port: int = 80,
    user: str = "admin",
    password: str = "Rotorconfig",
    timeout: float = 0.9,
) -> Optional[str]:
    """Schneller FW-String aus Web /api/status (ohne /api/config)."""
    try:
        from .dk8de_wlan_module import _http_get_json
    except Exception:
        return None
    st = _http_get_json(
        host,
        "/api/status",
        port=int(web_port or 80),
        user=str(user or "admin"),
        password=str(password if password is not None else "Rotorconfig"),
        timeout=float(timeout),
    )
    if not isinstance(st, dict):
        return None
    return normalize_version(str(st.get("fw") or "")) or None


def _check_network_modules(cfg: dict) -> List[ModuleVersionResult]:
    """Alle konfigurierten DK8DE WLAN-/LAN-Module parallel per Web abfragen."""
    try:
        from .network_modules import is_dk8de_vendor, modules_from_cfg
    except Exception:
        return []

    try:
        modules = modules_from_cfg(cfg)
    except Exception:
        modules = []

    jobs: List[tuple] = []
    for m in modules:
        if not is_dk8de_vendor(getattr(m, "vendor", "")):
            continue
        host = str(getattr(m, "host", "") or getattr(m, "contact_host", "") or "").strip()
        if not host or host in ("0.0.0.0", "-", "255.255.255.255"):
            continue
        kind = _module_kind(str(getattr(m, "vendor", "")))
        expected = LAN_MODULE_FW_VERSION if kind == "lan" else WLAN_MODULE_FW_VERSION
        uid = str(getattr(m, "uid", "") or "").strip().upper()
        label = _module_label(str(getattr(m, "name", "") or ""), uid, host, kind)
        jobs.append(
            (
                kind,
                label,
                uid,
                host,
                expected,
                int(getattr(m, "web_port", 80) or 80),
                str(getattr(m, "web_user", "admin") or "admin"),
                str(
                    getattr(m, "web_password", "Rotorconfig")
                    if getattr(m, "web_password", None) is not None
                    else "Rotorconfig"
                ),
            )
        )

    if not jobs:
        return []

    results: List[ModuleVersionResult] = []

    def _one(job: tuple) -> ModuleVersionResult:
        kind, label, uid, host, expected, web_port, user, password = job
        found = _read_dk8de_module_fw(
            host, web_port=web_port, user=user, password=password, timeout=0.9
        )
        if not found:
            return ModuleVersionResult(
                kind=kind,
                label=label,
                uid=uid,
                host=host,
                expected=expected,
                found=None,
                ok=True,
                offline=True,
            )
        ok = version_at_least(found, expected)
        return ModuleVersionResult(
            kind=kind,
            label=label,
            uid=uid,
            host=host,
            expected=expected,
            found=found,
            ok=ok,
            offline=False,
        )

    workers = max(1, min(8, len(jobs)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, j) for j in jobs]
        try:
            for fut in concurrent.futures.as_completed(
                futs, timeout=max(4.0, 1.2 * len(jobs))
            ):
                try:
                    results.append(fut.result())
                except Exception:
                    continue
        except concurrent.futures.TimeoutError:
            pass
    # Offline-Einträge für Jobs ohne Ergebnis (Timeout)
    done_hosts = {r.host for r in results}
    for job in jobs:
        kind, label, uid, host, expected, *_rest = job
        if host in done_hosts:
            continue
        results.append(
            ModuleVersionResult(
                kind=kind,
                label=label,
                uid=uid,
                host=host,
                expected=expected,
                found=None,
                ok=True,
                offline=True,
            )
        )
    # stabile Reihenfolge: WLAN vor LAN, dann Label
    results.sort(key=lambda r: (0 if r.kind == "wlan" else 1, r.label.lower(), r.host))
    return results


def run_compat_check(ctrl: Any, cfg: Optional[dict] = None) -> CompatCheckResult:
    """Synchron am Bus abfragen (UI-Thread: kurz halten / nach Connect verzögern).

    Netzwerkmodule (WLAN/LAN) werden unabhängig vom RS485-Bus per HTTP geprüft.
    """
    out = CompatCheckResult()
    cfg = cfg if isinstance(cfg, dict) else {}

    # Module zuerst / immer — braucht keinen Bus.
    try:
        out.modules = _check_network_modules(cfg)
    except Exception:
        out.modules = []
    online_mods = [m for m in out.modules if not m.offline]
    if online_mods:
        out.modules_ok = all(m.ok for m in online_mods)
    else:
        out.modules_ok = True
    for m in out.modules:
        if m.offline:
            out.info_lines.append(f"module_offline:{m.kind}:{m.label}:{m.expected}")
        elif not m.ok and m.found:
            out.info_lines.append(
                f"module_mismatch:{m.kind}:{m.label}:{m.found}:{m.expected}"
            )
        else:
            out.info_lines.append(f"module_ok:{m.kind}:{m.label}:{m.found}:{m.expected}")
    if any(m.kind == "wlan" for m in out.modules):
        out.wlan_note = WLAN_MODULE_FW_VERSION
    # Abwärtskompatible Pin-Hinweise nur wenn Module konfiguriert, aber keines erreichbar
    if any(m.kind == "wlan" for m in out.modules) and not any(
        m.kind == "wlan" and not m.offline for m in out.modules
    ):
        out.info_lines.append(f"wlan_pin:{WLAN_MODULE_FW_VERSION}")
    if any(m.kind == "lan" for m in out.modules) and not any(
        m.kind == "lan" and not m.offline for m in out.modules
    ):
        out.info_lines.append(f"lan_pin:{LAN_MODULE_FW_VERSION}")

    if ctrl is None or not _bus_connected(ctrl):
        out.rotor_ok = True  # kein Bus → kein Rotor-Mismatch-Dialog
        out.info_lines.append("bus_offline")
        return out

    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    enable_az = bool(getattr(ctrl, "enable_az", rb.get("enable_az", True)))
    enable_el = bool(getattr(ctrl, "enable_el", rb.get("enable_el", False)))

    rotor_results: List[AxisVersionResult] = []

    def _check_axis(axis: str, enable: bool, slave_attr: str, cfg_key: str, default_id: int) -> None:
        if not enable:
            return
        st = getattr(ctrl, axis.lower(), None)
        online = bool(getattr(st, "online", False)) if st is not None else False
        try:
            dst = int(getattr(ctrl, slave_attr, rb.get(cfg_key, default_id)) or default_id)
        except Exception:
            dst = default_id
        if dst <= 0:
            return
        if not online:
            # Offline: kein Hinweis, nicht in die Pflicht-Prüfung aufnehmen.
            rotor_results.append(
                AxisVersionResult(
                    axis=axis,
                    expected=ROTOR_FW_VERSION,
                    found=None,
                    ok=True,
                    offline=True,
                )
            )
            return
        raw = _sync_get(ctrl, dst, "GETVERSION", "ACK_GETVERSION")
        found = normalize_version(raw) if raw else None
        # Keine lesbare Version = Achse für den Check nicht erreichbar —
        # kein „Firmware passt nicht“ (nur echte gelesene, zu alte Stände).
        if not found:
            rotor_results.append(
                AxisVersionResult(
                    axis=axis,
                    expected=ROTOR_FW_VERSION,
                    found=None,
                    ok=True,
                    offline=True,
                    query_failed=True,
                )
            )
            return
        # Neuere Rotor-FW als Pin ist OK — Hinweis nur wenn älter.
        ok = version_at_least(found, ROTOR_FW_VERSION)
        rotor_results.append(
            AxisVersionResult(
                axis=axis,
                expected=ROTOR_FW_VERSION,
                found=found,
                ok=ok,
                query_failed=False,
            )
        )

    _check_axis("AZ", enable_az, "slave_az", "slave_az", 20)
    _check_axis("EL", enable_el, "slave_el", "slave_el", 21)
    out.rotor_axes = rotor_results
    # Pflicht-Hinweis nur für online geprüfte Achsen (offline ignorieren).
    checked = [r for r in rotor_results if not r.offline]
    if checked:
        out.rotor_ok = all(r.ok for r in checked)
    else:
        out.rotor_ok = True  # keine online Achse — kein Dialog

    # Controller (optional Info)
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    if bool(chw.get("enabled", True)):
        try:
            cont_id = int(chw.get("cont_id", 2) or 2)
        except Exception:
            cont_id = 2
        if cont_id > 0:
            raw = _sync_get(ctrl, cont_id, "GETCOVERSION", "ACK_GETCOVERSION")
            if raw:
                found = normalize_version(raw)
                out.controller_found = found
                out.controller_ok = versions_equal(found, CONTROLLER_FW_VERSION)
                if out.controller_ok is False:
                    out.info_lines.append(
                        f"controller:{found}:{CONTROLLER_FW_VERSION}"
                    )

    # Inklinometer: GETINCVER nur wenn Typ 4 bekannt/konfiguriert (EL zuerst).
    _inc_candidates: list[tuple[str, bool, str, str, int]] = [
        ("EL", enable_el, "slave_el", "slave_el", 21),
        ("AZ", enable_az, "slave_az", "slave_az", 20),
    ]
    known_et: Optional[int] = None
    try:
        if bool(getattr(ctrl, "encoder_type_known", False)):
            known_et = int(getattr(ctrl, "encoder_type", -1))
    except Exception:
        known_et = None
    want_inc = (known_et == 4) or _inclinometer_configured(ctrl, cfg)
    if want_inc:
        for axis, enable, slave_attr, cfg_key, default_id in _inc_candidates:
            if not enable:
                continue
            st = getattr(ctrl, axis.lower(), None)
            online = bool(getattr(st, "online", False)) if st is not None else False
            if not online:
                continue
            try:
                dst = int(getattr(ctrl, slave_attr, rb.get(cfg_key, default_id)) or default_id)
            except Exception:
                dst = default_id
            if dst <= 0:
                continue
            if known_et != 4:
                et_raw = _sync_get(ctrl, dst, "GETENCTYPE", "ACK_GETENCTYPE", timeout_s=1.0)
                if et_raw is None:
                    continue
                try:
                    et_val = int(str(et_raw).split(";")[0].strip().split(",")[0])
                except Exception:
                    continue
                if et_val != 4:
                    continue
            raw = _sync_get(ctrl, dst, "GETINCVER", "ACK_GETINCVER", timeout_s=1.2)
            if not raw:
                continue
            found = normalize_version(raw)
            out.inclinometer_found = found
            out.inclinometer_axis = axis
            out.inclinometer_ok = bool(found) and version_at_least(
                found, INCLINOMETER_FW_VERSION
            )
            if out.inclinometer_ok is False:
                out.info_lines.append(
                    f"inclinometer:{found or '?'}:{INCLINOMETER_FW_VERSION}:{axis}"
                )
            break
        else:
            out.inclinometer_ok = None
            out.info_lines.append(f"inclinometer_pin:{INCLINOMETER_FW_VERSION}")

    return out
