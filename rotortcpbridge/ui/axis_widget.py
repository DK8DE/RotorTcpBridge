"""Achsen-Panel (AZ/EL) mit Position, LEDs, Fehler, PWM-Slider."""
from __future__ import annotations

import math
import time as _time_mod

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .led_widget import Led
from .ui_utils import px_to_dip
from ..i18n import t

from ..rotor_model import WARNINGS, error_info


def _make_axis_panel(
    parent_box: QGroupBox,
    axis: str,
    controller,
) -> dict:
    """Erzeugt die Statusanzeige für eine Achse (AZ/EL). Gibt fields-Dict zurück."""
    form = QFormLayout(parent_box)

    def ro():
        e = QLineEdit()
        e.setReadOnly(True)
        return e

    def pair(label_text: str, field: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        lab = QLabel(label_text)
        lab.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(lab, 0)
        h.addWidget(field, 1)
        return w

    def row2(a_label: str, a_field: QWidget, b_label: str, b_field: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        h.addWidget(pair(a_label, a_field), 1)
        h.addWidget(pair(b_label, b_field), 1)
        return w

    def status_widget(label_text: str) -> tuple[QWidget, Led]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        lab = QLabel(label_text)
        lab.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        led = Led(px_to_dip(parent_box, 12), parent_box)
        led_wrap = QWidget()
        led_layout = QVBoxLayout(led_wrap)
        led_layout.setContentsMargins(0, 2, 0, 0)
        led_layout.addWidget(led)
        h.addWidget(led_wrap, 0)
        h.addWidget(lab, 0)
        return w, led

    def pwm_widget() -> tuple[QWidget, QSlider, QLabel]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 100)
        s.setSingleStep(1)
        s.setPageStep(5)
        lab = QLabel("0")
        lab.setFixedWidth(px_to_dip(parent_box, 36))
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(s, 1)
        h.addWidget(lab, 0)
        return w, s, lab

    fields = {
        "pos": ro(),
        "target": ro(),
        "ref_led": None,
        "moving_led": None,
        "online_led": None,
        "err": ro(),
        "warn": ro(),
        "tempa": ro(),
        "tempm": ro(),
        "wind": ro(),
        "winddir": ro(),
        "wind_pair_w": None,
        "winddir_pair_w": None,
        "pwm_slider": None,
        "pwm_val": None,
    }

    pwm_w, pwm_slider, pwm_val = pwm_widget()
    fields["pwm_slider"] = pwm_slider
    fields["pwm_val"] = pwm_val
    fields["_pwm_hold_until"] = 0.0

    axis_l = str(axis or "").lower().strip()
    if axis_l not in ("az", "el"):
        axis_l = "az"

    pending: dict[str, float | None] = {"v": None}
    send_timer = QTimer(parent_box)
    send_timer.setSingleShot(True)

    def _send_pwm(v: float) -> None:
        try:
            if axis_l == "az":
                controller.set_pwm_az(v)
            else:
                controller.set_pwm_el(v)
        except Exception:
            pass

    def _flush_send() -> None:
        v = pending.get("v")
        if v is not None:
            _send_pwm(float(v))

    def _schedule_send(v: float) -> None:
        pending["v"] = float(v)
        send_timer.start(150)

    def _set_hold():
        try:
            fields["_pwm_hold_until"] = float(_time_mod.time()) + 1.0
        except Exception:
            pass

    def _on_value_changed(val: int):
        try:
            pwm_val.setText(f"{int(val)}")
        except Exception:
            pass

    def _on_user_set(val: int):
        _set_hold()
        _schedule_send(float(int(val)))

    send_timer.timeout.connect(_flush_send)
    try:
        def _on_released():
            _set_hold()  # Nach Loslassen Hold verlängern
            pending["v"] = float(pwm_slider.value())  # Aktuellen Wert senden
            try:
                send_timer.stop()
            except Exception:
                pass
            _flush_send()
        pwm_slider.sliderReleased.connect(_on_released)
    except Exception:
        pass
    try:
        def _on_action(_action: int):
            _on_user_set(pwm_slider.value())
        pwm_slider.actionTriggered.connect(_on_action)
    except Exception:
        pass
    try:
        pwm_slider.sliderMoved.connect(_on_user_set)
    except Exception:
        pass
    pwm_slider.valueChanged.connect(_on_value_changed)

    status_row = QWidget()
    status_h = QHBoxLayout(status_row)
    status_h.setContentsMargins(0, 0, 0, 0)
    status_h.setSpacing(10)
    status_h.addWidget(pair(t("axis.pos_label"), fields["pos"]), 2)
    status_h.addWidget(pair(t("axis.target_label"), fields["target"]), 2)

    ref_w, ref_led = status_widget(t("axis.ref_label"))
    mv_w, mv_led = status_widget(t("axis.moving_label"))
    on_w, on_led = status_widget(t("axis.online_label"))
    fields["ref_led"] = ref_led
    fields["moving_led"] = mv_led
    fields["online_led"] = on_led
    status_h.addWidget(ref_w, 0)
    status_h.addWidget(mv_w, 0)
    status_h.addWidget(on_w, 0)
    form.addRow(status_row)

    if axis_l == "az":
        env_row = QWidget()
        env_h = QHBoxLayout(env_row)
        env_h.setContentsMargins(0, 0, 0, 0)
        env_h.setSpacing(10)
        env_h.addWidget(pair(t("axis.temp_ambient_label"), fields["tempa"]), 1)
        env_h.addWidget(pair(t("axis.temp_motor_label"), fields["tempm"]), 1)
        fields["wind_pair_w"] = pair(t("axis.wind_label"), fields["wind"])
        fields["winddir_pair_w"] = pair(t("axis.winddir_label"), fields["winddir"])
        env_h.addWidget(fields["wind_pair_w"], 1)
        env_h.addWidget(fields["winddir_pair_w"], 1)
        form.addRow(env_row)
    else:
        form.addRow(row2(t("axis.temp_ambient_label"), fields["tempa"], t("axis.temp_motor_label"), fields["tempm"]))

    form.addRow(t("axis.motorspeed_label"), pwm_w)
    form.addRow(t("axis.messages_label"), row2(t("axis.err_label"), fields["err"], t("axis.warn_label"), fields["warn"]))

    return fields


def fill_axis_panel(fields: dict, axis_state) -> None:
    """Aktualisiert die Anzeige eines Axis-Panels mit axis_state."""
    now = float(_time_mod.time())
    p = float(axis_state.get_smoothed_pos_d10f(now))
    fields["pos"].setText(f"{p / 10.0:.1f}")
    fields["target"].setText(f"{axis_state.target_d10 / 10:.1f}")

    offline = not bool(getattr(axis_state, "online", False))
    try:
        if fields.get("ref_led") is not None:
            fields["ref_led"].set_state(False if offline else bool(axis_state.referenced))
    except Exception:
        pass
    try:
        if fields.get("moving_led") is not None:
            fields["moving_led"].set_state(False if offline else bool(axis_state.moving))
    except Exception:
        pass
    try:
        if fields.get("online_led") is not None:
            fields["online_led"].set_state(bool(axis_state.online))
    except Exception:
        pass

    try:
        pwm_slider = fields.get("pwm_slider")
        if pwm_slider is not None:
            pwm_slider.setEnabled(not offline)
    except Exception:
        pass

    if offline:
        fields["err"].setText(t("axis.none"))
        fields["warn"].setText(t("axis.none"))
        fields["tempa"].setText("0.0")
        fields["tempm"].setText("0.0")
        fields["wind"].setText("0.0")
        fields["winddir"].setText("0.0")
        try:
            pwm_slider = fields.get("pwm_slider")
            pwm_val = fields.get("pwm_val")
            if pwm_slider is not None:
                pwm_slider.blockSignals(True)
                pwm_slider.setValue(0)
                pwm_slider.blockSignals(False)
            if pwm_val is not None:
                pwm_val.setText("0")
        except Exception:
            pass
        return

    err_name, _ = error_info(axis_state.error_code)
    if axis_state.error_code == 0:
        fields["err"].setText(t("axis.none"))
    else:
        fields["err"].setText(f"{axis_state.error_code} {err_name}")

    warn_list = sorted(list(axis_state.warnings))
    if warn_list:
        parts = []
        for wid in warn_list[:12]:
            wn = WARNINGS.get(wid, ("SW_UNKNOWN", "Unbekannt", "-"))
            parts.append(f"{wid}:{wn[0]}")
        fields["warn"].setText("; ".join(parts))
    else:
        fields["warn"].setText(t("axis.none"))

    tel = axis_state.telemetry
    fields["tempa"].setText("" if tel.temp_ambient_c is None else f"{tel.temp_ambient_c:.1f}")
    fields["tempm"].setText("" if tel.temp_motor_c is None else f"{tel.temp_motor_c:.1f}")
    fields["wind"].setText("" if tel.wind_kmh is None else f"{tel.wind_kmh:.1f}")
    fields["winddir"].setText("" if tel.wind_dir_deg is None else f"{tel.wind_dir_deg:.1f}")

    try:
        pwm_slider = fields.get("pwm_slider")
        pwm_val = fields.get("pwm_val")
        if pwm_slider is not None:
            min_ok = tel.pwm_min_pct is not None
            if min_ok:
                mn = float(tel.pwm_min_pct)
                mn = max(0.0, min(100.0, mn))
                try:
                    pwm_slider.setMinimum(int(math.ceil(mn)))
                except Exception:
                    pwm_slider.setMinimum(int(round(mn)))
            else:
                pwm_slider.setMinimum(0)
            pwm_slider.setEnabled(bool(axis_state.online) and (not offline) and bool(min_ok))

        hold_until = float(fields.get("_pwm_hold_until", 0.0) or 0.0)
        if pwm_slider is not None and (not pwm_slider.isSliderDown()) and (_time_mod.time() >= hold_until):
            if tel.pwm_max_pct is not None:
                v = float(tel.pwm_max_pct)
                iv = 100 if v >= 99.5 else int(round(v))
                cur = pwm_slider.value()
                # 100 nicht mit 99 überschreiben (Firmware nutzt oft 0-99, 99 = 100%)
                if iv == 99 and cur == 100:
                    pass  # User-Wunsch 100 beibehalten
                else:
                    pwm_slider.blockSignals(True)
                    pwm_slider.setValue(max(0, min(100, iv)))
                    pwm_slider.blockSignals(False)
                    if pwm_val is not None:
                        pwm_val.setText(f"{iv:d}")
    except Exception:
        pass
