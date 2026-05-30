# app.py  –  CV-Toposheet Web Interface
#
# A simple Flask cover page: upload a map → pipeline runs → view results.
#

import sys
import time
import threading
import queue
import json
import os
import re
import uuid
import shutil
import sqlite3
import importlib
import subprocess
from pathlib import Path
from urllib.parse import quote as url_quote, urlparse

from flask import Flask, request, redirect, url_for, send_file, Response, stream_with_context, jsonify
from werkzeug.utils import secure_filename

# In a frozen PyInstaller EXE, __file__ is inside _internal/ (read-only bundle dir).
# User-writable data (maps, results, .env, JSON credentials) must go next to the EXE.
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent   # install dir  e.g. C:\...\CV-Toposheet\
else:
    BASE_DIR = Path(__file__).parent          # project root (dev mode)

# MSIX (WindowsApps) and macOS .app bundles are read-only — use a writable user dir
# for ALL data that the app writes at runtime (maps, results, logs, flags, .env).
import platform as _platform
if getattr(sys, 'frozen', False):
    if _platform.system() == 'Darwin':
        _USER_DATA_DIR = Path.home() / 'Library' / 'Application Support' / 'CVToposheet'
    else:
        # Windows: %LOCALAPPDATA%\CVToposheet\ (writable even from MSIX sandbox)
        _USER_DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CVToposheet'
    _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    _USER_DATA_DIR = BASE_DIR

RESULTS_DIR = _USER_DATA_DIR / 'results'
MAPS_DIR    = _USER_DATA_DIR / 'maps'
LOGS_DIR    = _USER_DATA_DIR / 'logs'

FIRST_RUN_FLAG  = _USER_DATA_DIR / '.welcome_done'
ENV_FILE        = _USER_DATA_DIR / '.env'
AI_REPORTS_FILE = LOGS_DIR / 'ai_content_reports.jsonl'

# Load saved API keys / settings from the writable user data dir on every launch.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(ENV_FILE, override=False)  # override=False: don't clobber env vars already set

# ── Fresh-reinstall detection (MSIX / Mac .app / EXE) ────────────────────────
# On every (re)install the app binary is brand-new, so its ctime is later than
# any flag written during a previous install.  If we detect this, delete the
# flag so the welcome page shows once more for that fresh install.
def _reset_flag_if_reinstalled() -> None:
    if not getattr(sys, 'frozen', False):
        return   # dev mode — never auto-reset
    if not FIRST_RUN_FLAG.exists():
        return   # flag not written yet — nothing to reset
    try:
        exe_ctime  = Path(sys.executable).stat().st_ctime
        flag_mtime = FIRST_RUN_FLAG.stat().st_mtime
        if exe_ctime > flag_mtime:
            # Executable is newer than the flag → fresh reinstall
            FIRST_RUN_FLAG.unlink(missing_ok=True)
    except Exception:
        pass   # never break startup over this

_reset_flag_if_reinstalled()
# ─────────────────────────────────────────────────────────────────────────────

SUPPORT_ISSUES_URL = 'https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues/new'
SUPPORT_EMAIL = 'cvtoposheet@outlook.com'

ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

_process_lock = threading.Lock()
_current_proc = None   # currently running subprocess (for kill support)

_MODEL_ENVS = {
    'best':     {'LLM_PROVIDER': 'vertex',  'OCR_ENGINE': 'gcv'},
    'fast':     {'LLM_PROVIDER': 'groq',    'OCR_ENGINE': 'gcv'},
    'ensemble': {'LLM_PROVIDER': 'vertex',  'OCR_ENGINE': 'gcv',  'ENSEMBLE_MODE': 'true'},
    'openai':   {'LLM_PROVIDER': 'openai',  'OCR_ENGINE': 'gcv'},
    'claude':   {'LLM_PROVIDER': 'claude',  'OCR_ENGINE': 'gcv'},
    'grok':     {'LLM_PROVIDER': 'grok',    'OCR_ENGINE': 'gcv'},
    'gemini':   {'LLM_PROVIDER': 'gemini',  'OCR_ENGINE': 'gcv'},
    'openrouter': {'LLM_PROVIDER': 'openrouter', 'OCR_ENGINE': 'gcv'},
    'offline':    {'LLM_PROVIDER': 'groq',       'OCR_ENGINE': 'easyocr', 'OCR_WORKERS': '1'},
}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024   # 500 MB


# ── Helpers ───────────────────────────────────────────────────────────────────
def _feature_count() -> int:
    db = RESULTS_DIR / 'toposheet.db'
    if not db.exists():
        return 0
    try:
        with sqlite3.connect(str(db)) as con:
            return con.execute('SELECT COUNT(*) FROM features').fetchone()[0]
    except Exception:
        return 0


def _map_count() -> int:
    if not MAPS_DIR.exists():
        return 0
    return sum(1 for _ in MAPS_DIR.glob('*') if _.is_file() and _.suffix.lower() in ALLOWED_EXT)


def _merge_session_to_global(session_id: str) -> None:
    """Copy newly processed map(s) from a session DB into the global DB,
    then regenerate the global table_export.html so Map Database stays current."""
    session_db = _USER_DATA_DIR / 'results' / session_id / 'toposheet.db'
    global_db  = RESULTS_DIR / 'toposheet.db'
    if not session_db.exists():
        return
    try:
        from utils.db_utils import init_db
        if not global_db.exists():
            init_db(str(global_db))

        with sqlite3.connect(str(global_db)) as gcon:
            gcon.execute('ATTACH DATABASE ? AS sdb', (str(session_db),))
            maps_in_session = [r[0] for r in gcon.execute(
                'SELECT DISTINCT map_name FROM sdb.features'
            ).fetchall()]
            inserted = 0
            for map_name in maps_in_session:
                already = gcon.execute(
                    'SELECT 1 FROM features WHERE map_name=? LIMIT 1', (map_name,)
                ).fetchone()
                if not already:
                    cur = gcon.execute("""
                        INSERT INTO features
                            (map_name, original_text, feature_name, feature_type,
                             grid_reference, confidence, tile_x, tile_y,
                             bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                             lat_min, lat_max, lon_min, lon_max,
                             sheet_ref, district, survey_year, map_scale, verified)
                        SELECT map_name, original_text, feature_name, feature_type,
                               grid_reference, confidence, tile_x, tile_y,
                               bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                               lat_min, lat_max, lon_min, lon_max,
                               sheet_ref, district, survey_year, map_scale, verified
                        FROM sdb.features WHERE map_name=?
                    """, (map_name,))
                    inserted += cur.rowcount
            gcon.commit()

        if inserted > 0:
            # Regenerate the global HTML so Map Database reflects the new map
            genv = os.environ.copy()
            genv['RESULTS_FOLDER'] = str(RESULTS_DIR)
            genv['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(
                [sys.executable, 'export_table.py'],
                cwd=str(BASE_DIR), env=genv,
                timeout=120, capture_output=True
            )
            if result.returncode != 0:
                print('[merge] export_table.py failed:', result.stderr.decode(errors='replace'))
            print(f'[merge] Added {inserted} features from session {session_id} to global DB.')
        else:
            print(f'[merge] Session {session_id} maps already in global DB — skipped.')
    except Exception as e:
        print(f'[merge] Warning: could not merge session to global DB: {e}')


def _write_env(env: dict):
    lines = []
    if ENV_FILE.exists():
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    existing_keys = {}
    for i, line in enumerate(lines):
        m = re.match(r'^([A-Z_][A-Z0-9_]*)=', line)
        if m:
            existing_keys[m.group(1)] = i
    for k, v in env.items():
        if v:
            entry = f'{k}={v}\n'
            if k in existing_keys:
                lines[existing_keys[k]] = entry
            else:
                lines.append(entry)
        else:
            # Empty value — remove the key from .env so it doesn't come back on restart
            if k in existing_keys:
                lines[existing_keys[k]] = None  # mark for removal
    lines = [l for l in lines if l is not None]
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if not FIRST_RUN_FLAG.exists():
        return redirect('/welcome')
    try:
        feat_count = _feature_count()
    except Exception:
        feat_count = 0
    try:
        map_count = _map_count()
    except Exception:
        map_count = 0
    return _landing_html(feat_count, map_count)


@app.route('/welcome')
def welcome():
    return _WELCOME_TEMPLATE


@app.route('/dismiss_welcome', methods=['POST'])
def dismiss_welcome():
    try:
        FIRST_RUN_FLAG.touch()
    except Exception as e:
        app.logger.error("Could not write welcome flag to %s: %s", FIRST_RUN_FLAG, e)
    return redirect('/')


@app.route('/upload_service_account', methods=['POST'])
def upload_service_account():
    f = request.files.get('json_file')
    target = request.form.get('target', 'service_account.json')
    # Only allow safe filenames — must end in .json, no path traversal
    if not f or not f.filename.endswith('.json'):
        return json.dumps({'ok': False, 'error': 'Not a JSON file'}), 400
    safe_name = Path(target).name  # strip any path component
    if not safe_name.endswith('.json'):
        safe_name = 'service_account.json'
    dest = _USER_DATA_DIR / safe_name
    f.save(str(dest))
    # Update env so it takes effect immediately
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(dest)
    try:
        _write_env({'GOOGLE_APPLICATION_CREDENTIALS': safe_name})
        import config; importlib.reload(config)
    except Exception:
        pass
    return json.dumps({'ok': True, 'saved': safe_name})


def _session_feature_count(session_id: str) -> int:
    """Return feature row count for a session DB (0 if missing/error)."""
    db = _USER_DATA_DIR / 'results' / session_id / 'toposheet.db'
    if not db.exists():
        return 0
    try:
        with sqlite3.connect(str(db)) as con:
            row = con.execute('SELECT COUNT(*) FROM features').fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


@app.route('/save_env', methods=['POST'])
def save_env():
    ref = request.referrer or ''
    if not (ref.startswith('http://127.0.0.1:') or ref.startswith('http://localhost:')):
        return '', 403
    payload = request.get_json(force=True, silent=True) or {}
    for k, v in payload.items():
        if v:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]
    try:
        _write_env(payload)  # pass all keys including empty — _write_env removes blanked keys
    except Exception:
        pass
    try:
        import config
        importlib.reload(config)
    except Exception:
        pass
    return json.dumps({'ok': True})


@app.route('/report_ai_content', methods=['POST'])
def report_ai_content():
  try:
    ref = request.referrer or ''
    if ref:
      ref_host = (urlparse(ref).hostname or '').lower()
      req_host = (request.host.split(':', 1)[0] if request.host else '').lower()
      allowed_hosts = {'127.0.0.1', 'localhost', '0.0.0.0'}
      if ref_host and ref_host not in allowed_hosts and ref_host != req_host:
        return jsonify({'ok': False, 'error': 'Unauthorized request origin.'}), 403

    payload = request.get_json(force=True, silent=True) or {}
    report_id = f"AIR-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    def _clean_str(key: str, limit: int = 4000) -> str:
      return str(payload.get(key, '')).strip()[:limit]

    model = _clean_str('model', 80)
    category = _clean_str('category', 120)
    snippet = _clean_str('snippet', 2000)
    details = _clean_str('details', 4000)
    contact = _clean_str('contact', 200)
    files = payload.get('files') or []
    if not isinstance(files, list):
      files = []
    files = [str(item).strip()[:260] for item in files[:20] if str(item).strip()]

    if not details:
      return jsonify({'ok': False, 'error': 'Please describe the issue.'}), 400

    LOGS_DIR.mkdir(exist_ok=True)
    record = {
      'report_id': report_id,
      'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
      'category': category or 'inappropriate-output',
      'model': model,
      'files': files,
      'snippet': snippet,
      'details': details,
      'contact': contact,
    }
    with open(AI_REPORTS_FILE, 'a', encoding='utf-8') as handle:
      handle.write(json.dumps(record, ensure_ascii=True) + '\n')

    issue_title = url_quote(f'AI content report: {report_id}')
    issue_body = url_quote(
      f"Report ID: {report_id}\n"
      f"Category: {record['category']}\n"
      f"Model: {model or 'not provided'}\n"
      f"Snippet:\n{snippet or 'not provided'}\n\n"
      f"Details:\n{details}\n\n"
      f"Contact: {contact or 'not provided'}\n"
    )
    issue_url = f'{SUPPORT_ISSUES_URL}?title={issue_title}&body={issue_body}'
    report_text = (
      f"Report ID: {report_id}\n"
      f"Category: {record['category']}\n"
      f"Model: {model or 'not provided'}\n"
      f"Snippet:\n{snippet or 'not provided'}\n\n"
      f"Details:\n{details}\n\n"
      f"Contact: {contact or 'not provided'}"
    )
    email_subject = url_quote(f'CVToposheet AI content report: {report_id}')
    email_body = url_quote(report_text)
    email_url = f'mailto:{SUPPORT_EMAIL}?subject={email_subject}&body={email_body}'
    return jsonify({
      'ok': True,
      'report_id': report_id,
      'issue_url': issue_url,
      'report_text': report_text,
      'support_email': SUPPORT_EMAIL,
      'email_url': email_url,
    })
  except Exception as exc:
    return jsonify({'ok': False, 'error': f'Failed to save report: {exc}'}), 500


@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('map_file')
    model = request.form.get('model', 'best')

    if not files or not files[0].filename:
        return redirect('/')

    session_id = uuid.uuid4().hex[:12]
    session_maps = _USER_DATA_DIR / 'maps' / session_id
    session_maps.mkdir(parents=True, exist_ok=True)

    saved_names = []
    for f in files:
        if f.filename and Path(f.filename).suffix.lower() in ALLOWED_EXT:
            safe_name = secure_filename(f.filename)
            dest = session_maps / safe_name
            f.save(str(dest))
            saved_names.append(safe_name)

    if not saved_names:
        return redirect('/')

    combined = ','.join(saved_names)
    return redirect(f'/process/{url_quote(combined, safe="")}?model={url_quote(model, safe="")}&session={url_quote(session_id, safe="")}')


