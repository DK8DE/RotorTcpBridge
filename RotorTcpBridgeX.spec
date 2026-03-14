# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('rotortcpbridge\\rotor.ico',          'rotortcpbridge'),
        ('rotortcpbridge\\windPfeil.png',       'rotortcpbridge'),
        ('rotortcpbridge\\locales\\de.json',    'rotortcpbridge\\locales'),
        ('rotortcpbridge\\locales\\en.json',    'rotortcpbridge\\locales'),
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RotorTcpBridgeX',
)
