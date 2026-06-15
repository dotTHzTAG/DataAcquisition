# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\main.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\ui', 'ui'), ('C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\resources', 'resources'), ('C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\version.json', '.'), ('C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\profile_database.h5', '.')],
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
    name='DataAcquisition',
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
    icon=['C:\\Users\\Jongmin Lee\\Documents\\GitHub\\DataAcquisition\\resources\\icon.ico'],
    contents_directory='.',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DataAcquisition',
)
