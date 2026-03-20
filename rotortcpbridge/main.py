from __future__ import annotations
import sys

# Scheme-Registrierung VOR allen anderen Imports (sonst ignoriert Qt sie)
import rotortcpbridge.webengine_schemes  # noqa: F401

from PySide6.QtWidgets import QApplication

from .app_config import load_config, save_config
from .net_utils import check_internet

from .ui.map_window import install_rotortiles_handler
from .app_icon import get_app_icon
from .i18n import load_lang
from .logutil import LogBuffer
from .hardware_client import HardwareClient
from .rotor_controller import RotorController
from .pst_server import PstDualServer
from .udp_ucxlog import UdpUcxLogListener
from .udp_pst_rotator import UdpPstRotator
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

    hw = HardwareClient(cfg["hardware_link"], log)
    hw.start()

    rb = cfg["rotor_bus"]
    ctrl = RotorController(
        hw,
        rb["master_id"],
        rb["slave_az"],
        rb["slave_el"],
        log,
        enable_az=bool(rb.get("enable_az", True)),
        enable_el=bool(rb.get("enable_el", True)),
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
    if bool(cfg["pst_server"].get("enabled", True)):
        pst.start()

    # UDP UcxLog-Listener (wenn aktiviert)
    udp_ucxlog = UdpUcxLogListener(ctrl, log, cfg=cfg)
    ui_cfg = cfg.get("ui", {})
    udp_ucxlog.start(
        enabled=bool(ui_cfg.get("udp_ucxlog_enabled", False)),
        port=int(ui_cfg.get("udp_ucxlog_port", 12040)),
    )

    # UDP PST-Rotator-Emulation (wenn aktiviert)
    udp_pst = UdpPstRotator(ctrl, log, cfg=cfg)
    udp_pst.start(
        enabled=bool(ui_cfg.get("udp_pst_enabled", False)),
        port=int(ui_cfg.get("udp_pst_port", 12000)),
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
    w = MainWindow(cfg, ctrl, pst, hw, save_cfg_cb, log, udp_ucxlog=udp_ucxlog, udp_pst=udp_pst)
    w.resize(1100, 650)
    w.show()

    rc = app.exec()
    udp_ucxlog.stop()
    udp_pst.stop()
    log.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()

