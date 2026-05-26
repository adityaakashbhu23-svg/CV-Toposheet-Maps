# _build/app_entry.py  --  PyInstaller frozen entry point for CV-Toposheet
#
# This file lives in _build/ but adds the project root to sys.path so it
# can import app.py and all other project modules normally.
#
# Two modes when frozen:
#
#   1. Normal startup:   CVToposheet.exe
#      → starts the Flask server and opens the native app window (pywebview).
#
#   2. Script runner:    CVToposheet.exe some_script.py [args...]
#      → app.py calls subprocess([sys.executable, 'script.py']).
#        In a frozen exe sys.executable IS this exe, so we intercept and
#        run the requested script in-process via runpy.
#

import sys
import os
import io
from pathlib import Path

# Force UTF-8 stdout/stderr so emoji/unicode in print() never crash the EXE
try:
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr is not None and hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

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

# ── Normal startup: launch Flask + open native pywebview window ──────────────
import threading
import time

import socket

from app import app as flask_app

HOST = '127.0.0.1'


def _find_free_port(start: int = 5000, end: int = 5100) -> int:
    """Return the first available TCP port in [start, end)."""
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f'No free TCP port found in range {start}–{end}')


PORT = _find_free_port()


def _run_flask():
    """Run Flask server in a background daemon thread."""
    flask_app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def _wait_for_flask(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until Flask is accepting connections, then return True."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


if __name__ == '__main__':
    # 1. Start Flask in background thread
    threading.Thread(target=_run_flask, daemon=True).start()

    # 2. Wait until Flask is actually ready (not just a fixed sleep)
    _wait_for_flask(HOST, PORT)

    # 3. Open native app window (pywebview — no browser, no address bar)
    import webview

    window = webview.create_window(
        title='CV-Toposheet',
        url=f'http://{HOST}:{PORT}',
        width=1280,
        height=820,
        min_size=(900, 600),
        resizable=True,
        maximized=True,
        text_select=True,
    )

    def _intercept_new_windows():
        """
        After every page load: override window.open() so nothing can
        escape to the system browser.  Any popup/new-tab attempt is
        silently redirected to navigate the app window itself.
        """
        window.evaluate_js("""
            (function() {
                var _orig = window.open;
                window.open = function(url, target, features) {
                    if (url) { window.location.href = url; }
                    return null;
                };
            })();
        """)

    window.events.loaded += _intercept_new_windows
    window.events.shown += lambda: window.maximize()

    webview.start()
