from __future__ import annotations
import sys

# Scheme-Registrierung VOR allen anderen Imports (sonst ignoriert Qt sie)
import rotortcpbridge.webengine_schemes  # noqa: F401

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .app_config import load_config, save_config
from .net_utils import check_internet

from .ui.map_tiles import install_rotortiles_handler
from .ui.wheel_guard import install_wheel_guard
from .app_icon import get_app_icon
from .i18n import load_lang
from .logutil import LogBuffer
from .hardware_client import HardwareClient
from .rotor_controller import RotorController
from .pst_server import PstDualServer
from .pst_serial import PstSerialManager
from .udp_ucxlog import UdpUcxLogListener
from .udp_aswatchlist import UdpAswatchlistListener
from .udp_pst_rotator import UdpPstRotator
from .rig_bridge.manager import RigBridgeManager
from .ui.main_window import MainWindow


_SINGLE_INSTANCE_NAME = "RotorTcpBridge.SingleInstance"


def _focus_main_window(w: MainWindow) -> None:
    try:
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
    except Exception:
        pass


def _notify_running_instance(name: str) -> bool:
    """True wenn bereits eine Instanz läuft (wurde aktiviert)."""
    sock = QLocalSocket()
    try:
        sock.connectToServer(name)
        if not sock.waitForConnected(180):
            return False
        sock.write(b"ACTIVATE")
        sock.flush()
        sock.waitForBytesWritten(180)
        sock.disconnectFromServer()
        return True
    except Exception:
        return False
    finally:
        try:
            sock.abort()
        except Exception:
            pass


