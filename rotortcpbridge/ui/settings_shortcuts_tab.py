"""Einstellungen-Tab: globale Tastenkürzel (Windows)."""

from __future__ import annotations

import sys

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


def _letter_combo(parent: QWidget, current: str) -> QComboBox:
    cb = QComboBox(parent)
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        cb.addItem(c, c)
    u = (current or "A").strip().upper()[:1]
    if u not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        u = "A"
    cb.setCurrentIndex(ord(u) - ord("A"))
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
        self.cb_mod1 = _modifier_combo(self, "shift")
        self.cb_mod2 = _modifier_combo(self, "alt")
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
        self.cb_w = _letter_combo(self, "W")
        self.cb_d = _letter_combo(self, "D")
        self.cb_s = _letter_combo(self, "S")
        self.cb_a = _letter_combo(self, "A")
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
        self.cb_k = _letter_combo(self, "K")
        self.cb_m = _letter_combo(self, "M")
        self.cb_h = _letter_combo(self, "H")
        f2.addRow(t("settings.shortcuts_open_compass"), self.cb_k)
        f2.addRow(t("settings.shortcuts_open_map"), self.cb_m)
        f2.addRow(t("settings.shortcuts_open_elevation"), self.cb_h)
        root.addWidget(g_win)

        g_step = QGroupBox(t("settings.shortcuts_group_target_step"))
        f3 = QFormLayout(g_step)
        self.sp_step = QDoubleSpinBox()
        self.sp_step.setRange(0.1, 180.0)
        self.sp_step.setDecimals(1)
        self.sp_step.setSingleStep(1.0)
        self.sp_step.setSuffix("°")
        f3.addRow(t("settings.shortcuts_target_step_deg"), self.sp_step)
        self.cb_e = _letter_combo(self, "E")
        self.cb_q = _letter_combo(self, "Q")
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
        self.cb_el_plus = _letter_combo(self, "R")
        self.cb_el_minus = _letter_combo(self, "F")
        f_el.addRow(t("settings.shortcuts_el_target_step_deg"), self.sp_el_step)
        f_el.addRow(t("settings.shortcuts_el_target_plus"), self.cb_el_plus)
        f_el.addRow(t("settings.shortcuts_el_target_minus"), self.cb_el_minus)
        root.addWidget(self._g_el)

        root.addStretch(1)
        self._load_from_cfg()
        self.refresh_el_visibility()

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
            (self.cb_mod1, "modifier_1", "shift"),
            (self.cb_mod2, "modifier_2", "alt"),
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
        for cb, key in (
            (self.cb_w, "key_win_alt_w"),
            (self.cb_d, "key_win_alt_d"),
            (self.cb_s, "key_win_alt_s"),
            (self.cb_a, "key_win_alt_a"),
            (self.cb_k, "key_win_alt_compass"),
            (self.cb_m, "key_win_alt_map"),
            (self.cb_h, "key_win_alt_elevation"),
            (self.cb_e, "key_ctrl_alt_plus"),
            (self.cb_q, "key_ctrl_alt_minus"),
        ):
            u = str(gs.get(key, "A")).strip().upper()[:1]
            if u not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                u = "A"
            cb.setCurrentIndex(ord(u) - ord("A"))
        self.sp_step.setValue(float(gs.get("target_step_deg", 5.0)))
        self.sp_el_step.setValue(float(gs.get("el_target_step_deg", 5.0)))
        for cb, key, default in (
            (self.cb_el_plus, "key_el_target_plus", "R"),
            (self.cb_el_minus, "key_el_target_minus", "F"),
        ):
            u = str(gs.get(key, default)).strip().upper()[:1]
            if u not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                u = default
            cb.setCurrentIndex(ord(u) - ord("A"))

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
