"""Info-/About-Fenster für RotorTcpBridge."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
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
    """Info-/About-Fenster mit Logo, Metadaten und Lizenzboxen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("about.title"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        # Fest 500 dip breit, 500 dip hoch — Layout darf auf diesem Raster
        # leben, heightForWidth der Rich-Text-QLabels wird dadurch stabil.
        self.setFixedSize(500, 500)

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

        def _row(label_key: str, value: str) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(t(label_key) + ":")
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(70)
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            v.addLayout(row)

        _row("about.lbl_author", APP_AUTHOR)
        _row("about.lbl_version", f"v{APP_VERSION}")
        _row("about.lbl_date", APP_DATE)

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
