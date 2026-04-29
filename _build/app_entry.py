# _build/app_entry.py  --  PyInstaller frozen entry point for CV-Toposheet
#
# This file lives in _build/ but adds the project root to sys.path so it
# can import app.py and all other project modules normally.
#
# Two modes when frozen:
#
#   1. Normal startup:   CVToposheet.exe
#      → starts the Flask server and opens the browser automatically.
#
#   2. Script runner:    CVToposheet.exe some_script.py [args...]
#      → app.py calls subprocess([sys.executable, 'script.py']).
#        In a frozen exe sys.executable IS this exe, so we intercept and
#        run the requested script in-process via runpy.
#

import sys
import os
from pathlib import Path

# ── Add project root to sys.path (needed when running from _build/) ───────────
_this_dir = Path(__file__).resolve().parent   # _build/
_root_dir  = _this_dir.parent                  # project root
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

# ── Frozen script-runner shim ─────────────────────────────────────────────────
if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
    import runpy

    script_arg = sys.argv[1]
    sys.argv   = [sys.argv[0]] + sys.argv[2:]

    script_path = Path(script_arg)
    if not script_path.is_absolute():
        for candidate in (Path.cwd() / script_arg,
                          Path(sys._MEIPASS) / script_arg):
            if candidate.exists():
                script_path = candidate
                break

    runpy.run_path(str(script_path), run_name='__main__')
    sys.exit(0)

# ── Normal startup: launch Flask and open browser ─────────────────────────────
import webbrowser
import threading

from app import app as flask_app

HOST = '127.0.0.1'
PORT = 5000


def _open_browser():
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://{HOST}:{PORT}')


if __name__ == '__main__':
    print(f'CV-Toposheet starting at http://{HOST}:{PORT} ...')
    threading.Thread(target=_open_browser, daemon=True).start()
    flask_app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
