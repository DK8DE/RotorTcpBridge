"""Achsen-Panel (AZ/EL) mit Position, Telemetrie-Labels, LEDs, PWM-Slider."""

from __future__ import annotations

import math
import time as _time_mod

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .led_widget import Led
from .ui_utils import px_to_dip
from ..angle_utils import wrap_deg
from ..i18n import t


def _style_axis_value_label(lab: QLabel) -> None:
    """Werte im AZ/EL-Bereich fett hervorheben."""
    f = lab.font()
    f.setBold(True)
    lab.setFont(f)


def _label_text_pixel_width(fm, text: str) -> int:
    """Textbreite für Spaltenlayout (boundingRect ist zuverlässiger als horizontalAdvance bei ° u. a.)."""
    if not text:
        return 0
    return int(fm.boundingRect(text).width())


_AXIS_DESC_LABEL_KEYS = frozenset(
    {
        "axis.pos_label",
        "axis.target_label",
        "axis.temp_ambient_label",
        "axis.temp_motor_label",
        "axis.motorspeed_label",
    }
)


def _refresh_axis_table_desc_min_widths(parent_box: QGroupBox, pairs: list) -> None:
    """Nach setText / Sprachwechsel Mindestbreiten der Tabellen-Beschriftungen anpassen."""
    fm = parent_box.fontMetrics()
    pad = px_to_dip(parent_box, 8)
    extra_motor = px_to_dip(parent_box, 14)
    for item in pairs:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        lab, tkey = item[0], item[1]
        if lab is None or not isinstance(tkey, str) or tkey not in _AXIS_DESC_LABEL_KEYS:
            continue
        try:
            ex = extra_motor if tkey == "axis.temp_motor_label" else 0
            sh = (
                px_to_dip(parent_box, 20)
                if tkey in ("axis.target_label", "axis.temp_motor_label")
                else 0
            )
            lab.setMinimumWidth(_label_text_pixel_width(fm, lab.text()) + pad + ex + sh)
        except Exception:
            pass


def _align_axis_led_status_columns(
    parent_box: QGroupBox,
    lbl_ref: QLabel,
    lbl_moving: QLabel,
    lbl_online: QLabel,
    ref_w: QWidget,
    mv_w: QWidget,
    on_w: QWidget,
    ref_left_extra: int = 0,
) -> None:
    """Gleiche Spaltenbreiten für Ref / Fährt / Online (Tabellen-Optik, AZ und EL untereinander fluchtend)."""
    fm = parent_box.fontMetrics()
    keys = ("axis.ref_label", "axis.moving_label", "axis.online_label")
    # boundingRect zuverlässiger als horizontalAdvance (Umlaute, Homing vs. Ref)
    texts = [t(k) for k in keys]
    max_lab = max(_label_text_pixel_width(fm, tx) for tx in texts)
    extra_txt = px_to_dip(parent_box, 4)
    max_lab = max_lab + extra_txt
    max_lab = max(max_lab, _label_text_pixel_width(fm, "Home") + px_to_dip(parent_box, 4))
    led_sz = px_to_dip(parent_box, 12)
    gap = px_to_dip(parent_box, 3)
    pad = px_to_dip(parent_box, 1)
    col_w = led_sz + gap + max_lab + pad
    for lbl in (lbl_ref, lbl_moving, lbl_online):
        lbl.setFixedWidth(max_lab)
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    mv_w.setFixedWidth(col_w)
    on_w.setFixedWidth(col_w)
    ref_w.setFixedWidth(col_w + int(ref_left_extra))
    for w in (ref_w, mv_w, on_w):
        w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)


def _bold_value_font_metrics(parent_box: QGroupBox) -> QFontMetrics:
    """FontMetrics wie die fett gesetzten Wert-Labels."""
    f = QFont(parent_box.font())
    f.setBold(True)
    return QFontMetrics(f)


def _value_cell_min_width(parent_box: QGroupBox, indent_dip: int) -> int:
    """Mindestbreite für eine Wert-Zelle: Einzug + fette Ziffern + kleiner Puffer."""
    fm_val = _bold_value_font_metrics(parent_box)
    pad = px_to_dip(parent_box, 5)
    extra = px_to_dip(parent_box, 4)
    num_w = _label_text_pixel_width(fm_val, "888.8")
    return int(indent_dip + num_w + pad + extra)


def _apply_value_min_widths(_parent_box: QGroupBox, _fields: dict) -> None:
    """Mindestbreiten kommen aus den Raster-Spalten (_apply_axis_table_column_widths)."""
    return


