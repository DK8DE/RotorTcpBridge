"""Globale Tastenkürzel unter Windows (RegisterHotKey / WM_HOTKEY).

Nur ``sys.platform == "win32"``; andere Plattformen: No-Op.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from PySide6.QtCore import QByteArray

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MOD_NAME_TO_FLAG: Dict[str, int] = {
    "none": 0,
    "alt": MOD_ALT,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


def modifiers_mask_from_config(gs: dict) -> int:
    """Zwei frei wählbare Modifikatoren (modifier_1, modifier_2) zu RegisterHotKey-Flags."""
    def _one(key: str, default: str) -> str:
        v = gs.get(key, default)
        if v is None:
            return default
        s = str(v).strip().lower()
        if s in ("", "none", "-", "—"):
            return "none"
        if s in _MOD_NAME_TO_FLAG:
            return s
        return default

    m1 = _one("modifier_1", "shift")
    m2 = _one("modifier_2", "alt")
    out = int(_MOD_NAME_TO_FLAG.get(m1, 0)) | int(_MOD_NAME_TO_FLAG.get(m2, 0))
    if out == 0:
        out = MOD_SHIFT | MOD_ALT
    return out | MOD_NOREPEAT


def vk_from_letter(ch: str) -> int:
    u = (ch or "A").strip().upper()[:1]
    if not u or not ("A" <= u <= "Z"):
        return ord("A")
    return ord(u)


def _voidptr_to_int(message: Any) -> int:
    try:
        return int(message)
    except Exception:
        pass
    try:
        from PySide6 import shiboken6  # type: ignore

        if shiboken6.isValid(message):
            ptrs = shiboken6.getCppPointer(message)
            if ptrs:
                return int(ptrs[0])
    except Exception:
        pass
    return 0


class GlobalHotkeyController:
    """Registriert Hotkeys am HWND des Hauptfensters und liefert ``nativeEvent``-Handler."""

    _BASE_ID = 0xA700

    def __init__(
        self,
        hwnd_getter: Callable[[], int],
        on_hotkey: Callable[[str], None],
    ) -> None:
        self._hwnd_getter = hwnd_getter
        self._on_hotkey = on_hotkey
        self._registered_ids: List[int] = []
        self._id_to_action: Dict[int, str] = {}
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._RegisterHotKey = self._user32.RegisterHotKey
            self._RegisterHotKey.argtypes = [
                wintypes.HWND,
                wintypes.INT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            self._RegisterHotKey.restype = wintypes.BOOL
            self._UnregisterHotKey = self._user32.UnregisterHotKey
            self._UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
            self._UnregisterHotKey.restype = wintypes.BOOL
        else:
            self._user32 = None

    def unregister_all(self) -> None:
        if sys.platform != "win32" or not self._user32:
            return
        try:
            hwnd = int(self._hwnd_getter())
        except Exception:
            hwnd = 0
        if not hwnd:
            self._registered_ids.clear()
            self._id_to_action.clear()
            return
        for hid in list(self._registered_ids):
            try:
                self._UnregisterHotKey(hwnd, hid)
            except Exception:
                pass
        self._registered_ids.clear()
        self._id_to_action.clear()

    def apply_config(self, cfg: dict) -> None:
        """Konfiguration ``ui.global_shortcuts`` lesen und Hotkeys neu anlegen."""
        self.unregister_all()
        if sys.platform != "win32" or not self._user32:
            return
        ui = cfg.get("ui") or {}
        gs = ui.get("global_shortcuts") or {}
        if not bool(gs.get("enabled", True)):
            return
        try:
            hwnd = int(self._hwnd_getter())
        except Exception:
            return
        if not hwnd:
            return

        mods_all = modifiers_mask_from_config(gs)
        specs: List[Tuple[str, int, int]] = []
        # Reihenfolge: feste IDs pro Aktion — gleiche Modifikatoren für alle
        def add(action: str, letter: str) -> None:
            specs.append((action, mods_all, vk_from_letter(letter)))

        add("rot_w", str(gs.get("key_win_alt_w", "W")))
        add("rot_d", str(gs.get("key_win_alt_d", "D")))
        add("rot_s", str(gs.get("key_win_alt_s", "S")))
        add("rot_a", str(gs.get("key_win_alt_a", "A")))
        add("open_compass", str(gs.get("key_win_alt_compass", "K")))
        add("open_map", str(gs.get("key_win_alt_map", "M")))
        add("open_elevation", str(gs.get("key_win_alt_elevation", "H")))
        add("target_plus", str(gs.get("key_ctrl_alt_plus", "E")))
        add("target_minus", str(gs.get("key_ctrl_alt_minus", "Q")))
        rb = cfg.get("rotor_bus") or {}
        if bool(rb.get("enable_el", False)):
            add("el_target_plus", str(gs.get("key_el_target_plus", "R")))
            add("el_target_minus", str(gs.get("key_el_target_minus", "F")))

        seen: set[Tuple[int, int]] = set()
        n = 0
        for action, mods, vk in specs:
            key = ((mods & ~MOD_NOREPEAT) & 0xFFFF, vk & 0xFFFF)
            if key in seen:
                continue
            seen.add(key)
            hid = self._BASE_ID + n
            n += 1
            try:
                ok = bool(self._RegisterHotKey(hwnd, hid, mods | MOD_NOREPEAT, vk))
                if not ok:
                    ok = bool(self._RegisterHotKey(hwnd, hid, mods, vk))
            except Exception:
                ok = False
            if ok:
                self._registered_ids.append(hid)
                self._id_to_action[hid] = action

    def process_native_event(self, event_type: Any, message: Any) -> Optional[Tuple[bool, int]]:
        """Für ``QWidget.nativeEvent`` / Event-Filter: ``(True, 0)`` wenn WM_HOTKEY verarbeitet."""
        if sys.platform != "win32" or not self._user32:
            return None
        et = event_type
        try:
            et_b = bytes(et)
        except Exception:
            try:
                et_b = et if isinstance(et, (bytes, bytearray)) else str(et).encode()
            except Exception:
                et_b = b""
        if et_b != b"windows_generic_MSG":
            return None
        msg_ptr = _voidptr_to_int(message)
        if not msg_ptr:
            return None

        class _POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class _MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", _POINT),
            ]

        try:
            msg = ctypes.cast(msg_ptr, ctypes.POINTER(_MSG)).contents
        except Exception:
            return None
        if int(msg.message) != WM_HOTKEY:
            return None
        hid = int(msg.wParam)
        action = self._id_to_action.get(hid)
        if not action:
            return None
        try:
            self._on_hotkey(action)
        except Exception:
            pass
        return True, 0
