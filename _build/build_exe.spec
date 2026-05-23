# _build/build_exe.spec  --  PyInstaller spec for CV-Toposheet
#
# Spec lives in _build/ — all paths are resolved relative to the project root
# automatically via SPECPATH (PyInstaller variable = directory of this .spec file).
#
# Run from project root:
#   pyinstaller _build\build_exe.spec --clean --noconfirm
#   (or just double-click  _build\build_exe.bat)
#
# Output: dist\CVToposheet\CVToposheet.exe  (next to the project root)
# NOTE : copy your .env into dist\CVToposheet\ before running the exe.
#
# -*- mode: python ; coding: utf-8 -*-

import os

# SPECPATH = directory containing this spec file = _build/
# root     = project root  (parent of _build/)
root = os.path.dirname(SPECPATH)

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

flask_datas,    flask_bins,    flask_hi    = collect_all('flask')
werkzeug_datas, werkzeug_bins, werkzeug_hi = collect_all('werkzeug')
jinja2_datas,   jinja2_bins,   jinja2_hi   = collect_all('jinja2')
webview_datas,  webview_bins,  webview_hi  = collect_all('webview')


def R(*parts):
    """Return absolute path under project root."""
    return os.path.join(root, *parts)


datas = (
    flask_datas + werkzeug_datas + jinja2_datas + webview_datas
    + [
        # ── Entry point (lives in _build/) ─────────────────────────
        (os.path.join(SPECPATH, 'app_entry.py'), '.'),
        # ── Pipeline scripts ────────────────────────────────────────
        (R('app.py'),                 '.'),
        (R('config.py'),              '.'),
        (R('process_new_maps.py'),    '.'),
        (R('export_table.py'),        '.'),
        (R('run_pipeline.py'),        '.'),
        (R('resume_llm.py'),          '.'),
        (R('rename_maps.py'),         '.'),
        (R('1_tile_maps.py'),         '.'),
        (R('2_ocr_extraction.py'),    '.'),
        (R('3_grid_detection.py'),    '.'),
        (R('4_llm_cleaning.py'),      '.'),
        (R('5_database_assembly.py'), '.'),
        (R('6_query_interface.py'),   '.'),
        (R('7_gold_standard.py'),     '.'),
        (R('8_active_learning.py'),   '.'),
        (R('9_symbol_detector.py'),   '.'),
        # ── Utils package ───────────────────────────────────────────
        (R('utils', '__init__.py'),       'utils'),
        (R('utils', 'coords_utils.py'),   'utils'),
        (R('utils', 'cpu_utils.py'),      'utils'),
        (R('utils', 'db_utils.py'),       'utils'),
        (R('utils', 'image_utils.py'),    'utils'),
        (R('utils', 'llm_utils.py'),      'utils'),
        (R('utils', 'metadata_utils.py'), 'utils'),
        (R('utils', 'ocr_utils.py'),      'utils'),
        # ── Prompt text / spelling files ────────────────────────────
        (R('prompts'), 'prompts'),
        # ── Google Cloud credentials ─────────────────────────────────
        # NOTE: Credentials are NOT bundled here for public distribution.
        # For a PRIVATE build (personal use / trusted users only), you can
        # uncomment the two lines below to bake your credentials into the exe:
        #   (R('service_account.json'),  '.'),
        #   (R('service_account2.json'), '.'),
        # For public distribution, leave these commented — users supply their
        # own JSON file via the Settings page in the app (or place it next to
        # the exe after installation).
    ]
)

hiddenimports = (
    flask_hi + werkzeug_hi + jinja2_hi + webview_hi
    + collect_submodules('flask')
    + collect_submodules('werkzeug')
    + collect_submodules('webview')
    + [
        'utils', 'utils.coords_utils', 'utils.cpu_utils', 'utils.db_utils',
        'utils.image_utils', 'utils.llm_utils', 'utils.metadata_utils', 'utils.ocr_utils',
        'cv2',
        'PIL', 'PIL.Image', 'PIL.ImageOps', 'PIL.ImageDraw',
        'numpy',
        'easyocr',
        'openai',
        'google.cloud.vision', 'google.cloud.vision_v1',
        'google.auth', 'google.auth.transport', 'google.auth.transport.requests',
        'google.oauth2', 'google.oauth2.service_account',
        'google.generativeai',
        'pandas', 'tqdm', 'psutil',
        'dotenv', 'sqlite3', 'importlib', 'runpy', 'threading', 'queue',
        'webview', 'webview.platforms', 'webview.platforms.winforms',
        'pythonnet', 'clr', 'bottle', 'proxy_tools',
    ]
)

a = Analysis(
    [os.path.join(SPECPATH, 'app_entry.py')],
    pathex=[root],
    binaries=flask_bins + werkzeug_bins + jinja2_bins,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'matplotlib', 'scipy', 'IPython', 'jupyter',
              'notebook', 'pytest', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='CVToposheet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=os.path.join(SPECPATH, 'app_icon.ico'),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CVToposheet',
)
