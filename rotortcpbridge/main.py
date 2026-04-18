from __future__ import annotations
import sys

# Scheme-Registrierung VOR allen anderen Imports (sonst ignoriert Qt sie)
import rotortcpbridge.webengine_schemes  # noqa: F401

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from .app_config import load_config, save_config
from .net_utils import check_internet

from .ui.map_tiles import install_rotortiles_handler
from .app_icon import get_app_icon
from .i18n import load_lang
from .logutil import LogBuffer
from .hardware_client import HardwareClient
from .rotor_controller import RotorController
from .pst_server import PstDualServer
from .udp_ucxlog import UdpUcxLogListener
from .udp_aswatchlist import UdpAswatchlistListener
from .udp_pst_rotator import UdpPstRotator
from .rig_bridge.manager import RigBridgeManager
from .ui.main_window import MainWindow


def main():
    cfg = load_config()
    # Ohne Internet: Offline-Karte aktivieren (je nach Dark/Light), Live-SWPC deaktivieren
    if not check_internet():
        cfg.setdefault("ui", {})["map_offline"] = True
        cfg["ui"]["elevation_live_swpc"] = False
        save_config(cfg)
    load_lang(cfg.get("ui", {}).get("language", "de"))
    log = LogBuffer()
    rig_bridge_manager = RigBridgeManager(cfg.get("rig_bridge", {}), log.write)
    try:
        rb_cfg = cfg.get("rig_bridge", {})
        if bool(rb_cfg.get("enabled", False)) and bool(rb_cfg.get("auto_connect", False)):
            rig_bridge_manager.connect_radio_and_autostart_protocols()
    except Exception as exc:
        log.write("WARN", f"Rig-Bridge Autostart fehlgeschlagen: {exc}")

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
        listen_host=str(ui_cfg.get("udp_pst_listen_host", "0.0.0.0")),
    )

    # Beim Start einmal prüfen, ob die Rotoren bereits referenziert sind
    ctrl.check_ref_once()

    def save_cfg_cb(new_cfg):
        save_config(new_cfg)
        ctrl.update_polling(new_cfg.get("polling_ms", {}))
        hw.update_cfg(new_cfg["hardware_link"])
        log.write("INFO", "Config gespeichert")

    app = QApplication(sys.argv)
    install_rotortiles_handler()
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
    )
    w.resize(1100, 650)
    w.show()

    rc = app.exec()
    try:
        rig_bridge_manager.stop_all()
    except Exception:
        pass
    udp_ucxlog.stop()
    udp_aswatch.stop()
    udp_pst.stop()
    log.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
