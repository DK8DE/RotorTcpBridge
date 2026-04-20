"""Wrapper um ``setupc.exe`` (com0com Null-Modem-Treiber).

Die App selbst läuft ohne Admin-Rechte. ``setupc.exe`` braucht aber Admin
für alle schreibenden Aktionen (und zuverlässig auch für ``list``), deshalb
werden alle Aufrufe über ``ShellExecuteExW(verb="runas")`` eleviert; der
stdout wird via ``--output <tempfile>`` eingesammelt (ShellExecute liefert
keine Pipe).

Plattform: Windows. Auf anderen Systemen liefern die Funktionen
``is_installed()==False`` bzw. leere Paar-Liste.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")


class Com0ComError(RuntimeError):
    """Fehler beim Aufruf von ``setupc.exe``.

    Enthält Exit-Code und eingesammelten stdout/stderr-Text.
    """

    def __init__(self, message: str, exit_code: int = -1, output: str = ""):
        super().__init__(message)
        self.exit_code = int(exit_code)
        self.output = str(output or "")


@dataclass
class Com0ComPair:
    """Ein Paar aus der com0com-Ausgabe.

    ``index`` ist die Paar-Nummer n aus ``CNCAn``/``CNCBn``. ``side_a_name``
    und ``side_b_name`` sind die per ``PortName=`` zugewiesenen Windows-COM-
    Namen (z. B. ``COM20``); ``-`` wenn kein Name gesetzt ist.
    ``real_a`` / ``real_b`` enthalten den tatsächlichen Namen, falls
    ``PortName=COM#`` verwendet wurde (RealPortName=...).
    """

    index: int
    side_a_name: str = "-"
    side_b_name: str = "-"
    real_a: str = ""
    real_b: str = ""

    @property
    def effective_a(self) -> str:
        """Sichtbarer Port-Name für Seite A (RealPortName bevorzugt)."""
        return self.real_a if self.real_a else self.side_a_name

    @property
    def effective_b(self) -> str:
        return self.real_b if self.real_b else self.side_b_name


# ---------------------------------------------------------------- Discovery


_FALLBACK_PATHS = (
    r"C:\Program Files (x86)\com0com\setupc.exe",
    r"C:\Program Files\com0com\setupc.exe",
)

_REG_UNINSTALL_PATHS = (
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\com0com",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\com0com",
)


def find_setupc() -> Optional[Path]:
    """Pfad zu ``setupc.exe`` suchen (Registry + Standardpfade)."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # type: ignore
    except Exception:
        winreg = None  # type: ignore

    if winreg is not None:
        for sub in _REG_UNINSTALL_PATHS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:  # type: ignore[attr-defined]
                    loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                    p = Path(str(loc)) / "setupc.exe"
                    if p.is_file():
                        return p
            except OSError:
                continue
            except Exception:
                continue

    for fp in _FALLBACK_PATHS:
        p = Path(fp)
        if p.is_file():
            return p
    return None


def is_installed() -> bool:
    """True, wenn ``setupc.exe`` auffindbar ist."""
    return find_setupc() is not None


# -------------------------------------------------------------- Ausführung


def _ensure_setupc() -> Path:
    p = find_setupc()
    if p is None:
        raise Com0ComError(
            "com0com ist nicht installiert oder setupc.exe wurde nicht gefunden."
        )
    return p


