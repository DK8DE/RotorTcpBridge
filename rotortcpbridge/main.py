from __future__ import annotations
import sys
import threading

# Scheme-Registrierung VOR allen anderen Imports (sonst ignoriert Qt sie)
import rotortcpbridge.webengine_schemes  # noqa: F401

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .app_config import load_config, save_config

from .ui.map_tiles import install_rotortiles_handler
from .ui.wheel_guard import install_wheel_guard
from .app_icon import get_app_icon
from .i18n import load_lang
from .logutil import LogBuffer
from .hardware_client import HardwareClient
from .controller_remote_usb import ControllerRemoteUsbProxy
from .rotor_controller import RotorController
from .pst_server import PstDualServer
from .rotctld_server import RotctldServer, DEFAULT_ROTCTLD_PORT
from .gs232_server import Gs232Server, DEFAULT_GS232_PORT
from .easycomm_server import EasycommServer, DEFAULT_EASYCOMM_PORT
from .dcu1_server import Dcu1Server, DEFAULT_DCU1_PORT
from .n1mm_rotor_udp import N1mmRotorUdp
from .map_webserver import MapWebServer, DEFAULT_MAP_WEBSERVER_HOST, DEFAULT_MAP_WEBSERVER_PORT
from .pst_serial import PstSerialManager
from .udp_ucxlog import UdpUcxLogListener
from .udp_aswatchlist import UdpAswatchlistListener
from .udp_pst_rotator import UdpPstRotator
from .pst_target_push import PstTargetPush
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

    load_lang(cfg.get("ui", {}).get("language", "de"))
    log = LogBuffer()
    rig_bridge_manager = RigBridgeManager(cfg.get("rig_bridge", {}), log.write)

    hw = HardwareClient(cfg["hardware_link"], log)
    hw.start()

    ctrl_hw = HardwareClient(cfg.get("controller_link") or {}, log)

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

    remote_proxy = ControllerRemoteUsbProxy(hw, ctrl_hw, ctrl, log)
    remote_proxy.update_cfg(cfg)
    # Mode-Switch nicht im GUI-Start-Thread blockieren (früher: activate + Wait → langer Start).
    if ControllerRemoteUsbProxy.want_remote(cfg):
        threading.Thread(
            target=lambda: remote_proxy.apply_mode(True, timeout_s=10.0),
            name="remote-usb-boot",
            daemon=True,
        ).start()

    pst = PstDualServer(
        cfg["pst_server"]["listen_host"],
        int(cfg["pst_server"]["listen_port_az"]),
        int(cfg["pst_server"]["listen_port_el"]),
        ctrl,
        log,
        cfg=cfg,
    )

    # PST-Server beim Programmstart starten (wenn aktiviert)
    if bool(cfg["pst_server"].get("enabled", False)):
        pst.start()

    # Hamlib-kompatibler rotctld-TCP-Server (gpredict, SatNOGS, ...)
    rotctld_cfg = cfg.get("rotctld_server", {})
    rotctld = RotctldServer(
        str(rotctld_cfg.get("listen_host", "127.0.0.1")),
        int(rotctld_cfg.get("listen_port", DEFAULT_ROTCTLD_PORT)),
        ctrl,
        log,
        cfg=cfg,
    )
    if bool(rotctld_cfg.get("enabled", False)):
        rotctld.start()

    gs232_cfg = cfg.get("gs232_server", {}) or {}
    gs232 = Gs232Server(
        str(gs232_cfg.get("listen_host", "127.0.0.1")),
        int(gs232_cfg.get("listen_port", DEFAULT_GS232_PORT)),
        ctrl,
        log,
        cfg=cfg,
    )
    if bool(gs232_cfg.get("enabled", False)):
        gs232.start()

    easycomm_cfg = cfg.get("easycomm_server", {}) or {}
    easycomm = EasycommServer(
        str(easycomm_cfg.get("listen_host", "127.0.0.1")),
        int(easycomm_cfg.get("listen_port", DEFAULT_EASYCOMM_PORT)),
        ctrl,
        log,
        cfg=cfg,
    )
    if bool(easycomm_cfg.get("enabled", False)):
        easycomm.start()

    dcu1_cfg = cfg.get("dcu1_server", {}) or {}
    dcu1 = Dcu1Server(
        str(dcu1_cfg.get("listen_host", "127.0.0.1")),
        int(dcu1_cfg.get("listen_port", DEFAULT_DCU1_PORT)),
        ctrl,
        log,
        cfg=cfg,
    )
    if bool(dcu1_cfg.get("enabled", False)):
        dcu1.start()

    n1mm_rotor = N1mmRotorUdp(ctrl, log, cfg=cfg)

    # Antennenkarte als HTTP-Webserver (Start erst nach MapWindow-Wiring in MainWindow)
    mws_cfg = cfg.get("map_webserver", {}) or {}
    map_webserver = MapWebServer(
        str(mws_cfg.get("listen_host", DEFAULT_MAP_WEBSERVER_HOST)),
        int(mws_cfg.get("listen_port", DEFAULT_MAP_WEBSERVER_PORT)),
        log,
        password=str(mws_cfg.get("password", "rotor") or ""),
    )

    # SPID BIG-RAS / CAT über serielle Schnittstelle (com0com etc.)
    # Der Manager bekommt einen Zeiger auf die Rig-Bridge, damit
    # Rig-Listener das aktive Profil kennen und Schreibbefehle in die
    # bestehende CAT-Queue legen koennen.
    pst_serial = PstSerialManager(ctrl, log, rig_bridge=rig_bridge_manager, cfg=cfg)
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

    # UDP UcxLog-Listener (wenn aktiviert). Konflikt mit N1MM auf 12040:
    # N1MM hat Vorrang, falls beide in der Config aktiv wären.
    udp_ucxlog = UdpUcxLogListener(ctrl, log, cfg=cfg)
    ui_cfg = cfg.get("ui", {})
    n1mm_cfg = cfg.get("n1mm_rotor", {}) or {}
    n1mm_on = bool(n1mm_cfg.get("enabled", False))
    ucx_on = bool(ui_cfg.get("udp_ucxlog_enabled", False))
    if n1mm_on and ucx_on:
        log.write(
            "WARN",
            "N1MM Rotor UDP und UcxLog gleichzeitig aktiv — UcxLog wird nicht gestartet (Port 12040).",
        )
        ucx_on = False
    udp_ucxlog.start(
        enabled=ucx_on,
        port=int(ui_cfg.get("udp_ucxlog_port", 12040)),
        listen_host=str(ui_cfg.get("udp_ucxlog_listen_host", "127.0.0.1")),
    )
    n1mm_rotor.start(
        enabled=n1mm_on,
        listen_host=str(n1mm_cfg.get("listen_host", "127.0.0.1")),
        listen_port=int(n1mm_cfg.get("listen_port", 12040)),
        broadcast_host=str(n1mm_cfg.get("broadcast_host", "127.0.0.1")),
        broadcast_port=int(n1mm_cfg.get("broadcast_port", 13010)),
        rotor_name=str(n1mm_cfg.get("rotor_name", "") or ""),
    )

    # UDP PST-Rotator-Emulation (wenn aktiviert)
    udp_pst = UdpPstRotator(ctrl, log, cfg=cfg)
    udp_pst.start(
                enabled=bool(ui_cfg.get("udp_pst_enabled", False)),
        port=int(ui_cfg.get("udp_pst_port", 12000)),
        listen_host=str(ui_cfg.get("udp_pst_listen_host", "127.0.0.1")),
    )

    # Ausgehender Ziel-Push an PstRotator (Soll-Zeiger setzen; parallel zum SPID-TCP-Server)
    pst_target_push = PstTargetPush(ctrl, log, cfg=cfg)
    pst_target_push.start(
        enabled=bool(ui_cfg.get("pst_target_push_enabled", False)),
        host=str(ui_cfg.get("pst_target_push_host", "127.0.0.1")),
        port=int(ui_cfg.get("pst_target_push_port", 12000)),
    )

    def save_cfg_cb(new_cfg):
        save_config(new_cfg)
        ctrl.update_polling(new_cfg.get("polling_ms", {}))
        hw.update_cfg(new_cfg["hardware_link"])
        try:
            remote_proxy.update_cfg(new_cfg)
            want = ControllerRemoteUsbProxy.want_remote(new_cfg)
            if bool(remote_proxy.is_active()) != want:
                threading.Thread(
                    target=lambda: remote_proxy.apply_mode(want, timeout_s=8.0),
                    name="remote-usb-cfg",
                    daemon=True,
                ).start()
        except Exception as exc:
            log.write("WARN", f"Controller Remote USB update: {exc}")
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
        pst_target_push=pst_target_push,
        udp_aswatch=udp_aswatch,
        aswatch_bridge=aswatch_bridge,
        rig_bridge_manager=rig_bridge_manager,
        pst_serial=pst_serial,
        rotctld_server=rotctld,
        gs232_server=gs232,
        easycomm_server=easycomm,
        dcu1_server=dcu1,
        n1mm_rotor=n1mm_rotor,
        map_webserver=map_webserver,
        controller_remote_proxy=remote_proxy,
        ctrl_hw=ctrl_hw,
    )
    main_window_holder["window"] = w
    w.resize(1100, 650)
    _focus_main_window(w)
    # Erst wenn das Hauptfenster sichtbar ist mit Bus-Abfragen beginnen.
    QTimer.singleShot(250, ctrl.check_ref_once)

    rc = app.exec()
    try:
        remote_proxy.stop()
    except Exception:
        pass
    try:
        ctrl_hw.stop()
    except Exception:
        pass
    try:
        hw.stop()
    except Exception:
        pass
    try:
        rig_bridge_manager.stop_all()
    except Exception:
        pass
    udp_ucxlog.stop()
    try:
        n1mm_rotor.stop()
    except Exception:
        pass
    udp_aswatch.stop()
    udp_pst.stop()
    try:
        rotctld.stop()
    except Exception:
        pass
    try:
        gs232.stop()
    except Exception:
        pass
    try:
        easycomm.stop()
    except Exception:
        pass
    try:
        dcu1.stop()
    except Exception:
        pass
    try:
        map_webserver.stop()
    except Exception:
        pass
    try:
        pst_serial.stop_all()
    except Exception:
        pass
    log.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