def _apply_axis_table_column_widths(grid: QGridLayout, parent_box: QGroupBox) -> None:
    """Bezeichner-Spalten fest; Wert-Spalten nur knappe Mindestbreite — weiterer Platz über Stretch (Spalten 1/3).

    Spalte 0 und 2 getrennt: sonst wäre Spalte 0 so breit wie „Motor °C“ / Motorspeed (zu viel Abstand zu Pos/°C).
    """
    fm = parent_box.fontMetrics()
    pad_lab = px_to_dip(parent_box, 6)
    extra_motor_lab = px_to_dip(parent_box, 10)

    def _label_col_w(k: str) -> int:
        w = _label_text_pixel_width(fm, t(k)) + pad_lab
        if k == "axis.temp_motor_label":
            w += extra_motor_lab
        return w

    # Motorspeed sitzt unter dem Raster — nicht in die Spaltenminima mischen
    w_lab_col0 = max(
        _label_col_w("axis.pos_label"),
        _label_col_w("axis.temp_ambient_label"),
    )
    w_lab_col2 = max(
        _label_col_w("axis.target_label"),
        _label_col_w("axis.temp_motor_label"),
    )
    # Einzug der Werte (wie val_label) — für Mindestbreite der Wert-Spalten
    ind_pos = max(0, px_to_dip(parent_box, 10) - px_to_dip(parent_box, 15))
    ind_ziel_motor = max(0, px_to_dip(parent_box, 30) - px_to_dip(parent_box, 15))
    w_val_col1 = _value_cell_min_width(parent_box, ind_pos)
    w_val_col3 = _value_cell_min_width(parent_box, ind_ziel_motor)
    grid.setColumnMinimumWidth(0, int(w_lab_col0))
    grid.setColumnMinimumWidth(2, int(w_lab_col2))
    grid.setColumnMinimumWidth(1, int(w_val_col1))
    grid.setColumnMinimumWidth(3, int(w_val_col3))


