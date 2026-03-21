# -*- mode: python ; coding: utf-8 -*-
import re, os

# ── Version aus version.py lesen ──────────────────────────────────────────
_ver_src = open('rotortcpbridge/version.py', encoding='utf-8').read()
APP_VERSION   = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',   _ver_src).group(1)
APP_AUTHOR    = re.search(r'APP_AUTHOR\s*=\s*"([^"]+)"',    _ver_src).group(1)
APP_COPYRIGHT = re.search(r'APP_COPYRIGHT\s*=\s*"([^"]+)"', _ver_src).group(1)

# Windows benötigt 4-teilige Version (Major.Minor.Patch.Build)
_parts = (APP_VERSION + '.0.0.0').split('.')[:4]
_vi    = tuple(int(x) for x in _parts)

# Windows Version-Info-Datei erzeugen (sichtbar in Datei-Eigenschaften)
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
        StringStruct('FileDescription',  'RotorTcpBridge'),
        StringStruct('FileVersion',      '{APP_VERSION}'),
        StringStruct('InternalName',     'RotorTcpBridge'),
        StringStruct('LegalCopyright',   '{APP_COPYRIGHT}'),
        StringStruct('OriginalFilename', 'RotorTcpBridge.exe'),
        StringStruct('ProductName',      'RotorTcpBridge'),
        StringStruct('ProductVersion',   '{APP_VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])
  ]
)"""
with open('_version_info.txt', 'w', encoding='utf-8') as _f:
    _f.write(_ver_info)
# ──────────────────────────────────────────────────────────────────────────

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bilder und Icons
        ('rotortcpbridge\\rotor.ico',               'rotortcpbridge'),
        ('rotortcpbridge\\windPfeil.png',            'rotortcpbridge'),
        ('rotortcpbridge\\Antenne.png',              'rotortcpbridge'),
        ('rotortcpbridge\\Antenne_T.png',            'rotortcpbridge'),
        ('rotortcpbridge\\User.PNG',                 'rotortcpbridge'),
        # Sprachdateien
        ('rotortcpbridge\\locales\\de.json',         'rotortcpbridge\\locales'),
        ('rotortcpbridge\\locales\\en.json',         'rotortcpbridge\\locales'),
        # Leaflet + Maidenhead (Offline-Karte, inline eingebettet)
        ('rotortcpbridge\\static\\leaflet.min.js',   'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\leaflet.css',      'rotortcpbridge\\static'),
        ('rotortcpbridge\\static\\maidenhead.js',    'rotortcpbridge\\static'),
    ],
    hiddenimports=[],
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
    name='RotorTcpBridge',
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
    version='_version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    # Offline-Kartenkacheln (z/x/y.png – Tree() sammelt alle Unterordner automatisch)
    Tree('rotortcpbridge\\KartenLight', prefix='rotortcpbridge\\KartenLight'),
    Tree('rotortcpbridge\\KartenDark',  prefix='rotortcpbridge\\KartenDark'),
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RotorTcpBridge',
)