def _run_plain(exe: Path, args: List[str], timeout_s: float = 20.0) -> Tuple[int, str]:
    """Direkter Aufruf ohne Elevation (funktioniert nur, wenn der Aufrufer
    bereits Admin-Rechte besitzt). Liefert (returncode, combined_output).
    """
    flags = 0
    if IS_WINDOWS:
        flags = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_s,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise Com0ComError(f"setupc.exe Timeout: {exc}") from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _run_elevated(exe: Path, args: List[str], timeout_s: float = 30.0) -> Tuple[int, str]:
    """``setupc.exe`` via ShellExecuteExW(verb="runas") mit UAC-Prompt.

    stdout/stderr werden nicht gepipet (das geht mit ShellExecute nicht),
    stattdessen wird ``--output <tempfile>`` an die Argumente gehängt und
    die Datei nach Ende gelesen.
    """
    if not IS_WINDOWS:
        raise Com0ComError("Elevated-Ausführung wird nur unter Windows unterstützt.")

    import ctypes
    from ctypes import wintypes

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="setupc_", suffix=".txt")
    os.close(tmp_fd)
    try:
        full_args = ["--output", tmp_path, *args]
        params = " ".join(_quote_arg(a) for a in full_args)

        # SHELLEXECUTEINFOW
        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SEE_MASK_FLAG_NO_UI = 0x00000400
        SW_HIDE = 0

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = str(exe)
        sei.lpParameters = params
        sei.lpDirectory = str(exe.parent)
        sei.nShow = SW_HIDE
        sei.hInstApp = None

        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.get_last_error()
            # 1223 = ERROR_CANCELLED (UAC abgelehnt)
            if err == 1223:
                raise Com0ComError("UAC-Prompt wurde abgebrochen.", exit_code=1223)
            raise Com0ComError(
                f"ShellExecuteExW fehlgeschlagen (GetLastError={err}).",
                exit_code=err,
            )

        h_proc = sei.hProcess
        if not h_proc:
            return 0, ""

        # Auf Prozessende warten
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102
        ms = int(max(1.0, timeout_s) * 1000)
        rc = kernel32.WaitForSingleObject(h_proc, ms)
        exit_code = ctypes.c_ulong(0)
        if rc == WAIT_OBJECT_0:
            kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code))
        else:
            # Timeout: Prozess weiter laufen lassen, aber Fehler melden
            kernel32.CloseHandle(h_proc)
            raise Com0ComError(
                f"setupc.exe Timeout nach {timeout_s:.1f}s (läuft evtl. noch).",
                exit_code=int(WAIT_TIMEOUT),
            )
        kernel32.CloseHandle(h_proc)

        # Kleiner Schlag damit die Datei komplett geflusht ist
        time.sleep(0.05)
        try:
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                out = f.read()
        except Exception:
            out = ""

        return int(exit_code.value), out
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _quote_arg(a: str) -> str:
    """Windows-Argumentquoting für ShellExecute."""
    s = str(a)
    if not s:
        return '""'
    if any(c in s for c in (" ", "\t", '"')):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run(exe: Path, args: List[str], timeout_s: float = 30.0) -> Tuple[int, str]:
    """Plain falls Admin, sonst elevated."""
    if _is_admin():
        return _run_plain(exe, args, timeout_s=timeout_s)
    return _run_elevated(exe, args, timeout_s=timeout_s)


# ---------------------------------------------------------------- Parsing


# Typische Zeilen:
#   CNCA0 PortName=COM20
#   CNCB0 PortName=COM#,RealPortName=COM7
#   CNCB1 PortName=-
_LINE_RE = re.compile(r"^\s*(CNCA|CNCB)(\d+)\s+(.*)$")

# Registry-Ort, an dem com0com seine Paare als Unterschlüssel ablegt.
# Der Schlüssel ist standardmäßig für alle Nutzer lesbar — damit können wir
# die Paare ohne Admin-Rechte / UAC-Prompt auflisten.
_REG_PARAMETERS_PATH = r"SYSTEM\CurrentControlSet\Services\com0com\Parameters"


def _detect_real_ports_via_pyserial() -> dict[str, str]:
    """Abbildung ``CNCA0``/``CNCB0`` → echter COM-Name via pyserial.

    com0com hängt seine Geräte mit Hardware-ID ``COM0COM\\PORT&CNCA0`` etc.
    ein; die Port-Namen (z. B. ``COM50``) stehen in ``list_ports`` unter
    ``device``. Das funktioniert komplett ohne Adminrechte.
    """
    result: dict[str, str] = {}
    try:
        from serial.tools import list_ports  # pyserial
    except Exception:
        return result
    try:
        ports = list(list_ports.comports())
    except Exception:
        return result
    for p in ports:
        haystack = f"{getattr(p, 'hwid', '') or ''} {getattr(p, 'description', '') or ''}".upper()
        m = re.search(r"CNC([AB])(\d+)", haystack)
        if not m:
            continue
        side_id = f"CNC{m.group(1)}{m.group(2)}"
        dev = getattr(p, "device", "") or ""
        if dev:
            result[side_id] = dev
    return result


