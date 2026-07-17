# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# PyInstaller 静态分析追踪不到 uvicorn 字符串引用 'Flowcut.api.server:app'
# 必须显式列出 Flowcut 所有子模块
_flowcut_modules = [
    'Flowcut', 'Flowcut.agent', 'Flowcut.agent.capabilities', 'Flowcut.agent.cold_path',
    'Flowcut.agent.first_token', 'Flowcut.agent.main_agent', 'Flowcut.agent.postprocess',
    'Flowcut.api', 'Flowcut.api.container', 'Flowcut.api.deps', 'Flowcut.api.routes',
    'Flowcut.api.routes.auth', 'Flowcut.api.routes.chat', 'Flowcut.api.routes.creatives',
    'Flowcut.api.routes.health', 'Flowcut.api.routes.highlight_assets',
    'Flowcut.api.routes.materials', 'Flowcut.api.routes.qianchuan',
    'Flowcut.api.routes.reference_videos', 'Flowcut.api.routes.scripts',
    'Flowcut.api.routes.sessions', 'Flowcut.api.routes.tasks', 'Flowcut.api.server',
    'Flowcut.auth', 'Flowcut.auth.security', 'Flowcut.browser', 'Flowcut.browser.client',
    'Flowcut.browser.network', 'Flowcut.config', 'Flowcut.context',
    'Flowcut.context.providers', 'Flowcut.runtime', 'Flowcut.runtime.executors',
    'Flowcut.runtime.reconcile', 'Flowcut.runtime.streams', 'Flowcut.runtime.worker',
    'Flowcut.scripts', 'Flowcut.scripts.audit_consistency', 'Flowcut.scripts.check_highlight',
    'Flowcut.scripts.compare_scene_detect', 'Flowcut.scripts.create_user',
    'Flowcut.scripts.cron_qianchuan_sync', 'Flowcut.scripts.import_qianchuan_cookies',
    'Flowcut.scripts.perf_highlight_baseline', 'Flowcut.scripts.perf_highlight_plan',
    'Flowcut.scripts.record_qianchuan_traffic', 'Flowcut.scripts.reset_db',
    'Flowcut.scripts.reverse_highlight', 'Flowcut.scripts.reverse_highlight.align',
    'Flowcut.scripts.reverse_highlight.run', 'Flowcut.scripts.spike_asr_response',
    'Flowcut.services', 'Flowcut.services.clip_planner', 'Flowcut.services.douyin_client',
    'Flowcut.services.embedding', 'Flowcut.services.gemini_video',
    'Flowcut.services.material_matcher', 'Flowcut.services.qianchuan_publisher',
    'Flowcut.services.qianchuan_scraper', 'Flowcut.services.scene_align',
    'Flowcut.services.script_generator', 'Flowcut.services.zip_parser',
    'Flowcut.storage', 'Flowcut.storage.creative_repo', 'Flowcut.storage.database',
    'Flowcut.storage.highlight_asset_repo', 'Flowcut.storage.material_repo',
    'Flowcut.storage.oss_client', 'Flowcut.storage.qianchuan_repo',
    'Flowcut.storage.reference_video_repo', 'Flowcut.storage.script_repo',
    'Flowcut.storage.session_repo', 'Flowcut.storage.session_store',
    'Flowcut.storage.task_repo', 'Flowcut.storage.user_repo', 'Flowcut.storage.vector_store',
    'Flowcut.tools', 'Flowcut.tools.account_stats', 'Flowcut.tools.check_task_status',
    'Flowcut.tools.compose_video', 'Flowcut.tools.create_cross_episode_highlights',
    'Flowcut.tools.creative_stats', 'Flowcut.tools.decompose_video',
    'Flowcut.tools.export_package', 'Flowcut.tools.generate_scripts',
    'Flowcut.tools.list_highlight_assets', 'Flowcut.tools.match_by_script',
    'Flowcut.tools.material_stats', 'Flowcut.tools.navigate_to',
    'Flowcut.tools.publish_to_qianchuan', 'Flowcut.tools.search_creatives',
    'Flowcut.tools.search_materials', 'Flowcut.tools.search_materials_by_name',
    'Flowcut.tools.update_script', 'Flowcut.tools.upload_script',
]

# workspace/*.md 文件需要打包进去（系统 prompt）
workspace_files = [(str(p), 'Flowcut/workspace') for p in Path('Flowcut/workspace').glob('*.md')]
# journey 子目录
journey_files = [(str(p), 'Flowcut/workspace/journey') for p in Path('Flowcut/workspace/journey').glob('*.md') if Path('Flowcut/workspace/journey').exists()]

a = Analysis(
    ['flowcut_server_entry.py'],
    pathex=['.'],
    binaries=[],
    datas=workspace_files + journey_files,
    hiddenimports=sorted(set(_flowcut_modules + collect_submodules('Flowcut') + [
        # FastAPI / Starlette
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        # aiomysql / PyMySQL
        'aiomysql',
        'pymysql',
        'pymysql.converters',
        # google-genai
        'google.genai',
        # aiohttp
        'aiohttp',
        'aiohttp.connector',
        # scenedetect
        'scenedetect',
        'scenedetect.detectors',
        # bcrypt
        'bcrypt',
        # json_repair
        'json_repair',
        # loguru
        'loguru',
        # pydantic
        'pydantic',
        'pydantic.deprecated.class_validators',
        'pydantic.v1',
    ])),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'notebook', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='flowcut_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
