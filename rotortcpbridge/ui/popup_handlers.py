"""Fehler- und Warnungs-Popup-Logik (Cooldown, einmalige Anzeige)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QMessageBox

from ..rotor_model import error_info, warning_info
from ..i18n import t


class ErrorPopupHandler:
    """Verwaltet Fehler-Popups mit Cooldown."""

    def __init__(self):
        self._last_az: int = 0
        self._last_el: int = 0
        self._recent_code_az: int = 0
        self._recent_code_el: int = 0
        self._recent_time_az: float = 0.0
        self._recent_time_el: float = 0.0
        self._clear_since_az: float = 0.0
        self._clear_since_el: float = 0.0

    def maybe_show(self, parent: QWidget, axis_label: str, current_code: int) -> None:
        import time as _time

        now = float(_time.time())
        cooldown_s = 6.0
        clear_stable_s = 5.0
        try:
            code = int(current_code)
        except Exception:
            code = 0

        is_az = axis_label.upper() == "AZ"
        if is_az:
            last, recent_code = self._last_az, self._recent_code_az
            recent_ts, clear_since = self._recent_time_az, self._clear_since_az
        else:
            last, recent_code = self._last_el, self._recent_code_el
            recent_ts, clear_since = self._recent_time_el, self._clear_since_el

        if code == 0:
            if is_az:
                if clear_since <= 0.0:
                    self._clear_since_az = now
                elif (now - clear_since) >= clear_stable_s:
                    self._last_az = 0
                    self._clear_since_az = 0.0
            else:
                if clear_since <= 0.0:
                    self._clear_since_el = now
                elif (now - clear_since) >= clear_stable_s:
                    self._last_el = 0
                    self._clear_since_el = 0.0
            return

        if is_az:
            self._clear_since_az = 0.0
        else:
            self._clear_since_el = 0.0

        if code == last:
            return
        if code != 0 and code == recent_code and (now - recent_ts) < cooldown_s:
            return

        name, txt = error_info(code)
        title = t("popup.error_title", axis=axis_label.upper())
        msg = t("popup.error_msg", code=code, name=name, txt=txt)

        if is_az:
            self._last_az = code
            self._recent_code_az = code
            self._recent_time_az = now
        else:
            self._last_el = code
            self._recent_code_el = code
            self._recent_time_el = now

        QMessageBox.warning(parent, title, msg, QMessageBox.StandardButton.Ok)


class WarningPopupHandler:
    """Verwaltet Warnungs-Popups mit Cooldown."""

    def __init__(self):
        self._last_az: frozenset[int] = frozenset()
        self._last_el: frozenset[int] = frozenset()
        self._recent_az: frozenset[int] = frozenset()
        self._recent_el: frozenset[int] = frozenset()
        self._recent_time_az: float = 0.0
        self._recent_time_el: float = 0.0
        self._clear_since_az: float = 0.0
        self._clear_since_el: float = 0.0

    def maybe_show(self, parent: QWidget, axis_label: str, axis_state) -> None:
        import time as _time

        now = float(_time.time())
        cooldown_s = 10.0
        stable_clear_s = 8.0

        try:
            cur_set = set(getattr(axis_state, "warnings", set()) or set())
        except Exception:
            cur_set = set()
        cur_set.discard(0)

        is_az = axis_label.upper() == "AZ"
        if is_az:
            last = set(self._last_az)
            recent_set = frozenset(self._recent_az)
            recent_ts = self._recent_time_az
            clear_since = self._clear_since_az
        else:
            last = set(self._last_el)
            recent_set = frozenset(self._recent_el)
            recent_ts = self._recent_time_el
            clear_since = self._clear_since_el

        if not cur_set:
            if last:
                if clear_since <= 0:
                    if is_az:
                        self._clear_since_az = now
                    else:
                        self._clear_since_el = now
                elif (now - clear_since) >= stable_clear_s:
                    if is_az:
                        self._last_az = frozenset()
                        self._clear_since_az = 0.0
                    else:
                        self._last_el = frozenset()
                        self._clear_since_el = 0.0
            return

        if is_az:
            self._clear_since_az = 0.0
        else:
            self._clear_since_el = 0.0

        new_ids = sorted(list(cur_set - last))
        if not new_ids:
            return

        cur_frozen = frozenset(cur_set)
        if cur_frozen == recent_set and (now - recent_ts) < cooldown_s:
            return

        if is_az:
            self._last_az = cur_frozen
            self._recent_az = cur_frozen
            self._recent_time_az = now
        else:
            self._last_el = cur_frozen
            self._recent_el = cur_frozen
            self._recent_time_el = now

        lines = []
        for wid in new_ids:
            name, meaning, todo = warning_info(wid)
            lines.append(
                f"{wid}: {name}\n{t('popup.warn_meaning', meaning=meaning)}\n{t('popup.warn_tip', todo=todo)}"
            )

        title = t("popup.warn_title", axis=axis_label.upper())
        msg = t("popup.warn_msg", lines="\n\n".join(lines))
        QMessageBox.warning(parent, title, msg, QMessageBox.StandardButton.Ok)