def _make_axis_panel(
    parent_box: QGroupBox,
    axis: str,
    controller,
) -> dict:
    """Erzeugt die Statusanzeige für eine Achse (AZ/EL). Gibt fields-Dict zurück."""
    outer = QVBoxLayout(parent_box)
    try:
        outer.setContentsMargins(
            px_to_dip(parent_box, 8),
            px_to_dip(parent_box, 4),
            px_to_dip(parent_box, 8),
            px_to_dip(parent_box, 4),
        )
    except Exception:
        pass
    outer.setSpacing(px_to_dip(parent_box, 6))

    data_grid = QGridLayout()
    data_grid.setContentsMargins(0, 0, 0, 0)
    data_grid.setHorizontalSpacing(px_to_dip(parent_box, 4))
    data_grid.setVerticalSpacing(px_to_dip(parent_box, 6))

    def val_label(*, indent_dip: int | None = None) -> QLabel:
        """Anzeige-Wert (Pos, Ziel, °C …) als Label, linksbündig wie eine Tabellenzelle."""
        v = QLabel("")
        v.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Minimum: nicht unter Mindestbreite schrumpfen; Expanding weglassen (weniger Überdeckung)
        v.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        v.setWordWrap(False)
        # Einzug im Wertfeld; ~15px weniger Abstand Label↔Zahl als zuvor (10→0 bzw. 30→15 DIP-Parameter)
        ind = px_to_dip(parent_box, 10) if indent_dip is None else indent_dip
        v.setIndent(ind)
        _style_axis_value_label(v)
        return v

    def desc_label(
        text: str,
        *,
        extra_min_pad: int = 0,
        shift_right_dip: int = 0,
    ) -> QLabel:
        """Beschriftung linksbündig; wächst mit der Spalte, damit nichts abgeschnitten wird."""
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lab.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred
        )
        lab.setWordWrap(False)
        if shift_right_dip:
            lab.setIndent(shift_right_dip)
        fm = parent_box.fontMetrics()
        lab.setMinimumWidth(
            _label_text_pixel_width(fm, text)
            + px_to_dip(parent_box, 6)
            + extra_min_pad
            + shift_right_dip
        )
        return lab

    def status_widget(
        label_text: str, *, left_margin: int = 0, right_margin: int = 0
    ) -> tuple[QWidget, Led, QLabel]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(left_margin, 0, right_margin, 0)
        h.setSpacing(4)
        lab = QLabel(label_text)
        # Preferred: nicht auf Minimum schrumpfen (Maximum clippte „Homing“ am Rand)
        lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        led = Led(px_to_dip(parent_box, 12), parent_box)
        led_wrap = QWidget()
        led_layout = QVBoxLayout(led_wrap)
        led_layout.setContentsMargins(0, 2, 0, 0)
        led_layout.addWidget(led)
        h.addWidget(led_wrap, 0)
        h.addWidget(lab, 0)
        return w, led, lab

    def pwm_widget() -> tuple[QSlider, QLabel]:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 100)
        s.setSingleStep(1)
        s.setPageStep(5)
        lab = QLabel("0")
        lab.setFixedWidth(px_to_dip(parent_box, 36))
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        _style_axis_value_label(lab)
        return s, lab

    _val_indent_std = max(0, px_to_dip(parent_box, 10) - px_to_dip(parent_box, 15))
    _val_indent_ziel_motor = max(0, px_to_dip(parent_box, 30) - px_to_dip(parent_box, 15))
    fields = {
        "pos": val_label(indent_dip=_val_indent_std),
        "target": val_label(indent_dip=_val_indent_ziel_motor),
        "ref_led": None,
        "moving_led": None,
        "online_led": None,
        "tempa": val_label(indent_dip=_val_indent_std),
        "tempm": val_label(indent_dip=_val_indent_ziel_motor),
        "pwm_slider": None,
        "pwm_val": None,
    }

    pwm_slider, pwm_val = pwm_widget()
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

    _pair_shift_dip = px_to_dip(parent_box, 16)
    lbl_pos = desc_label(t("axis.pos_label"))
    lbl_target = desc_label(
        t("axis.target_label"),
        shift_right_dip=_pair_shift_dip,
    )

    _led_gap = px_to_dip(parent_box, 4)
    mv_w, mv_led, lbl_moving = status_widget(t("axis.moving_label"))
    ref_w, ref_led, lbl_ref = status_widget(t("axis.ref_label"), left_margin=_led_gap)
    on_w, on_led, lbl_online = status_widget(t("axis.online_label"))
    fields["ref_led"] = ref_led
    fields["moving_led"] = mv_led
    fields["online_led"] = on_led

    _led_block_shift = px_to_dip(parent_box, 10)
    spacer_led_block = QWidget()
    spacer_led_block.setFixedWidth(_led_block_shift)
    spacer_led_block.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    _al_led = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    # Zeile 0: Pos / Ziel | Abstand | Fährt/Ref; Online in Zeile 1 — linksbündig, nicht auf Zellenbreite strecken
    data_grid.addWidget(lbl_pos, 0, 0)
    data_grid.addWidget(fields["pos"], 0, 1)
    data_grid.addWidget(lbl_target, 0, 2)
    data_grid.addWidget(fields["target"], 0, 3)
    data_grid.addWidget(spacer_led_block, 0, 4, 2, 1, _al_led)
    data_grid.addWidget(mv_w, 0, 5, 1, 1, _al_led)
    data_grid.addWidget(ref_w, 0, 6, 1, 1, _al_led)
    data_grid.addWidget(on_w, 1, 5, 1, 2, _al_led)
    # Freien Platz in die Wert-Spalten legen (zwischen Bezeichner und LED-Block verteilen)
    data_grid.setColumnStretch(1, 1)
    data_grid.setColumnStretch(3, 1)
    data_grid.setColumnStretch(7, 0)

    fields["_led_status_align"] = (
        parent_box,
        lbl_ref,
        lbl_moving,
        lbl_online,
        ref_w,
        mv_w,
        on_w,
        _led_gap,
    )
    _align_axis_led_status_columns(
        parent_box, lbl_ref, lbl_moving, lbl_online, ref_w, mv_w, on_w, _led_gap
    )

    # Explizite (QLabel, Übersetzungsschlüssel)-Liste — zuverlässiger als Dict-Lookups
    i18n_pairs: list[tuple[QLabel, str]] = [
        (lbl_pos, "axis.pos_label"),
        (lbl_target, "axis.target_label"),
        (lbl_moving, "axis.moving_label"),
        (lbl_ref, "axis.ref_label"),
        (lbl_online, "axis.online_label"),
    ]

    if axis_l == "az":
        lbl_ta = desc_label(t("axis.temp_ambient_label"))
        lbl_tm = desc_label(
            t("axis.temp_motor_label"),
            extra_min_pad=px_to_dip(parent_box, 14),
            shift_right_dip=_pair_shift_dip,
        )
        data_grid.addWidget(lbl_ta, 1, 0)
        data_grid.addWidget(fields["tempa"], 1, 1)
        data_grid.addWidget(lbl_tm, 1, 2)
        data_grid.addWidget(fields["tempm"], 1, 3)
        i18n_pairs.extend(
            [
                (lbl_ta, "axis.temp_ambient_label"),
                (lbl_tm, "axis.temp_motor_label"),
            ]
        )
    else:
        lbl_ta = desc_label(t("axis.temp_ambient_label"))
        lbl_tm = desc_label(
            t("axis.temp_motor_label"),
            extra_min_pad=px_to_dip(parent_box, 14),
            shift_right_dip=_pair_shift_dip,
        )
        data_grid.addWidget(lbl_ta, 1, 0)
        data_grid.addWidget(fields["tempa"], 1, 1)
        data_grid.addWidget(lbl_tm, 1, 2)
        data_grid.addWidget(fields["tempm"], 1, 3)
        i18n_pairs.extend(
            [
                (lbl_ta, "axis.temp_ambient_label"),
                (lbl_tm, "axis.temp_motor_label"),
            ]
        )

    _apply_axis_table_column_widths(data_grid, parent_box)
    _apply_value_min_widths(parent_box, fields)
    fields["_axis_data_grid"] = data_grid
    fields["_axis_data_grid_parent"] = parent_box

    outer.addLayout(data_grid)

    lbl_pwm = desc_label(t("axis.motorspeed_label"))
    pwm_row = QWidget()
    pwm_h = QHBoxLayout(pwm_row)
    pwm_h.setContentsMargins(0, 0, 0, 0)
    pwm_h.setSpacing(6)
    pwm_h.addWidget(lbl_pwm, 0)
    pwm_h.addWidget(pwm_slider, 1)
    pwm_h.addWidget(pwm_val, 0)
    outer.addWidget(pwm_row)
    i18n_pairs.append((lbl_pwm, "axis.motorspeed_label"))

    fields["_i18n_pairs"] = i18n_pairs
    return fields


