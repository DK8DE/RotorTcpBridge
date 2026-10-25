"""Gepinnte Firmware-Bins von GitHub Releases nach Downloads laden."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .app_config import appdata_dir
from .version import (
    APP_VERSION,
    CONTROLLER_FW_VERSION,
    FIRMWARE_UPDATE_REQUIRED,
    FW_DOWNLOAD_ROOT_NAME,
    FW_GITHUB_ORG,
    FW_REPO_CONTROLLER,
    FW_REPO_INCLINOMETER,
    FW_REPO_ROTOR,
    FW_REPO_WLAN,
    INCLINOMETER_FW_VERSION,
    ROTOR_FW_VERSION,
    WLAN_MODULE_FW_VERSION,
)

ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class FirmwareComponent:
    """Eine gepinnte Komponente mit GitHub-Assets."""

    key: str
    repo: str
    version: str
    assets: Tuple[str, ...]
    readme_lines: Tuple[str, ...]

    @property
    def folder_name(self) -> str:
        return f"{self.repo}-{self.version}"

    @property
    def tag(self) -> str:
        return f"v{self.version}"


def _user_downloads_dir() -> Path:
    home = Path.home()
    # Windows: oft übersetzt „Downloads“; USERPROFILE\\Downloads ist Standard.
    for name in ("Downloads", "Download"):
        p = home / name
        if p.is_dir():
            return p
    # Fallback: anlegen
    p = home / "Downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundle_root_for_app(app_version: Optional[str] = None) -> Path:
    """Downloads/Rotor Firmwareupdate/{APP_VERSION}/"""
    ver = str(app_version or APP_VERSION).strip() or APP_VERSION
    return _user_downloads_dir() / FW_DOWNLOAD_ROOT_NAME / ver


def component_dir(comp: FirmwareComponent, *, app_version: Optional[str] = None) -> Path:
    return bundle_root_for_app(app_version) / comp.folder_name


def pinned_components() -> List[FirmwareComponent]:
    """Aktuelle Pins aus version.py."""
    return [
        FirmwareComponent(
            key="rotor",
            repo=FW_REPO_ROTOR,
            version=ROTOR_FW_VERSION,
            assets=(
                f"Rotor-{ROTOR_FW_VERSION}-firmware.bin",
                f"Rotor-{ROTOR_FW_VERSION}-factory.bin",
            ),
            readme_lines=(
                f"Rotor Firmware {ROTOR_FW_VERSION}",
                "",
                "SoftAP-OTA: SETWLAN:1 → http://192.168.4.1 → Update → firmware.bin",
                "USB/Webflasher: factory.bin ab Adresse 0x0",
                f"Release: https://github.com/{FW_GITHUB_ORG}/{FW_REPO_ROTOR}/releases/tag/v{ROTOR_FW_VERSION}",
            ),
        ),
        FirmwareComponent(
            key="controller",
            repo=FW_REPO_CONTROLLER,
            version=CONTROLLER_FW_VERSION,
            assets=(
                f"Rotor_Display_5-{CONTROLLER_FW_VERSION}-update.zip",
                f"Rotor_Display_5-{CONTROLLER_FW_VERSION}-full-install.zip",
            ),
            readme_lines=(
                f"Hardware-Controller (Rotor Display) {CONTROLLER_FW_VERSION}",
                "",
                "USB: ESP Web Tools / update.zip bzw. full-install.zip",
                f"Release: https://github.com/{FW_GITHUB_ORG}/{FW_REPO_CONTROLLER}/releases/tag/v{CONTROLLER_FW_VERSION}",
            ),
        ),
        FirmwareComponent(
            key="inclinometer",
            repo=FW_REPO_INCLINOMETER,
            version=INCLINOMETER_FW_VERSION,
            assets=(
                f"Inklinometer-{INCLINOMETER_FW_VERSION}-firmware.bin",
                f"Inklinometer-{INCLINOMETER_FW_VERSION}-factory.bin",
            ),
            readme_lines=(
                f"Inklinometer {INCLINOMETER_FW_VERSION}",
                "",
                "Nur nötig bei Encoder-Typ 4. SoftAP-OTA bzw. USB factory.bin.",
                f"Release: https://github.com/{FW_GITHUB_ORG}/{FW_REPO_INCLINOMETER}/releases/tag/v{INCLINOMETER_FW_VERSION}",
            ),
        ),
        FirmwareComponent(
            key="wlan",
            repo=FW_REPO_WLAN,
            version=WLAN_MODULE_FW_VERSION,
            assets=(
                f"WLAN_to_RS485_v{WLAN_MODULE_FW_VERSION}_firmware.bin",
                f"WLAN_to_RS485_v{WLAN_MODULE_FW_VERSION}_factory.bin",
            ),
            readme_lines=(
                f"WLAN↔RS485-Modul {WLAN_MODULE_FW_VERSION}",
                "",
                "Nur nötig wenn ein DK8DE WLAN-Modul eingesetzt wird.",
                f"Release: https://github.com/{FW_GITHUB_ORG}/{FW_REPO_WLAN}/releases/tag/v{WLAN_MODULE_FW_VERSION}",
            ),
        ),
    ]


def asset_url(comp: FirmwareComponent, asset: str) -> str:
    return (
        f"https://github.com/{FW_GITHUB_ORG}/{comp.repo}/releases/download/"
        f"{comp.tag}/{asset}"
    )


def _state_path() -> Path:
    return appdata_dir() / "firmware_bundle_state.json"


def load_bundle_state() -> dict:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_bundle_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def current_pin_fingerprint() -> dict:
    return {
        "app_version": APP_VERSION,
        "rotor": ROTOR_FW_VERSION,
        "controller": CONTROLLER_FW_VERSION,
        "inclinometer": INCLINOMETER_FW_VERSION,
        "wlan": WLAN_MODULE_FW_VERSION,
    }


def component_assets_complete(comp: FirmwareComponent, *, app_version: Optional[str] = None) -> bool:
    d = component_dir(comp, app_version=app_version)
    if not d.is_dir():
        return False
    for name in comp.assets:
        p = d / name
        try:
            if not p.is_file() or p.stat().st_size <= 0:
                return False
        except Exception:
            return False
    return True


def all_assets_complete(
    components: Optional[Sequence[FirmwareComponent]] = None,
    *,
    app_version: Optional[str] = None,
) -> bool:
    comps = list(components) if components is not None else pinned_components()
    return all(component_assets_complete(c, app_version=app_version) for c in comps)


def _write_readme(comp: FirmwareComponent, dest: Path) -> None:
    try:
        (dest / "README.txt").write_text(
            "\n".join(comp.readme_lines) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _download_file(url: str, dest: Path, *, timeout_s: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"RotorTcpBridge/{APP_VERSION}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def download_missing_assets(
    *,
    progress: Optional[ProgressCb] = None,
    components: Optional[Sequence[FirmwareComponent]] = None,
    app_version: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Fehlende Assets laden. Returns (all_ok, error_messages)."""
    if not FIRMWARE_UPDATE_REQUIRED:
        return True, []
    comps = list(components) if components is not None else pinned_components()
    errors: List[str] = []
    for comp in comps:
        dest_dir = component_dir(comp, app_version=app_version)
        dest_dir.mkdir(parents=True, exist_ok=True)
        _write_readme(comp, dest_dir)
        for asset in comp.assets:
            target = dest_dir / asset
            if target.is_file() and target.stat().st_size > 0:
                continue
            url = asset_url(comp, asset)
            if progress:
                try:
                    progress(f"{comp.repo}: {asset}")
                except Exception:
                    pass
            try:
                _download_file(url, target)
            except urllib.error.HTTPError as exc:
                errors.append(f"{comp.repo} {comp.tag}/{asset}: HTTP {exc.code}")
            except Exception as exc:
                errors.append(f"{comp.repo} {comp.tag}/{asset}: {exc}")
    ok = not errors and all_assets_complete(comps, app_version=app_version)
    if ok or all_assets_complete(comps, app_version=app_version):
        st = load_bundle_state()
        st["pins"] = current_pin_fingerprint()
        st["downloaded"] = True
        save_bundle_state(st)
    return (len(errors) == 0), errors


def open_bundle_folder() -> Path:
    """Bundle-Root anlegen und Pfad zurückgeben (für QDesktopServices)."""
    root = bundle_root_for_app()
    root.mkdir(parents=True, exist_ok=True)
    return root


def needs_download() -> bool:
    if not FIRMWARE_UPDATE_REQUIRED:
        return False
    return not all_assets_complete()