@app.route('/kill', methods=['POST'])
def kill_process():
    global _current_proc
    killed = False
    if _current_proc is not None:
        try:
            if os.name == 'nt':
                # Force-kill process AND all its children (OCR workers, LLM calls etc.)
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(_current_proc.pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                _current_proc.kill()
            killed = True
        except Exception:
            pass
        finally:
            _current_proc = None
    # Release the lock so the next map can be processed immediately
    try:
        _process_lock.release()
    except RuntimeError:
        pass  # wasn't held
    return json.dumps({'killed': killed})


@app.route('/restart', methods=['POST'])
def restart_server():
    """Kill any running job and release the lock so the server is ready for a new upload."""
    global _current_proc
    # Force-kill running subprocess and any children
    if _current_proc is not None:
        try:
            if os.name == 'nt':
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(_current_proc.pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                _current_proc.kill()
        except Exception:
            pass
        finally:
            _current_proc = None
    # Release the processing lock so new jobs can run
    try:
        _process_lock.release()
    except RuntimeError:
        pass
    return json.dumps({'ok': True})


@app.route('/stream/<path:filename>')
def stream(filename):
    model = request.args.get('model', 'best')
    session_id = request.args.get('session', '')
    return Response(
        stream_with_context(_stream_gen(filename, model, session_id)),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _stream_gen(filename, model, session_id):
    global _current_proc
    env_overrides = _MODEL_ENVS.get(model, _MODEL_ENVS['best'])

    # ── API key check ────────────────────────────────────────────────────────
    _KEY_NEEDED = {
        'vertex':      None,                   # uses service-account JSON, no API key
        'gemini':      'GEMINI_API_KEY',
        'groq':        'GROQ_API_KEY',
        'openai':      'OPENAI_API_KEY',
        'claude':      'CLAUDE_API_KEY',
        'grok':        'GROK_API_KEY',
        'openrouter':  'OPENROUTER_API_KEY',
    }
    provider = env_overrides.get('LLM_PROVIDER', 'vertex')
    required_key = _KEY_NEEDED.get(provider)
    if required_key and not os.environ.get(required_key, '').strip():
        friendly = {
            'GEMINI_API_KEY':     'Gemini API Key',
            'GROQ_API_KEY':       'Groq API Key',
            'OPENAI_API_KEY':     'OpenAI API Key',
            'CLAUDE_API_KEY':     'Claude API Key',
            'GROK_API_KEY':       'Grok (xAI) API Key',
            'OPENROUTER_API_KEY': 'OpenRouter API Key',
        }.get(required_key, required_key)
        msg = (f'❌ No {friendly} found. '
               f'Please go to ⚙️ Settings, enter your {friendly}, and save before processing.')
        yield f'data: {json.dumps({"msg": msg, "error": True})}\n\n'
        return
    # Also check service-account JSON for vertex LLM or GCV OCR (both need it)
    ocr_engine = env_overrides.get('OCR_ENGINE', os.environ.get('OCR_ENGINE', 'gcv')).lower()
    needs_gcp = (provider == 'vertex') or (ocr_engine == 'gcv')
    if needs_gcp:
        creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '').strip()
        _gcp_found = (creds and Path(creds).exists()) or \
             (_USER_DATA_DIR / 'service_account.json').exists() or \
             (_USER_DATA_DIR / 'service_account2.json').exists() or \
             (_USER_DATA_DIR / 'Service_account_Backup.json').exists() or \
             (BASE_DIR / 'service_account.json').exists() or \
             (BASE_DIR / 'Service_account_Backup.json').exists()
        if not _gcp_found:
            if provider == 'vertex':
                detail = 'Vertex AI LLM + Google Cloud Vision OCR both require'
            else:
                detail = 'Google Cloud Vision OCR requires'
            msg = (f'❌ No Google service-account JSON found. '
                   f'{detail} a GCP service account. '
             f'Please go to ⚙️ Settings, upload your service_account.json, and save before processing.')
            yield f'data: {json.dumps({"msg": msg, "error": True})}\n\n'
            return
    # ── end API key check ────────────────────────────────────────────────────

    if not _process_lock.acquire(blocking=False):
        yield f'data: {json.dumps({"msg": "Another map is already being processed. Please wait and try again."})}\n\n'
        yield f'data: {json.dumps({"error": True})}\n\n'
        return

    try:
        env = os.environ.copy()
        env.update(env_overrides)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'

        # Local OCR (EasyOCR/Tesseract) can trigger native DLL crashes when
        # BLAS/OpenMP thread pools over-subscribe in frozen builds.
        if env_overrides.get('OCR_ENGINE', '').lower() != 'gcv':
          # Force safe single-thread native runtime settings for local OCR.
          # setdefault is not enough because user/system env may already set
          # aggressive OpenMP/BLAS values that crash frozen EasyOCR/Torch.
          env['OMP_NUM_THREADS'] = '1'
          env['OPENBLAS_NUM_THREADS'] = '1'
          env['MKL_NUM_THREADS'] = '1'
          env['NUMEXPR_NUM_THREADS'] = '1'
          env['TORCH_NUM_THREADS'] = '1'
          env['TORCH_NUM_INTEROP_THREADS'] = '1'
          env['OMP_WAIT_POLICY'] = 'PASSIVE'
          env['KMP_AFFINITY'] = 'disabled'
          env['KMP_BLOCKTIME'] = '0'
          env['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
          env['OPENCV_OPENCL_RUNTIME'] = 'disabled'
          env['OPENBLAS_CORETYPE'] = 'GENERIC'
          env['MKL_THREADING_LAYER'] = 'SEQUENTIAL'
          env['MKL_ENABLE_INSTRUCTIONS'] = 'COMPATIBLE'
          env['OMP_DYNAMIC'] = 'FALSE'
          env['PYTORCH_JIT'] = '0'
          env['GOMP_SPINCOUNT'] = '0'
          env['GOMP_STACKSIZE'] = '65536'

        session_maps_dir    = str(_USER_DATA_DIR / 'maps'    / session_id)
        session_results_dir = str(_USER_DATA_DIR / 'results' / session_id)
        session_logs_dir    = str(_USER_DATA_DIR / 'logs'    / session_id)
        Path(session_maps_dir).mkdir(parents=True, exist_ok=True)
        Path(session_results_dir).mkdir(parents=True, exist_ok=True)
        Path(session_logs_dir).mkdir(parents=True, exist_ok=True)
        env['MAPS_FOLDER']    = session_maps_dir
        env['RESULTS_FOLDER'] = session_results_dir
        env['LOGS_FOLDER']    = session_logs_dir
        results_url = f'/results/{session_id}'

        yield f'data: {json.dumps({"msg": f"Starting processing for {filename}..."})}\n\n'

        for label, script in [('pipeline', 'process_new_maps.py'),
                               ('export',   'export_table.py')]:
            if label == 'export':
                yield f'data: {json.dumps({"msg": "Exporting results table..."})}\n\n'

            proc = subprocess.Popen(
                [sys.executable, script],
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=env,
            )
            _current_proc = proc

            # Read subprocess output in a background thread so we can send
            # SSE keepalive heartbeats when the process is silent (e.g. GCV
            # API calls), preventing the browser from closing the connection.
            line_q = queue.Queue()
            def _reader(pipe, q):
                for ln in iter(pipe.readline, ''):
                    q.put(ln)
                q.put(None)  # sentinel
            t = threading.Thread(target=_reader, args=(proc.stdout, line_q), daemon=True)
            t.start()

            while True:
                try:
                    line = line_q.get(timeout=15)
                except queue.Empty:
                    # No output for 15 s — send a keepalive comment so the
                    # browser knows the connection is still alive.
                    yield ': keepalive\n\n'
                    continue
                if line is None:  # sentinel — subprocess closed stdout
                    break
                line = line.rstrip()
                if line:
                    yield f'data: {json.dumps({"msg": line})}\n\n'

            proc.wait()
            _current_proc = None
            if label == 'pipeline' and proc.returncode not in (0, None):
                yield f'data: {json.dumps({"msg": f"[ERROR] Pipeline exited with code {proc.returncode} — check logs", "error": True})}\n\n'
                return

        # Merge this session into the global DB so Map Database stays up to date
        yield 'data: {"msg": "Saving to Map Database..."}\n\n'
        _merge_session_to_global(session_id)

        session_rows = _session_feature_count(session_id)
        if session_rows == 0:
          warn_msg = (
            'Processing completed, but 0 features were extracted. '
            'View Results may look empty. Try a clearer map, higher contrast scan, '
            'or run with GCV OCR for stronger detection.'
          )
          yield f'data: {json.dumps({"done": True, "redirect": results_url, "empty": True, "msg": warn_msg})}\n\n'
        else:
          ok_msg = f'Done! Extracted {session_rows} feature(s). Click View Results.'
          yield f'data: {json.dumps({"done": True, "redirect": results_url, "msg": ok_msg})}\n\n'

    finally:
        try:
            _process_lock.release()
        except RuntimeError:
            pass


@app.route('/process/<path:filename>')
def process(filename):
    model = request.args.get('model', 'best')
    session_id = request.args.get('session', '')
    return _process_html(filename, model, session_id)


@app.route('/get_env')
def get_env():
    # Only allow requests originating from the app's own pages (pywebview or localhost).
    # Direct browser navigation (typing the URL) sends no Referer — block it.
    ref = request.referrer or ''
    if not (ref.startswith('http://127.0.0.1:') or ref.startswith('http://localhost:')):
        return '', 403
    keys = ['GROQ_API_KEY', 'GEMINI_API_KEY', 'GEMINI_API_KEY_2', 'OPENAI_API_KEY',
            'CLAUDE_API_KEY', 'GROK_API_KEY', 'GOOGLE_API_KEY', 'OPENROUTER_API_KEY',
            'OPENROUTER_MODEL', 'GOOGLE_APPLICATION_CREDENTIALS',
            'VERTEX_PROJECT', 'VERTEX_LOCATION', 'VERTEX_MODEL']
    env_data = {k: os.environ.get(k, '') for k in keys}

    # Migrate legacy OpenRouter default model to the new recommended auto router.
    old_openrouter_default = 'meta-llama/llama-3.3-70b-instruct:free'
    if env_data.get('OPENROUTER_MODEL', '').strip() in ('', old_openrouter_default):
      env_data['OPENROUTER_MODEL'] = 'openrouter/auto'
      os.environ['OPENROUTER_MODEL'] = 'openrouter/auto'
      try:
        _write_env({'OPENROUTER_MODEL': 'openrouter/auto'})
      except Exception:
        pass

    return json.dumps(env_data)


@app.route('/results', methods=['GET', 'HEAD'])
@app.route('/results/<session_id>', methods=['GET', 'HEAD'])
def results(session_id=None):
    if session_id:
        out = _USER_DATA_DIR / 'results' / session_id / 'table_export.html'
        res_dir = str(_USER_DATA_DIR / 'results' / session_id)
    else:
        out = RESULTS_DIR / 'table_export.html'
        res_dir = str(RESULTS_DIR)

    # HEAD request (used by the polling check in the processing page)
    # Only return 200 if the HTML already exists — don't generate on HEAD
    if request.method == 'HEAD':
        if out.exists():
            return '', 200
        return '', 404

    # If HTML not yet generated, try to generate it on-demand from the DB
    # (also runs when DB doesn't exist yet — export_table handles empty/missing DB gracefully)
    if not out.exists():
        try:
            Path(res_dir).mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env['RESULTS_FOLDER'] = res_dir
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUNBUFFERED'] = '1'
            subprocess.run(
                [sys.executable, 'export_table.py'],
                cwd=str(BASE_DIR), env=env,
                timeout=60, capture_output=True
            )
        except Exception:
            pass

    if out.exists():
        return send_file(str(out))
    return ('<h2 style="font-family:sans-serif;padding:40px;color:#888">'
            'No results yet – please upload and process a map first.'
            ' <a href="/">&#8592; Go back</a></h2>'), 404


@app.route('/delete_maps', methods=['POST', 'OPTIONS'])
def delete_maps():
    # Allow CORS so the page can call this even when opened as file://
    if request.method == 'OPTIONS':
        resp = app.response_class(status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'POST'
        return resp

    data = request.get_json(silent=True) or {}
    maps_to_delete = data.get('maps', [])
    if not maps_to_delete:
        resp = app.response_class(json.dumps({'ok': False, 'error': 'No maps specified'}),
                                  mimetype='application/json')
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp, 400

    deleted = 0
    db_path = RESULTS_DIR / 'toposheet.db'
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as con:
                placeholders = ','.join('?' * len(maps_to_delete))
                cur = con.execute(
                    f'DELETE FROM features WHERE map_name IN ({placeholders})',
                    maps_to_delete
                )
                deleted = cur.rowcount
                con.commit()
        except Exception as e:
            resp = app.response_class(json.dumps({'ok': False, 'error': str(e)}),
                                      mimetype='application/json')
            resp.headers['Access-Control-Allow-Origin'] = '*'
            return resp, 500

    # Regenerate the HTML export so it reflects the deletion
    try:
        env = os.environ.copy()
        env['RESULTS_FOLDER'] = str(RESULTS_DIR)
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run([sys.executable, 'export_table.py'],
                                cwd=str(BASE_DIR), env=env, timeout=60, capture_output=True)
        if result.returncode != 0:
            print('[export] export_table.py failed:', result.stderr.decode(errors='replace'))
    except Exception as e:
        print('[export] export_table.py exception:', e)

    resp = app.response_class(
        json.dumps({'ok': True, 'deleted_rows': deleted, 'maps': len(maps_to_delete)}),
        mimetype='application/json'
    )
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


# ── Welcome / first-run onboarding page ──────────────────────────────────────
_WELCOME_TEMPLATE = """<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>Welcome to CV-Toposheet</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
html, body { height:100%; overflow:hidden; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; }
.shell { display:flex; flex-direction:column; height:100vh; }

/* Header */
.hdr { background:#0E7490; padding:12px 28px; flex-shrink:0; display:flex; align-items:center; justify-content:space-between; }
.hdr-logo { font-size:1.15em; font-weight:700; color:#fff; }
.hdr-sub  { font-size:0.76em; color:rgba(255,255,255,.75); margin-top:1px; }
.hdr-step { font-size:0.82em; color:rgba(255,255,255,.85); font-weight:600; background:rgba(255,255,255,.15); padding:4px 12px; border-radius:20px; }

/* Progress bar */
.prog { height:3px; background:rgba(14,116,144,.15); flex-shrink:0; }
.prog-fill { height:100%; background:#22d3ee; transition:width .35s ease; }

/* Slide container */
.slides { flex:1; position:relative; overflow:hidden; }
.slide { position:absolute; inset:0; display:flex; justify-content:center; align-items:flex-start; padding:14px 20px 10px; overflow:hidden; transition:opacity .25s, transform .25s; }
.slide.hidden { opacity:0; pointer-events:none; transform:translateX(40px); }
.slide.hidden-left { opacity:0; pointer-events:none; transform:translateX(-40px); }
.inner { width:100%; max-width:680px; display:flex; flex-direction:column; gap:9px; }
.slide#slide1 .inner { gap:8px; transform:translateY(-6px); }

/* Slide title */
.slide-icon { font-size:1.6em; text-align:center; }
.slide-title { font-size:1.35em; font-weight:800; color:#0E7490; text-align:center; }
.slide-sub   { font-size:0.88em; color:#4a6070; text-align:center; line-height:1.5; }

/* Cards */
.card { background:#fff; border-radius:12px; border:1.5px solid #d4eaf0; padding:10px 16px; box-shadow:0 2px 10px rgba(14,116,144,.07); }
.card-row { display:flex; align-items:flex-start; gap:12px; }
.card-icon { font-size:1.4em; flex-shrink:0; margin-top:2px; }
.card-title { font-weight:700; color:#0E7490; font-size:0.95em; margin-bottom:2px; }
.card-desc  { font-size:0.84em; color:#5a7a8a; line-height:1.5; }
.card-link  { font-size:0.83em; color:#0891b2; margin-top:3px; }
.badge { display:inline-block; font-size:0.7em; font-weight:700; border-radius:4px; padding:2px 7px; margin-left:6px; vertical-align:middle; }
.badge-free { background:#dcfce7; color:#166534; }
.badge-paid { background:#fce7f3; color:#9d174d; }
.badge-opt  { background:#fef9c3; color:#92400e; }
.note { background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; padding:7px 12px; font-size:0.82em; color:#92400e; line-height:1.4; }
.what-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.wc { background:#f0f9fb; border:1px solid #b8dde8; border-radius:8px; padding:10px 12px; }
.wc-num   { font-size:1.1em; font-weight:800; color:#0E7490; }
.wc-title { font-weight:700; font-size:0.86em; color:#1e3a4a; }
.wc-desc  { font-size:0.77em; color:#6b8a99; margin-top:2px; }
.divider  { text-align:center; font-size:0.76em; color:#aaa; font-weight:700; letter-spacing:.04em; }
.ready-big { text-align:center; font-size:3em; margin:4px 0; }
.ready-list { display:flex; flex-direction:column; gap:8px; }
.ready-row { display:flex; align-items:center; gap:10px; background:#fff; border:1.5px solid #d4eaf0; border-radius:10px; padding:11px 16px; }
.ready-row .ri { font-size:1.3em; flex-shrink:0; }
.ready-row .rt { font-size:0.88em; color:#2a4a5a; line-height:1.4; }
.ready-row .rt b { color:#0E7490; }

/* Footer nav */
.nav { flex-shrink:0; background:#fff; border-top:1px solid #e0eaf0; padding:14px 28px; display:flex; align-items:center; justify-content:space-between; }
.dots { display:flex; gap:8px; }
.dot { width:8px; height:8px; border-radius:50%; background:#d0e8f0; transition:background .25s, transform .25s; }
.dot.active { background:#0E7490; transform:scale(1.3); }
.dot.done   { background:#67c8e0; }
.btn { border:none; border-radius:9px; padding:11px 28px; font-size:0.95em; font-weight:700; cursor:pointer; transition:all .18s; }
.btn-back { background:#f0f4f8; color:#5a7a8a; }
.btn-back:hover { background:#e0eaf0; }
.btn-next { background:linear-gradient(135deg,#0e7490,#0891b2); color:#fff; box-shadow:0 3px 12px rgba(14,116,144,.3); }
.btn-next:hover { background:linear-gradient(135deg,#0891b2,#06b6d4); box-shadow:0 5px 18px rgba(14,116,144,.45); transform:translateY(-1px); }
.btn-start { background:linear-gradient(135deg,#0e7490,#0891b2); color:#fff; padding:11px 32px; box-shadow:0 3px 12px rgba(14,116,144,.3); }
.btn-start:hover { background:linear-gradient(135deg,#0891b2,#06b6d4); box-shadow:0 5px 18px rgba(14,116,144,.45); transform:translateY(-1px); }
.invisible { visibility:hidden; }
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <div class="hdr">
    <div>
      <div class="hdr-logo">CV-Toposheet</div>
      <div class="hdr-sub">AI-Powered Historical Toposheet Digitization</div>
    </div>
    <div class="hdr-step" id="stepLabel">Step 1 of 4</div>
  </div>
  <div class="prog"><div class="prog-fill" id="progFill" style="width:25%"></div></div>

  <!-- Slides -->
  <div class="slides">

    <!-- Slide 1: What is this app -->
    <div class="slide" id="slide0">
      <div class="inner">
        <div class="slide-icon">&#128255;</div>
        <div class="slide-title">Welcome to CV-Toposheet!</div>
        <div class="slide-sub">Automatically extract &amp; digitize geographic feature names from scanned historical topographic maps.</div>
        <div class="what-grid">
          <div class="wc"><div class="wc-num">1</div><div class="wc-title">Upload</div><div class="wc-desc">Drop a scanned toposheet (JPG / PNG / TIF)</div></div>
          <div class="wc"><div class="wc-num">2</div><div class="wc-title">OCR</div><div class="wc-desc">AI reads all text from every tile of the map</div></div>
          <div class="wc"><div class="wc-num">3</div><div class="wc-title">Clean</div><div class="wc-desc">LLM corrects errors &amp; classifies features</div></div>
          <div class="wc"><div class="wc-num">4</div><div class="wc-title">Search</div><div class="wc-desc">Query &amp; export the full results database</div></div>
        </div>
        <div class="note">&#128336; This setup screen appears <b>once only</b>. Takes about 60 seconds. You can revisit instructions anytime in <b>Settings &rarr; Help</b>.</div>
      </div>
    </div>

    <!-- Slide 2: API Key -->
    <div class="slide hidden" id="slide1">
      <div class="inner">
        <div class="slide-icon">&#128273;</div>
        <div class="slide-title">Step 1: Add an LLM API Key</div>
        <div class="slide-sub">Required for feature extraction. Go to <b>&#9881; Settings</b> on the home screen and paste at least one key.</div>
        <div class="card">
          <div class="card-row">
            <div class="card-icon">&#9889;</div>
            <div>
              <div class="card-title">Groq API Key <span class="badge badge-free">FREE &amp; FASTEST</span></div>
              <div class="card-desc">No credit card needed. Best for first-time users.</div>
              <div class="card-link">&#128279; console.groq.com &rarr; Sign up &rarr; API Keys &rarr; Create</div>
            </div>
          </div>
        </div>
        <div class="divider">or</div>
        <div class="card">
          <div class="card-row">
            <div class="card-icon">&#128171;</div>
            <div>
              <div class="card-title">Gemini API Key <span class="badge badge-free">FREE</span></div>
              <div class="card-desc">Google Gemini Flash. Free tier is generous.</div>
              <div class="card-link">&#128279; aistudio.google.com/apikey &rarr; Create API key</div>
            </div>
          </div>
        </div>
        <div class="divider">or</div>
        <div class="card">
          <div class="card-row">
            <div class="card-icon">&#128176;</div>
            <div>
              <div class="card-title">Other API Keys <span class="badge badge-paid">PAID</span></div>
              <div class="card-desc">Claude (Anthropic), Grok (xAI), OpenAI, and more. Add via <b>&#9881; Settings</b> after setup.</div>
              <div class="card-link">&#128279; anthropic.com &nbsp;|&nbsp; x.ai/api &nbsp;|&nbsp; platform.openai.com</div>
            </div>
          </div>
        </div>
        <div class="note">&#9888;&#65039; <b>Without an API key</b> the pipeline runs but produces wrong results: blank Feature Types, 0.50 confidence, noise rows.</div>
      </div>
    </div>

    <!-- Slide 3: GCV JSON -->
    <div class="slide hidden" id="slide2">
      <div class="inner">
        <div class="slide-icon">&#9729;&#65039;</div>
        <div class="slide-title">Step 2: Google Cloud Vision JSON</div>
        <div class="slide-sub">Strongly recommended for best accuracy. Enables premium OCR via Google Cloud Vision API. Without it, the app uses EasyOCR (offline, slower, less accurate).</div>
        <div class="card">
          <div class="card-title" style="margin-bottom:8px;">How to get the JSON file</div>
          <div class="card-desc" style="line-height:1.8;">
            1. Go to <b>console.cloud.google.com</b><br>
            2. Enable <b>Cloud Vision API</b> (APIs &amp; Services &rarr; Library)<br>
            3. IAM &amp; Admin &rarr; Service Accounts &rarr; Create &rarr; Keys &rarr; Add Key &rarr; JSON<br>
            4. File downloads automatically
          </div>
        </div>
        <div class="card">
          <div class="card-row">
            <div class="card-icon">&#128196;</div>
            <div>
              <div class="card-title">Upload in Settings</div>
              <div class="card-desc">Click <b>&#9881; Settings &rarr; Google Cloud &rarr; Upload GCP Service Account JSON</b> and select the downloaded file.</div>
            </div>
          </div>
        </div>
        <div class="note">&#9989; For best accuracy, set this up now. You can also add it later via <b>Settings &#8594; Help &#8594; Google Cloud</b>.</div>
        <div class="note" style="margin-top:8px;background:#f0fdf4;border-color:#86efac;color:#166534;">&#11088; For even better results, also enable <b>Vertex AI API</b> in Google Cloud Console (APIs &amp; Services &rarr; Library &rarr; search &ldquo;Vertex AI&rdquo; &rarr; Enable).</div>
      </div>
    </div>

    <!-- Slide 4: Ready -->
    <div class="slide hidden" id="slide3">
      <div class="inner">
        <div class="ready-big">&#128640;</div>
        <div class="slide-title">You&rsquo;re Ready to Go!</div>
        <div class="slide-sub">Here&rsquo;s how to process your first map.</div>
        <div class="ready-list">
          <div class="ready-row"><div class="ri">&#128444;&#65039;</div><div class="rt"><b>Upload:</b> Drag &amp; drop a JPG / PNG / TIF toposheet onto the home screen (up to 500 MB)</div></div>
          <div class="ready-row"><div class="ri">&#127917;</div><div class="rt"><b>Choose Model:</b> Select a processing model from the right panel (Best Quality recommended)</div></div>
          <div class="ready-row"><div class="ri">&#9654;&#65039;</div><div class="rt"><b>Process:</b> Click <b>Process Map</b> and watch live progress (5&ndash;30 min depending on map size)</div></div>
          <div class="ready-row"><div class="ri">&#128202;</div><div class="rt"><b>Search &amp; Export:</b> Find results in <b>Map Database</b>. Export to CSV or Excel anytime.</div></div>
        </div>
      </div>
    </div>

  </div><!-- /slides -->

  <!-- Navigation footer -->
  <div class="nav">
    <button class="btn btn-back invisible" id="btnBack" onclick="go(-1)">&#8592; Back</button>
    <div class="dots">
      <div class="dot active" id="dot0"></div>
      <div class="dot" id="dot1"></div>
      <div class="dot" id="dot2"></div>
      <div class="dot" id="dot3"></div>
    </div>
    <button class="btn btn-next" id="btnNext" onclick="go(1)">Next &rarr;</button>
  </div>

</div><!-- /shell -->

<form id="dismissForm" action="/dismiss_welcome" method="post" style="display:none"></form>

<script>
var cur = 0, total = 4;
function go(dir) {
  var next = cur + dir;
  if (cur === total - 1 && dir === 1) { document.getElementById('dismissForm').submit(); return; }
  if (next < 0 || next >= total) return;
  var slides = document.querySelectorAll('.slide');
  slides[cur].classList.add(dir > 0 ? 'hidden' : 'hidden-left');
  slides[next].classList.remove('hidden', 'hidden-left');
  cur = next;
  update();
}
function update() {
  document.getElementById('stepLabel').textContent = 'Step ' + (cur+1) + ' of ' + total;
  document.getElementById('progFill').style.width = ((cur+1)/total*100) + '%';
  for (var i=0; i<total; i++) {
    var d = document.getElementById('dot'+i);
    d.className = 'dot' + (i===cur?' active':(i<cur?' done':''));
  }
  var back = document.getElementById('btnBack');
  var next = document.getElementById('btnNext');
  back.className = 'btn btn-back' + (cur===0?' invisible':'');
  if (cur === total-1) {
    next.textContent = '\\u{1F680} Get Started';
    next.className = 'btn btn-start';
  } else {
    next.textContent = 'Next \\u2192';
    next.className = 'btn btn-next';
  }
}
</script>
</body>
</html>"""


# ── Landing page HTML ─────────────────────────────────────────────────────────
def _landing_html(feat_count: int, map_count: int) -> str:
    html = _LANDING_TEMPLATE
    html = html.replace('__FEAT_COUNT__', str(feat_count))
    html = html.replace('__MAP_COUNT__', str(map_count))
    return html


_LANDING_TEMPLATE = """<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>CV-Toposheet - Map Digitization</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
html { height:100%; }
body { min-height:100vh; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; display:flex; flex-direction:column; }
.hdr { display:flex; align-items:center; justify-content:space-between; background:#0E7490; padding:clamp(10px,1.8vw,14px) clamp(14px,2.5vw,28px); flex-shrink:0; flex-wrap:wrap; gap:8px; }
.hdr-logo { font-size:clamp(1em,2vw,1.25em); font-weight:700; color:#fff; letter-spacing:-.01em; }
.hdr-logo span { color:#fff; }
.hdr-sub  { font-size:0.78em; color:rgba(255,255,255,.75); margin-top:2px; }
.hdr-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.report-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:0 16px; height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; }
.report-btn:hover { background:rgba(255,255,255,.25); }
.settings-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:0 16px; height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; }
.settings-btn:hover { background:rgba(255,255,255,.25); }
#uploadForm { flex:1 0 auto; display:flex; flex-direction:column; }
.body { flex:1 0 auto; display:flex; flex-direction:row; gap:clamp(8px,1.5vw,16px); align-items:stretch; padding:clamp(6px,1vw,10px) clamp(4px,0.8vw,8px); }
.left { flex:1; min-width:0; display:flex; flex-direction:column; }
.right { width:clamp(300px,32vw,460px); min-width:0; flex-shrink:0; display:flex; flex-direction:column; padding:clamp(6px,1vw,12px); gap:0; background:#fff; border:2px solid #93c5d4; border-radius:14px; box-shadow:0 2px 16px rgba(14,116,144,.08); }
.right-title { font-size:0.55em; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#0E7490; margin-bottom:4px; padding-bottom:3px; border-bottom:1.5px solid #e0f2f7; flex-shrink:0; }
.model-label { font-size:0.63em; font-weight:600; color:#6b8a99; text-transform:uppercase; letter-spacing:.08em; margin-bottom:4px; flex-shrink:0; }
.model-cards { display:flex; flex-direction:column; gap:4px; margin-bottom:3px; flex:1; overflow-y:auto; min-height:0; padding-right:3px; scrollbar-width:thin; scrollbar-color:#93c5d4 #f0f4f8; }
.model-cards::-webkit-scrollbar { width:6px; }
.model-cards::-webkit-scrollbar-track { background:#f0f4f8; border-radius:3px; }
.model-cards::-webkit-scrollbar-thumb { background:#93c5d4; border-radius:3px; }
.model-cards::-webkit-scrollbar-thumb:hover { background:#0e7490; }
.model-card { border:1.5px solid #ccdde3; border-radius:6px; padding:3px 8px; cursor:pointer; transition:all .2s; display:flex; align-items:center; gap:6px; background:#fff; box-shadow:0 1px 2px rgba(14,116,144,.05); flex-shrink:0; }
.model-card:hover { border-color:#0e7490; background:#f0f9fb; box-shadow:0 2px 10px rgba(14,116,144,.15); }
.model-card input[type=radio] { accent-color:#0e7490; margin:0; flex-shrink:0; width:13px; height:13px; }
.mc-body { flex:1; }
.mc-body .mc-name { font-size:0.74em; font-weight:700; color:#1e3a4a; display:flex; align-items:center; gap:4px; flex-wrap:wrap; }
.mc-body .mc-desc { font-size:0.65em; color:#6b8a99; margin-top:0px; line-height:1.2; }
.mc-key { display:inline; font-size:1em; font-weight:600; background:#e0f2f7; border:1px solid #b8dde8; border-radius:3px; padding:0 3px; color:#0e7490; letter-spacing:.01em; }
.badge { display:inline-block; border-radius:3px; padding:0px 4px; font-size:0.58em; font-weight:700; }
.badge-best    { background:#d0f0f7; color:#0e7490; }
.badge-fast    { background:#fef9c3; color:#a16207; }
.badge-gemini  { background:#e8f5e9; color:#2e7d32; }
.badge-gemini  { background:#e8f5e9; color:#2e7d32; }
.badge-offline { background:#ede9fe; color:#6d28d9; }
.model-card.selected { border-color:#0e7490; border-width:1.5px; background:#e8f5f9; box-shadow:0 0 0 2px rgba(14,116,144,.10); }
.divider { flex-shrink:0; min-height:4px; max-height:8px; }
.info-strip { background:#f0f9fb; border-radius:5px; border:1px solid #b8dde8; padding:3px 7px; margin-top:4px; flex-shrink:0; font-size:0.54em; color:#4a7080; line-height:1.3; }
.info-strip b { color:#0E7490; }
.process-btn { width:100%; padding:7px; background:linear-gradient(135deg,#0e7490,#0891b2); color:white; border:none; border-radius:7px; font-size:0.8em; font-weight:700; cursor:pointer; transition:all .2s; box-shadow:0 2px 8px rgba(14,116,144,.3); letter-spacing:.01em; flex-shrink:0; }
.process-btn:hover { background:linear-gradient(135deg,#0891b2,#06b6d4); box-shadow:0 5px 22px rgba(14,116,144,.55); transform:translateY(-1px); }
.process-btn:disabled { background:#dde8ec; color:#7a9daa; cursor:not-allowed; box-shadow:none; transform:none; }
.process-btn .btn-sub { display:block; font-size:0.68em; font-weight:400; color:rgba(255,255,255,.70); margin-top:2px; }
.drop-zone { position:relative; border:2px dashed #93c5d4; border-radius:14px; background:#fff; flex:1; min-height:clamp(160px,25vh,300px); display:flex; flex-direction:column; align-items:center; justify-content:center; padding:clamp(14px,2.5vw,24px) clamp(10px,2vw,16px) clamp(10px,2vw,16px); transition:border .18s,background .18s; cursor:pointer; overflow:visible; }
.drop-zone.drag-over { border-color:#0e7490; background:#f6fdff; box-shadow:0 4px 32px rgba(14,116,144,.18); }
.dz-icon-row { display:flex; align-items:center; justify-content:center; gap:clamp(12px,2.5vw,28px); margin-bottom:10px; }
.dz-icon-side { font-size:clamp(3.5em,6vw,6em); line-height:1; }
.dz-icon { font-size:clamp(4em,6.5vw,6.5em); line-height:1; }
.dz-icon-world { font-size:clamp(4em,6.5vw,6.5em); line-height:1; filter:grayscale(1) brightness(0.6); -webkit-filter:grayscale(1) brightness(0.6); opacity:0.55; }
.dz-title { font-size:clamp(0.9em,1.5vw,1.05em); font-weight:700; color:#1e3a4a; margin-top:4px; text-align:center; }
.dz-sub   { font-size:clamp(0.68em,1.2vw,0.75em); color:#6b8a99; text-align:center; }
.dz-badge { background:#e0f2f7; border:1px solid #93c5d4; border-radius:20px; padding:4px 12px; font-size:0.72em; color:#0e7490; letter-spacing:.06em; margin-top:6px; display:inline-block; white-space:nowrap; }
#preview { display:flex; gap:12px; flex-wrap:wrap; justify-content:center; max-width:580px; margin-top:10px; padding:4px 8px 4px 8px; position:relative; z-index:10; pointer-events:auto; }
.prev-thumb { width:72px; height:72px; object-fit:cover; border-radius:6px; border:2px solid #93c5d4; box-shadow:0 2px 8px rgba(0,0,0,.15); display:block; }
.prev-name { font-size:0.68em; color:#0e7490; text-align:center; max-width:72px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.prev-item { display:flex; flex-direction:column; align-items:center; gap:3px; position:relative; cursor:default; }
.prev-remove { position:absolute; top:3px; right:3px; width:22px; height:22px; border-radius:50%; background:rgba(220,38,38,0.92); color:#fff; border:2px solid #fff; font-size:13px; line-height:1; display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:0 1px 5px rgba(0,0,0,.4); z-index:20; font-weight:900; opacity:0; transition:opacity .15s; pointer-events:auto; }
.prev-item:hover .prev-remove { opacity:1; }
.drop-zone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; z-index:5; }
/* Settings Modal */
.key-section { border:1px solid #e0e7ef; border-radius:8px; margin-bottom:12px; background:#f8fafc; }
.key-section-title { font-weight:700; font-size:0.92em; padding:8px 12px; background:#e0f2f7; border-radius:8px 8px 0 0; cursor:pointer; user-select:none; display:flex; align-items:center; justify-content:space-between; }
.key-section.collapsed .key-section-title::after { content:'\\25BC'; font-size:0.75em; }
.key-section:not(.collapsed) .key-section-title::after { content:'\\25B2'; font-size:0.75em; }
.key-section.collapsed .key-row { display:none; }
.key-row { display:flex; align-items:center; justify-content:space-between; padding:7px 12px; border-bottom:1px solid #e0e7ef; gap:12px; }
.key-row:last-child { border-bottom:none; }
.key-row label { font-size:0.82em; color:#334; min-width:160px; }
.key-row-input { display:flex; align-items:center; gap:6px; flex:1; }
.key-row-input input { flex:1; border:1px solid #cdd; border-radius:5px; padding:5px 8px; font-size:0.82em; }
.eye-btn { background:none; border:none; cursor:pointer; font-size:1.1em; padding:0 2px; }
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,.35); align-items:center; justify-content:center; z-index:1000; }
.modal-overlay.open { display:flex; }
.modal-card { background:#fff; border-radius:14px; box-shadow:0 6px 40px rgba(14,116,144,.22); padding:0; min-width:360px; max-width:560px; width:95vw; max-height:88vh; display:flex; flex-direction:column; }
.modal-header { display:flex; align-items:center; justify-content:space-between; padding:18px 20px 0 20px; flex-shrink:0; }
.modal-header h2 { font-size:1.1em; color:#0E7490; }
.modal-close { background:none; border:none; font-size:1.3em; cursor:pointer; color:#888; line-height:1; }
.modal-body { padding:14px 20px; overflow-y:auto; flex:1; }
.modal-footer { display:flex; align-items:center; justify-content:flex-end; gap:12px; padding:12px 20px 16px 20px; border-top:1px solid #e0e7ef; flex-shrink:0; }
.save-notice { color:#16a34a; font-weight:700; display:none; align-items:center; gap:6px; font-size:0.9em; margin-right:auto; }
.btn-cancel { background:#e0e7ef; color:#0E7490; border:none; border-radius:6px; padding:8px 18px; font-size:0.95em; font-weight:700; cursor:pointer; }
.btn-save { background:#0E7490; color:#fff; border:none; border-radius:6px; padding:8px 18px; font-size:0.95em; font-weight:700; cursor:pointer; }
.show-all-btn { background:none; border:none; color:#0E7490; font-weight:700; cursor:pointer; font-size:0.85em; margin-bottom:10px; text-decoration:underline; }
.upload-json-row { display:flex; flex-direction:column; gap:6px; padding:8px 12px; border-bottom:1px solid #e0e7ef; }
.upload-json-row label { font-size:0.82em; color:#334; font-weight:600; }
.upload-json-row .upload-hint { font-size:0.75em; color:#888; }
.upload-json-btn { background:#0e7490; color:#fff; border:none; border-radius:5px; padding:5px 12px; font-size:0.8em; font-weight:700; cursor:pointer; }
.upload-json-btn:hover { background:#0891b2; }
.upload-status { font-size:0.78em; margin-top:2px; }
.custom-key-add-row { display:flex; gap:6px; padding:8px 12px; }
.custom-key-add-row input { flex:1; border:1px solid #cdd; border-radius:5px; padding:5px 8px; font-size:0.82em; }
.custom-key-add-btn { background:#0e7490; color:#fff; border:none; border-radius:5px; padding:5px 12px; font-size:0.82em; font-weight:700; cursor:pointer; white-space:nowrap; }
.custom-key-add-btn:hover { background:#0891b2; }
.badge-premium { background:#fef3c7; color:#92400e; }
.badge-optional { background:#f0fdf4; color:#166534; }
.badge-claude  { background:#fae8ff; color:#86198f; }
.badge-grok    { background:#fff7ed; color:#c2410c; }
.report-help { font-size:0.8em; color:#5a7a8a; line-height:1.55; margin-bottom:12px; }
.report-grid { display:flex; flex-direction:column; gap:10px; }
.report-field { display:flex; flex-direction:column; gap:5px; }
.report-field label { font-size:0.82em; color:#334; font-weight:600; }
.report-field input, .report-field select, .report-field textarea { width:100%; border:1px solid #cdd; border-radius:6px; padding:8px 10px; font-size:0.82em; font-family:inherit; }
.report-field textarea { min-height:90px; resize:vertical; }
.report-readonly { background:#f8fafc; color:#5a7a8a; }
.report-status { display:none; font-size:0.82em; line-height:1.45; margin-right:auto; max-width:60%; }
.report-status.ok { color:#166534; display:block; }
.report-status.err { color:#b91c1c; display:block; }
.report-link { color:#0E7490; font-weight:700; text-decoration:underline; }
.report-actions { display:flex; gap:10px; margin-right:auto; align-items:center; flex-wrap:wrap; }
.report-actions.open { display:flex; }
.report-action-btn { border:none; border-radius:6px; padding:8px 12px; font-size:0.82em; font-weight:700; cursor:pointer; }
.report-action-copy { background:#e0f2f7; color:#0E7490; }
.report-action-copy:hover { background:#cbe8f1; }
.report-action-link { color:#0E7490; text-decoration:underline; font-size:0.82em; font-weight:700; }
#reportModal .btn-cancel,
#reportModal .btn-save { min-width:132px; display:inline-flex; align-items:center; justify-content:center; }
/* PIN Lock */
.pin-overlay { display:none; position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,.55); align-items:center; justify-content:center; z-index:2000; }
.pin-overlay.open { display:flex; }
.pin-card { background:#fff; border-radius:14px; box-shadow:0 6px 40px rgba(14,116,144,.30); padding:28px 30px 24px; min-width:300px; max-width:360px; width:90vw; text-align:center; }
.pin-card h3 { margin:0 0 6px; font-size:1.1em; color:#0E7490; }
.pin-card p  { margin:0 0 18px; font-size:0.85em; color:#555; }
.pin-inputs  { display:flex; gap:10px; justify-content:center; margin-bottom:10px; }
.pin-inputs input { width:48px; height:54px; text-align:center; font-size:1.6em; font-weight:700; border:2px solid #c0d9e4; border-radius:8px; outline:none; transition:border-color .2s; color:#0E7490; }
.pin-inputs input:focus { border-color:#0E7490; }
.pin-error  { color:#dc2626; font-size:0.82em; min-height:18px; margin-bottom:8px; }
.pin-actions { display:flex; gap:10px; justify-content:center; margin-top:14px; }
.pin-btn  { border:none; border-radius:6px; padding:8px 20px; font-size:0.9em; font-weight:700; cursor:pointer; }
.pin-btn-cancel { background:#e0e7ef; color:#0E7490; }
.pin-btn-primary { background:#0E7490; color:#fff; }
.pin-btn-danger  { background:#fee2e2; color:#dc2626; }
@keyframes pinShake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-8px)} 40%,80%{transform:translateX(8px)} }
.pin-shake { animation:pinShake .4s ease; }
.modal-lock-btn { background:none; border:none; font-size:1.2em; cursor:pointer; padding:0 6px; line-height:1; opacity:.7; }
.modal-lock-btn:hover { opacity:1; }
.modal-info-btn { background:#0E7490; border:none; font-size:0.72em; cursor:pointer; padding:0 8px 0 6px; line-height:1; color:#fff; font-weight:700; height:22px; border-radius:20px; display:inline-flex; align-items:center; gap:4px; opacity:.85; }
.modal-info-btn:hover { opacity:1; background:#0891b2; }
.help-overlay { display:none; position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,.55); align-items:center; justify-content:center; z-index:1200; }
.help-overlay.open { display:flex; }
.help-box { background:#fff; border-radius:14px; box-shadow:0 8px 40px rgba(14,116,144,.3); width:90vw; max-width:620px; max-height:85vh; display:flex; flex-direction:column; overflow:hidden; }
.help-box-hdr { background:#0E7490; padding:13px 18px; display:flex; align-items:center; justify-content:space-between; flex-shrink:0; }
.help-box-hdr h3 { color:#fff; font-size:0.98em; font-weight:700; }
.help-box-close { background:none; border:none; color:#fff; font-size:1.3em; cursor:pointer; line-height:1; }
.help-box-body { padding:18px 20px; overflow-y:auto; flex:1; }
.help-sec-title { font-size:0.97em; font-weight:800; color:#0E7490; margin:0 0 10px; display:flex; align-items:center; gap:7px; }
.help-sep { height:1px; background:#e0eaf0; margin:18px 0; }
.help-what-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px; }
.help-wc { background:#f0f9fb; border:1px solid #b8dde8; border-radius:8px; padding:10px 12px; }
.help-wc-num { font-size:1em; font-weight:800; color:#0E7490; }
.help-wc-title { font-weight:700; font-size:0.85em; color:#1e3a4a; }
.help-wc-desc { font-size:0.76em; color:#6b8a99; margin-top:2px; }
.help-card { background:#f8fafc; border:1.5px solid #d4eaf0; border-radius:9px; padding:11px 14px; margin-bottom:8px; }
.help-card-title { font-weight:700; color:#0E7490; font-size:0.88em; margin-bottom:4px; }
.help-card-desc { font-size:0.82em; color:#5a7a8a; line-height:1.65; }
.help-card-link { font-size:0.8em; color:#0891b2; margin-top:4px; }
.help-note { background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; padding:9px 13px; font-size:0.81em; color:#92400e; line-height:1.5; margin-top:8px; }
.help-divider { text-align:center; font-size:0.75em; color:#aaa; font-weight:700; margin:8px 0; }
.help-badge-free { display:inline-block; font-size:0.68em; font-weight:700; border-radius:4px; padding:1px 6px; margin-left:5px; background:#dcfce7; color:#166534; vertical-align:middle; }
.help-badge-opt { display:inline-block; font-size:0.68em; font-weight:700; border-radius:4px; padding:1px 6px; margin-left:5px; background:#fef9c3; color:#92400e; vertical-align:middle; }
.help-ready-row { display:flex; align-items:flex-start; gap:10px; background:#f8fafc; border:1px solid #d4eaf0; border-radius:8px; padding:10px 13px; margin-bottom:6px; }
.help-ready-row .hri { font-size:1.1em; flex-shrink:0; margin-top:1px; }
.help-ready-row .hrt { font-size:0.83em; color:#2a4a5a; line-height:1.4; }
.pin-confirm-label { font-size:0.82em; color:#555; margin:12px 0 6px; }
.db-banner-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:0 clamp(8px,1.5vw,16px); height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; text-decoration:none; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; white-space:nowrap; }
.db-banner-btn:hover { background:rgba(255,255,255,.25); }
.kill-banner-btn { background:#dc2626; border:1px solid #dc2626; color:#fff; border-radius:7px; padding:0 clamp(8px,1.5vw,16px); height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; white-space:nowrap; }
.kill-banner-btn:hover { background:#b91c1c; }
/* ── Responsive breakpoints ── */
@media (max-width:1100px) {
  .body { flex-direction:column; align-items:center; gap:14px; padding:14px 10px; }
  .left, .right { width:100%; max-width:720px; }
  .right { width:100%; max-width:720px; }
  .drop-zone { min-height:220px; flex:none; }
}
@media (max-width:768px) {
  .hdr { padding:10px 14px; }
  .hdr-sub { display:none; }
  .settings-btn, .db-banner-btn, .kill-banner-btn, .report-btn { height:32px; font-size:0.78em; }
  .body { padding:8px; gap:10px; }
  .drop-zone { min-height:180px; padding:12px 10px; }
  .dz-icon-row { gap:10px; }
  .right { border-radius:10px; }
  .modal-card { min-width:0; width:95vw; }
}
@media (max-width:520px) {
  .hdr-logo { font-size:0.95em; }
  .db-banner-btn .db-label { display:none; }
  .kill-banner-btn .kill-label { display:none; }
  .report-btn .report-label { display:none; }
  .settings-btn .settings-label { display:none; }
  .body { padding:6px; gap:8px; }
  .drop-zone { min-height:150px; padding:10px 8px; }
  .dz-icon-row { margin-bottom:6px; }
  .dz-badge { font-size:0.65em; padding:3px 8px; }
  .right { padding:8px; }
  .process-btn { font-size:0.85em; padding:10px; }
}
@media print { .modal-overlay, .hdr-actions { display:none !important; } }
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-logo">CV-<span>Toposheet</span></div>
    <div class="hdr-sub">Extracted Map Features</div>
  </div>
  <div class="hdr-actions">
    <a href="/results" class="db-banner-btn" title="View all processed maps in the database">&#128202;&nbsp;<span class="db-label">Map Database</span></a>
    <button class="kill-banner-btn" id="killBannerBtn" onclick="killRunningJob()" title="Kill any running job and restart the server">&#9632;<span class="kill-label"> Restart Server</span></button>
    <button type="button" class="report-btn" id="reportBtn" title="Report inappropriate or unsafe AI-generated content">&#9888;&#xFE0E;<span class="report-label"> Report AI Content</span></button>
    <button class="settings-btn" onclick="openSettings()">&#9881;&#xFE0E;<span class="settings-label"> Settings</span></button>
  </div>
</div>

<form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data">
<div class="body">
  <div class="left">
      <div class="drop-zone" id="dropZone">
        <input type="file" name="map_file" id="fileInput" accept=".jpg,.jpeg,.png,.tif,.tiff" multiple>
        <div class="dz-icon-row">
          <!-- Icon 1: Colourful topographic mountain map -->
          <span class="dz-icon-side"><svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 72 72" style="display:inline-block;vertical-align:middle">
            <rect x="4" y="4" width="64" height="64" rx="7" fill="#fffde7" stroke="#f59e0b" stroke-width="2.5"/>
            <!-- sky gradient band -->
            <rect x="5" y="5" width="62" height="28" rx="6" fill="#bfecfd"/>
            <!-- green ground -->
            <rect x="5" y="45" width="62" height="22" rx="0" fill="#a7d994"/>
            <rect x="5" y="55" width="62" height="12" rx="0" fill="#86c96e"/>
            <!-- main mountain -->
            <polygon points="36,12 58,48 14,48" fill="#8fae6b" stroke="#4a7c30" stroke-width="1.8" stroke-linejoin="round"/>
            <!-- mountain shadow side -->
            <polygon points="36,12 58,48 36,48" fill="#7a9c58" stroke="none"/>
            <!-- snow cap -->
            <polygon points="36,12 42,26 30,26" fill="#fff" stroke="#cbd5e1" stroke-width="1"/>
            <!-- contour lines (warm orange) -->
            <path d="M18,52 Q36,46 54,52" fill="none" stroke="#f59e0b" stroke-width="1.5" stroke-linecap="round"/>
            <path d="M10,58 Q36,51 62,58" fill="none" stroke="#f59e0b" stroke-width="1.5" stroke-linecap="round"/>
            <path d="M5,64 Q36,57 67,64" fill="none" stroke="#d97706" stroke-width="1.4" stroke-linecap="round"/>
            <!-- border -->
            <rect x="4" y="4" width="64" height="64" rx="7" fill="none" stroke="#f59e0b" stroke-width="2.5"/>
          </svg></span>
          <!-- Icon 2: Medium grey world map -->
          <span class="dz-icon-side" style="filter:grayscale(1) brightness(1.1) contrast(0.5);-webkit-filter:grayscale(1) brightness(1.1) contrast(0.5);opacity:0.55;">&#128506;</span>
          <!-- Icon 3: Colourful AI laptop -->
          <span class="dz-icon-side"><svg xmlns="http://www.w3.org/2000/svg" width="1em" height="0.9em" viewBox="0 0 80 70" style="display:inline-block;vertical-align:middle">
            <!-- screen frame -->
            <rect x="8" y="5" width="64" height="42" rx="5" fill="#1e3a5f" stroke="#3b82f6" stroke-width="2.2"/>
            <!-- screen glow -->
            <rect x="13" y="10" width="54" height="32" rx="2" fill="#0f172a"/>
            <!-- subtle screen gradient overlay -->
            <rect x="13" y="10" width="54" height="16" rx="2" fill="url(#sg)" opacity="0.3"/>
            <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#60a5fa" stop-opacity="0.5"/><stop offset="1" stop-color="#0f172a" stop-opacity="0"/></linearGradient></defs>
            <!-- neural net connections -->
            <line x1="40" y1="26" x2="24" y2="17" stroke="#60a5fa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="56" y2="17" stroke="#60a5fa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="24" y2="35" stroke="#60a5fa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="56" y2="35" stroke="#60a5fa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="40" y2="14" stroke="#a78bfa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="40" y2="38" stroke="#a78bfa" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="19" y2="26" stroke="#34d399" stroke-width="1.2" opacity="0.9"/>
            <line x1="40" y1="26" x2="61" y2="26" stroke="#34d399" stroke-width="1.2" opacity="0.9"/>
            <!-- outer nodes -->
            <circle cx="40" cy="14" r="3.2" fill="#a78bfa" stroke="#fff" stroke-width="1"/>
            <circle cx="40" cy="38" r="3.2" fill="#a78bfa" stroke="#fff" stroke-width="1"/>
            <circle cx="19" cy="26" r="3.2" fill="#34d399" stroke="#fff" stroke-width="1"/>
            <circle cx="61" cy="26" r="3.2" fill="#34d399" stroke="#fff" stroke-width="1"/>
            <circle cx="24" cy="17" r="2.6" fill="#60a5fa" stroke="#fff" stroke-width="0.8"/>
            <circle cx="56" cy="17" r="2.6" fill="#60a5fa" stroke="#fff" stroke-width="0.8"/>
            <circle cx="24" cy="35" r="2.6" fill="#60a5fa" stroke="#fff" stroke-width="0.8"/>
            <circle cx="56" cy="35" r="2.6" fill="#60a5fa" stroke="#fff" stroke-width="0.8"/>
            <!-- centre node glowing -->
            <circle cx="40" cy="26" r="6" fill="#f59e0b" stroke="#fff" stroke-width="1.8"/>
            <circle cx="40" cy="26" r="2.8" fill="#fff"/>
            <!-- laptop base -->
            <path d="M4,47 Q40,51 76,47 L72,61 Q40,64 8,61 Z" fill="#3b82f6" stroke="#1e3a5f" stroke-width="1.8"/>
            <!-- keyboard strip -->
            <rect x="28" y="53" width="24" height="3.5" rx="1.8" fill="#93c5fd"/>
            <!-- hinge -->
            <rect x="34" y="46" width="12" height="3" rx="1.5" fill="#1e3a5f"/>
          </svg></span>
        </div>
        <div class="dz-title">Upload Toposheet</div>
        <div class="dz-sub">Drag &amp; drop map images here, or click to browse</div>
        <div class="dz-badge">JPG &middot; PNG &middot; TIF &middot; Up to 500 MB each</div>
        <div id="preview"></div>
      </div>
  </div>

  <div class="right">
    <div class="right-title">Choose Model</div>
    <div class="model-cards" id="modelCards">
      <label class="model-card selected" onclick="selectCard(this)" data-model="best">
        <input type="radio" name="model_sel" value="best" checked>
        <div class="mc-body">
          <div class="mc-name">1. Best Quality <span class="badge badge-best">RECOMMENDED</span></div>
          <div class="mc-desc">GCV OCR (parallel) + Vertex AI Gemini 2.5 Flash</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="fast">
        <input type="radio" name="model_sel" value="fast">
        <div class="mc-body">
          <div class="mc-name">2. Fast <span class="badge badge-fast">QUICK</span></div>
          <div class="mc-desc">GCV OCR (parallel) + Groq LLaMA 3.3 70B</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="ensemble">
        <input type="radio" name="model_sel" value="ensemble">
        <div class="mc-body">
          <div class="mc-name">3. All LLMs Together <span class="badge badge-premium">PREMIUM</span></div>
          <div class="mc-desc">GCV OCR + all configured LLMs run in parallel</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="openai">
        <input type="radio" name="model_sel" value="openai">
        <div class="mc-body">
          <div class="mc-name">4. OpenAI GPT-4o <span class="badge badge-optional">OPTIONAL</span></div>
          <div class="mc-desc">GCV OCR + GPT-4o-mini</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="claude">
        <input type="radio" name="model_sel" value="claude">
        <div class="mc-body">
          <div class="mc-name">5. Claude Haiku <span class="badge badge-claude">CLAUDE</span></div>
          <div class="mc-desc">GCV OCR + Claude Haiku</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="grok">
        <input type="radio" name="model_sel" value="grok">
        <div class="mc-body">
          <div class="mc-name">6. Grok (xAI) <span class="badge badge-grok">GROK</span></div>
          <div class="mc-desc">GCV OCR + Grok-3-mini</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="gemini">
        <input type="radio" name="model_sel" value="gemini">
        <div class="mc-body">
          <div class="mc-name">7. Gemini API <span class="badge badge-gemini">GEMINI KEY</span></div>
          <div class="mc-desc">GCV OCR + Gemini 1.5 Flash</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="openrouter">
        <input type="radio" name="model_sel" value="openrouter">
        <div class="mc-body">
          <div class="mc-name">8. OpenRouter <span class="badge badge-optional">OPTIONAL</span></div>
          <div class="mc-desc">GCV OCR + OpenRouter (any model)</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="offline">
        <input type="radio" name="model_sel" value="offline">
        <div class="mc-body">
          <div class="mc-name">9. Offline <span class="badge badge-offline">NO CLOUD</span></div>
          <div class="mc-desc">EasyOCR (local) + Groq LLaMA 3.3 70B</div>
        </div>
      </label>
      <div class="info-strip" id="modelInfo">
        <b>Best Quality:</b> Uses Google Cloud Vision for OCR and Vertex AI Gemini for feature extraction. Requires internet &amp; GCP credentials.
      </div>
    </div>
    <input type="hidden" name="model" id="modelInput" value="best">
    <button type="submit" class="process-btn" id="processBtn" disabled>
      &#9654; Process Map
      <span class="btn-sub">Select a map file to continue</span>
    </button>
  </div>
</div>
</form>

<script>
const MODEL_INFO = {
  best:     '<b>Best Quality:</b> Parallel GCV OCR tiles + Vertex AI Gemini 2.5 Flash. Largest batches (150 items), fastest Gemini model. Requires GCP service account.',
  fast:     '<b>Groq LLaMA (Fast):</b> Parallel GCV OCR + Groq LLaMA 3.1 8B. No inter-batch delays. Fastest total processing time. Free Groq API key needed.',
  ensemble: '<b>Ensemble (All LLMs):</b> Parallel GCV OCR + ALL your configured LLMs run simultaneously. Results merged by majority vote for maximum confidence. Slowest but most accurate.',
  openai:   '<b>OpenAI GPT-4o:</b> Parallel GCV OCR + GPT-4o-mini. 80 items/batch. Reliable paid OpenAI API.',
  claude:   '<b>Claude Haiku:</b> Parallel GCV OCR + Claude 3.5 Haiku. Strong reasoning. Anthropic API key required.',
  grok:     '<b>Grok (xAI):</b> Parallel GCV OCR + Grok-3-mini. Fast xAI inference. xAI API key required.',
  gemini:      '<b>Gemini API:</b> Google Cloud Vision OCR + Gemini (API key). Uses GEMINI_API_KEY from .env no Vertex AI or GCP billing needed.',
  openrouter:  '<b>OpenRouter:</b> Parallel GCV OCR + any model via OpenRouter. Access 100s of models (free and paid) with one API key. OpenRouter API key required.',
  offline:     '<b>Offline OCR:</b> EasyOCR runs locally on your machine. No Google Cloud Vision or GCP service account needed. Internet required for LLM cleaning. Use this mode when you have no GCP credentials.',
};

function killRunningJob() {
  const btn = document.getElementById('killBannerBtn');
  btn.disabled = true;
  btn.style.background = '#b91c1c';
  btn.textContent = '⏳ Stopping…';
  fetch('/restart', {method:'POST'})
    .finally(() => {
      btn.textContent = '✔ Done — going home…';
      setTimeout(() => { window.location.href = '/'; }, 800);
    });
}
function selectCard(el) {
  document.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const val = el.querySelector('input[type=radio]').value;
  document.getElementById('modelInput').value = val;
  const infoEl = document.getElementById('modelInfo');
  if (infoEl && MODEL_INFO[val]) {
    const counts = infoEl.querySelector('span');
    infoEl.innerHTML = (MODEL_INFO[val] || '') + (counts ? '<br>' + counts.outerHTML : '');
  }
}

const fileInput = document.getElementById('fileInput');
const dropZone  = document.getElementById('dropZone');
const preview   = document.getElementById('preview');
const processBtn = document.getElementById('processBtn');

fileInput.addEventListener('change', showPreviews);
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  // Merge dropped files into _fileList (don't replace — accumulate)
  Array.from(e.dataTransfer.files).forEach(f => {
    const already = _fileList.some(x => x.name === f.name && x.size === f.size);
    if (!already) _fileList.push(f);
  });
  _syncInputFiles();
  _renderPreviews();
});

// DataTransfer-based file list so we can remove individual files
let _fileList = [];   // array of File objects

function _syncInputFiles() {
  const dt = new DataTransfer();
  _fileList.forEach(f => dt.items.add(f));
  fileInput.files = dt.files;
}

function _renderPreviews() {
  preview.innerHTML = '';
  if (!_fileList.length) {
    processBtn.disabled = true;
    processBtn.querySelector('.btn-sub').textContent = 'Select a map file to continue';
    return;
  }
  processBtn.disabled = false;
  processBtn.querySelector('.btn-sub').textContent = _fileList.length + ' file(s) ready';
  _fileList.forEach((f, idx) => {
    const item = document.createElement('div');
    item.className = 'prev-item';

    // Remove (×) button
    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'prev-remove';
    rm.title = 'Remove ' + f.name;
    rm.textContent = '\u00D7';
    rm.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _fileList.splice(idx, 1);
      _syncInputFiles();
      _renderPreviews();
    });
    rm.addEventListener('mouseenter', e => e.stopPropagation());
    rm.addEventListener('mouseover', e => e.stopPropagation());

    const img = document.createElement('img');
    img.className = 'prev-thumb';
    img.src = URL.createObjectURL(f);

    const name = document.createElement('div');
    name.className = 'prev-name';
    name.textContent = f.name;

    item.appendChild(rm);
    item.appendChild(img);
    item.appendChild(name);
    preview.appendChild(item);
  });
}

function showPreviews() {
  // Merge newly picked files into _fileList (don't replace — accumulate)
  const incoming = Array.from(fileInput.files);
  incoming.forEach(f => {
    const already = _fileList.some(x => x.name === f.name && x.size === f.size);
    if (!already) _fileList.push(f);
  });
  _syncInputFiles();
  _renderPreviews();
}

// Settings modal
const SECTION_IDS = ['llm','google','vertex','models','custom'];

// ── PIN Lock ──────────────────────────────────────────────────────────────
const PIN_KEY = 'cvt_settings_pin';
function _getPin()   { return localStorage.getItem(PIN_KEY) || ''; }
function _savePin(p) { localStorage.setItem(PIN_KEY, p); }
function _clearPin() { localStorage.removeItem(PIN_KEY); }

function _loadSettingsData() {
  fetch('/get_env').then(r => r.json()).then(data => {
    const legacyOpenRouterModel = 'meta-llama/llama-3.3-70b-instruct:free';
    const currentOpenRouterModel = (data.OPENROUTER_MODEL || '').trim();
    if (!currentOpenRouterModel || currentOpenRouterModel === legacyOpenRouterModel) {
      data.OPENROUTER_MODEL = 'openrouter/auto';
      // Persist migration so next app start stays on the new default.
      fetch('/save_env', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ OPENROUTER_MODEL: 'openrouter/auto' })
      }).catch(() => {});
    }
    for (const [k, v] of Object.entries(data)) {
      const inp = document.getElementById('inp_' + k);
      if (inp) inp.value = v || '';
    }
  }).catch(() => {});
  document.getElementById('settingsModal').classList.add('open');
  _updateLockBtn();
}

function openSettings() {
  if (_getPin()) { _showPinEntry(); }
  else           { _loadSettingsData(); }
}

function closeSettings() {
  document.getElementById('settingsModal').classList.remove('open');
}
function openHelp() {
  document.getElementById('helpOverlay').classList.add('open');
}
function closeHelp() {
  document.getElementById('helpOverlay').classList.remove('open');
}

const REPORT_GITHUB_NEW_ISSUE_URL = 'https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues/new';

function buildQuickGithubIssueUrl() {
  const model = getReportModelValue() || 'not provided';
  const title = encodeURIComponent('AI content report (quick)');
  const body = encodeURIComponent(
    'Category: AI content concern\\n' +
    'Model: ' + model + '\\n\\n' +
    'Please describe the issue here.\\n'
  );
  return REPORT_GITHUB_NEW_ISSUE_URL + '?title=' + title + '&body=' + body;
}

function updateQuickGithubReportLink() {
  const issueLink = document.getElementById('reportIssueLink');
  if (!issueLink) return;
  issueLink.href = buildQuickGithubIssueUrl();
  issueLink.textContent = 'Report in GitHub';
}

function openReportModal(evt) {
  try {
    if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
    const model = document.getElementById('modelInput');
    const selectedNameEl = document.querySelector('.model-card.selected .mc-name');
    const selectedName = selectedNameEl ? selectedNameEl.textContent.replace(/\\s+/g, ' ').trim() : '';
    const selectedCode = model ? model.value : '';
    const modelSelect = document.getElementById('report_model');
    const modelOther = document.getElementById('report_model_other');
    const known = ['best','fast','ensemble','openai','claude','grok','gemini','openrouter','offline'];
    window._lastReportPayload = null;
    if (modelSelect) {
      if (known.includes(selectedCode)) {
        modelSelect.value = selectedCode;
        if (modelOther) modelOther.value = '';
      } else {
        modelSelect.value = 'other';
        if (modelOther) modelOther.value = selectedName || selectedCode;
      }
    }
    toggleReportModelOther();
    const catEl = document.getElementById('report_category');
    if (catEl) catEl.value = 'inappropriate-output';
    const snipEl = document.getElementById('report_snippet');
    if (snipEl) snipEl.value = '';
    const detEl = document.getElementById('report_details');
    if (detEl) detEl.value = '';
    const conEl = document.getElementById('report_contact');
    if (conEl) conEl.value = '';
    const statusEl = document.getElementById('reportStatus');
    if (statusEl) { statusEl.className = 'report-status'; statusEl.style.display = 'none'; statusEl.innerHTML = ''; }
    updateQuickGithubReportLink();
    const modal = document.getElementById('reportModal');
    if (modal) {
      modal.classList.add('open');
    } else {
      alert('Report modal not found on this page. Please go to the main page to report AI content.');
    }
  } catch (err) {
    var errBanner = document.getElementById('_reportErrBanner');
    if (!errBanner) {
      errBanner = document.createElement('div');
      errBanner.id = '_reportErrBanner';
      errBanner.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#dc2626;color:#fff;padding:12px 24px;border-radius:8px;z-index:9999;font-weight:bold;box-shadow:0 4px 12px rgba(0,0,0,.4);';
      document.body.appendChild(errBanner);
    }
    errBanner.textContent = 'Report button error: ' + err.message;
    errBanner.style.display = 'block';
    setTimeout(function(){ errBanner.style.display = 'none'; }, 8000);
  }
}

function closeReportModal() {
  document.getElementById('reportModal').classList.remove('open');
}

function toggleReportModelOther() {
  const select = document.getElementById('report_model');
  const row = document.getElementById('report_model_other_row');
  if (!select || !row) return;
  row.style.display = select.value === 'other' ? 'flex' : 'none';
  updateQuickGithubReportLink();
}

function getReportModelValue() {
  const select = document.getElementById('report_model');
  const other = document.getElementById('report_model_other');
  if (!select) return '';
  if (select.value === 'other') {
    return (other && other.value ? other.value.trim() : 'Other');
  }
  const opt = select.options[select.selectedIndex];
  return opt ? opt.text : select.value;
}

function openEmailFallback(emailUrl) {
  if (!emailUrl) return;
  try {
    // Primary path for desktop browser/webview mailto handling.
    window.location.href = emailUrl;
    // Secondary path for environments that ignore location mailto navigation.
    setTimeout(() => {
      try {
        const a = document.createElement('a');
        a.href = emailUrl;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } catch (_) {}
    }, 50);
  } catch (_) {}
}

function submitAIReport() {
  const status = document.getElementById('reportStatus');
  const submitBtn = document.getElementById('reportSubmitBtn');
  const payload = {
    category: document.getElementById('report_category').value,
    model: getReportModelValue(),
    snippet: document.getElementById('report_snippet').value.trim(),
    details: document.getElementById('report_details').value.trim(),
    contact: document.getElementById('report_contact').value.trim(),
  };
  if (document.getElementById('report_model').value === 'other' && !payload.model) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Please type the model name for Other.';
    return;
  }
  if (!payload.details) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Please describe the issue before submitting.';
    return;
  }
  submitBtn.disabled = true;
  submitBtn.textContent = 'Submitting...';
  fetch('/report_ai_content', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  })
    .then(async r => {
      const raw = await r.text();
      let data = null;
      if (raw) {
        try {
          data = JSON.parse(raw);
        } catch (_err) {
          data = null;
        }
      }
      return { ok: r.ok, status: r.status, data, raw };
    })
    .then(({ ok, status: httpStatus, data, raw }) => {
      if (!ok || !data || !data.ok) {
        const msg = (data && data.error)
          || (raw && raw.trim())
          || ('Submission failed (HTTP ' + httpStatus + ').');
        throw new Error(msg);
      }
      window._lastReportPayload = data;
      status.className = 'report-status ok';
      status.style.display = 'block';
      status.innerHTML = 'Saved with reference <b>' + data.report_id + '</b>.';
      const actions = document.getElementById('reportActions');
      actions.classList.add('open');
      document.getElementById('reportIssueLink').href = data.issue_url;
      document.getElementById('reportIssueLink').textContent = 'Report in GitHub';
      openEmailFallback(data.email_url);
    })
    .catch(err => {
      status.className = 'report-status err';
      status.style.display = 'block';
      status.textContent = err.message || 'Submission failed.';
    })
    .finally(() => {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit';
    });
}

function copyAIReport() {
  const status = document.getElementById('reportStatus');
  const data = window._lastReportPayload;
  if (!data || !data.report_text) {
    status.className = 'report-status err';
    status.style.display = 'block';
    status.textContent = 'Submit a report first, then copy.';
    return;
  }
  navigator.clipboard.writeText(data.report_text)
    .then(() => {
      status.className = 'report-status ok';
      status.style.display = 'block';
      status.textContent = 'Report copied. You can paste it into email or support chat.';
    })
    .catch(() => {
      status.className = 'report-status err';
      status.style.display = 'block';
      status.textContent = 'Copy failed. Please use the email or GitHub link.';
    });
}

function saveSettings() {
  const payload = {};
  document.querySelectorAll('.key-row-input input').forEach(inp => {
    const key = inp.id.replace('inp_', '');
    payload[key] = inp.value.trim();
  });
  if (!payload.OPENROUTER_MODEL) payload.OPENROUTER_MODEL = 'openrouter/auto';
  fetch('/save_env', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(() => {
    const notice = document.getElementById('saveNotice');
    notice.style.display = 'flex';
    setTimeout(() => { notice.style.display = 'none'; closeSettings(); }, 1200);
  });
}

function toggleEye(key) {
  const inp = document.getElementById('inp_' + key);
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

function uploadJsonFile(inputId, targetName, statusId) {
  const fileInput = document.getElementById(inputId);
  const statusEl = document.getElementById(statusId);
  if (!fileInput.files.length) { statusEl.style.color='#c00'; statusEl.textContent='No file selected.'; return; }
  const fd = new FormData();
  fd.append('json_file', fileInput.files[0]);
  fd.append('target', targetName);
  statusEl.style.color='#888'; statusEl.textContent='Uploading...';
  fetch('/upload_service_account', { method:'POST', body:fd })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { statusEl.style.color='#16a34a'; statusEl.textContent='\u2713 ' + fileInput.files[0].name + ' uploaded successfully'; }
      else      { statusEl.style.color='#c00';     statusEl.textContent='Error: ' + (d.error||'failed'); }
    }).catch(() => { statusEl.style.color='#c00'; statusEl.textContent='Upload failed.'; });
}

function addCustomKeyRow() {
  const name = document.getElementById('custom_key_name').value.trim().toUpperCase().replace(/[^A-Z0-9_]/g,'');
  const val  = document.getElementById('custom_key_value').value.trim();
  if (!name) { document.getElementById('custom_key_name').focus(); return; }
  // Avoid duplicates
  if (document.getElementById('inp_' + name)) { alert('Key "' + name + '" already exists above.'); return; }
  const container = document.getElementById('custom_key_rows');
  const row = document.createElement('div'); row.className = 'key-row'; row.id = 'custom_row_' + name;
  row.innerHTML = `<label style="font-size:0.82em;min-width:160px;">${name}</label>
    <div class="key-row-input">
      <input type="password" id="inp_${name}" class="key-row-input" value="${val.replace(/"/g,'&quot;')}" style="flex:1;border:1px solid #cdd;border-radius:5px;padding:5px 8px;font-size:0.82em;">
      <button class="eye-btn" onclick="toggleEye('${name}')">&#128065;</button>
      <button class="eye-btn" style="color:#c00;" onclick="document.getElementById('custom_row_${name}').remove()" title="Remove">&#10005;</button>
    </div>`;
  container.appendChild(row);
  document.getElementById('custom_key_name').value = '';
  document.getElementById('custom_key_value').value = '';
  document.getElementById('custom_key_name').focus();
}

function toggleSection(id) {
  document.getElementById('key_section_' + id).classList.toggle('collapsed');
}

function showAllSections() {
  SECTION_IDS.forEach(id => {
    document.getElementById('key_section_' + id).classList.remove('collapsed');
  });
}

document.addEventListener('DOMContentLoaded', function() {
  var reportBtn = document.getElementById('reportBtn');
  if (reportBtn) {
    reportBtn.addEventListener('click', function(e) { openReportModal(e); });
  }
  document.getElementById('settingsModal').addEventListener('click', function(e) {
    if (e.target === this) closeSettings();
  });
});

// ── PIN helpers ──────────────────────────────────────────────────────────
function _clearBoxes(prefix) {
  for (let i = 0; i < 4; i++) { const el = document.getElementById(prefix+i); if(el) el.value=''; }
}
function _readBoxes(prefix) {
  return [0,1,2,3].map(i => { const el=document.getElementById(prefix+i); return el ? el.value : ''; }).join('');
}
function _pinNav(e, prefix, idx, onComplete) {
  if (e.key === 'Backspace') {
    e.preventDefault(); e.target.value = '';
    if (idx > 0) document.getElementById(prefix+(idx-1)).focus();
    return;
  }
  if (!/^[0-9]$/.test(e.key)) { e.preventDefault(); return; }
  e.preventDefault(); e.target.value = e.key;
  if (idx < 3) { document.getElementById(prefix+(idx+1)).focus(); }
  else { setTimeout(onComplete, 60); }
}

// ── PIN Entry modal (gate before settings opens) ──────────────────────────
function _showPinEntry() {
  const m = document.getElementById('pinEntryModal');
  document.body.appendChild(m);
  _clearBoxes('pe');
  document.getElementById('pinEntryError').textContent = '';
  m.classList.add('open');
  setTimeout(() => document.getElementById('pe0').focus(), 50);
}
function _closePinEntry() { document.getElementById('pinEntryModal').classList.remove('open'); }
function _submitPinEntry() {
  const v = _readBoxes('pe');
  if (v.length < 4) return;
  if (v === _getPin()) { _closePinEntry(); _loadSettingsData(); }
  else {
    const c = document.getElementById('pinEntryCard');
    c.classList.add('pin-shake'); setTimeout(() => c.classList.remove('pin-shake'), 450);
    document.getElementById('pinEntryError').textContent = 'Incorrect PIN. Try again.';
    _clearBoxes('pe'); setTimeout(() => document.getElementById('pe0').focus(), 50);
  }
}

// ── PIN Setup modal (set / change / remove) ───────────────────────────────
// _pinStep: 'verify' | 'new' | 'confirm'
let _pinStep = '', _pinFirst = '';
function openPinSetup() {
  const m = document.getElementById('pinSetupModal');
  document.body.appendChild(m);
  _pinFirst = '';
  const hasPin = !!_getPin();
  _pinStep = hasPin ? 'verify' : 'new';
  _clearBoxes('px');
  document.getElementById('pinSetupError').textContent = '';
  document.getElementById('pinRemoveBtn').style.display = 'none';
  if (!hasPin) {
    document.getElementById('pinSetupTitle').textContent = '🔒 Set PIN Lock';
    document.getElementById('pinSetupDesc').textContent  = 'Enter a 4-digit PIN';
  } else {
    document.getElementById('pinSetupTitle').textContent = '🔒 Change PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter your current PIN';
  }
  m.classList.add('open');
  setTimeout(() => document.getElementById('px0').focus(), 50);
}
function closePinSetup() { document.getElementById('pinSetupModal').classList.remove('open'); }
function removePin() { _clearPin(); closePinSetup(); _updateLockBtn(); _showSettingsToast('PIN removed.'); }
function _pinSetupNext() {
  const v = _readBoxes('px');
  if (v.length < 4) return;
  const err = document.getElementById('pinSetupError');
  const card = document.getElementById('pinSetupCard');
  function shake() { card.classList.add('pin-shake'); setTimeout(()=>card.classList.remove('pin-shake'),450); }

  if (_pinStep === 'verify') {
    if (v !== _getPin()) {
      err.textContent = 'Incorrect PIN.'; shake();
      _clearBoxes('px'); setTimeout(() => document.getElementById('px0').focus(), 50);
      return;
    }
    _pinStep = 'new'; err.textContent = '';
    _clearBoxes('px');
    document.getElementById('pinSetupTitle').textContent = '🔒 Set New PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter a new 4-digit PIN';
    document.getElementById('pinRemoveBtn').style.display = 'inline-block';
    setTimeout(() => document.getElementById('px0').focus(), 50);

  } else if (_pinStep === 'new') {
    _pinFirst = v; _pinStep = 'confirm'; err.textContent = '';
    _clearBoxes('px');
    document.getElementById('pinSetupTitle').textContent = '🔒 Confirm PIN';
    document.getElementById('pinSetupDesc').textContent  = 'Enter the PIN again to confirm';
    setTimeout(() => document.getElementById('px0').focus(), 50);

  } else if (_pinStep === 'confirm') {
    if (v !== _pinFirst) {
      err.textContent = 'PINs do not match — try again.'; shake();
      _pinStep = 'new'; _pinFirst = '';
      _clearBoxes('px');
      document.getElementById('pinSetupTitle').textContent = '🔒 Set PIN Lock';
      document.getElementById('pinSetupDesc').textContent  = 'Enter a 4-digit PIN';
      setTimeout(() => document.getElementById('px0').focus(), 50);
      return;
    }
    _savePin(v); closePinSetup(); _updateLockBtn(); _showSettingsToast('PIN lock set ✓');
  }
}
function _updateLockBtn() {
  const btn = document.getElementById('pinLockBtn');
  if (!btn) return;
  const has = !!_getPin();
  btn.textContent = has ? '🔒' : '🔓';
  btn.title = has ? 'PIN lock ON · click to change/remove' : 'No PIN · click to set lock';
}
function _showSettingsToast(msg) {
  const n = document.getElementById('saveNotice');
  if (!n) return;
  n.style.display = 'flex';
  const sp = n.querySelector('span');
  if (sp) sp.textContent = msg; else n.textContent = msg;
  setTimeout(() => n.style.display = 'none', 2500);
}
// Init lock icon on page load
window.addEventListener('DOMContentLoaded', _updateLockBtn);</script>

<!-- PIN Entry Modal (gate before settings opens when PIN is set) -->
<div class="pin-overlay" id="pinEntryModal">
  <div class="pin-card" id="pinEntryCard">
    <h3>🔒 Settings Locked</h3>
    <p>Enter your 4-digit PIN to open Settings</p>
    <div class="pin-inputs">
      <input type="password" inputmode="numeric" maxlength="1" id="pe0" onkeydown="_pinNav(event,'pe',0,_submitPinEntry)">
      <input type="password" inputmode="numeric" maxlength="1" id="pe1" onkeydown="_pinNav(event,'pe',1,_submitPinEntry)">
      <input type="password" inputmode="numeric" maxlength="1" id="pe2" onkeydown="_pinNav(event,'pe',2,_submitPinEntry)">
      <input type="password" inputmode="numeric" maxlength="1" id="pe3" onkeydown="_pinNav(event,'pe',3,_submitPinEntry)">
    </div>
    <div class="pin-error" id="pinEntryError"></div>
    <div class="pin-actions">
      <button class="pin-btn pin-btn-cancel" onclick="_closePinEntry()">Cancel</button>
    </div>
  </div>
</div>

<!-- PIN Setup Modal (set / change / remove) -->
<div class="pin-overlay" id="pinSetupModal">
  <div class="pin-card" id="pinSetupCard">
    <h3 id="pinSetupTitle">🔒 Set PIN Lock</h3>
    <p id="pinSetupDesc">Enter a 4-digit PIN</p>
    <div class="pin-inputs">
      <input type="password" inputmode="numeric" maxlength="1" id="px0" onkeydown="_pinNav(event,'px',0,_pinSetupNext)">
      <input type="password" inputmode="numeric" maxlength="1" id="px1" onkeydown="_pinNav(event,'px',1,_pinSetupNext)">
      <input type="password" inputmode="numeric" maxlength="1" id="px2" onkeydown="_pinNav(event,'px',2,_pinSetupNext)">
      <input type="password" inputmode="numeric" maxlength="1" id="px3" onkeydown="_pinNav(event,'px',3,_pinSetupNext)">
    </div>
    <div class="pin-error" id="pinSetupError"></div>
    <div class="pin-actions">
      <button class="pin-btn pin-btn-cancel" onclick="closePinSetup()">Cancel</button>
      <button class="pin-btn pin-btn-danger" id="pinRemoveBtn" onclick="removePin()" style="display:none">Remove PIN</button>
    </div>
  </div>
</div>

  <!-- AI Report Modal -->
  <div class="modal-overlay" id="reportModal" onclick="if(event.target===this)closeReportModal()">
    <div class="modal-card">
      <div class="modal-header">
        <h2>&#9888; Report AI Content</h2>
        <button class="modal-close" onclick="closeReportModal()">&#x2715;</button>
      </div>
      <div class="modal-body">
        <p class="report-help">Use this form to report inappropriate, unsafe, or clearly incorrect AI-generated content. The app saves a local report with a reference ID and gives you a direct GitHub issue link for the publisher.</p>
        <div class="report-grid">
          <div class="report-field">
            <label>Issue type</label>
            <select id="report_category">
              <option value="inappropriate-output">Inappropriate or unsafe output</option>
              <option value="hallucination">Incorrect or hallucinated content</option>
              <option value="bias">Biased or offensive wording</option>
              <option value="other">Other AI content concern</option>
            </select>
          </div>
          <div class="report-field">
            <label>Model</label>
            <select id="report_model" onchange="toggleReportModelOther()">
              <option value="best">Best Quality (Vertex AI Gemini)</option>
              <option value="fast">Fast (Groq LLaMA)</option>
              <option value="ensemble">All LLMs Together (Ensemble)</option>
              <option value="openai">OpenAI GPT-4o</option>
              <option value="claude">Claude Haiku</option>
              <option value="grok">Grok (xAI)</option>
              <option value="gemini">Gemini API</option>
              <option value="openrouter">OpenRouter</option>
              <option value="offline">Offline (EasyOCR + Groq)</option>
              <option value="other">Other (type manually)</option>
            </select>
          </div>
          <div class="report-field" id="report_model_other_row" style="display:none;">
            <label>Other model name</label>
            <input type="text" id="report_model_other" placeholder="Type model name" oninput="updateQuickGithubReportLink()">
          </div>
          <div class="report-field">
            <label>Problematic output snippet</label>
            <textarea id="report_snippet" placeholder="Paste the AI-generated text or result that caused concern."></textarea>
          </div>
          <div class="report-field">
            <label>What went wrong</label>
            <textarea id="report_details" placeholder="Describe what the AI generated and why it should be reviewed."></textarea>
          </div>
          <div class="report-field">
            <label>Contact (optional)</label>
            <input type="text" id="report_contact" placeholder="Email or other contact info">
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <span class="report-status" id="reportStatus"></span>
        <div class="report-actions" id="reportActions">
          <a class="report-action-link" id="reportIssueLink" href="#" target="_blank" rel="noopener">Report in GitHub</a>
        </div>
        <button type="button" class="btn-cancel" onclick="closeReportModal()">Cancel</button>
        <button type="button" class="btn-save" id="reportSubmitBtn" onclick="submitAIReport()">Submit</button>
      </div>
    </div>
  </div>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-card">
    <div class="modal-header">
      <h2>&#9881; API Key Settings</h2>
      <div style="display:flex;align-items:center;gap:4px;">
        <button class="modal-info-btn" onclick="event.stopPropagation(); openHelp()" title="Help &amp; Setup Guide">&#8505; Info</button>
        <button class="modal-lock-btn" id="pinLockBtn" onclick="event.stopPropagation(); openPinSetup()" title="Set PIN lock">🔓</button>
        <button class="modal-close" onclick="closeSettings()">&#x2715;</button>
      </div>
    </div>
    <div class="modal-body">
      <button class="show-all-btn" onclick="showAllSections()">Show All Sections</button>

      <div class="key-section" id="key_section_llm">
        <div class="key-section-title" onclick="toggleSection('llm')">LLM Providers</div>
        <div class="key-row">
          <label>Groq API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_GROQ_API_KEY" placeholder="gsk_...">
            <button class="eye-btn" onclick="toggleEye('GROQ_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>Gemini API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_GEMINI_API_KEY" placeholder="AIza...">
            <button class="eye-btn" onclick="toggleEye('GEMINI_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>Gemini API Key 2</label>
          <div class="key-row-input">
            <input type="password" id="inp_GEMINI_API_KEY_2" placeholder="AIza...">
            <button class="eye-btn" onclick="toggleEye('GEMINI_API_KEY_2')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>OpenAI API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_OPENAI_API_KEY" placeholder="sk-...">
            <button class="eye-btn" onclick="toggleEye('OPENAI_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>Claude (Anthropic) API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_CLAUDE_API_KEY" placeholder="sk-ant-...">
            <button class="eye-btn" onclick="toggleEye('CLAUDE_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>Grok (xAI) API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_GROK_API_KEY" placeholder="xai-...">
            <button class="eye-btn" onclick="toggleEye('GROK_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>OpenRouter API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_OPENROUTER_API_KEY" placeholder="sk-or-...">
            <button class="eye-btn" onclick="toggleEye('OPENROUTER_API_KEY')">&#128065;</button>
          </div>
        </div>
      </div>

      <div class="key-section collapsed" id="key_section_models">
        <div class="key-section-title" onclick="toggleSection('models')">Model Versions</div>
        <div class="key-row">
          <label>Groq Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_GROQ_MODEL" placeholder="llama-3.1-8b-instant">
          </div>
        </div>
        <div class="key-row">
          <label>Gemini Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_GEMINI_MODEL" placeholder="gemini-2.5-flash">
          </div>
        </div>
        <div class="key-row">
          <label>OpenAI Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_OPENAI_MODEL" placeholder="gpt-4o-mini">
          </div>
        </div>
        <div class="key-row">
          <label>Claude Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_CLAUDE_MODEL" placeholder="claude-haiku-4-5">
          </div>
        </div>
        <div class="key-row">
          <label>Grok Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_GROK_MODEL" placeholder="grok-3-mini">
          </div>
        </div>
        <div class="key-row">
          <label>Vertex AI Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_VERTEX_MODEL" placeholder="gemini-2.5-flash">
          </div>
        </div>
        <div class="key-row">
          <label>OpenRouter Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_OPENROUTER_MODEL" placeholder="openrouter/auto">
          </div>
        </div>
      </div>

      <div class="key-section" id="key_section_google">
        <div class="key-section-title" onclick="toggleSection('google')">Google Cloud</div>
        <div class="key-row">
          <label>Cloud Vision / Maps API Key</label>
          <div class="key-row-input">
            <input type="password" id="inp_GOOGLE_API_KEY" placeholder="AIza...">
            <button class="eye-btn" onclick="toggleEye('GOOGLE_API_KEY')">&#128065;</button>
          </div>
        </div>
        <div class="key-row">
          <label>Service Account JSON Path</label>
          <div class="key-row-input">
            <input type="text" id="inp_GOOGLE_APPLICATION_CREDENTIALS" placeholder="service_account.json">
          </div>
        </div>
        <div class="upload-json-row">
          <label>Upload GCP Service Account JSON (OCR)</label>
          <span class="upload-hint">Saves as <b>service_account.json</b> - used for Google Cloud Vision OCR</span>
          <div style="display:flex;gap:8px;align-items:center;">
            <input type="file" id="gcv_json_file" accept=".json" style="font-size:0.8em;flex:1;">
            <button class="upload-json-btn" onclick="uploadJsonFile('gcv_json_file','service_account.json','gcv_upload_status')">Upload</button>
          </div>
          <span class="upload-status" id="gcv_upload_status"></span>
        </div>
        <div class="upload-json-row">
          <label>Upload GCP Service Account JSON (Vertex AI)</label>
          <span class="upload-hint">Saves as <b>service_account.json</b> - requires Vertex AI API enabled + <b>Vertex AI User</b> IAM role in GCP</span>
          <div style="display:flex;gap:8px;align-items:center;">
            <input type="file" id="vertex_json_file" accept=".json" style="font-size:0.8em;flex:1;">
            <button class="upload-json-btn" onclick="uploadJsonFile('vertex_json_file','service_account.json','vertex_upload_status')">Upload</button>
          </div>
          <span class="upload-status" id="vertex_upload_status"></span>
        </div>
      </div>

      <div class="key-section" id="key_section_vertex">
        <div class="key-section-title" onclick="toggleSection('vertex')">Google Vertex AI</div>
        <div class="key-row">
          <label>Project ID</label>
          <div class="key-row-input">
            <input type="text" id="inp_VERTEX_PROJECT" placeholder="my-gcp-project">
          </div>
        </div>
        <div class="key-row">
          <label>Region</label>
          <div class="key-row-input">
            <input type="text" id="inp_VERTEX_LOCATION" placeholder="us-central1">
          </div>
        </div>
      </div>

      <div class="key-section" id="key_section_custom">
        <div class="key-section-title" onclick="toggleSection('custom')">Custom / Extra API Keys</div>
        <div id="custom_key_rows"></div>
        <div class="custom-key-add-row">
          <input type="text" id="custom_key_name" placeholder="ENV_VARIABLE_NAME" style="text-transform:uppercase;">
          <input type="password" id="custom_key_value" placeholder="value">
          <button class="custom-key-add-btn" onclick="addCustomKeyRow()">+ Add</button>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <span class="save-notice" id="saveNotice">&#10003; Saved to .env</span>
      <button class="btn-cancel" onclick="closeSettings()">Cancel</button>
      <button class="btn-save" onclick="saveSettings()">Save Keys</button>
    </div>
  </div>
</div>

<!-- Help Overlay -->
<div class="help-overlay" id="helpOverlay" onclick="if(event.target===this)closeHelp()">
  <div class="help-box">
    <div class="help-box-hdr">
      <h3>&#8505;&nbsp; Help &amp; Setup Guide</h3>
      <button class="help-box-close" onclick="closeHelp()">&#x2715;</button>
    </div>
    <div class="help-box-body">

      <!-- What is this app -->
      <div class="help-sec-title">&#128255; What is CV-Toposheet?</div>
      <p style="font-size:0.84em;color:#4a6070;line-height:1.55;margin-bottom:10px;">Automatically extract &amp; digitize geographic feature names from scanned historical topographic maps.</p>
      <div class="help-what-grid">
        <div class="help-wc"><div class="help-wc-num">1</div><div class="help-wc-title">Upload</div><div class="help-wc-desc">Drop a scanned toposheet (JPG / PNG / TIF)</div></div>
        <div class="help-wc"><div class="help-wc-num">2</div><div class="help-wc-title">OCR</div><div class="help-wc-desc">AI reads all text from every tile of the map</div></div>
        <div class="help-wc"><div class="help-wc-num">3</div><div class="help-wc-title">Clean</div><div class="help-wc-desc">LLM corrects errors &amp; classifies features</div></div>
        <div class="help-wc"><div class="help-wc-num">4</div><div class="help-wc-title">Search</div><div class="help-wc-desc">Query &amp; export the full results database</div></div>
      </div>

      <div class="help-sep"></div>

      <!-- LLM API Key -->
      <div class="help-sec-title">&#128273; Step 1: Add an LLM API Key</div>
      <p style="font-size:0.83em;color:#4a6070;line-height:1.5;margin-bottom:10px;">Required for feature extraction. Paste at least one key in <b>&#9881; Settings</b>.</p>
      <div class="help-card">
        <div class="help-card-title">&#9889; Groq API Key <span class="help-badge-free">FREE &amp; FASTEST</span></div>
        <div class="help-card-desc">No credit card needed. Best for first-time users.</div>
        <div class="help-card-link">&#128279; console.groq.com &rarr; Sign up &rarr; API Keys &rarr; Create</div>
      </div>
      <div class="help-divider">or</div>
      <div class="help-card">
        <div class="help-card-title">&#128171; Gemini API Key <span class="help-badge-free">FREE</span></div>
        <div class="help-card-desc">Google Gemini Flash. Free tier is generous.</div>
        <div class="help-card-link">&#128279; aistudio.google.com/apikey &rarr; Create API key</div>
      </div>
      <div class="help-divider">or</div>
      <div class="help-card">
        <div class="help-card-title">&#128176; Other API Keys <span style="display:inline-block;font-size:0.7em;font-weight:700;border-radius:4px;padding:2px 7px;margin-left:6px;vertical-align:middle;background:#fce7f3;color:#9d174d;">PAID</span></div>
        <div class="help-card-desc">Claude (Anthropic), Grok (xAI), OpenAI, and more. Add via <b>&#9881; Settings</b> after setup.</div>
        <div class="help-card-link">&#128279; anthropic.com &nbsp;|&nbsp; x.ai/api &nbsp;|&nbsp; platform.openai.com</div>
      </div>
      <div class="help-note">&#9888;&#65039; <b>Without an API key</b> the pipeline runs but produces wrong results: blank Feature Types, 0.50 confidence, noise rows.</div>

      <div class="help-sep"></div>

      <!-- GCV JSON -->
      <div class="help-sec-title">&#9729;&#65039; Step 2: Google Cloud Vision JSON</div>
      <p style="font-size:0.83em;color:#4a6070;line-height:1.5;margin-bottom:10px;">Strongly recommended for best accuracy. Enables premium OCR via Google Cloud Vision API. Without it, the app uses EasyOCR (offline, slower, less accurate).</p>
      <div class="help-card">
        <div class="help-card-title">How to get the JSON file</div>
        <div class="help-card-desc">1. Go to <b>console.cloud.google.com</b><br>2. Enable <b>Cloud Vision API</b> (APIs &amp; Services &rarr; Library)<br>3. IAM &amp; Admin &rarr; Service Accounts &rarr; Create &rarr; Keys &rarr; Add Key &rarr; JSON<br>4. File downloads automatically</div>
      </div>
      <div class="help-card" style="margin-top:8px;">
        <div class="help-card-title">&#128196; Upload in Settings</div>
        <div class="help-card-desc">Click <b>&#9881; Settings &rarr; Google Cloud &rarr; Upload GCP Service Account JSON</b> and select the downloaded file.</div>
      </div>
      <div class="help-note" style="margin-top:8px;background:#f0fdf4;border-color:#86efac;color:#166534;">&#11088; For even better results, also enable <b>Vertex AI API</b> in Google Cloud Console (APIs &amp; Services &rarr; Library &rarr; search &ldquo;Vertex AI&rdquo; &rarr; Enable).</div>

      <div class="help-sep"></div>

      <!-- Model Versions -->
      <div class="help-sec-title">&#9881;&#65039; Model Versions (optional)</div>
      <p style="font-size:0.83em;color:#4a6070;line-height:1.5;margin-bottom:10px;">Default model versions work for most users. Only change if you want a specific version.</p>
      <div class="help-card">
        <div class="help-card-title">Default models</div>
        <div class="help-card-desc">Groq: <b>llama-3.1-8b-instant</b> &nbsp;|&nbsp; Gemini: <b>gemini-2.5-flash</b><br>OpenAI: <b>gpt-4o-mini</b> &nbsp;|&nbsp; Claude: <b>claude-haiku-4-5</b><br>Grok: <b>grok-3-mini</b> &nbsp;|&nbsp; OpenRouter: <b>openrouter/auto</b></div>
      </div>
      <div class="help-note">&#128161; To use a different model, open <b>&#9881; Settings &rarr; Model Versions</b>, type the model name and save.</div>

      <div class="help-sep"></div>

      <!-- AI reporting -->
      <div class="help-sec-title">&#9888;&#65039; Report AI-Generated Content</div>
      <p style="font-size:0.83em;color:#4a6070;line-height:1.5;margin-bottom:10px;">If the AI generates inappropriate, unsafe, or clearly incorrect content, click <b>&#9888; Report AI Content</b> in the top bar. The app stores a report reference and opens a publisher issue link so the content can be reviewed.</p>

      <div class="help-sep"></div>

      <!-- PIN Lock -->
      <div class="help-sec-title">&#128274; PIN Lock (optional)</div>
      <p style="font-size:0.83em;color:#4a6070;line-height:1.5;margin-bottom:10px;">Protect your API keys with a 4-digit PIN so no one else can open Settings.</p>
      <div class="help-card">
        <div class="help-card-title">Set a PIN</div>
        <div class="help-card-desc">Open <b>&#9881; Settings</b> &rarr; click the <b>&#128275; lock icon</b> (top right of Settings) &rarr; enter a 4-digit PIN &rarr; confirm.</div>
      </div>
      <div class="help-card" style="margin-top:8px;">
        <div class="help-card-title">Remove a PIN</div>
        <div class="help-card-desc">Open <b>&#9881; Settings</b> &rarr; enter your current PIN &rarr; click <b>Remove PIN</b>.</div>
      </div>

      <div class="help-sep"></div>

      <!-- How to process -->
      <div class="help-sec-title">&#128640; How to Process Your First Map</div>
      <div class="help-ready-row"><div class="hri">&#128444;&#65039;</div><div class="hrt"><b>Upload:</b> Drag &amp; drop a JPG / PNG / TIF toposheet onto the home screen (up to 500 MB)</div></div>
      <div class="help-ready-row"><div class="hri">&#127917;</div><div class="hrt"><b>Choose Model:</b> Select a processing model from the right panel (Best Quality recommended)</div></div>
      <div class="help-ready-row"><div class="hri">&#9654;&#65039;</div><div class="hrt"><b>Process:</b> Click <b>Process Map</b> and watch live progress (5&ndash;30 min depending on map size)</div></div>
      <div class="help-ready-row"><div class="hri">&#128202;</div><div class="hrt"><b>Search &amp; Export:</b> Find results in <b>Map Database</b>. Export to CSV or Excel anytime.</div></div>

    </div>
  </div>
</div>

</body>
</html>"""


def _process_html(filename: str, model: str = 'best', session_id: str = '') -> str:
    import html as _html
    enc = url_quote(filename, safe='')
    model_enc = url_quote(model, safe='')
    session_enc = url_quote(session_id, safe='')
    safe_filename = _html.escape(filename)

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>Processing &ndash; {safe_filename}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ height:100vh; overflow:hidden; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; display:flex; flex-direction:column; }}
.hdr {{ display:flex;align-items:center;justify-content:space-between;background:#0E7490;padding:14px 28px;flex-shrink:0; }}
.hdr-logo {{ font-size:1.25em; font-weight:700; color:#fff; letter-spacing:-.01em; }}
.hdr-logo span {{ color:#fff; }}
.hdr-sub {{ font-size:0.78em; color:rgba(255,255,255,.75); margin-top:2px; }}
.page {{ flex:1; min-height:0; overflow-y:auto; display:flex; justify-content:center; align-items:stretch; padding:10px 16px; }}
.card {{ background:#fff; border-radius:16px; box-shadow:0 2px 24px rgba(14,116,144,.12); padding:22px 28px; max-width:680px; width:100%; display:flex; flex-direction:column; }}
.status-row {{ display:flex; align-items:center; gap:14px; margin-bottom:14px; }}
.spinner {{ width:28px; height:28px; border:3px solid #e0e0e0; border-top-color:#0E7490; border-radius:50%; animation:spin .8s linear infinite; flex-shrink:0; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
.status-text {{ font-size:1.1em; font-weight:600; color:#333; }}
.filename-tag {{ background:#e8f5f9; color:#0E7490; padding:4px 11px; border-radius:5px; font-size:0.88em; font-weight:600; margin-left:8px; word-break:break-all; }}
.phases {{ display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }}
.phase {{ padding:5px 14px; border-radius:13px; font-size:0.8em; font-weight:600; background:#f0f0f0; color:#999; transition:all .3s; }}
.phase.active {{ background:#0E7490; color:white; }}
.phase.done   {{ background:#27ae60; color:white; }}
.log {{ background:#1a1a2e; color:#a8d8a8; border-radius:10px; padding:14px 16px; font-family:'Consolas','Courier New',monospace; font-size:0.82em; min-height:80px; flex:1; overflow-y:auto; line-height:1.55; margin-bottom:16px; white-space:pre-wrap; word-break:break-all; }}
.line-phase {{ color:#67d8f0; font-weight:bold; }}
.line-done  {{ color:#67f0a0; font-weight:bold; }}
.line-warn  {{ color:#f0c040; }}
.line-err   {{ color:#f06060; }}
.btn-row {{ display:flex; gap:12px; }}
.view-btn {{ flex:1; padding:13px; text-align:center; background:#22c55e; color:white; border:none; border-radius:8px; font-size:1em; font-weight:700; cursor:pointer; text-decoration:none; display:inline-block; transition:background .2s; opacity:0.45; pointer-events:none; }}
.view-btn.ready       {{ opacity:1; pointer-events:auto; }}
.view-btn.ready:hover {{ background:#16a34a; }}
.back-btn {{ flex:1; padding:13px; text-align:center; background:#f0f0f0; color:#555; border:none; border-radius:8px; font-size:1em; cursor:pointer; text-decoration:none; display:inline-block; }}
.back-btn:hover {{ background:#e0e0e0; }}
.kill-btn {{ background:#dc2626; border:none; color:#fff; border-radius:7px; padding:7px 16px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:flex; align-items:center; gap:6px; }}
.kill-btn:hover {{ background:#b91c1c; }}
.kill-btn:disabled {{ background:#999; cursor:not-allowed; }}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-logo">CV-<span>Toposheet</span></div>
    <div class="hdr-sub">Extracted Map Features</div>
  </div>
  <button class="kill-btn" id="killBtn" onclick="killProcess()">&#9632;&nbsp; Cancel Job</button>
</div>

<div class="page">
  <div class="card">
    <div class="status-row">
      <div class="spinner" id="spinner"></div>
      <div class="status-text">
        Processing <span class="filename-tag">{safe_filename}</span>
      </div>
    </div>

    <div class="phases">
      <div class="phase" id="p1">1 &middot; Tiling</div>
      <div class="phase" id="p2">2 &middot; OCR</div>
      <div class="phase" id="p3">3 &middot; Grid</div>
      <div class="phase" id="p4">4 &middot; LLM</div>
      <div class="phase" id="p5">5 &middot; Database</div>
      <div class="phase" id="p6">Export</div>
    </div>

    <div class="log" id="logBox"></div>

    <div class="btn-row">
      <a href="/results/{session_enc}" class="view-btn" id="viewBtn">&#128202;&nbsp; View Results</a>
      <a href="/" class="back-btn">&#8592;&nbsp; Upload Another Map</a>
    </div>
  </div>
</div>

<script>
const logBox  = document.getElementById('logBox');
const viewBtn = document.getElementById('viewBtn');
const spinner = document.getElementById('spinner');
const killBtn = document.getElementById('killBtn');
let currentPhase = null;

function killProcess() {{
  killBtn.disabled = true;
  killBtn.textContent = '⏳ Cancelling…';
  killBtn.style.background = '#b91c1c';
  fetch('/kill', {{method:'POST'}}).finally(() => {{
    window.location.href = '/';
  }});
}}

function setPhase(id) {{
  if (currentPhase === id) return;
  if (currentPhase) document.getElementById(currentPhase).className = 'phase done';
  document.getElementById(id).className = 'phase active';
  currentPhase = id;
}}

function appendLine(msg, cls) {{
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = msg + '\\n';
  logBox.appendChild(span);
  logBox.scrollTop = logBox.scrollHeight;
}}

const es = new EventSource('/stream/{enc}?model={model_enc}&session={session_enc}');

es.onmessage = function(e) {{
  const data = JSON.parse(e.data);
  const msg  = data.msg || '';

  if      (/Phase 1|Tiling/i.test(msg))   {{ setPhase('p1'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 2|OCR/i.test(msg))      {{ setPhase('p2'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 3|Grid/i.test(msg))     {{ setPhase('p3'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 4|LLM/i.test(msg))      {{ setPhase('p4'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 5|Database/i.test(msg)) {{ setPhase('p5'); appendLine(msg, 'line-phase'); }}
  else if (/[Ee]xport/i.test(msg))        {{ setPhase('p6'); appendLine(msg, 'line-phase'); }}
  else if (/ERROR|Error/i.test(msg))      {{ appendLine(msg, 'line-err');  }}
  else if (/warn/i.test(msg))             {{ appendLine(msg, 'line-warn'); }}
  else if (msg.startsWith('\\u2705'))      {{ appendLine(msg, 'line-done'); }}
  else                                    {{ appendLine(msg, '');          }}

  if (data.done) {{
    spinner.style.display = 'none';
    killBtn.style.display = 'none';
    viewBtn.classList.add('ready');
    if (data.redirect) viewBtn.href = data.redirect;
    es.close();
  }}
  if (data.error) {{
    spinner.style.display = 'none';
    killBtn.style.display = 'none';
    appendLine('Processing stopped. See log above for details.', 'line-err');
    es.close();
  }}
}};

// CRITICAL: close on connection drop — without this, EventSource auto-reconnects
// every 3s and re-spawns the pipeline subprocess in an infinite loop.
var _disconnected = false;
es.onerror = function() {{
  if (_disconnected) return;
  _disconnected = true;
  es.close();
  spinner.style.display = 'none';
  killBtn.style.display = 'none';
  appendLine('[Stream disconnected — OCR/LLM processing continues in the background.]', 'line-warn');
  appendLine('[Polling for completion... View Results will activate automatically when done.]', 'line-warn');
  // Poll results endpoint every 20 s; enable View Results once HTML is available
  var _poll = setInterval(function() {{
    fetch('/results/{session_enc}', {{method:'HEAD'}})
      .then(function(r) {{
        if (r.ok) {{
          clearInterval(_poll);
          viewBtn.classList.add('ready');
          appendLine('✅ Processing complete! Click View Results.', 'line-done');
        }}
      }})
      .catch(function() {{}});
  }}, 20000);
}};
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import logging
    # Kill any leftover processes on port 5000 before binding
    if os.name == 'nt':
        try:
            result = subprocess.check_output(
                'netstat -ano | findstr "127.0.0.1:5000" | findstr LISTENING',
                shell=True, stderr=subprocess.DEVNULL
            ).decode()
            for line in result.strip().splitlines():
                parts = line.split()
                pid = parts[-1] if parts else None
                if pid and pid.isdigit() and int(pid) != os.getpid():
                    subprocess.call(['taskkill', '/F', '/PID', pid],
                                    shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print()
    print('=' * 52)
    print('  CV-Toposheet Map Digitization Interface')
    print('  Open your browser: http://localhost:5000')
    print('=' * 52)
    print()
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True)
