"""Info-/About-Fenster für RotorTcpBridge."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from ..version import APP_AUTHOR, APP_COPYRIGHT, APP_DATE, APP_NAME, APP_VERSION

_LICENSE_TEXT = """\
Copyright {copyright}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""


class AboutWindow(QDialog):
    """Einfaches Info-/About-Fenster."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("about.title"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 12)

        # --- App-Name groß ---
        lbl_app = QLabel(APP_NAME)
        lbl_app.setStyleSheet("font-size: 20px; font-weight: bold;")
        lbl_app.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_app)

        # --- Metadaten-Tabelle ---
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(0, 4, 0, 4)
        meta_layout.setSpacing(4)

        def _row(label_key: str, value: str) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(t(label_key) + ":")
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(90)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            meta_layout.addLayout(row)

        _row("about.lbl_author",  APP_AUTHOR)
        _row("about.lbl_version", f"v{APP_VERSION}")
        _row("about.lbl_date",    APP_DATE)

        root.addWidget(meta)

        # --- Lizenz-Überschrift ---
        lbl_lic = QLabel(t("about.lbl_license"))
        lbl_lic.setStyleSheet("font-weight: bold; margin-top: 4px;")
        root.addWidget(lbl_lic)

        # --- Lizenztext (scrollbar) ---
        lic_text = QLabel(_LICENSE_TEXT.format(copyright=APP_COPYRIGHT))
        lic_text.setWordWrap(True)
        lic_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lic_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        lic_text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(lic_text)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(220)
        root.addWidget(scroll)

        # --- OK-Button ---
        btn_ok = QPushButton(t("about.btn_close"))
        btn_ok.setFixedWidth(80)
        btn_ok.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())
