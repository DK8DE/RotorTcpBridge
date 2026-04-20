"""UI-Tab für com0com + PST-Serial-Listener."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import com0com
from ..i18n import t
from .led_widget import Led
from .ui_utils import px_to_dip


_BAUD_RATES = (
    300,
    600,
    1200,
    2400,
    4800,
    9600,
    14400,
    19200,
    28800,
    38400,
    57600,
    115200,
    230400,
    460800,
    921600,
)


class _NewPairDialog(QDialog):
    """Einfacher Dialog mit zwei Port-Feldern für ein neues com0com-Paar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("com0com.dlg_new_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("com0com.dlg_new_text")))
        form = QFormLayout()
        self.ed_a = QLineEdit("COM#")
        self.ed_b = QLineEdit("COM#")
        form.addRow(t("com0com.dlg_new_port_a"), self.ed_a)
        form.addRow(t("com0com.dlg_new_port_b"), self.ed_b)
        layout.addLayout(form)
        hint = QLabel(t("com0com.dlg_uac_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def values(self) -> tuple[str, str]:
        return (self.ed_a.text().strip() or "COM#", self.ed_b.text().strip() or "COM#")


class Com0ComTab(QWidget):
    """Einstellungen-Tab: com0com-Status, Paare und PST-Serial-Listener."""

    def __init__(
        self,
        cfg: dict,
        pst_serial,
        save_cfg_cb,
        parent: Optional[QWidget] = None,
        rig_bridge_manager=None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.pst_serial = pst_serial
        self.save_cfg_cb = save_cfg_cb
        self._rig_bridge_manager = rig_bridge_manager
        self._pairs: List[com0com.Com0ComPair] = []
        self._pairs_loaded_once: bool = False
        # Guard gegen ungewollte Start/Stop-Toggles, wenn die Tabelle
        # programmatisch befuellt wird (z.B. _load_from_config).
        self._suppress_toggle: bool = False
        self._build_ui()
        self._load_from_config()
        # Paar-Liste erst beim ersten Anzeigen laden (vermeidet setupc-Aufruf
        # beim Programmstart). ``list_pairs`` läuft ohne UAC, aber wir
        # minimieren trotzdem unnötige Aufrufe.
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_listener_states)
        self._timer.start()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._pairs_loaded_once:
            self._pairs_loaded_once = True
            self.refresh_pairs()

    # --------------------------------------------------------------- Build UI
    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)

        # Status-Block
        gb_status = QGroupBox(t("com0com.group_status"))
        fl = QFormLayout(gb_status)
        self._led_installed = Led(14, self)
        self._lbl_installed = QLabel("-")
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self._led_installed, 0, Qt.AlignmentFlag.AlignLeft)
        wl.addWidget(self._lbl_installed, 1)
        fl.addRow(t("com0com.lbl_installed"), wrap)
        main.addWidget(gb_status)

        # Beschreibungs-Block
        gb_desc = QGroupBox(t("com0com.group_description"))
        vl_desc = QVBoxLayout(gb_desc)
        vl_desc.setContentsMargins(6, 6, 6, 6)
        lbl_desc = QLabel(t("com0com.description_text"))
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lbl_desc.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        scroll_desc = QScrollArea()
        scroll_desc.setWidgetResizable(True)
        scroll_desc.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_desc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_desc.setWidget(lbl_desc)
        scroll_desc.setFixedHeight(px_to_dip(self, 100))
        vl_desc.addWidget(scroll_desc)
        main.addWidget(gb_desc)

        # Paare-Block
        gb_pairs = QGroupBox(t("com0com.group_pairs"))
        vl_pairs = QVBoxLayout(gb_pairs)
        self.tbl_pairs = QTableWidget(0, 5)
        self.tbl_pairs.setHorizontalHeaderLabels(
            [
                t("com0com.col_index"),
                t("com0com.col_side_a"),
                t("com0com.col_effective_a"),
                t("com0com.col_side_b"),
                t("com0com.col_effective_b"),
            ]
        )
        self.tbl_pairs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_pairs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_pairs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl_pairs.verticalHeader().setVisible(False)
        hdr = self.tbl_pairs.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for i in (1, 2, 3, 4):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        # Die "internen" Registry-Seiten (CNCA0/CNCB0 etc.) helfen dem Nutzer
        # nicht – nur die vergebenen COM-Nummern in Side-A/Side-B-Effective
        # sind relevant.
        self.tbl_pairs.setColumnHidden(1, True)
        self.tbl_pairs.setColumnHidden(3, True)
        self.tbl_pairs.setMinimumHeight(px_to_dip(self, 140))
        vl_pairs.addWidget(self.tbl_pairs)

        btn_row = QHBoxLayout()
        self.btn_new = QPushButton(t("com0com.btn_new"))
        self.btn_remove = QPushButton(t("com0com.btn_remove"))
        tip_runas = t("com0com.tooltip_runas")
        for b in (self.btn_new, self.btn_remove):
            b.setToolTip(tip_runas)
        self.btn_new.clicked.connect(self._on_new_pair)
        self.btn_remove.clicked.connect(self._on_remove_pair)
        btn_row.addWidget(self.btn_new)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch(1)
        vl_pairs.addLayout(btn_row)
        main.addWidget(gb_pairs)

        # Listener-Block
        gb_lst = QGroupBox(t("pst_serial.group"))
        vl_lst = QVBoxLayout(gb_lst)

        self.tbl_listeners = QTableWidget(0, 5)
        self.tbl_listeners.setHorizontalHeaderLabels(
            [
                t("pst_serial.col_port"),
                t("pst_serial.col_baud"),
                t("pst_serial.col_target"),
                t("pst_serial.col_active"),
                t("pst_serial.col_state"),
            ]
        )
        self.tbl_listeners.verticalHeader().setVisible(False)
        hdr2 = self.tbl_listeners.horizontalHeader()
        hdr2.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr2.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.tbl_listeners.setColumnWidth(4, px_to_dip(self, 100))
        self.tbl_listeners.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_listeners.setMinimumHeight(px_to_dip(self, 140))
        vl_lst.addWidget(self.tbl_listeners)

        btn_row2 = QHBoxLayout()
        self.btn_add_listener = QPushButton(t("pst_serial.btn_add"))
        self.btn_remove_listener = QPushButton(t("pst_serial.btn_remove"))
        self.btn_add_listener.clicked.connect(self._on_add_listener)
        self.btn_remove_listener.clicked.connect(self._on_remove_listener)
        btn_row2.addWidget(self.btn_add_listener)
        btn_row2.addWidget(self.btn_remove_listener)
        btn_row2.addStretch(1)
        vl_lst.addLayout(btn_row2)

        main.addWidget(gb_lst)
        main.addStretch(1)

    # ----------------------------------------------------------- Config I/O
    def _load_from_config(self) -> None:
        ps = self.cfg.get("pst_serial") or {}
        self.tbl_listeners.setRowCount(0)
        # Waehrend des programmatischen Aufbaus feuert toggled(…) sonst fuer
        # jeden Haken; das Auto-Start-Verhalten soll aber ausschliesslich vom
        # Nutzer oder vom pst_serial.start_all() beim Programmstart ausgeloest
        # werden. Deshalb Signale kurz ignorieren.
        self._suppress_toggle = True
        try:
            for item in ps.get("listeners") or []:
                if not isinstance(item, dict):
                    continue
                port = str(item.get("port", "") or "").strip()
                baud = int(item.get("baudrate", 115200) or 115200)
                enabled = bool(item.get("enabled", True))
                target = str(item.get("target", "rotor") or "rotor").strip() or "rotor"
                self._append_listener_row(port, baud, enabled, target)
        finally:
            self._suppress_toggle = False
        self._apply_unique_constraints()

    def to_config(self) -> Dict[str, Any]:
        listeners: list[dict] = []
        for row in range(self.tbl_listeners.rowCount()):
            port_w = self.tbl_listeners.cellWidget(row, 0)
            baud_w = self.tbl_listeners.cellWidget(row, 1)
            target_w = self.tbl_listeners.cellWidget(row, 2)
            chk = self._listener_chk(row)
            if not isinstance(port_w, QComboBox) or not isinstance(baud_w, QComboBox):
                continue
            port = str(port_w.currentData() or port_w.currentText() or "").strip()
            if not port:
                continue
            try:
                baud = int(baud_w.currentText())
            except ValueError:
                baud = 115200
            enabled = bool(chk.isChecked()) if chk is not None else True
            target = "rotor"
            if isinstance(target_w, QComboBox):
                target = str(target_w.currentData() or "rotor").strip() or "rotor"
            listeners.append(
                {
                    "port": port,
                    "baudrate": baud,
                    "enabled": enabled,
                    "target": target,
                }
            )
        return {
            # Globales "enabled" wird aus den einzelnen Listener-Haken abgeleitet:
            # sobald irgendein Listener aktiv ist, gilt PST-Serial als aktiv. Ohne
            # aktive Zuordnung ist das Gesamtmodul inaktiv, ein separater Haken ist
            # dadurch hinfaellig.
            "enabled": any(bool(listener.get("enabled", False)) for listener in listeners),
            "listeners": listeners,
        }

    # --------------------------------------------------------- com0com Paare
    def refresh_pairs(self) -> None:
        exe = com0com.find_setupc()
        installed = exe is not None
        self._led_installed.set_state(bool(installed))
        if installed:
            self._lbl_installed.setText(t("com0com.lbl_installed"))
        else:
            self._lbl_installed.setText(t("com0com.lbl_not_installed"))

        self._pairs = com0com.list_pairs() if installed else []
        self.tbl_pairs.setRowCount(0)
        for pr in self._pairs:
            row = self.tbl_pairs.rowCount()
            self.tbl_pairs.insertRow(row)
            self.tbl_pairs.setItem(row, 0, QTableWidgetItem(str(pr.index)))
            self.tbl_pairs.setItem(row, 1, QTableWidgetItem(pr.side_a_name))
            self.tbl_pairs.setItem(row, 2, QTableWidgetItem(pr.effective_a))
            self.tbl_pairs.setItem(row, 3, QTableWidgetItem(pr.side_b_name))
            self.tbl_pairs.setItem(row, 4, QTableWidgetItem(pr.effective_b))

        # Port-Auswahl in Listenern nachziehen
        self._update_listener_port_choices()

    def _known_port_options(self) -> list[tuple[str, str]]:
        """Liste ``(label, port)`` für die Listener-Combobox.

        Pro com0com-Paar wird **eine** Option angeboten: die A-Seite als
        Listener-Port (den wir selbst öffnen). Das Label nennt zusätzlich
        die Gegenseite, die das externe Programm nutzen soll, z. B.
        ``"COM4   (extern: COM10)"``. Falls die A-Seite nicht verfügbar
        ist (kein realer Name erkennbar), fällt die Option auf die
        B-Seite zurück und benennt A als extern.
        ``port`` ist der reine Portname, der an den Listener geht.
        """

        def _clean(n: str) -> str:
            n = (n or "").strip()
            if not n or n == "-" or n == "COM#":
                return ""
            return n

        options: list[tuple[str, str]] = []
        seen: set[str] = set()
        for pr in self._pairs:
            a = _clean(pr.effective_a) or _clean(pr.side_a_name)
            b = _clean(pr.effective_b) or _clean(pr.side_b_name)
            # Bevorzugt A als Listener-Port, sonst B.
            if a:
                listener, extern = a, b
            elif b:
                listener, extern = b, a
            else:
                continue
            if listener in seen:
                continue
            seen.add(listener)
            if extern:
                label = f"{listener}   (extern: {extern})"
            else:
                label = listener
            options.append((label, listener))
        return options

    def _known_ports(self) -> list[str]:
        return [port for _lbl, port in self._known_port_options()]

    def _update_listener_port_choices(self) -> None:
        options = self._known_port_options()
        for row in range(self.tbl_listeners.rowCount()):
            cb = self.tbl_listeners.cellWidget(row, 0)
            if not isinstance(cb, QComboBox):
                continue
            current = str(cb.currentData() or cb.currentText() or "").strip()
            cb.blockSignals(True)
            cb.clear()
            ports_in_options = [p for _lbl, p in options]
            if current and current not in ports_in_options:
                # Der Port aus der Config existiert aktuell nicht (mehr) —
                # Eintrag trotzdem behalten, damit die Config nicht verloren geht.
                cb.addItem(current, current)
            for lbl, p in options:
                cb.addItem(lbl, p)
            cb.setEditable(False)
            if current:
                idx = cb.findData(current)
                if idx < 0:
                    idx = cb.findText(current)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self._apply_unique_constraints()

    def _not_installed_warning(self) -> bool:
        if com0com.is_installed():
            return False
        QMessageBox.warning(
            self,
            t("com0com.dlg_not_installed_title"),
            t("com0com.dlg_not_installed_text"),
        )
        return True

    def _schedule_post_change_refresh(self) -> None:
        """Nach setupc-Änderung (install / remove) genau **einmal**
        zeitversetzt aktualisieren.

        Windows enumeriert die beiden Seiten eines com0com-Paars nicht
        atomar (Seite B kann einige hundert ms länger brauchen als
        Seite A). Ein einzelner Refresh ~1,5 s nach der setupc-Aktion
        fängt diesen Nachlauf sauber ab.

        Mehrere gestaffelte Refreshes wurden bewusst entfernt: sie
        erzeugen sonst während der nachfolgenden Sekunden wiederholt
        ``setCurrentIndex``-Events in den Listener-Comboboxen und
        machen die Auswahl von Ports durch den Nutzer unmoeglich.
        """
        QTimer.singleShot(1500, self.refresh_pairs)

    def _on_new_pair(self) -> None:
        if self._not_installed_warning():
            return
        # Windows vergibt ueber ``PortName=COM#`` die naechste freie
        # COM-Nummer selbst. Ein Namensdialog ist nicht noetig und wuerde
        # den Nutzer nur mit den internen CNCAn/CNCBn-Registry-Namen
        # verwirren.
        try:
            com0com.install_pair("COM#", "COM#")
        except com0com.Com0ComError as exc:
            QMessageBox.warning(self, t("com0com.dlg_error_title"), str(exc))
        self.refresh_pairs()
        self._schedule_post_change_refresh()

    def _collect_pair_ports(self, pr: "com0com.Com0ComPair") -> set[str]:
        """Alle Port-Namen eines Paars als normalisierte Menge.

        Liefert sowohl die in der Registry eingetragenen ``PortName=``-
        Werte als auch die von Windows vergebenen ``RealPortName`` /
        pyserial-Devices zurück. Platzhalter (``-``, ``COM#``, leer)
        werden ausgefiltert. Dadurch treffen wir in
        ``_remove_listeners_for_ports`` zuverlässig die Listener-Zeilen,
        egal ob der Listener mit ``COM4`` (RealPort) oder ``COM#``
        (Registry) konfiguriert ist.
        """
        result: set[str] = set()
        for name in (
            pr.side_a_name,
            pr.side_b_name,
            pr.effective_a,
            pr.effective_b,
            pr.real_a,
            pr.real_b,
        ):
            n = (name or "").strip().upper()
            if n and n not in ("-", "COM#"):
                result.add(n)
        return result

    def _find_listeners_for_ports(self, ports: set[str]) -> list[tuple[int, str]]:
        """``(row, port)`` aller Listener-Zeilen zurückgeben, deren Port
        zu ``ports`` gehört. Reihenfolge absteigend — praktisch fuer
        anschliessendes ``removeRow``.
        """
        hits: list[tuple[int, str]] = []
        for row in range(self.tbl_listeners.rowCount() - 1, -1, -1):
            port = self._row_port(row).strip().upper()
            if port and port in ports:
                hits.append((row, port))
        return hits

    def _remove_listeners_for_ports(self, ports: set[str]) -> list[str]:
        """Listener-Zeilen entfernen, Listener im Manager stoppen.

        Gibt die Liste der (eindeutigen) entfernten Portnamen zurueck.
        """
        removed: list[str] = []
        for row, port in self._find_listeners_for_ports(ports):
            self.tbl_listeners.removeRow(row)
            if port not in removed:
                removed.append(port)
        if removed and self.pst_serial is not None:
            for port in removed:
                try:
                    self.pst_serial.stop_port(port)
                except Exception:
                    pass
            try:
                self.pst_serial.update_config(self.to_config())
            except Exception:
                pass
        return removed

    def _on_remove_pair(self) -> None:
        if self._not_installed_warning():
            return
        row = self.tbl_pairs.currentRow()
        if row < 0 or row >= len(self._pairs):
            return
        pr = self._pairs[row]
        pair_ports = self._collect_pair_ports(pr)
        affected_listener_ports = sorted({p for _r, p in self._find_listeners_for_ports(pair_ports)})

        msg = (
            t("com0com.dlg_remove_text")
            .replace("{index}", str(pr.index))
            .replace("{a}", pr.effective_a)
            .replace("{b}", pr.effective_b)
        )
        if affected_listener_ports:
            msg = (
                msg
                + "\n\n"
                + t("com0com.dlg_remove_listener_note").replace(
                    "{ports}", ", ".join(affected_listener_ports)
                )
            )
        if (
            QMessageBox.question(self, t("com0com.dlg_remove_title"), msg)
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            com0com.remove_pair(pr.index)
        except com0com.Com0ComError as exc:
            QMessageBox.warning(self, t("com0com.dlg_error_title"), str(exc))
            # Paar ist noch da — Listener NICHT entfernen, sonst verliert
            # der Nutzer seine Konfiguration ohne Not.
            self.refresh_pairs()
            self._schedule_post_change_refresh()
            return

        # Remove erfolgreich: zuerst Listener stoppen/entfernen, DANACH
        # refresh — sonst koennte der nachgezogene refresh die Ports
        # kurzzeitig noch als "vorhanden" melden.
        self._remove_listeners_for_ports(pair_ports)
        self.refresh_pairs()
        self._schedule_post_change_refresh()

    def _on_rename_side(self) -> None:
        if self._not_installed_warning():
            return
        row = self.tbl_pairs.currentRow()
        if row < 0 or row >= len(self._pairs):
            return
        pr = self._pairs[row]
        sides = [f"CNCA{pr.index}", f"CNCB{pr.index}"]
        side, ok = QInputDialog.getItem(
            self,
            t("com0com.dlg_rename_title"),
            t("com0com.dlg_rename_text").replace("{side}", ""),
            sides,
            0,
            False,
        )
        if not ok:
            return
        current = pr.side_a_name if side.endswith(f"A{pr.index}") else pr.side_b_name
        new_name, ok = QInputDialog.getText(
            self,
            t("com0com.dlg_rename_title"),
            t("com0com.dlg_rename_text").replace("{side}", side),
            text=current if current not in ("", "-") else "COM#",
        )
        if not ok:
            return
        new_name = (new_name or "").strip() or "COM#"
        try:
            com0com.change_port_name(side, new_name)
        except com0com.Com0ComError as exc:
            QMessageBox.warning(self, t("com0com.dlg_error_title"), str(exc))
        self.refresh_pairs()
        self._schedule_post_change_refresh()

    # ------------------------------------------------------ Listener-Tabelle
    def _append_listener_row(
        self, port: str, baud: int, enabled: bool, target: str = "rotor"
    ) -> int:
        row = self.tbl_listeners.rowCount()
        self.tbl_listeners.insertRow(row)

        cb_port = QComboBox()
        cb_port.setEditable(False)
        options = self._known_port_options()
        known_ports = [p for _lbl, p in options]
        if port and port not in known_ports:
            cb_port.addItem(port, port)
        for lbl, p in options:
            cb_port.addItem(lbl, p)
        if port:
            idx = cb_port.findData(port)
            if idx >= 0:
                cb_port.setCurrentIndex(idx)
        cb_port.currentIndexChanged.connect(self._on_unique_selection_changed)
        self.tbl_listeners.setCellWidget(row, 0, cb_port)

        cb_baud = QComboBox()
        for b in _BAUD_RATES:
            cb_baud.addItem(str(b))
        baud_i = cb_baud.findText(str(int(baud)))
        if baud_i < 0:
            cb_baud.addItem(str(int(baud)))
            baud_i = cb_baud.count() - 1
        cb_baud.setCurrentIndex(baud_i)
        self.tbl_listeners.setCellWidget(row, 1, cb_baud)

        cb_target = QComboBox()
        self._populate_target_combo(cb_target, target)
        cb_target.currentIndexChanged.connect(self._on_unique_selection_changed)

        # Wenn der Nutzer die Zielauswahl auf "Rotor" setzt, setzen wir die
        # Baudrate dieser Zeile automatisch auf 1200 — mehr unterstuetzt der
        # SPID ROT2PROG nicht. Aendern kann der Nutzer sie danach weiterhin.
        def _on_target_changed(_idx: int, _cb_baud: QComboBox = cb_baud,
                               _cb_target: QComboBox = cb_target) -> None:
            tgt = str(_cb_target.currentData() or _cb_target.currentText() or "").strip()
            if tgt == "rotor":
                self._set_baud_combo(_cb_baud, 1200)

        cb_target.currentIndexChanged.connect(_on_target_changed)
        self.tbl_listeners.setCellWidget(row, 2, cb_target)

        chk = QCheckBox()
        chk.setChecked(bool(enabled))
        # Haken = Start/Stop-Schalter. Da Signal-Handler ohne Row-Index
        # arbeiten muessen (Reihenfolge kann sich durch Entfernen aendern),
        # leiten wir ueber das wrap-Widget auf die Checkbox zurueck und
        # suchen die passende Zeile per `_row_of_chk`.
        chk.toggled.connect(self._on_listener_toggled)
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch(1)
        wl.addWidget(chk)
        wl.addStretch(1)
        # Zugriff auf Checkbox per Helper `_listener_chk` (liest wrap._chk)
        wrap._chk = chk  # type: ignore[attr-defined]
        self.tbl_listeners.setCellWidget(row, 3, wrap)

        lbl_state = QLabel(t("pst_serial.state_stopped"))
        self.tbl_listeners.setCellWidget(row, 4, lbl_state)

        return row

    def _populate_target_combo(self, cb: QComboBox, current: str) -> None:
        """Befuellt die Ziel-Combobox mit ``Rotor`` + pro Rig-Profil einen
        Eintrag ``Rig: <Name>``. ``userData`` traegt den serialisierten
        ``target``-Wert (``"rotor"`` oder ``"rig:<profile_id>"``)."""
        cb.blockSignals(True)
        cb.clear()
        cb.addItem(t("pst_serial.target_rotor"), "rotor")
        profiles = self._rig_profiles()
        for pr in profiles:
            pid = str(pr.get("id", "")).strip()
            if not pid:
                continue
            name = str(pr.get("name", "") or pid)
            label = t("pst_serial.target_rig_label").replace("{name}", name)
            cb.addItem(label, f"rig:{pid}")
        # Auswahl setzen. Unbekannter ``target`` bleibt als Fallback erhalten.
        want = (current or "rotor").strip() or "rotor"
        idx = cb.findData(want)
        if idx < 0 and want.startswith("rig:"):
            # Profil gibt es aktuell nicht → trotzdem als Eintrag ergaenzen,
            # damit die Konfig nicht stillschweigend verloren geht.
            pid = want.split(":", 1)[1]
            cb.addItem(
                t("pst_serial.target_rig_missing").replace("{id}", pid),
                want,
            )
            idx = cb.count() - 1
        if idx < 0:
            idx = 0
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def _refresh_target_combos(self) -> None:
        """Target-Comboboxen aller Listener-Zeilen mit der aktuellen
        Profil-Liste abgleichen. Nur wenn sich die Signatur aendert, werden
        die Items neu gebaut — der vom Nutzer gewaehlte ``target`` bleibt
        erhalten."""
        profiles = self._rig_profiles()
        sig = tuple(
            (str(p.get("id", "")), str(p.get("name", ""))) for p in profiles
        )
        if getattr(self, "_target_combo_sig", None) == sig:
            return
        self._target_combo_sig = sig
        for row in range(self.tbl_listeners.rowCount()):
            w = self.tbl_listeners.cellWidget(row, 2)
            if not isinstance(w, QComboBox):
                continue
            current = str(w.currentData() or "rotor")
            self._populate_target_combo(w, current)
        self._apply_unique_constraints()

    def _used_values_except(
        self, column: int, except_row: int, default_for_empty: str = ""
    ) -> set[str]:
        """Bereits in anderen Zeilen gewählte Combo-Werte der Spalte ``column``.

        Verwendet wird ``currentData()`` (oder als Fallback ``currentText()``).
        Leerwerte bleiben erlaubt, damit eine noch nicht gesetzte Zeile nicht
        andere Zeilen blockiert.
        """
        used: set[str] = set()
        for row in range(self.tbl_listeners.rowCount()):
            if row == except_row:
                continue
            cb = self.tbl_listeners.cellWidget(row, column)
            if not isinstance(cb, QComboBox):
                continue
            val = str(cb.currentData() or cb.currentText() or "").strip()
            if not val:
                val = default_for_empty
            if val:
                used.add(val)
        return used

    def _apply_unique_constraints(self) -> None:
        """Eindeutigkeit der **Ports** durchsetzen.

        Jeder com0com-Port darf nur einer Zeile zugeordnet sein —
        physikalisch koennen zwei Listener nicht denselben seriellen Port
        gleichzeitig oeffnen.

        Fuer die **Ziele** (Spalte 2) gibt es bewusst *keine*
        Uniqueness-Beschraenkung mehr: Rotor und Rig-Profile werden
        intern als geteilter Singleton bedient (gemeinsamer State-Cache,
        gemeinsame Kommando-Queue). Dadurch lassen sich ueber mehrere
        com0com-Paare beliebig viele externe Programme parallel an das
        gleiche Gerat binden. Konkurrierende SET-Befehle werden vom
        Controller / Manager serialisiert.

        Bereits vergebene Ports werden in den anderen Port-Combos
        deaktiviert (aber nicht entfernt), damit die Auswahl sichtbar
        bleibt. Der aktuell in einer Zeile gewaehlte Port bleibt immer
        aktiv.
        """
        for row in range(self.tbl_listeners.rowCount()):
            cb = self.tbl_listeners.cellWidget(row, 0)
            if not isinstance(cb, QComboBox):
                continue
            model = cb.model()
            if not isinstance(model, QStandardItemModel):
                continue
            used = self._used_values_except(0, row)
            current = str(cb.currentData() or cb.currentText() or "").strip()
            for i in range(cb.count()):
                data = cb.itemData(i)
                value = str(data or cb.itemText(i) or "").strip()
                item = model.item(i)
                if item is None:
                    continue
                if value == current:
                    item.setEnabled(True)
                    continue
                item.setEnabled(value not in used)
        self._update_add_listener_enabled()

    def _update_add_listener_enabled(self) -> None:
        """Hinzufuege-Button nur aktiv, solange noch mindestens ein Port
        frei ist.

        Ziele (Rotor / Rig-Profile) sind nicht mehr beschraenkt, sie
        koennen mehrfach zugewiesen werden. Solange also mindestens ein
        com0com-Port noch keiner Zeile zugeordnet ist, macht eine weitere
        Zeile Sinn."""
        btn = getattr(self, "btn_add_listener", None)
        if btn is None:
            return
        available_ports = {p for _lbl, p in self._known_port_options()}
        used_ports = self._used_values_except(0, -1)
        free_ports = available_ports - used_ports
        btn.setEnabled(bool(free_ports))

    def _on_unique_selection_changed(self, _index: int = -1) -> None:
        """Signal-Handler fuer Port-/Ziel-Combos: Eindeutigkeit nachziehen."""
        if getattr(self, "_suppress_toggle", False):
            return
        self._apply_unique_constraints()

    def _first_unused_port(self) -> str:
        """Ersten COM-Port liefern, der noch in keiner Listener-Zeile genutzt wird."""
        used = self._used_values_except(0, -1)
        for _lbl, p in self._known_port_options():
            if p not in used:
                return p
        return ""

    def _first_unused_target(self) -> str:
        """Default-Ziel fuer neu hinzugefuegte Zeilen.

        Ziele sind nicht mehr eindeutig (derselbe Rotor/dasselbe Rig darf
        ueber mehrere com0com-Paare parallel bedient werden), daher waehlen
        wir hier einfach immer ``"rotor"`` als vernuenftigen
        Ausgangspunkt — der Nutzer kann in der Zeile jederzeit auf ein
        Rig-Profil umschalten."""
        return "rotor"

    def _rig_profiles(self) -> list[dict]:
        """Hilfsabfrage: Profilliste aus dem RigBridgeManager, mit Fallback
        auf die Config, falls der Manager nicht verfuegbar ist."""
        rbm = getattr(self, "_rig_bridge_manager", None)
        if rbm is not None:
            try:
                return list(rbm.list_profiles() or [])
            except Exception:
                pass
        rigs = (self.cfg.get("rig_bridge") or {}).get("rigs") or []
        return [p for p in rigs if isinstance(p, dict)]

    def _listener_chk(self, row: int) -> Optional[QCheckBox]:
        w = self.tbl_listeners.cellWidget(row, 3)
        if w is None:
            return None
        return getattr(w, "_chk", None)

    def _row_of_chk(self, chk: QCheckBox) -> int:
        """Zeile finden, in der diese Checkbox sitzt.

        Noetig, weil das ``toggled``-Signal keinen Row-Index mitliefert und
        sich die Reihenfolge durch ``removeRow`` nachtraeglich verschieben
        kann (ein im Row-Index gebundenes Lambda waere dann stale).
        """
        for row in range(self.tbl_listeners.rowCount()):
            if self._listener_chk(row) is chk:
                return row
        return -1

    def _on_listener_toggled(self, checked: bool) -> None:
        """Haken in der Listener-Zeile = Start/Stop-Schalter.

        Gesetzt  -> pst_serial.start_port(port)
        Entfernt -> pst_serial.stop_port(port)

        Waehrend ``_load_from_config`` laeuft, ist ``_suppress_toggle``
        aktiv, damit das Programm nicht beim Aufbau der Tabelle direkt
        lauter Ports oeffnet — das erledigt pst_serial.start_all() bereits
        beim Programmstart basierend auf der Config.
        """
        if self._suppress_toggle:
            return
        chk = self.sender()
        if not isinstance(chk, QCheckBox):
            return
        row = self._row_of_chk(chk)
        if row < 0:
            return
        if checked:
            self._start_row(row)
        else:
            self._stop_row(row)

    def _on_add_listener(self) -> None:
        # Neue Zeile immer mit einem Port/Ziel anlegen, das nicht schon
        # vergeben ist. Jeder COM-Port und jedes Rig-/Rotor-Ziel darf nur
        # einmal zugeordnet werden.
        default_port = self._first_unused_port()
        default_target = self._first_unused_target()
        # SPID ROT2PROG kann max. 1200 Baud; fuer den Rotor also 1200 als
        # Default, fuer Rigs bleibt 115200 die gaengige Ausgangsbasis.
        default_baud = 1200 if default_target == "rotor" else 115200
        # Neue Zeilen bewusst ohne gesetzten Haken anlegen — der Nutzer
        # soll Port/Baud einstellen koennen, bevor der Listener startet.
        self._append_listener_row(default_port, default_baud, False, default_target)
        self._apply_unique_constraints()

    def _set_baud_combo(self, cb: QComboBox, baud: int) -> None:
        """Waehlt in der Baudraten-Combo den passenden Eintrag aus; fehlt er,
        wird er nachgetragen. Wird z.B. automatisch getriggert, wenn das Ziel
        einer Zeile auf ``rotor`` umgestellt wird (SPID kann nur 1200 Baud)."""
        text = str(int(baud))
        idx = cb.findText(text)
        if idx < 0:
            cb.addItem(text)
            idx = cb.count() - 1
        if cb.currentIndex() != idx:
            cb.setCurrentIndex(idx)

    def _on_remove_listener(self) -> None:
        row = self.tbl_listeners.currentRow()
        if row < 0:
            return
        # Laufenden Listener vorher stoppen, sonst bleibt der Thread offen.
        port = self._row_port(row)
        if port and self.pst_serial is not None:
            try:
                self.pst_serial.stop_port(port)
            except Exception:
                pass
        self.tbl_listeners.removeRow(row)
        self._apply_unique_constraints()

    def _row_port(self, row: int) -> str:
        port_w = self.tbl_listeners.cellWidget(row, 0)
        if not isinstance(port_w, QComboBox):
            return ""
        return str(port_w.currentData() or port_w.currentText() or "").strip()

    def _start_row(self, row: int) -> None:
        if self.pst_serial is None:
            return
        port = self._row_port(row)
        if not port:
            return
        # Erst Config ans Manager-Objekt geben (damit Port evtl. existiert)
        try:
            self.pst_serial.update_config(self.to_config())
            self.pst_serial.start_port(port)
        except Exception:
            pass
        self._refresh_listener_states()

    def _stop_row(self, row: int) -> None:
        if self.pst_serial is None:
            return
        port = self._row_port(row)
        if not port:
            return
        try:
            self.pst_serial.stop_port(port)
        except Exception:
            pass
        self._refresh_listener_states()

    def _refresh_listener_states(self) -> None:
        if self.pst_serial is None:
            return
        # Target-Comboboxen lazy aktualisieren, falls neue Profile hinzukamen
        # oder welche entfernt wurden. Das betrifft nur Eintraege; die
        # aktuelle Auswahl bleibt stehen, solange ihr ``target`` weiterhin
        # vorkommt (sonst bleibt er als "missing"-Eintrag stehen).
        self._refresh_target_combos()
        for row in range(self.tbl_listeners.rowCount()):
            state_lbl = self.tbl_listeners.cellWidget(row, 4)
            if not isinstance(state_lbl, QLabel):
                continue
            port = self._row_port(row)
            p = None
            try:
                p = self.pst_serial.get(port) if port else None
            except Exception:
                p = None
            if p is None:
                state_lbl.setText(t("pst_serial.state_stopped"))
                continue
            if getattr(p, "last_error", ""):
                state_lbl.setText(
                    t("pst_serial.state_error").replace("{msg}", str(p.last_error))
                )
            elif getattr(p, "running", False):
                state_lbl.setText(t("pst_serial.state_running"))
            else:
                state_lbl.setText(t("pst_serial.state_stopped"))
