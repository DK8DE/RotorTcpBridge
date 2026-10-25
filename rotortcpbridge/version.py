"""Versions- und Metadaten für RotorTcpBridge."""

APP_NAME = "RotorTcpBridge"
APP_VERSION = "2.4.3"
APP_DATE = "25.10.2026"
APP_AUTHOR = "Jörg Körner DK8DE"
APP_COPYRIGHT = "2026 Jörg Körner DK8DE"

# True = gepinnte Firmware-Bins von GitHub laden + Update-Hinweise aktiv
FIRMWARE_UPDATE_REQUIRED = True

# Gepinnte Firmware (GitHub-Release-Tag v{X.Y.Z} muss existieren)
ROTOR_FW_VERSION = "1.10.8"
CONTROLLER_FW_VERSION = "1.5.9"
INCLINOMETER_FW_VERSION = "1.3.4"
WLAN_MODULE_FW_VERSION = "1.5.1"

# GitHub-Org und Repos für Bundle-Download
FW_GITHUB_ORG = "DK8DE"
FW_REPO_ROTOR = "Rotor_Firmware"
FW_REPO_CONTROLLER = "Rotor_Display_5"
FW_REPO_INCLINOMETER = "Inklinometer_IIS2ICLXTR"
FW_REPO_WLAN = "WLAN_to_RS485_Modul"

# Unterordner unter Benutzer-Downloads
FW_DOWNLOAD_ROOT_NAME = "Rotor Firmwareupdate"
