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
from urllib.parse import quote as url_quote

from flask import Flask, request, redirect, url_for, send_file, Response, stream_with_context

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / '.env'

RESULTS_DIR = BASE_DIR / 'results'
MAPS_DIR = BASE_DIR / 'maps'

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
    'offline':  {'LLM_PROVIDER': 'groq',    'OCR_ENGINE': 'easyocr', 'OCR_WORKERS': '1'},
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
    return sum(1 for _ in MAPS_DIR.rglob('*') if _.suffix.lower() in ALLOWED_EXT)


def _merge_session_to_global(session_id: str) -> None:
    """Copy newly processed map(s) from a session DB into the global DB,
    then regenerate the global table_export.html so Map Database stays current."""
    session_db = BASE_DIR / 'results' / session_id / 'toposheet.db'
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
            subprocess.run(
                [sys.executable, 'export_table.py'],
                cwd=str(BASE_DIR), env=genv,
                timeout=120, capture_output=True
            )
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
        entry = f'{k}={v}\n'
        if k in existing_keys:
            lines[existing_keys[k]] = entry
        else:
            lines.append(entry)
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    try:
        feat_count = _feature_count()
    except Exception:
        feat_count = 0
    try:
        map_count = _map_count()
    except Exception:
        map_count = 0
    return _landing_html(feat_count, map_count)


@app.route('/save_env', methods=['POST'])
def save_env():
    payload = request.get_json(force=True, silent=True) or {}
    for k, v in payload.items():
        if v:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]
    try:
        _write_env({k: v for k, v in payload.items() if v})
    except Exception:
        pass
    try:
        import config
        importlib.reload(config)
    except Exception:
        pass
    return json.dumps({'ok': True})


@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('map_file')
    model = request.form.get('model', 'best')

    if not files or not files[0].filename:
        return redirect('/')

    session_id = uuid.uuid4().hex[:12]
    session_maps = BASE_DIR / 'maps' / session_id
    session_maps.mkdir(parents=True, exist_ok=True)

    saved_names = []
    for f in files:
        if f.filename and Path(f.filename).suffix.lower() in ALLOWED_EXT:
            dest = session_maps / f.filename
            f.save(str(dest))
            saved_names.append(f.filename)

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

    if not _process_lock.acquire(blocking=False):
        yield f'data: {json.dumps({"msg": "Another map is already being processed. Please wait and try again."})}\n\n'
        yield f'data: {json.dumps({"error": True})}\n\n'
        return

    try:
        env = os.environ.copy()
        env.update(env_overrides)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'

        session_maps_dir    = str(BASE_DIR / 'maps'    / session_id)
        session_results_dir = str(BASE_DIR / 'results' / session_id)
        session_logs_dir    = str(BASE_DIR / 'logs'    / session_id)
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

        yield f'data: {json.dumps({"done": True, "redirect": results_url, "msg": "Done! Click View Results."})}\n\n'

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
    keys = ['GROQ_API_KEY', 'GEMINI_API_KEY', 'GEMINI_API_KEY_2', 'OPENAI_API_KEY',
            'CLAUDE_API_KEY', 'GROK_API_KEY', 'GOOGLE_API_KEY',
            'GOOGLE_APPLICATION_CREDENTIALS', 'VERTEX_PROJECT', 'VERTEX_LOCATION', 'VERTEX_MODEL']
    return json.dumps({k: os.environ.get(k, '') for k in keys})


