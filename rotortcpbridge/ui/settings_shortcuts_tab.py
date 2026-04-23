"""Einstellungen-Tab: globale Tastenkürzel (Windows)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt


def _modifier_combo(parent: QWidget, current: str) -> QComboBox:
    cb = QComboBox(parent)
    items = (
        ("settings.shortcuts_mod_none", "none"),
        ("settings.shortcuts_mod_alt", "alt"),
        ("settings.shortcuts_mod_ctrl", "control"),
        ("settings.shortcuts_mod_shift", "shift"),
        ("settings.shortcuts_mod_win", "win"),
    )
    for tr_key, data in items:
        cb.addItem(t(tr_key), data)
    cur = (current or "none").strip().lower()
    if cur not in ("none", "alt", "control", "shift", "win"):
        cur = "none"
    for i in range(cb.count()):
        if cb.itemData(i) == cur:
            cb.setCurrentIndex(i)
            break
    return cb


# Zusätzliche Tasten (VK siehe global_hotkeys_win.vk_from_key_spec)
_SPECIAL_HOTKEY_ENTRIES: tuple[tuple[str, str], ...] = (
    ("settings.shortcuts_key_left", "LEFT"),
    ("settings.shortcuts_key_up", "UP"),
    ("settings.shortcuts_key_right", "RIGHT"),
    ("settings.shortcuts_key_down", "DOWN"),
    ("settings.shortcuts_key_page_up", "PRIOR"),
    ("settings.shortcuts_key_page_down", "NEXT"),
    ("settings.shortcuts_key_plus", "OEM_PLUS"),
    ("settings.shortcuts_key_minus", "OEM_MINUS"),
    ("settings.shortcuts_key_numpad_plus", "NUMPAD_ADD"),
    ("settings.shortcuts_key_numpad_minus", "NUMPAD_SUBTRACT"),
)


# F1 … F12 (Label identisch zum Token)
_FUNCTION_KEY_TOKENS: tuple[str, ...] = tuple(f"F{i}" for i in range(1, 13))


def _fill_hotkey_combo(cb: QComboBox) -> None:
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        cb.addItem(c, c)
    for tok in _FUNCTION_KEY_TOKENS:
        cb.addItem(tok, tok)
    for tr_key, data in _SPECIAL_HOTKEY_ENTRIES:
        cb.addItem(t(tr_key), data)
    for d in "0123456789":
        cb.addItem(d, d)


_HOTKEY_SINGLE_CHAR = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_HOTKEY_FUNCTION_KEYS = frozenset(_FUNCTION_KEY_TOKENS)


def _select_hotkey_combo(cb: QComboBox, saved: str, default: str) -> None:
    s = str(saved if saved is not None else default).strip().upper()
    if len(s) == 1 and s not in _HOTKEY_SINGLE_CHAR:
        s = str(default).strip().upper()
    for i in range(cb.count()):
        if str(cb.itemData(i) or "") == s:
            cb.setCurrentIndex(i)
            return
    d = str(default).strip().upper()
    if len(d) == 1 and d in _HOTKEY_SINGLE_CHAR:
        for i in range(cb.count()):
            if cb.itemData(i) == d:
                cb.setCurrentIndex(i)
                return
    cb.setCurrentIndex(0)


def _hotkey_key_combo(parent: QWidget, initial: str) -> QComboBox:
    cb = QComboBox(parent)
    _fill_hotkey_combo(cb)
    _select_hotkey_combo(cb, initial, initial)
    return cb


class ShortcutsTab(QWidget):
    """Konfiguration ``ui.global_shortcuts`` bearbeiten."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        root = QVBoxLayout(self)
        self.chk_enabled = QCheckBox(t("settings.shortcuts_enabled"))
        self.chk_enabled.setToolTip(tt("settings.shortcuts_enabled_tooltip"))
        root.addWidget(self.chk_enabled)

        self._lbl_platform = QLabel()
        self._lbl_platform.setWordWrap(True)
        if sys.platform != "win32":
            self._lbl_platform.setText(t("settings.shortcuts_win_only"))
        root.addWidget(self._lbl_platform)

        g_mod = QGroupBox(t("settings.shortcuts_modifiers_group"))
        fm = QFormLayout(g_mod)
        self.cb_mod1 = _modifier_combo(self, "control")
        self.cb_mod2 = _modifier_combo(self, "shift")
        self.cb_mod1.setToolTip(tt("settings.shortcuts_modifier_tooltip"))
        self.cb_mod2.setToolTip(tt("settings.shortcuts_modifier_tooltip"))
        fm.addRow(t("settings.shortcuts_modifier_slot1"), self.cb_mod1)
        fm.addRow(t("settings.shortcuts_modifier_slot2"), self.cb_mod2)
        self._lbl_mod_hint = QLabel(t("settings.shortcuts_modifiers_hint"))
        self._lbl_mod_hint.setWordWrap(True)
        fm.addRow(self._lbl_mod_hint)
        root.addWidget(g_mod)

        g_rot = QGroupBox(t("settings.shortcuts_group_rotor"))
        fl = QFormLayout(g_rot)
        self.cb_w = _hotkey_key_combo(self, "UP")
        self.cb_d = _hotkey_key_combo(self, "RIGHT")
        self.cb_s = _hotkey_key_combo(self, "DOWN")
        self.cb_a = _hotkey_key_combo(self, "LEFT")
        self.sp_deg_w = QDoubleSpinBox()
        self.sp_deg_d = QDoubleSpinBox()
        self.sp_deg_s = QDoubleSpinBox()
        self.sp_deg_a = QDoubleSpinBox()
        for sp in (self.sp_deg_w, self.sp_deg_d, self.sp_deg_s, self.sp_deg_a):
            sp.setRange(0.0, 359.99)
            sp.setDecimals(2)
            sp.setSingleStep(1.0)
            sp.setSuffix("°")
        fl.addRow(t("settings.shortcuts_target_angle_1"), self._row_key_deg(self.cb_w, self.sp_deg_w))
        fl.addRow(t("settings.shortcuts_target_angle_2"), self._row_key_deg(self.cb_d, self.sp_deg_d))
        fl.addRow(t("settings.shortcuts_target_angle_3"), self._row_key_deg(self.cb_s, self.sp_deg_s))
        fl.addRow(t("settings.shortcuts_target_angle_4"), self._row_key_deg(self.cb_a, self.sp_deg_a))
        root.addWidget(g_rot)

        g_win = QGroupBox(t("settings.shortcuts_group_windows"))
        f2 = QFormLayout(g_win)
        self.cb_k = _hotkey_key_combo(self, "K")
        self.cb_m = _hotkey_key_combo(self, "M")
        self.cb_h = _hotkey_key_combo(self, "H")
        f2.addRow(t("settings.shortcuts_open_compass"), self.cb_k)
        f2.addRow(t("settings.shortcuts_open_map"), self.cb_m)
        f2.addRow(t("settings.shortcuts_open_elevation"), self.cb_h)
        root.addWidget(g_win)

        g_ant = QGroupBox(t("settings.shortcuts_group_antenna"))
        f_ant = QFormLayout(g_ant)
        self.cb_ant1 = _hotkey_key_combo(self, "1")
        self.cb_ant2 = _hotkey_key_combo(self, "2")
        self.cb_ant3 = _hotkey_key_combo(self, "3")
        self._lbl_ant_shortcut_1 = QLabel()
        self._lbl_ant_shortcut_2 = QLabel()
        self._lbl_ant_shortcut_3 = QLabel()
        self._lbl_ant_shortcut = (
            self._lbl_ant_shortcut_1,
            self._lbl_ant_shortcut_2,
            self._lbl_ant_shortcut_3,
        )
        f_ant.addRow(self._lbl_ant_shortcut_1, self.cb_ant1)
        f_ant.addRow(self._lbl_ant_shortcut_2, self.cb_ant2)
        f_ant.addRow(self._lbl_ant_shortcut_3, self.cb_ant3)
        root.addWidget(g_ant)

        g_step = QGroupBox(t("settings.shortcuts_group_target_step"))
        f3 = QFormLayout(g_step)
        self.sp_step = QDoubleSpinBox()
        self.sp_step.setRange(0.1, 180.0)
        self.sp_step.setDecimals(1)
        self.sp_step.setSingleStep(1.0)
        self.sp_step.setSuffix("°")
        f3.addRow(t("settings.shortcuts_target_step_deg"), self.sp_step)
        self.cb_e = _hotkey_key_combo(self, "PRIOR")
        self.cb_q = _hotkey_key_combo(self, "NEXT")
        f3.addRow(t("settings.shortcuts_target_plus"), self.cb_e)
        f3.addRow(t("settings.shortcuts_target_minus"), self.cb_q)
        root.addWidget(g_step)

        self._g_el = QGroupBox(t("settings.shortcuts_group_el_target_step"))
        f_el = QFormLayout(self._g_el)
        self.sp_el_step = QDoubleSpinBox()
        self.sp_el_step.setRange(0.1, 90.0)
        self.sp_el_step.setDecimals(1)
        self.sp_el_step.setSingleStep(1.0)
        self.sp_el_step.setSuffix("°")
        self.cb_el_plus = _hotkey_key_combo(self, "R")
        self.cb_el_minus = _hotkey_key_combo(self, "F")
        f_el.addRow(t("settings.shortcuts_el_target_step_deg"), self.sp_el_step)
        f_el.addRow(t("settings.shortcuts_el_target_plus"), self.cb_el_plus)
        f_el.addRow(t("settings.shortcuts_el_target_minus"), self.cb_el_minus)
        root.addWidget(self._g_el)

        root.addStretch(1)
        self._load_from_cfg()
        self.refresh_el_visibility()
        self.refresh_antenna_shortcut_row_labels()
        for _cb in self._hotkey_combos_all():
            _cb.currentIndexChanged.connect(self._on_hotkey_combo_changed)
        self._refresh_hotkey_duplicate_ui()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        sw = self._settings_window()
        if sw is not None and hasattr(sw, "maybe_refresh_antenna_names_for_shortcuts_tab"):
            sw.maybe_refresh_antenna_names_for_shortcuts_tab()
        QTimer.singleShot(0, self.refresh_antenna_shortcut_row_labels)
        QTimer.singleShot(400, self.refresh_antenna_shortcut_row_labels)

    def _settings_window(self):
        w = self.parent()
        while w is not None:
            if hasattr(w, "_antenna_name_edits_az") and hasattr(
                w, "maybe_refresh_antenna_names_for_shortcuts_tab"
            ):
                return w
            w = w.parent()
        return None

    def _antenna_display_names_three(self) -> list[str]:
        sw = self._settings_window()
        out: list[str] = []
        for i in range(3):
            name = ""
            if sw is not None:
                try:
                    eds = getattr(sw, "_antenna_name_edits_az", None)
                    if eds and i < len(eds):
                        name = eds[i].text().strip()
                except Exception:
                    name = ""
            if not name:
                ui = self._cfg.get("ui") or {}
                names = list(ui.get("antenna_names") or [])
                if i < len(names):
                    name = str(names[i]).strip()
            if not name:
                name = t(f"settings.antenna_{i + 1}")
            out.append(name)
        return out

    def refresh_antenna_shortcut_row_labels(self) -> None:
        """Zeilenbeschriftung: gewählte Taste + Antennenname (Controller/Config)."""
        names = self._antenna_display_names_three()
        for i, (lbl, cb) in enumerate(
            zip(self._lbl_ant_shortcut, (self.cb_ant1, self.cb_ant2, self.cb_ant3))
        ):
            key = self._hotkey_token(cb)
            nm = names[i] if i < len(names) else t(f"settings.antenna_{i + 1}")
            lbl.setText(f"{key} — {nm}")

    def _hotkey_combos_all(self) -> tuple[QComboBox, ...]:
        """Alle Tasten-Dropdowns (gleiche Modifier-Ebene); Reihenfolge für Duplikat-Auflösung."""
        return (
            self.cb_w,
            self.cb_d,
            self.cb_s,
            self.cb_a,
            self.cb_k,
            self.cb_m,
            self.cb_h,
            self.cb_ant1,
            self.cb_ant2,
            self.cb_ant3,
            self.cb_e,
            self.cb_q,
            self.cb_el_plus,
            self.cb_el_minus,
        )

    def _hotkey_token(self, cb: QComboBox) -> str:
        return str(cb.currentData() or "").strip().upper()

    def _resolve_hotkey_duplicates(self) -> None:
        """Nacheinander: erste Zuordnung gewinnt, spätere Felder auf erste freie Taste setzen."""
        used: set[str] = set()
        for cb in self._hotkey_combos_all():
            d = self._hotkey_token(cb)
            if d and d in used:
                for i in range(cb.count()):
                    nd = str(cb.itemData(i) or "").strip().upper()
                    if nd and nd not in used:
                        cb.setCurrentIndex(i)
                        d = nd
                        break
            if d:
                used.add(d)

    def _apply_hotkey_item_enable_state(self) -> None:
        """Bereits vergebene Tasten in den anderen Dropdowns deaktivieren."""
        combos = self._hotkey_combos_all()
        for cb in combos:
            model = cb.model()
            if not isinstance(model, QStandardItemModel):
                continue
            my = self._hotkey_token(cb)
            others = {self._hotkey_token(c) for c in combos if c is not cb}
            for row in range(cb.count()):
                d = str(cb.itemData(row) or "").strip().upper()
                item = model.item(row)
                if item is None:
                    continue
                item.setEnabled((d not in others) or d == my)

    def _refresh_hotkey_duplicate_ui(self) -> None:
        combos = self._hotkey_combos_all()
        for c in combos:
            c.blockSignals(True)
        try:
            self._resolve_hotkey_duplicates()
            self._apply_hotkey_item_enable_state()
        finally:
            for c in combos:
                c.blockSignals(False)

    def _on_hotkey_combo_changed(self, _index: int) -> None:
        self._refresh_hotkey_duplicate_ui()
        self.refresh_antenna_shortcut_row_labels()

    def retranslate_hotkey_combo_texts(self) -> None:
        """Nach Sprachwechsel: Beschriftungen der Sondertasten in allen Combos aktualisieren."""
        for cb in (
            self.cb_w,
            self.cb_d,
            self.cb_s,
            self.cb_a,
            self.cb_k,
            self.cb_m,
            self.cb_h,
            self.cb_e,
            self.cb_q,
            self.cb_ant1,
            self.cb_ant2,
            self.cb_ant3,
            self.cb_el_plus,
            self.cb_el_minus,
        ):
            # Reihenfolge in _fill_hotkey_combo: 26 Buchstaben, dann F1–F12,
            # dann Sondertasten (Pfeile/Bild/+/−/Num-+/Num-−), dann 10 Ziffern.
            offset = 26 + len(_FUNCTION_KEY_TOKENS)
            for i, (tr_key, _data) in enumerate(_SPECIAL_HOTKEY_ENTRIES):
                cb.setItemText(offset + i, t(tr_key))
        self._refresh_hotkey_duplicate_ui()
        self.refresh_antenna_shortcut_row_labels()

    @staticmethod
    def _row_key_deg(cb: QComboBox, sp: QDoubleSpinBox) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(cb)
        h.addWidget(sp)
        return w

    def refresh_el_visibility(self) -> None:
        el_on: bool | None = None
        w = self.parent()
        while w is not None:
            if hasattr(w, "chk_enable_el"):
                try:
                    el_on = bool(w.chk_enable_el.isChecked())
                except Exception:
                    el_on = None
                break
            w = w.parent()
        if el_on is None:
            el_on = bool((self._cfg.get("rotor_bus") or {}).get("enable_el", False))
        self._g_el.setVisible(el_on)

    def _load_from_cfg(self) -> None:
        gs = (self._cfg.get("ui") or {}).get("global_shortcuts") or {}
        self.chk_enabled.setChecked(bool(gs.get("enabled", True)))
        for cb, key, default in (
            (self.cb_mod1, "modifier_1", "control"),
            (self.cb_mod2, "modifier_2", "shift"),
        ):
            cur = str(gs.get(key, default) or default).strip().lower()
            if cur not in ("none", "alt", "control", "shift", "win"):
                cur = default
            for i in range(cb.count()):
                if cb.itemData(i) == cur:
                    cb.setCurrentIndex(i)
                    break
        self.sp_deg_w.setValue(float(gs.get("antenna_deg_w", 0.0)))
        self.sp_deg_d.setValue(float(gs.get("antenna_deg_d", 90.0)))
        self.sp_deg_s.setValue(float(gs.get("antenna_deg_s", 180.0)))
        self.sp_deg_a.setValue(float(gs.get("antenna_deg_a", 270.0)))
        for cb, key, default in (
            (self.cb_w, "key_win_alt_w", "UP"),
            (self.cb_d, "key_win_alt_d", "RIGHT"),
            (self.cb_s, "key_win_alt_s", "DOWN"),
            (self.cb_a, "key_win_alt_a", "LEFT"),
            (self.cb_k, "key_win_alt_compass", "K"),
            (self.cb_m, "key_win_alt_map", "M"),
            (self.cb_h, "key_win_alt_elevation", "H"),
            (self.cb_e, "key_ctrl_alt_plus", "PRIOR"),
            (self.cb_q, "key_ctrl_alt_minus", "NEXT"),
        ):
            _select_hotkey_combo(cb, str(gs.get(key, default)), default)
        self.sp_step.setValue(float(gs.get("target_step_deg", 3.0)))
        self.sp_el_step.setValue(float(gs.get("el_target_step_deg", 5.0)))
        for cb, key, default in (
            (self.cb_el_plus, "key_el_target_plus", "R"),
            (self.cb_el_minus, "key_el_target_minus", "F"),
        ):
            _select_hotkey_combo(cb, str(gs.get(key, default)), default)
        for cb, key, default in (
            (self.cb_ant1, "key_antenna_1", "1"),
            (self.cb_ant2, "key_antenna_2", "2"),
            (self.cb_ant3, "key_antenna_3", "3"),
        ):
            _select_hotkey_combo(cb, str(gs.get(key, default)), default)

    def apply_to_cfg(self, cfg: dict) -> None:
        gs = cfg.setdefault("ui", {}).setdefault("global_shortcuts", {})
        gs["enabled"] = bool(self.chk_enabled.isChecked())
        gs["modifier_1"] = self.cb_mod1.currentData()
        gs["modifier_2"] = self.cb_mod2.currentData()
        gs["antenna_deg_w"] = float(self.sp_deg_w.value())
        gs["antenna_deg_d"] = float(self.sp_deg_d.value())
        gs["antenna_deg_s"] = float(self.sp_deg_s.value())
        gs["antenna_deg_a"] = float(self.sp_deg_a.value())
        gs["key_win_alt_w"] = self.cb_w.currentData()
        gs["key_win_alt_d"] = self.cb_d.currentData()
        gs["key_win_alt_s"] = self.cb_s.currentData()
        gs["key_win_alt_a"] = self.cb_a.currentData()
        gs["key_win_alt_compass"] = self.cb_k.currentData()
        gs["key_win_alt_map"] = self.cb_m.currentData()
        gs["key_win_alt_elevation"] = self.cb_h.currentData()
        gs["key_ctrl_alt_plus"] = self.cb_e.currentData()
        gs["key_ctrl_alt_minus"] = self.cb_q.currentData()
        gs["target_step_deg"] = float(self.sp_step.value())
        gs["el_target_step_deg"] = float(self.sp_el_step.value())
        gs["key_el_target_plus"] = self.cb_el_plus.currentData()
        gs["key_el_target_minus"] = self.cb_el_minus.currentData()
        gs["key_antenna_1"] = self.cb_ant1.currentData()
        gs["key_antenna_2"] = self.cb_ant2.currentData()
        gs["key_antenna_3"] = self.cb_ant3.currentData()
