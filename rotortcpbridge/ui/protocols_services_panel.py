"""Gemeinsames Protokoll-/Services-Panel (Inbetriebnahme; Katalog auch für Settings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t


@dataclass(frozen=True)
class ServiceSpec:
    """Ein Emulations-/Service-Eintrag mit Config-Schlüsseln und Defaults."""

    key: str
    title_key: str
    info_key: str
    has_host_port: bool = True
    default_host: str = "127.0.0.1"
    default_port: int = 0
    # port_field: listen_port | listen_port_az | udp_pst_port (ui)
    port_field: str = "listen_port"
    host_field: str = "listen_host"
    enabled_field: str = "enabled"
    # cfg_root: None = top-level key; "ui" = under cfg["ui"] with special field names
    cfg_root: Optional[str] = None


# Einzige Liste der Wizard-Services (Defaults/Keys). Settings-Tab Protokolle
# nutzt dieselben Config-Keys; erweiterte Optionen bleiben dort.
SERVICE_SPECS: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "pst_server",
        "setup.svc_pst",
        "settings.group_spid_emulation",
        default_port=4001,
        port_field="listen_port_az",
    ),
    ServiceSpec(
        "ui_udp_pst",
        "setup.svc_pst_udp",
        "setup.svc_pst_udp_info",
        default_port=12000,
        port_field="udp_pst_port",
        host_field="udp_pst_listen_host",
        enabled_field="udp_pst_enabled",
        cfg_root="ui",
    ),
    ServiceSpec(
        "rotctld_server",
        "setup.svc_rotctld",
        "settings.rotctld_info",
        default_port=4533,
    ),
    ServiceSpec(
        "gs232_server",
        "setup.svc_gs232",
        "settings.gs232_info",
        default_port=4003,
    ),
    ServiceSpec(
        "easycomm_server",
        "setup.svc_easycomm",
        "settings.group_easycomm_server",
        default_port=4535,
    ),
    ServiceSpec(
        "dcu1_server",
        "setup.svc_dcu1",
        "settings.group_dcu1_server",
        default_port=4004,
    ),
    ServiceSpec(
        "n1mm_rotor",
        "setup.svc_n1mm",
        "settings.group_n1mm_rotor",
        has_host_port=False,
    ),
    ServiceSpec(
        "map_webserver",
        "setup.svc_map",
        "setup.svc_map_info",
        default_host="0.0.0.0",
        default_port=80,
    ),
)


class ProtocolsServicesPanel(QWidget):
    """Collapsible Enable + Host/Port je Service (geführte Settings-Ansicht)."""

    def __init__(
        self,
        cfg: dict,
        parent=None,
        *,
        open_com0com_cb: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        self._open_com0com_cb = open_com0com_cb
        self._blocks: list[tuple[ServiceSpec, QCheckBox, QWidget, dict[str, Any]]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        for spec in SERVICE_SPECS:
            chk = QCheckBox(t(spec.title_key))
            box = QWidget()
            fl = QFormLayout(box)
            info = QLabel(t(spec.info_key, fallback=t(spec.title_key)))
            info.setWordWrap(True)
            info.setStyleSheet("color: gray;")
            fl.addRow(info)
            fields: dict[str, Any] = {}
            if spec.has_host_port:
                ed_host = QLineEdit()
                sp_port = QSpinBox()
                sp_port.setRange(1, 65535)
                fl.addRow(t("setup.svc_host"), ed_host)
                fl.addRow(t("setup.svc_port"), sp_port)
                fields["host"] = ed_host
                fields["port"] = sp_port
            box.setVisible(False)
            chk.toggled.connect(box.setVisible)
            root.addWidget(chk)
            root.addWidget(box)
            self._blocks.append((spec, chk, box, fields))

        tip = QLabel(t("setup.svc_com0com_hint"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        root.addWidget(tip)
        btn_com = QPushButton(t("setup.svc_com0com_open"))
        btn_com.clicked.connect(self._open_com0com)
        root.addWidget(btn_com)
        root.addStretch(1)
        self.load_from_cfg()

    def set_cfg(self, cfg: dict) -> None:
        self._cfg = cfg if isinstance(cfg, dict) else {}

    def _open_com0com(self) -> None:
        if callable(self._open_com0com_cb):
            self._open_com0com_cb()

    def load_from_cfg(self) -> None:
        cfg = self._cfg
        for spec, chk, _box, fields in self._blocks:
            if spec.cfg_root == "ui":
                ui = cfg.get("ui") or {}
                chk.setChecked(bool(ui.get(spec.enabled_field, False)))
                if "host" in fields:
                    fields["host"].setText(
                        str(ui.get(spec.host_field, spec.default_host) or spec.default_host)
                    )
                    fields["port"].setValue(
                        int(ui.get(spec.port_field, spec.default_port) or spec.default_port)
                    )
                continue
            block = cfg.get(spec.key) or {}
            chk.setChecked(bool(block.get(spec.enabled_field, False)))
            if "host" not in fields:
                continue
            fields["host"].setText(
                str(block.get(spec.host_field, spec.default_host) or spec.default_host)
            )
            fields["port"].setValue(
                int(block.get(spec.port_field, spec.default_port) or spec.default_port)
            )

    def apply_to_cfg(self, cfg: dict) -> None:
        for spec, chk, _box, fields in self._blocks:
            if spec.cfg_root == "ui":
                ui = cfg.setdefault("ui", {})
                ui[spec.enabled_field] = bool(chk.isChecked())
                if "host" in fields:
                    ui[spec.host_field] = fields["host"].text().strip() or spec.default_host
                    ui[spec.port_field] = int(fields["port"].value())
                continue
            block = cfg.setdefault(spec.key, {})
            block[spec.enabled_field] = bool(chk.isChecked())
            if "host" in fields:
                block[spec.host_field] = fields["host"].text().strip() or spec.default_host
                block[spec.port_field] = int(fields["port"].value())
