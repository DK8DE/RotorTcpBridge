"""Statistik-Fenster mit Last-Ringen (CAL/Langzeit/Aktuell) für AZ und EL."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QShowEvent, QCloseEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy, QGroupBox

from ..app_icon import get_app_icon
from ..i18n import t
from ..compass.statistic_compass_widget import StatisticCompassWidget


def _make_stat_row(cal_w: StatisticCompassWidget, live_w: StatisticCompassWidget,
                   acc_w: StatisticCompassWidget) -> QHBoxLayout:
    """Erstellt eine Zeile mit 3 Statistik-Kompassen (skalieren mit Fenster)."""
    row = QHBoxLayout()
    row.setSpacing(12)
    for lbl, w in [(t("stats.label_cal"), cal_w), (t("stats.label_longterm"), live_w), (t("stats.label_current"), acc_w)]:
        box = QFrame()
        box.setFrameStyle(QFrame.Shape.StyledPanel)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        l = QVBoxLayout(box)
        l.setContentsMargins(10, 10, 10, 10)
        l.addWidget(QLabel(lbl), alignment=Qt.AlignmentFlag.AlignCenter)
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        w.setMinimumSize(120, 120)
        l.addWidget(w, 1)  # stretch=1, füllt verfügbaren Platz
        row.addWidget(box, 1)
    return row


class StatisticsWindow(QDialog):
    """Fenster mit Statistik-Kompassen: AZ (Vollkreis), ggf. EL (Viertelkreis)."""

    def __init__(self, cfg: dict, controller, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.setWindowTitle(t("stats.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.setMinimumSize(520, 380)
        self.resize(700, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        slave_az = cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = cfg.get("rotor_bus", {}).get("slave_el", "?")

        # AZ-Zeile (Vollkreis)
        gb_az = QGroupBox(f"AZ ID:{slave_az}")
        gb_az.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stat_cal = StatisticCompassWidget(elevation=False)
        self.stat_live = StatisticCompassWidget(elevation=False)
        self.stat_placeholder = StatisticCompassWidget(elevation=False)
        az_row = _make_stat_row(self.stat_cal, self.stat_live, self.stat_placeholder)
        gb_az.setLayout(az_row)
        root.addWidget(gb_az, 1)  # stretch=1, skaliert mit Fenster

        # EL-Zeile (Viertelkreis), sichtbar wenn EL aktiv
        self.gb_el = QGroupBox(f"EL ID:{slave_el}")
        self.gb_el.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stat_cal_el = StatisticCompassWidget(elevation=True)
        self.stat_live_el = StatisticCompassWidget(elevation=True)
        self.stat_placeholder_el = StatisticCompassWidget(elevation=True)
        el_row = _make_stat_row(self.stat_cal_el, self.stat_live_el, self.stat_placeholder_el)
        self.gb_el.setLayout(el_row)
        root.addWidget(self.gb_el, 1)  # stretch=1, skaliert mit Fenster

        self._update_el_visibility()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # Timer nur starten wenn Fenster gezeigt wird (showEvent)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        el_on = bool(getattr(self.ctrl, "enable_el", False))
        self.resize(1000, 1080 if el_on else 540)
        if hasattr(self.ctrl, "set_statistics_window_open"):
            self.ctrl.set_statistics_window_open(True)
        if hasattr(self.ctrl, "request_immediate_stats"):
            self.ctrl.request_immediate_stats()  # Priorität 0, sofort vor GETPOSDG
        self._timer.start(200)
        self._tick()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        if hasattr(self.ctrl, "set_statistics_window_open"):
            self.ctrl.set_statistics_window_open(False)
        super().closeEvent(event)

    def _update_el_visibility(self) -> None:
        """EL-Zeile anzeigen/ausblenden je nach enable_el."""
        el_on = bool(getattr(self.ctrl, "enable_el", False))
        self.gb_el.setVisible(el_on)

    @Slot()
    def _tick(self) -> None:
        try:
            self._update_el_visibility()

            az = self.ctrl.az
            cal_state = getattr(az, "cal_state", 0)
            live_cw = getattr(az, "live_bins_cw", None)
            live_ccw = getattr(az, "live_bins_ccw", None)

            if cal_state == 2:
                self.stat_cal.set_bins(
                    getattr(az, "cal_bins_cw", None),
                    getattr(az, "cal_bins_ccw", None),
                )
            else:
                self.stat_cal.set_bins(live_cw, live_ccw)

            self.stat_live.set_bins(live_cw, live_ccw)
            acc_cw = getattr(az, "acc_bins_cw", None)
            acc_ccw = getattr(az, "acc_bins_ccw", None)
            self.stat_placeholder.set_bins(acc_cw, acc_ccw)

            # EL-Daten wenn aktiv
            el_on = bool(getattr(self.ctrl, "enable_el", False))
            if el_on:
                el = getattr(self.ctrl, "el", None)
                if el is not None:
                    el_cal = getattr(el, "cal_state", 0)
                    el_live_cw = getattr(el, "live_bins_cw", None)
                    el_live_ccw = getattr(el, "live_bins_ccw", None)
                    if el_cal == 2:
                        self.stat_cal_el.set_bins(
                            getattr(el, "cal_bins_cw", None),
                            getattr(el, "cal_bins_ccw", None),
                        )
                    else:
                        self.stat_cal_el.set_bins(el_live_cw, el_live_ccw)
                    self.stat_live_el.set_bins(el_live_cw, el_live_ccw)
                    self.stat_placeholder_el.set_bins(
                        getattr(el, "acc_bins_cw", None),
                        getattr(el, "acc_bins_ccw", None),
                    )
        except Exception:
            pass