def main():
    cfg = load_config()
    app = QApplication(sys.argv)
    # Tray: Hauptfenster kann per hide() unsichtbar sein, während Kompass/Einstellungen
    # offen sind. Default (True) würde beim Schließen des letzten sichtbaren Fensters die
    # ganze App beenden — daher False; Beenden erfolgt über MainWindow.closeEvent.
    app.setQuitOnLastWindowClosed(False)
    if _notify_running_instance(_SINGLE_INSTANCE_NAME):
        app.quit()
        return
    # Verwaiste lokale Server-Handle von abgestürzter Instanz bereinigen.
    try:
        QLocalServer.removeServer(_SINGLE_INSTANCE_NAME)
    except Exception:
        pass
    single_server = QLocalServer(app)
    single_server.listen(_SINGLE_INSTANCE_NAME)
    # Referenz halten (sonst GC -> kein Aktivierungs-Signal mehr)
    app._single_instance_server = single_server  # type: ignore[attr-defined]

    main_window_holder: dict[str, MainWindow | None] = {"window": None}

    def _on_single_instance_request() -> None:
        try:
            while single_server.hasPendingConnections():
                client = single_server.nextPendingConnection()
                if client is None:
                    break
                try:
                    client.waitForReadyRead(40)
                except Exception:
                    pass
                try:
                    client.readAll()
                except Exception:
                    pass
                try:
                    client.disconnectFromServer()
                except Exception:
                    pass
            w0 = main_window_holder.get("window")
            if w0 is not None:
                _focus_main_window(w0)
        except Exception:
            pass

    single_server.newConnection.connect(_on_single_instance_request)

    # Ohne Internet: Offline-Karte aktivieren (je nach Dark/Light), Live-SWPC deaktivieren
    if not check_internet():
        cfg.setdefault("ui", {})["map_offline"] = True
        cfg["ui"]["elevation_live_swpc"] = False
        save_config(cfg)
    load_lang(cfg.get("ui", {}).get("language", "de"))
    log = LogBuffer()
    rig_bridge_manager = RigBridgeManager(cfg.get("rig_bridge", {}), log.write)

    hw = HardwareClient(cfg["hardware_link"], log)
    hw.start()

    rb = cfg["rotor_bus"]
    chw = cfg.get("controller_hw") or {}
    ctrl = RotorController(
        hw,
        rb["master_id"],
        rb["slave_az"],
        rb["slave_el"],
        log,
        enable_az=bool(rb.get("enable_az", True)),
        enable_el=bool(rb.get("enable_el", True)),
        setposcc_ignore_src_master_ids=rb.get(
            "setposcc_ignore_src_master_ids", []
        ),
        setposcc_controller_src_id=int(chw.get("cont_id", 2) or 0),
    )
    ctrl.update_polling(cfg.get("polling_ms", {}))

    pst = PstDualServer(
        cfg["pst_server"]["listen_host"],
        int(cfg["pst_server"]["listen_port_az"]),
        int(cfg["pst_server"]["listen_port_el"]),
        ctrl,
        log,
    )

    # PST-Server beim Programmstart starten (wenn aktiviert)
    if bool(cfg["pst_server"].get("enabled", False)):
        pst.start()

    # SPID BIG-RAS / CAT über serielle Schnittstelle (com0com etc.)
    # Der Manager bekommt einen Zeiger auf die Rig-Bridge, damit
    # Rig-Listener das aktive Profil kennen und Schreibbefehle in die
    # bestehende CAT-Queue legen koennen.
    pst_serial = PstSerialManager(ctrl, log, rig_bridge=rig_bridge_manager)
    pst_serial.update_config(cfg.get("pst_serial", {}))
    if bool(cfg.get("pst_serial", {}).get("enabled", False)):
        pst_serial.start_all()

    # Rig-Bridge-COM **nach** PST-Serial-Listenern: sonst kann Autoconnect zuerst
    # denselben COM-Port öffnen (z. B. com0com-Ende = Profil-COM) und die
    # virtuellen PST-/RIG-Listener bekommen PermissionError (13).
    try:
        if bool(rig_bridge_manager._cfg.enabled) and bool(rig_bridge_manager._cfg.auto_connect):
            rig_bridge_manager.connect_radio_and_autostart_protocols()
    except Exception as exc:
        log.write("WARN", f"Rig-Bridge Autostart fehlgeschlagen: {exc}")

    # UDP UcxLog-Listener (wenn aktiviert)
    udp_ucxlog = UdpUcxLogListener(ctrl, log, cfg=cfg)
    ui_cfg = cfg.get("ui", {})
    udp_ucxlog.start(
        enabled=bool(ui_cfg.get("udp_ucxlog_enabled", False)),
        port=int(ui_cfg.get("udp_ucxlog_port", 12040)),
        listen_host=str(ui_cfg.get("udp_ucxlog_listen_host", "127.0.0.1")),
    )

    # UDP PST-Rotator-Emulation (wenn aktiviert)
    udp_pst = UdpPstRotator(ctrl, log, cfg=cfg)
    udp_pst.start(
        enabled=bool(ui_cfg.get("udp_pst_enabled", True)),
        port=int(ui_cfg.get("udp_pst_port", 12000)),
        listen_host=str(ui_cfg.get("udp_pst_listen_host", "127.0.0.1")),
    )

    # Beim Start einmal prüfen, ob die Rotoren bereits referenziert sind
    ctrl.check_ref_once()

    def save_cfg_cb(new_cfg):
        save_config(new_cfg)
        ctrl.update_polling(new_cfg.get("polling_ms", {}))
        hw.update_cfg(new_cfg["hardware_link"])
        log.write("INFO", "Config gespeichert")

    install_rotortiles_handler()
    # Verhindert, dass Mausrad ueber ComboBoxen/Spinboxen deren Wert aendert,
    # ohne dass das Widget fokussiert ist (gilt global fuer alle Fenster).
    install_wheel_guard(app)
    # App-Icon global setzen (wirkt als Default für alle Fenster)
    app.setWindowIcon(get_app_icon())

    # QObject/Signal NUR nach QApplication – sonst werden Slots u. U. nie aufgerufen
    class AswatchBridge(QObject):
        users = Signal(list)
        airplanes = Signal(list)
        asnearest_summary = Signal(list)

    aswatch_bridge = AswatchBridge(app)
    udp_aswatch = UdpAswatchlistListener(
        log,
        cfg,
        emit_fn=aswatch_bridge.users.emit,
        emit_air_fn=aswatch_bridge.airplanes.emit,
        emit_summary_fn=aswatch_bridge.asnearest_summary.emit,
    )
    udp_aswatch.start(
        enabled=bool(ui_cfg.get("aswatch_udp_enabled", False)),
        port=int(ui_cfg.get("aswatch_udp_port", 9872)),
        listen_host=str(ui_cfg.get("aswatch_udp_listen_host", "127.0.0.1")),
    )

    w = MainWindow(
        cfg,
        ctrl,
        pst,
        hw,
        save_cfg_cb,
        log,
        udp_ucxlog=udp_ucxlog,
        udp_pst=udp_pst,
        udp_aswatch=udp_aswatch,
        aswatch_bridge=aswatch_bridge,
        rig_bridge_manager=rig_bridge_manager,
        pst_serial=pst_serial,
    )
    main_window_holder["window"] = w
    w.resize(1100, 650)
    _focus_main_window(w)

    rc = app.exec()
    try:
        rig_bridge_manager.stop_all()
    except Exception:
        pass
    udp_ucxlog.stop()
    udp_aswatch.stop()
    udp_pst.stop()
    try:
        pst_serial.stop_all()
    except Exception:
        pass
    log.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