@app.route('/results', methods=['GET', 'HEAD'])
@app.route('/results/<session_id>', methods=['GET', 'HEAD'])
def results(session_id=None):
    if session_id:
        out = BASE_DIR / 'results' / session_id / 'table_export.html'
        res_dir = str(BASE_DIR / 'results' / session_id)
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
        subprocess.run([sys.executable, 'export_table.py'],
                       cwd=str(BASE_DIR), env=env, timeout=60, capture_output=True)
    except Exception:
        pass

    resp = app.response_class(
        json.dumps({'ok': True, 'deleted_rows': deleted, 'maps': len(maps_to_delete)}),
        mimetype='application/json'
    )
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


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
body { height:100vh; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; display:flex; flex-direction:column; overflow:hidden; }
.hdr { display:flex;align-items:center;justify-content:space-between;background:#0E7490;padding:14px 28px;flex-shrink:0; }
.hdr-logo { font-size:1.25em; font-weight:700; color:#fff; letter-spacing:-.01em; }
.hdr-logo span { color:#fff; }
.hdr-sub  { font-size:0.78em; color:rgba(255,255,255,.75); margin-top:2px; }
.hdr-actions { display:flex; align-items:center; gap:10px; }
.settings-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:0 16px; height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; }
.settings-btn:hover { background:rgba(255,255,255,.25); }
#uploadForm { flex:1; display:flex; flex-direction:column; min-height:0; }
.body { flex:1; display:flex; flex-direction:row; gap:16px; align-items:stretch; justify-content:stretch; padding:10px 8px; min-height:0; }
.left { flex:1; min-width:280px; display:flex; flex-direction:column; }
.right { width:440px; min-width:380px; max-width:520px; display:flex; flex-direction:column; padding:8px 12px; gap:0; background:#fff; border:2px solid #93c5d4; border-radius:14px; box-shadow:0 2px 16px rgba(14,116,144,.08); }
.right-title { font-size:0.55em; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#0E7490; margin-bottom:4px; padding-bottom:3px; border-bottom:1.5px solid #e0f2f7; flex-shrink:0; }
.model-label { font-size:0.63em; font-weight:600; color:#6b8a99; text-transform:uppercase; letter-spacing:.08em; margin-bottom:4px; flex-shrink:0; }
.model-cards { display:flex; flex-direction:column; gap:1px; margin-bottom:4px; flex:1; overflow-y:auto; min-height:0; padding-right:3px; scrollbar-width:thin; scrollbar-color:#93c5d4 #f0f4f8; }
.model-cards::-webkit-scrollbar { width:6px; }
.model-cards::-webkit-scrollbar-track { background:#f0f4f8; border-radius:3px; }
.model-cards::-webkit-scrollbar-thumb { background:#93c5d4; border-radius:3px; }
.model-cards::-webkit-scrollbar-thumb:hover { background:#0e7490; }
.model-card { border:1.5px solid #ccdde3; border-radius:6px; padding:3px 7px; cursor:pointer; transition:all .2s; display:flex; align-items:center; gap:6px; background:#fff; box-shadow:0 1px 2px rgba(14,116,144,.05); flex-shrink:0; }
.model-card:hover { border-color:#0e7490; background:#f0f9fb; box-shadow:0 2px 10px rgba(14,116,144,.15); }
.model-card input[type=radio] { accent-color:#0e7490; margin:0; flex-shrink:0; width:13px; height:13px; }
.mc-body { flex:1; }
.mc-body .mc-name { font-size:0.68em; font-weight:700; color:#1e3a4a; display:flex; align-items:center; gap:4px; flex-wrap:wrap; }
.mc-body .mc-desc { font-size:0.55em; color:#6b8a99; margin-top:0px; line-height:1.2; }
.badge { display:inline-block; border-radius:3px; padding:0px 4px; font-size:0.58em; font-weight:700; }
.badge-best    { background:#d0f0f7; color:#0e7490; }
.badge-fast    { background:#fef9c3; color:#a16207; }
.badge-offline { background:#ede9fe; color:#6d28d9; }
.model-card.selected { border-color:#0e7490; border-width:1.5px; background:#e8f5f9; box-shadow:0 0 0 2px rgba(14,116,144,.10); }
.divider { flex-shrink:0; min-height:4px; max-height:8px; }
.info-strip { background:#f0f9fb; border-radius:5px; border:1px solid #b8dde8; padding:3px 7px; margin-top:4px; flex-shrink:0; font-size:0.54em; color:#4a7080; line-height:1.3; }
.info-strip b { color:#0E7490; }
.process-btn { width:100%; padding:7px; background:linear-gradient(135deg,#0e7490,#0891b2); color:white; border:none; border-radius:7px; font-size:0.8em; font-weight:700; cursor:pointer; transition:all .2s; box-shadow:0 2px 8px rgba(14,116,144,.3); letter-spacing:.01em; flex-shrink:0; }
.process-btn:hover { background:linear-gradient(135deg,#0891b2,#06b6d4); box-shadow:0 5px 22px rgba(14,116,144,.55); transform:translateY(-1px); }
.process-btn:disabled { background:#dde8ec; color:#7a9daa; cursor:not-allowed; box-shadow:none; transform:none; }
.process-btn .btn-sub { display:block; font-size:0.68em; font-weight:400; color:rgba(255,255,255,.70); margin-top:2px; }
.drop-zone { position:relative; border:2px dashed #93c5d4; border-radius:14px; background:#fff; flex:1; min-height:180px; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:24px 16px 16px 16px; transition:border .18s,background .18s; cursor:pointer; overflow:visible; }
.drop-zone.drag-over { border-color:#0e7490; background:#f6fdff; box-shadow:0 4px 32px rgba(14,116,144,.18); }
.dz-icon-row { display:flex; align-items:center; justify-content:center; gap:28px; margin-bottom:10px; }
.dz-icon-side { font-size:6em; line-height:1; }
.dz-icon { font-size:6.5em; line-height:1; }
.dz-icon-world { font-size:6.5em; line-height:1; filter:grayscale(1) brightness(0.6); -webkit-filter:grayscale(1) brightness(0.6); opacity:0.55; }
.dz-title { font-size:1.05em; font-weight:700; color:#1e3a4a; margin-top:4px; text-align:center; }
.dz-sub   { font-size:0.75em; color:#6b8a99; text-align:center; }
.dz-badge { background:#e0f2f7; border:1px solid #93c5d4; border-radius:20px; padding:5px 16px; font-size:0.75em; color:#0e7490; letter-spacing:.06em; margin-top:6px; display:inline-block; white-space:nowrap; }
#preview { display:flex; gap:12px; flex-wrap:wrap; justify-content:center; max-width:580px; margin-top:10px; padding:4px 8px 4px 8px; position:relative; z-index:10; pointer-events:auto; }
.prev-thumb { width:72px; height:72px; object-fit:cover; border-radius:6px; border:2px solid #93c5d4; box-shadow:0 2px 8px rgba(0,0,0,.15); display:block; }
.prev-name { font-size:0.68em; color:#0e7490; text-align:center; max-width:72px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.prev-item { display:flex; flex-direction:column; align-items:center; gap:3px; position:relative; cursor:default; }
.prev-remove { position:absolute; top:3px; right:3px; width:22px; height:22px; border-radius:50%; background:rgba(220,38,38,0.92); color:#fff; border:2px solid #fff; font-size:13px; line-height:1; display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:0 1px 5px rgba(0,0,0,.4); z-index:20; font-weight:900; opacity:0; transition:opacity .15s; pointer-events:auto; }
.prev-item:hover .prev-remove { opacity:1; }
.drop-zone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; z-index:5; }
@media (max-width:1100px) { .body { flex-direction:column; align-items:center; gap:18px; padding:18px 8px; } .left, .right { width:100%; max-width:700px; min-width:0; } }
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
.badge-premium { background:#fef3c7; color:#92400e; }
.badge-optional { background:#f0fdf4; color:#166534; }
.badge-claude  { background:#fae8ff; color:#86198f; }
.badge-grok    { background:#fff7ed; color:#c2410c; }
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
.pin-confirm-label { font-size:0.82em; color:#555; margin:12px 0 6px; }
.db-banner-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:0 16px; height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; text-decoration:none; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; }
.db-banner-btn:hover { background:rgba(255,255,255,.25); }
.kill-banner-btn { background:#dc2626; border:1px solid #dc2626; color:#fff; border-radius:7px; padding:0 16px; height:36px; font-size:0.85em; font-weight:700; cursor:pointer; transition:background .2s; display:inline-flex; align-items:center; gap:5px; box-sizing:border-box; }
.kill-banner-btn:hover { background:#b91c1c; }
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
    <a href="/results" class="db-banner-btn" title="View all processed maps in the database" target="_blank">&#128202;&nbsp; Map Database</a>
    <button class="kill-banner-btn" id="killBannerBtn" onclick="killRunningJob()" title="Kill any running job and restart the server">&#9632; Restart Server</button>
    <button class="settings-btn" onclick="openSettings()">&#9881;&#xFE0E; Settings</button>
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
          <div class="mc-desc">GCV OCR (parallel) + Vertex AI Gemini 2.5 Flash<br>Highest accuracy &middot; 150 items/batch &middot; GCP service account</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="fast">
        <input type="radio" name="model_sel" value="fast">
        <div class="mc-body">
          <div class="mc-name">2. Fast <span class="badge badge-fast">QUICK</span></div>
          <div class="mc-desc">GCV OCR (parallel) + Groq LLaMA 3.1 8B<br>Fastest end-to-end &middot; 60 items/batch &middot; free Groq tier</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="ensemble">
        <input type="radio" name="model_sel" value="ensemble">
        <div class="mc-body">
          <div class="mc-name">3. All LLMs Together <span class="badge badge-premium">PREMIUM</span></div>
          <div class="mc-desc">GCV OCR + all configured LLMs run in parallel<br>Majority vote consensus &middot; highest confidence</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="openai">
        <input type="radio" name="model_sel" value="openai">
        <div class="mc-body">
          <div class="mc-name">4. OpenAI GPT-4o <span class="badge badge-optional">OPTIONAL</span></div>
          <div class="mc-desc">GCV OCR + GPT-4o-mini<br>Reliable, paid OpenAI API</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="claude">
        <input type="radio" name="model_sel" value="claude">
        <div class="mc-body">
          <div class="mc-name">5. Claude Haiku <span class="badge badge-claude">CLAUDE</span></div>
          <div class="mc-desc">GCV OCR + Claude 3.5 Haiku<br>Anthropic, Claude API</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="grok">
        <input type="radio" name="model_sel" value="grok">
        <div class="mc-body">
          <div class="mc-name">6. Grok (xAI) <span class="badge badge-grok">GROK</span></div>
          <div class="mc-desc">GCV OCR + Grok-3-mini<br>Fast inference, xAI API</div>
        </div>
      </label>
      <label class="model-card" onclick="selectCard(this)" data-model="offline">
        <input type="radio" name="model_sel" value="offline">
        <div class="mc-body">
          <div class="mc-name">7. Offline OCR <span class="badge badge-offline">NO GOOGLE</span></div>
          <div class="mc-desc">EasyOCR + Groq LLaMA 3.1<br>No Google Cloud needed</div>
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
  offline:  '<b>Offline OCR:</b> EasyOCR (local, no Google Cloud) + Groq LLaMA. Sequential OCR tiles – slower but no GCP needed.',
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
  fileInput.files = e.dataTransfer.files;
  showPreviews();
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
  _fileList = Array.from(fileInput.files);
  _renderPreviews();
}

// Settings modal
const SECTION_IDS = ['llm','google','vertex'];

// ── PIN Lock ──────────────────────────────────────────────────────────────
const PIN_KEY = 'cvt_settings_pin';
function _getPin()   { return localStorage.getItem(PIN_KEY) || ''; }
function _savePin(p) { localStorage.setItem(PIN_KEY, p); }
function _clearPin() { localStorage.removeItem(PIN_KEY); }

function _loadSettingsData() {
  fetch('/get_env').then(r => r.json()).then(data => {
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

function saveSettings() {
  const payload = {};
  document.querySelectorAll('.key-row-input input').forEach(inp => {
    const key = inp.id.replace('inp_', '');
    payload[key] = inp.value.trim();
  });
  fetch('/save_env', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(() => {
    const notice = document.getElementById('saveNotice');
    notice.style.display = 'flex';
    setTimeout(() => notice.style.display = 'none', 2500);
  });
}

function toggleEye(key) {
  const inp = document.getElementById('inp_' + key);
  inp.type = inp.type === 'password' ? 'text' : 'password';
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
  btn.title = has ? 'PIN lock ON — click to change/remove' : 'No PIN — click to set lock';
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

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-card">
    <div class="modal-header">
      <h2>&#9881; API Key Settings</h2>
      <div style="display:flex;align-items:center;gap:4px;">
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
            <input type="text" id="inp_GOOGLE_APPLICATION_CREDENTIALS" placeholder="C:/path/to/service_account.json">
          </div>
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
        <div class="key-row">
          <label>Model</label>
          <div class="key-row-input">
            <input type="text" id="inp_VERTEX_MODEL" placeholder="gemini-1.5-pro">
          </div>
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

</body>
</html>"""


def _process_html(filename: str, model: str = 'best', session_id: str = '') -> str:
    enc = url_quote(filename, safe='')
    model_enc = url_quote(model, safe='')
    session_enc = url_quote(session_id, safe='')

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>Processing &ndash; {filename}</title>
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
        Processing <span class="filename-tag">{filename}</span>
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
                    subprocess.call(f'taskkill /F /PID {pid}',
                                    shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