def retranslate_axis_panel(fields: dict) -> None:
    """Aktualisiert alle Achsen-Labels nach Sprachwechsel."""
    pairs = fields.get("_i18n_pairs")
    if isinstance(pairs, list) and pairs:
        for lab, tkey in pairs:
            try:
                if lab is not None and isinstance(tkey, str):
                    lab.setText(t(tkey))
            except Exception:
                pass
        dgp = fields.get("_axis_data_grid_parent")
        if dgp is not None:
            try:
                _refresh_axis_table_desc_min_widths(dgp, pairs)
            except Exception:
                pass
        align = fields.get("_led_status_align")
        if align is not None:
            try:
                _align_axis_led_status_columns(*align)
            except Exception:
                pass
        dg = fields.get("_axis_data_grid")
        dgp_grid = fields.get("_axis_data_grid_parent")
        if dg is not None and dgp_grid is not None:
            try:
                _apply_axis_table_column_widths(dg, dgp_grid)
            except Exception:
                pass
        if dgp_grid is not None:
            try:
                _apply_value_min_widths(dgp_grid, fields)
            except Exception:
                pass
        return
    # Fallback: ältere Dict-Struktur (falls noch vorhanden)
    m = fields.get("_i18n")
    if not isinstance(m, dict):
        return
    mapping: list[tuple[str, str]] = [
        ("pos", "axis.pos_label"),
        ("target", "axis.target_label"),
        ("ref", "axis.ref_label"),
        ("moving", "axis.moving_label"),
        ("online", "axis.online_label"),
        ("tempa", "axis.temp_ambient_label"),
        ("tempm", "axis.temp_motor_label"),
        ("motorspeed_row", "axis.motorspeed_label"),
    ]
    for key, tkey in mapping:
        lab = m.get(key)
        if lab is not None:
            try:
                lab.setText(t(tkey))
            except Exception:
                pass
    align = fields.get("_led_status_align")
    if align is not None:
        try:
            _align_axis_led_status_columns(*align)
        except Exception:
            pass
    dg = fields.get("_axis_data_grid")
    dgp = fields.get("_axis_data_grid_parent")
    if dg is not None and dgp is not None:
        try:
            _apply_axis_table_column_widths(dg, dgp)
        except Exception:
            pass
    if dgp is not None:
        try:
            _apply_value_min_widths(dgp, fields)
        except Exception:
            pass


def fill_axis_panel(fields: dict, axis_state) -> None:
    """Aktualisiert die Anzeige eines Axis-Panels mit axis_state."""
    now = float(_time_mod.time())
    p = float(axis_state.get_smoothed_pos_d10f(now))
    pos_deg = p / 10.0
    wrap_az = bool(getattr(axis_state, "position_wrap_360", False))
    if wrap_az:
        pos_deg = wrap_deg(pos_deg)
    fields["pos"].setText(f"{pos_deg:.1f}")
    if bool(getattr(axis_state, "referenced", False)):
        tgt_deg = axis_state.target_d10 / 10.0
        if wrap_az:
            tgt_deg = wrap_deg(tgt_deg)
        fields["target"].setText(f"{tgt_deg:.1f}")
    else:
        fields["target"].setText("–")

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
        fields["tempa"].setText("0.0")
        fields["tempm"].setText("0.0")
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

    tel = axis_state.telemetry
    fields["tempa"].setText("" if tel.temp_ambient_c is None else f"{tel.temp_ambient_c:.1f}")
    fields["tempm"].setText("" if tel.temp_motor_c is None else f"{tel.temp_motor_c:.1f}")

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
        if (
            pwm_slider is not None
            and (not pwm_slider.isSliderDown())
            and (_time_mod.time() >= hold_until)
        ):
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
