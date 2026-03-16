# -*- mode: python ; coding: utf-8 -*-


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
