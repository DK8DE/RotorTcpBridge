"""Dialoge zum Anlegen/Bearbeiten von Rotorübersicht-Instanzen (IP + Port)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..app_icon import get_app_icon
from ..i18n import t
from ..rotor_overview_config import (
    DEFAULT_APP_PORT,
    LOCAL_SITE_ID,
    deep_copy_config,
    default_antenna,
    default_site,
    normalize_antenna,
    normalize_site,
)
from .ui_utils import px_to_dip


def _is_local(site: dict | None) -> bool:
    if not isinstance(site, dict):
        return False
    return str(site.get("kind") or "") == "local" or str(site.get("id") or "") == LOCAL_SITE_ID


def _antenna_label(slot: int, cfg_ant: dict | None, live_ant: dict | None) -> str:
    name = ""
    if isinstance(live_ant, dict):
        name = str(live_ant.get("name") or "").strip()
    if not name and isinstance(cfg_ant, dict):
        name = str(cfg_ant.get("name") or "").strip()
    if not name or name.lower().startswith("antenne "):
        return t("overview.antenna_n", n=slot)
    return f"{t('overview.antenna_n', n=slot)} · {name}"


class RotorOverviewSiteDialog(QDialog):
    """Eigene Instanz: Ein/Aus + Antennen. Fremd: Host/Port/Passwort + Antennen."""

    def __init__(
        self,
        site: dict,
        parent=None,
        *,
        live_antennas: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("overview.site_edit_title"))
        self.setWindowIcon(get_app_icon())
        self._site = normalize_site(site) or default_site()
        self._local = _is_local(self._site)
        self._live_ants = list(live_antennas or [])
        root = QVBoxLayout(self)
        hint = QLabel(
            t("overview.local_hint") if self._local else t("overview.add_hint")
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        self.chk_enabled = QCheckBox(t("overview.field_enabled"))
        self.chk_enabled.setChecked(bool(self._site.get("enabled", True)))
        form.addRow(self.chk_enabled)
        self.ed_host = QLineEdit(str(self._site.get("host") or ""))
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(int(self._site.get("port") or DEFAULT_APP_PORT))
        self.ed_password = QLineEdit(str(self._site.get("password") or ""))
        self.ed_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_password.setPlaceholderText(t("overview.password_hint"))
        if self._local:
            self.ed_host.setEnabled(False)
            self.sp_port.setEnabled(False)
            self.ed_password.setEnabled(False)
        else:
            form.addRow(t("overview.field_host"), self.ed_host)
            form.addRow(t("overview.field_port"), self.sp_port)
            form.addRow(t("overview.field_password"), self.ed_password)
        root.addLayout(form)
        if not self._local:
            extra = QLabel(t("overview.map_server_hint"))
            extra.setWordWrap(True)
            root.addWidget(extra)

        ants_box = QGroupBox(t("overview.antennas_group"))
        ants_l = QVBoxLayout(ants_box)
        ant_hint = QLabel(t("overview.antennas_select_hint"))
        ant_hint.setWordWrap(True)
        ants_l.addWidget(ant_hint)
        cfg_ants = list(self._site.get("antennas") or [])
        self._ant_checks: List[QCheckBox] = []
        for i in range(3):
            slot = i + 1
            cfg_ant = cfg_ants[i] if i < len(cfg_ants) else default_antenna(slot)
            live_ant = self._live_ants[i] if i < len(self._live_ants) else None
            chk = QCheckBox(_antenna_label(slot, cfg_ant, live_ant))
            chk.setChecked(bool(cfg_ant.get("enabled", True)))
            ants_l.addWidget(chk)
            self._ant_checks.append(chk)
        root.addWidget(ants_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(px_to_dip(self, 460), px_to_dip(self, 380))

    def result_site(self) -> dict:
        out = dict(self._site)
        out["enabled"] = bool(self.chk_enabled.isChecked())
        ants = list(out.get("antennas") or [])
        while len(ants) < 3:
            ants.append(default_antenna(len(ants) + 1))
        for i, chk in enumerate(self._ant_checks):
            ant = normalize_antenna(ants[i], i + 1)
            ant["enabled"] = bool(chk.isChecked())
            ants[i] = ant
        out["antennas"] = ants
        if self._local:
            out["id"] = LOCAL_SITE_ID
            out["kind"] = "local"
        else:
            out["kind"] = "app"
            out["host"] = self.ed_host.text().strip()
            out["port"] = int(self.sp_port.value())
            out["password"] = self.ed_password.text()
        return normalize_site(out) or out


class RotorOverviewSettingsDialog(QDialog):
    """Liste der Instanzen (eigene fest, fremde per IP:Port)."""

    def __init__(
        self,
        cfg: dict,
        parent=None,
        *,
        default_port: int | None = None,
        live: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("overview.settings_title"))
        self.setWindowIcon(get_app_icon())
        self._cfg = deep_copy_config(cfg)
        self._default_port = int(default_port or DEFAULT_APP_PORT)
        self._live = dict(live or {})
        root = QVBoxLayout(self)
        self.lst = QListWidget()
        root.addWidget(self.lst)
        row = QHBoxLayout()
        self.btn_add = QPushButton(t("overview.btn_add"))
        self.btn_edit = QPushButton(t("overview.btn_edit"))
        self.btn_del = QPushButton(t("overview.btn_delete"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_del.clicked.connect(self._on_delete)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        root.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._reload_list()
        self.resize(px_to_dip(self, 480), px_to_dip(self, 360))

    def _live_antennas_for(self, site: dict) -> List[Dict[str, Any]]:
        sid = str(site.get("id") or "")
        st = self._live.get(sid)
        ants = getattr(st, "antennas", None) if st is not None else None
        return list(ants or [])

    def _reload_list(self) -> None:
        self.lst.clear()
        for s in self._cfg.get("sites") or []:
            en = t("overview.enabled_yes") if s.get("enabled") else t("overview.enabled_no")
            if _is_local(s):
                text = f"{t('overview.local_site')}  ({en})"
            else:
                host = str(s.get("host") or "")
                port = s.get("port")
                text = f"{host}:{port}  ({en})"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, str(s.get("id")))
            self.lst.addItem(it)

    def _selected_index(self) -> int:
        return self.lst.currentRow()

    def _on_add(self) -> None:
        site = default_site(name=t("overview.new_site"), port=self._default_port)
        dlg = RotorOverviewSiteDialog(site, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._cfg.setdefault("sites", []).append(dlg.result_site())
        self._reload_list()

    def _on_edit(self) -> None:
        i = self._selected_index()
        sites = self._cfg.get("sites") or []
        if i < 0 or i >= len(sites):
            return
        dlg = RotorOverviewSiteDialog(
            sites[i], self, live_antennas=self._live_antennas_for(sites[i])
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sites[i] = dlg.result_site()
        self._reload_list()

    def _on_delete(self) -> None:
        i = self._selected_index()
        sites = self._cfg.get("sites") or []
        if i < 0 or i >= len(sites):
            return
        if _is_local(sites[i]):
            QMessageBox.information(
                self, t("overview.title"), t("overview.cannot_delete_local")
            )
            return
        del sites[i]
        self._reload_list()

    def result_cfg(self) -> dict:
        return deep_copy_config(self._cfg)
