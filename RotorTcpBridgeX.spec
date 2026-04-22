# -*- mode: python ; coding: utf-8 -*-
# Alternative EXE „RotorTcpBridgeX“ — gleicher Funktionsumfang wie RotorTcpBridge.spec
# (Assets, Offline-Karten, WebEngine-Hiddenimports). Nur Produkt-/EXE-Name abweichend.
import re

# ── Version aus version.py lesen ──────────────────────────────────────────
_ver_src = open('rotortcpbridge/version.py', encoding='utf-8').read()
APP_VERSION   = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',   _ver_src).group(1)
APP_AUTHOR    = re.search(r'APP_AUTHOR\s*=\s*"([^"]+)"',    _ver_src).group(1)
APP_COPYRIGHT = re.search(r'APP_COPYRIGHT\s*=\s*"([^"]+)"', _ver_src).group(1)

_parts = (APP_VERSION + '.0.0.0').split('.')[:4]
_vi    = tuple(int(x) for x in _parts)

_ver_info = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_vi},
    prodvers={_vi},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040704b0', [
        StringStruct('CompanyName',      '{APP_AUTHOR}'),
        StringStruct('FileDescription',  'RotorTcpBridgeX'),
        StringStruct('FileVersion',      '{APP_VERSION}'),
        StringStruct('InternalName',     'RotorTcpBridgeX'),
        StringStruct('LegalCopyright',   '{APP_COPYRIGHT}'),
        StringStruct('OriginalFilename', 'RotorTcpBridgeX.exe'),
        StringStruct('ProductName',      'RotorTcpBridgeX'),
        StringStruct('ProductVersion',   '{APP_VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])
  ]
)"""
with open('_version_info_x.txt', 'w', encoding='utf-8') as _f:
    _f.write(_ver_info)
# ──────────────────────────────────────────────────────────────────────────

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('rotortcpbridge\\rotor.ico',               'rotortcpbridge'),
        ('rotortcpbridge\\windPfeil.png',            'rotortcpbridge'),
        ('rotortcpbridge\\Antenne.png',              'rotortcpbridge'),
        ('rotortcpbridge\\Antenne_T.png',            'rotortcpbridge'),
        ('rotortcpbridge\\User.PNG',                 'rotortcpbridge'),
        ('rotortcpbridge\\User_ACC.png',             'rotortcpbridge'),
        ('rotortcpbridge\\InstallerSmall.png',       'rotortcpbridge'),
        ('rotortcpbridge\\locales\\de.json',         'rotortcpbridge\\locales'),
        ('rotortcpbridge\\locales\\en.json',         'rotortcpbridge\\locales'),
        ('rotortcpbridge\\static\\leaflet.min.js',   'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\leaflet.css',      'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\maidenhead.js',    'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\leaflet.markercluster.js', 'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\MarkerCluster.css', 'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\MarkerCluster.Default.css', 'rotortcpbridge\\static'),
    ],
    hiddenimports=[
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineCore',
        'serial',
        'serial.tools.list_ports',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RotorTcpBridgeX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['rotortcpbridge\\rotor.ico'],
    version='_version_info_x.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    Tree('rotortcpbridge\\KartenLight', prefix='rotortcpbridge\\KartenLight'),
    Tree('rotortcpbridge\\KartenDark',  prefix='rotortcpbridge\\KartenDark'),
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RotorTcpBridgeX',
)