def _list_pairs_from_registry() -> List[Com0ComPair]:
    """Paare direkt aus der Registry lesen (keine Admin-Rechte nötig)."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg  # type: ignore
    except Exception:
        return []

    pairs: dict[int, Com0ComPair] = {}
    try:
        # 64-bit Hive explizit öffnen — der Treiber läuft 64-bittig.
        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)  # type: ignore[attr-defined]
        root = winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_LOCAL_MACHINE, _REG_PARAMETERS_PATH, 0, access  # type: ignore[attr-defined]
        )
    except OSError:
        return []
    except Exception:
        return []

    try:
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, i)  # type: ignore[attr-defined]
            except OSError:
                break
            i += 1
            m = re.match(r"^CNC([AB])(\d+)$", sub_name)
            if not m:
                continue
            side = m.group(1)
            try:
                idx = int(m.group(2))
            except ValueError:
                continue
            try:
                sub = winreg.OpenKey(root, sub_name, 0, access)  # type: ignore[attr-defined]
            except OSError:
                continue
            port_name = "-"
            real_name = ""
            try:
                try:
                    v, _ = winreg.QueryValueEx(sub, "PortName")  # type: ignore[attr-defined]
                    port_name = str(v) if v else "-"
                except OSError:
                    pass
                try:
                    v, _ = winreg.QueryValueEx(sub, "RealPortName")  # type: ignore[attr-defined]
                    real_name = str(v) if v else ""
                except OSError:
                    pass
            finally:
                try:
                    winreg.CloseKey(sub)  # type: ignore[attr-defined]
                except Exception:
                    pass

            pair = pairs.setdefault(idx, Com0ComPair(index=idx))
            if side == "A":
                pair.side_a_name = port_name or "-"
                pair.real_a = real_name or ""
            else:
                pair.side_b_name = port_name or "-"
                pair.real_b = real_name or ""
    finally:
        try:
            winreg.CloseKey(root)  # type: ignore[attr-defined]
        except Exception:
            pass

    # Wenn ``PortName=COM#`` steht, hat Windows den tatsächlichen Namen
    # automatisch vergeben — den holen wir aus pyserial/list_ports.
    real_map = _detect_real_ports_via_pyserial()
    for p in pairs.values():
        key_a = f"CNCA{p.index}"
        key_b = f"CNCB{p.index}"
        if not p.real_a and key_a in real_map:
            p.real_a = real_map[key_a]
        if not p.real_b and key_b in real_map:
            p.real_b = real_map[key_b]

    # „Geister-Paare" aussortieren: setupc lässt beim Remove manchmal
    # Registry-Einträge mit ``PortName=COM#`` ohne realen Port zurück.
    # Solche leeren Einträge blenden wir aus – sonst erscheinen sie in
    # der UI mit vier Mal "COM#" ohne sinnvolle Bedeutung.
    def _is_ghost(p: Com0ComPair) -> bool:
        def _empty(name: str, real: str) -> bool:
            n = (name or "").strip().upper()
            r = (real or "").strip()
            if r:
                return False
            return n in ("", "-", "COM#")

        return _empty(p.side_a_name, p.real_a) and _empty(p.side_b_name, p.real_b)

    return [pairs[k] for k in sorted(pairs.keys()) if not _is_ghost(pairs[k])]


def _parse_list_output(text: str) -> List[Com0ComPair]:
    pairs: dict[int, Com0ComPair] = {}
    for raw in (text or "").splitlines():
        m = _LINE_RE.match(raw)
        if not m:
            continue
        side = m.group(1)  # 'CNCA' oder 'CNCB'
        try:
            idx = int(m.group(2))
        except ValueError:
            continue
        rest = m.group(3).strip()
        params = {}
        for part in rest.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip()] = v.strip()
        port_name = params.get("PortName", "-").strip() or "-"
        real_name = params.get("RealPortName", "").strip()

        pair = pairs.setdefault(idx, Com0ComPair(index=idx))
        if side == "CNCA":
            pair.side_a_name = port_name
            pair.real_a = real_name
        else:
            pair.side_b_name = port_name
            pair.real_b = real_name
    return [pairs[k] for k in sorted(pairs.keys())]


# --------------------------------------------------------------- Public API


def list_pairs() -> List[Com0ComPair]:
    """Existierende com0com-Paare zurückliefern (leer wenn nicht installiert).

    Primäre Quelle ist die Registry (``HKLM\\SYSTEM\\CurrentControlSet\\
    Services\\com0com\\Parameters``) — für Lesen braucht es keine
    Administratorrechte, es erscheint **kein UAC-Prompt**.

    Fallback: ``setupc.exe list`` als plain-Call (ebenfalls ohne
    Elevation). Bei Fehler einfach leere Liste.
    """
    if not IS_WINDOWS:
        return []

    # 1. Versuch: Registry lesen (schnell, ohne UAC)
    try:
        pairs = _list_pairs_from_registry()
    except Exception:
        pairs = []
    if pairs:
        return pairs

    # 2. Fallback: setupc.exe ohne Elevation — falls Registry-Layout
    #    ungewöhnlich ist oder Schlüssel nicht lesbar war.
    try:
        exe = _ensure_setupc()
    except Com0ComError:
        return []
    try:
        _rc, out = _run_plain(exe, ["--silent", "list"], timeout_s=10.0)
    except Com0ComError:
        return []
    except Exception:
        return []
    return _parse_list_output(out)


def install_pair(port_a: str = "COM#", port_b: str = "COM#") -> Tuple[int, str]:
    """Neues Paar anlegen.

    Standardmäßig lässt ``PortName=COM#`` Windows eine freie COM-Nummer
    wählen; über ``PortName=COM20`` lässt sich ein fester Name setzen.
    """
    exe = _ensure_setupc()
    args = [
        "--silent",
        "install",
        f"PortName={port_a or 'COM#'}",
        f"PortName={port_b or 'COM#'}",
    ]
    rc, out = _run(exe, args, timeout_s=60.0)
    if rc != 0:
        raise Com0ComError(
            f"install fehlgeschlagen (rc={rc}).", exit_code=rc, output=out
        )
    return rc, out


def remove_pair(index: int) -> Tuple[int, str]:
    """Paar mit Nummer ``n`` entfernen (A und B gemeinsam)."""
    exe = _ensure_setupc()
    rc, out = _run(exe, ["--silent", "remove", str(int(index))], timeout_s=60.0)
    if rc != 0:
        raise Com0ComError(
            f"remove {index} fehlgeschlagen (rc={rc}).", exit_code=rc, output=out
        )
    return rc, out


def change_port_name(side_id: str, new_name: str) -> Tuple[int, str]:
    """Port-Name einer Seite ändern. ``side_id`` z. B. ``CNCA0`` oder ``CNCB0``."""
    exe = _ensure_setupc()
    side_id = str(side_id or "").strip().upper()
    if not re.match(r"^CNC[AB]\d+$", side_id):
        raise Com0ComError(f"Ungültige side_id: {side_id!r}")
    new_name = str(new_name or "").strip() or "COM#"
    rc, out = _run(
        exe,
        ["--silent", "change", side_id, f"PortName={new_name}"],
        timeout_s=60.0,
    )
    if rc != 0:
        raise Com0ComError(
            f"change {side_id} fehlgeschlagen (rc={rc}).", exit_code=rc, output=out
        )
    return rc, out
