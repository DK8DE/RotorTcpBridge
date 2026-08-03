"""Info-/About-Fenster für RotorTcpBridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from ..version import APP_AUTHOR, APP_COPYRIGHT, APP_DATE, APP_NAME, APP_VERSION


# Rahmen + leicht abgesetzter Hintergrund fuer die Lizenzboxen. Gleicher Stil
# fuer Apache (eigene Lizenz) und GPL (com0com) -> optisch zusammenhaengend.
_BOX_STYLE = (
    "QFrame#licBox {"
    " background-color: palette(alternate-base);"
    " border: 1px solid palette(mid);"
    " border-radius: 4px;"
    "}"
    " QFrame#licBox QLabel { background: transparent; border: none; }"
)

_LABEL_WIDTH = 95


def _logo_pixmap(target_dip: int = 84) -> QPixmap:
    """Laedt das InstallerSmall-Logo als QPixmap; leeres QPixmap bei Fehler.

    Sucht erst im Package (``rotortcpbridge/InstallerSmall.png`` – wird so auch
    von PyInstaller-Builds mit eingepackt), dann als Fallback im Repo-Root.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "InstallerSmall.png",
        here.parent.parent / "InstallerSmall.png",
    ]
    for p in candidates:
        if p.exists():
            pm = QPixmap(str(p))
            if not pm.isNull():
                return pm.scaledToWidth(
                    target_dip,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return QPixmap()


class AboutWindow(QDialog):
    """Info-/About-Fenster mit Logo, Metadaten, HW-Versionen und Lizenzboxen."""

    # Reader-Thread → GUI (GETCOVERSION / GETVERSION)
    sig_hw_version = Signal(str, object)  # key, text|None

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        ctrl: Any | None = None,
        cfg: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.ctrl = ctrl
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self._hw_queue: list[tuple[str, int, str, str]] = []
        self._hw_labels: dict[str, QLabel] = {}
        self._query_busy = False

        self.setWindowTitle(t("about.title"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        # Etwas höher wegen Controller-/Rotor-Versionszeilen
        self.setFixedSize(500, 560)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 12)

        root.addWidget(self._build_header())
        root.addWidget(self._build_license_header())
        root.addWidget(self._build_apache_box())
        root.addWidget(self._build_third_party_header())
        root.addWidget(self._build_com0com_box())
        root.addStretch(1)
        root.addLayout(self._build_button_row())

        self.sig_hw_version.connect(self._on_hw_version)
        QTimer.singleShot(0, self._start_hw_version_queries)

    # ------------------------------------------------------------------
    # Bausteine
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        """Logo links, App-Name und Metadaten rechts."""
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        logo_lbl = QLabel()
        pm = _logo_pixmap(target_dip=88)
        if not pm.isNull():
            logo_lbl.setPixmap(pm)
            logo_lbl.setFixedSize(pm.size())
        else:
            logo_lbl.setFixedSize(88, 88)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        h.addWidget(logo_lbl, 0, Qt.AlignmentFlag.AlignTop)

        meta = QWidget()
        meta.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(meta)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        lbl_app = QLabel(APP_NAME)
        lbl_app.setStyleSheet("font-size: 20px; font-weight: bold;")
        lbl_app.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        v.addWidget(lbl_app)

        v.addSpacing(2)

        def _row(label_key: str, value: str, *, store_key: str | None = None) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(t(label_key) + ":")
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(_LABEL_WIDTH)
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            v.addLayout(row)
            if store_key:
                self._hw_labels[store_key] = val

        _row("about.lbl_author", APP_AUTHOR)
        _row("about.lbl_version", f"v{APP_VERSION}")
        _row("about.lbl_date", APP_DATE)
        _row("about.lbl_controller", t("about.ver_na"), store_key="controller")
        _row("about.lbl_rotor_az", t("about.ver_na"), store_key="az")
        _row("about.lbl_rotor_el", t("about.ver_na"), store_key="el")

        h.addWidget(meta, 1, Qt.AlignmentFlag.AlignTop)
        return header

    def _build_license_header(self) -> QLabel:
        lbl = QLabel(t("about.lbl_license"))
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _build_third_party_header(self) -> QLabel:
        lbl = QLabel(t("about.lbl_third_party"))
        lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        return lbl

    def _build_apache_box(self) -> QFrame:
        """Apache-2.0-Lizenz der eigenen App als gerahmte Rich-Text-Box."""
        box = QFrame()
        box.setObjectName("licBox")
        box.setStyleSheet(_BOX_STYLE)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)

        copyright_lbl = QLabel(f"Copyright {APP_COPYRIGHT}")
        copyright_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        lay.addWidget(copyright_lbl)

        intro = self._rich_label("about.lbl_apache_intro")
        lay.addWidget(intro)

        warranty = self._rich_label("about.lbl_apache_warranty")
        lay.addWidget(warranty)

        links = self._rich_label("about.lbl_apache_links")
        lay.addWidget(links)
        return box

    def _build_com0com_box(self) -> QFrame:
        """com0com-Lizenzhinweis (GPL v2) als gerahmte Rich-Text-Box."""
        box = QFrame()
        box.setObjectName("licBox")
        box.setStyleSheet(_BOX_STYLE)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(self._rich_label("about.lbl_com0com_intro"))
        lay.addWidget(self._rich_label("about.lbl_com0com_license"))
        lay.addWidget(self._rich_label("about.lbl_com0com_links"))
        return box

    @staticmethod
    def _rich_label(key: str) -> QLabel:
        w = QLabel(t(key))
        w.setWordWrap(True)
        w.setTextFormat(Qt.TextFormat.RichText)
        w.setOpenExternalLinks(True)
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        w.setStyleSheet("font-size: 11px;")
        return w

    def _build_button_row(self) -> QHBoxLayout:
        btn_ok = QPushButton(t("about.btn_close"))
        btn_ok.setFixedWidth(90)
        btn_ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_ok)
        return row

    # ------------------------------------------------------------------
    # HW-Versionen (GETCOVERSION / GETVERSION)
    # ------------------------------------------------------------------

    def _set_hw_text(self, key: str, text: str) -> None:
        lbl = self._hw_labels.get(key)
        if lbl is not None:
            lbl.setText(text)

    def _bus_connected(self) -> bool:
        try:
            hw = getattr(self.ctrl, "hw", None)
            if hw is not None and hasattr(hw, "is_connected"):
                return bool(hw.is_connected())
        except Exception:
            pass
        return False

    def _start_hw_version_queries(self) -> None:
        """Controller und Rotor(en) anfragen, sofern konfiguriert/online."""
        queue: list[tuple[str, int, str, str]] = []
        bus_ok = self._bus_connected() and self.ctrl is not None
        send_ok = bus_ok and hasattr(self.ctrl, "send_ui_command")

        chw = self.cfg.get("controller_hw") if isinstance(self.cfg.get("controller_hw"), dict) else {}
        rb = self.cfg.get("rotor_bus") if isinstance(self.cfg.get("rotor_bus"), dict) else {}

        # Controller
        if bool(chw.get("enabled", True)):
            if not send_ok:
                self._set_hw_text("controller", t("about.ver_no_bus"))
            else:
                try:
                    cont_id = int(chw.get("cont_id", 2) or 2)
                except Exception:
                    cont_id = 2
                self._set_hw_text("controller", t("about.ver_query"))
                queue.append(("controller", cont_id, "GETCOVERSION", "ACK_GETCOVERSION"))
        else:
            self._set_hw_text("controller", t("about.ver_na"))

        # Rotor AZ
        enable_az = bool(getattr(self.ctrl, "enable_az", rb.get("enable_az", True))) if self.ctrl else bool(rb.get("enable_az", True))
        if enable_az:
            az = getattr(self.ctrl, "az", None) if self.ctrl else None
            online = bool(getattr(az, "online", False)) if az is not None else False
            if not send_ok:
                self._set_hw_text("az", t("about.ver_no_bus"))
            elif not online:
                self._set_hw_text("az", t("about.ver_offline"))
            else:
                try:
                    dst = int(getattr(self.ctrl, "slave_az", rb.get("slave_az", 20)))
                except Exception:
                    dst = int(rb.get("slave_az", 20) or 20)
                self._set_hw_text("az", t("about.ver_query"))
                queue.append(("az", dst, "GETVERSION", "ACK_GETVERSION"))
        else:
            self._set_hw_text("az", t("about.ver_na"))

        # Rotor EL
        enable_el = bool(getattr(self.ctrl, "enable_el", rb.get("enable_el", False))) if self.ctrl else bool(rb.get("enable_el", False))
        if enable_el:
            el = getattr(self.ctrl, "el", None) if self.ctrl else None
            online = bool(getattr(el, "online", False)) if el is not None else False
            if not send_ok:
                self._set_hw_text("el", t("about.ver_no_bus"))
            elif not online:
                self._set_hw_text("el", t("about.ver_offline"))
            else:
                try:
                    dst = int(getattr(self.ctrl, "slave_el", rb.get("slave_el", 21)))
                except Exception:
                    dst = int(rb.get("slave_el", 21) or 21)
                self._set_hw_text("el", t("about.ver_query"))
                queue.append(("el", dst, "GETVERSION", "ACK_GETVERSION"))
        else:
            self._set_hw_text("el", t("about.ver_na"))

        self._hw_queue = queue
        self._run_next_hw_query()

    def _run_next_hw_query(self) -> None:
        if self._query_busy:
            return
        if not self._hw_queue:
            return
        if self.ctrl is None or not hasattr(self.ctrl, "send_ui_command"):
            return

        key, dst, cmd, expect = self._hw_queue.pop(0)
        self._query_busy = True

        def done(tel, err) -> None:
            text: Optional[str] = None
            if err or tel is None:
                text = None
            else:
                cmd_u = str(getattr(tel, "cmd", "") or "").upper()
                if cmd_u.startswith("NAK_"):
                    text = None
                else:
                    # ACK_GET… oder Kurzform ACK_… ohne GET
                    ok = cmd_u.startswith(expect) or (
                        "GET" in expect
                        and cmd_u.startswith(expect.replace("GET", "", 1))
                    )
                    if ok:
                        raw = str(getattr(tel, "params", "") or "").strip()
                        # erster Parameter; Rest (z. B. ;rotor_id) weglassen
                        text = raw.split(";")[0].strip() or None
            try:
                self.sig_hw_version.emit(str(key), text)
            except RuntimeError:
                pass

        try:
            self.ctrl.send_ui_command(
                int(dst),
                str(cmd),
                "0",
                expect_prefix=str(expect),
                timeout_s=1.5,
                priority=1,
                on_done=done,
                apply_local_state=False,
            )
        except Exception:
            self._query_busy = False
            self.sig_hw_version.emit(str(key), None)

    @Slot(str, object)
    def _on_hw_version(self, key: str, value: object) -> None:
        self._query_busy = False
        if value is None or str(value).strip() == "":
            self._set_hw_text(key, t("about.ver_fail"))
        else:
            self._set_hw_text(key, str(value).strip())
        # Nächste Abfrage erst im GUI-Thread (nach kurzer Bus-Pause)
        QTimer.singleShot(120, self._run_next_hw_query)
