"""Versions- und Metadaten für RotorTcpBridge."""

APP_NAME = "RotorTcpBridge"
APP_VERSION = "2.5"
APP_DATE = "25.10.2026"
APP_AUTHOR = "Jörg Körner DK8DE"
APP_COPYRIGHT = "2026 Jörg Körner DK8DE"

# True = Rotor-Versionscheck und Update-Hinweise beim Start
FIRMWARE_UPDATE_REQUIRED = True

# Empfohlene Firmware (Vergleich mit Live-GETVERSION)
# Quelle: neueste GitHub-Releases der Repos unten (scripts/sync_fw_versions_from_github.py)
ROTOR_FW_VERSION = "1.10.11"
CONTROLLER_FW_VERSION = "1.5.9"
INCLINOMETER_FW_VERSION = "1.3.7"
WLAN_MODULE_FW_VERSION = "1.5.1"

# Download-Seite (kein Auto-Download in der App)
DOWNLOAD_PAGE_URL = "https://dk8.de/de/download/"

# GitHub: App-Release-Check beim Start
APP_GITHUB_ORG = "DK8DE"
APP_GITHUB_REPO = "RotorTcpBridge"

# GitHub-Repos für empfohlene Firmware (Release-Tag → version.py)
# Org = APP_GITHUB_ORG, sofern nicht anders gesetzt.
FW_GITHUB_REPOS: dict[str, str] = {
    "ROTOR_FW_VERSION": "Rotor_Firmware",
    "CONTROLLER_FW_VERSION": "Rotor_Display_5",
    "INCLINOMETER_FW_VERSION": "Inklinometer_IIS2ICLXTR",
    "WLAN_MODULE_FW_VERSION": "WLAN_to_RS485_Modul",
}
