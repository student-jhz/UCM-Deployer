# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单文件窗口程序 UCM-Deployer.exe
# 构建：.venv\Scripts\python -m PyInstaller --noconfirm --clean ucm_deployer.spec

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('docs/用户手册.md', 'docs')],
    hiddenimports=[
        'paramiko',
        'cryptography',
        'cryptography.hazmat.bindings._rust',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'pytest',
        'IPython',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UCM-Deployer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
