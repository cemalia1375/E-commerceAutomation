# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for FlowCut backend server.

构建命令（在 SimpleClaw/ 目录下）：
    pyinstaller flowcut.spec --noconfirm

产物：dist/flowcut_server/flowcut_server.exe（含所有依赖）
复制整个 dist/flowcut_server/ 目录到 Electron extraResources/backend/
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['Flowcut/api/server.py'],
    pathex=[str(Path(__file__).parent)],
    binaries=[],
    datas=[
        # workspace 提示词文件 → 打包后位于 _MEIPASS/workspace/
        ('Flowcut/workspace', 'workspace'),
    ],
    hiddenimports=[
        # scenedetect pyav 后端（动态加载，需显式声明）
        'av',
        'scenedetect.backends.pyav',
        # asyncio / uvloop 相关
        'asyncio',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # aiomysql
        'aiomysql',
        # google-genai
        'google.genai',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # playwright/chromium 绝不打包
        'playwright',
        'playwright.async_api',
        # 排除 opencv（已切换到 pyav）
        'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='flowcut_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 保留控制台便于排查问题；发布版可改 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='flowcut_server',
)
