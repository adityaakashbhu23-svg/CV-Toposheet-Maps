# app.py  –  CV-Toposheet Web Interface
#
# A simple Flask cover page: upload a map → pipeline runs → view results.
#
# Usage:
#   python app.py                OR   double-click  Start App.bat
#   Then open:  http://localhost:5000

import os
import re
import sys
import json
import sqlite3
import subprocess
import threading
import importlib
import uuid
from pathlib import Path
from urllib.parse import quote as url_quote

from flask import (Flask, request, redirect, url_for,
                   send_file, Response, stream_with_context)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
MAPS_DIR    = BASE_DIR / 'maps'
RESULTS_DIR = BASE_DIR / 'results'
HTML_OUT    = RESULTS_DIR / 'table_export.html'

ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

# Prevent two pipelines running at the same time
_process_lock = threading.Lock()

ENV_FILE = BASE_DIR / '.env'

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024   # 500 MB


# ── Helpers ───────────────────────────────────────────────────────────────────
def _feature_count() -> int:
    db = RESULTS_DIR / 'toposheet.db'
    if not db.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db))
        n = conn.execute('SELECT COUNT(*) FROM features').fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def _map_count() -> int:
    if not MAPS_DIR.exists():
        return 0
    return len([f for f in MAPS_DIR.iterdir()
                if f.suffix.lower() in ALLOWED_EXT])


def _read_env() -> dict:
    """Read key=value pairs from .env file."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _write_env(data: dict):
    """Write dict back to .env, preserving comments."""
    existing = ENV_FILE.read_text(encoding='utf-8') if ENV_FILE.exists() else ''
    lines = existing.splitlines()
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            k = stripped.split('=', 1)[0].strip()
            if k in data:
                new_lines.append(f'{k}={data[k]}')
                updated_keys.add(k)
                continue
        new_lines.append(line)
    # append keys not already present
    for k, v in data.items():
        if k not in updated_keys:
            new_lines.append(f'{k}={v}')
    ENV_FILE.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')


# ── Routes ────────────────────────────────────────────────────────────────────
_SETTINGS_KEYS = [
    'GROQ_API_KEY', 'GEMINI_API_KEY', 'GEMINI_API_KEY_2',
    'OPENAI_API_KEY', 'CLAUDE_API_KEY', 'GROK_API_KEY',
    'VERTEX_PROJECT', 'VERTEX_LOCATION', 'VERTEX_MODEL',
    'GOOGLE_APPLICATION_CREDENTIALS',
    'LLM_PROVIDER', 'OCR_ENGINE', 'MAP_COUNTRY',
]


@app.route('/settings', methods=['GET'])
def settings_get():
    env = _read_env()
    data = {k: env.get(k, '') for k in _SETTINGS_KEYS}
    return json.dumps(data)


@app.route('/settings', methods=['POST'])
def settings_post():
    data = request.get_json(force=True) or {}
    clean = {k: v for k, v in data.items() if k in _SETTINGS_KEYS}
    _write_env(clean)
    # Apply immediately to running process — no restart needed
    for k, v in clean.items():
        if v:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]
    try:
        import config
        importlib.reload(config)
    except Exception:
        pass
    return json.dumps({'ok': True})


@app.route('/')
def index():
    return _landing_html(_feature_count(), _map_count())


@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('map_file')
    model = request.form.get('model', 'best')

    if not files or not files[0].filename:
        return redirect('/')

    # Each upload gets its own session ID so users never see each other's results
    session_id = uuid.uuid4().hex[:12]
    session_maps = BASE_DIR / 'maps' / session_id
    session_maps.mkdir(parents=True, exist_ok=True)

    saved = []
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        safe_name = re.sub(r'[^\w\-. ]', '_', Path(file.filename).name)
        file.save(str(session_maps / safe_name))
        saved.append(safe_name)

    if not saved:
        return ('<h2 style="font-family:sans-serif;padding:40px;color:#c0392b">'
                'Error: Only image files (JPG, PNG, TIF) are accepted.</h2>'
                '<p style="font-family:sans-serif;padding:0 40px">'
                '<a href="/">&#8592; Go back</a></p>'), 400

    label = ', '.join(saved[:3]) + (f' +{len(saved)-3} more' if len(saved) > 3 else '')
    return redirect(url_for('process_page', filename=label, model=model, session=session_id))


@app.route('/process/<path:filename>')
def process_page(filename):
    model      = request.args.get('model', 'best')
    session_id = request.args.get('session', '')
    return _process_html(filename, model, session_id)


@app.route('/stream/<path:filename>')
def stream(filename):
    """Server-Sent Events: run pipeline then export, stream every log line."""
    model         = request.args.get('model', 'best')
    session_id    = request.args.get('session', '')
    env_overrides = _MODEL_ENVS.get(model, _MODEL_ENVS['best'])

    def generate():
        if not _process_lock.acquire(blocking=False):
            yield f'data: {json.dumps({"msg": "⚠ Another map is already being processed. Please wait and try again."})}\n\n'
            yield f'data: {json.dumps({"error": True})}\n\n'
            return

        try:
            env = os.environ.copy()
            env.update(env_overrides)

            # Per-session isolation: each user gets own maps + results + logs folder
            if session_id:
                session_maps_dir    = str(BASE_DIR / 'maps'    / session_id)
                session_results_dir = str(BASE_DIR / 'results' / session_id)
                session_logs_dir    = str(BASE_DIR / 'logs'    / session_id)
                Path(session_maps_dir).mkdir(parents=True, exist_ok=True)
                Path(session_results_dir).mkdir(parents=True, exist_ok=True)
                Path(session_logs_dir).mkdir(parents=True, exist_ok=True)
                # config.py reads these env vars for MAPS_FOLDER, RESULTS_FOLDER, LOGS_FOLDER
                env['MAPS_FOLDER']    = session_maps_dir
                env['RESULTS_FOLDER'] = session_results_dir
                env['LOGS_FOLDER']    = session_logs_dir
                results_url = f'/results/{session_id}'
            else:
                results_url = '/results'

            for label, script in [('pipeline', 'process_new_maps.py'),
                                   ('export',   'export_table.py')]:
                if label == 'export':
                    yield f'data: {json.dumps({"msg": "▶ Exporting results table..."})}\n\n'

                proc = subprocess.Popen(
                    [sys.executable, script],
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                for line in iter(proc.stdout.readline, ''):
                    line = line.rstrip()
                    if line:
                        yield f'data: {json.dumps({"msg": line})}\n\n'
                proc.wait()

            yield f'data: {json.dumps({"done": True, "redirect": results_url, "msg": "✅ Done! Click View Results."})}\n\n'

        finally:
            _process_lock.release()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/results')
@app.route('/results/<session_id>')
def results(session_id=None):
    if session_id:
        out = BASE_DIR / 'results' / session_id / 'table_export.html'
    else:
        out = HTML_OUT
    if out.exists():
        return send_file(str(out))
    return ('<h2 style="font-family:sans-serif;padding:40px;color:#888">'
            'No results yet – please upload and process a map first.'
            ' <a href="/">&#8592; Go back</a></h2>'), 404


# ── Landing page HTML ─────────────────────────────────────────────────────────
def _landing_html(feat_count: int, map_count: int) -> str:

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CV-Toposheet – Map Digitization</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
html {{ height:100vh; overflow:hidden; }}
body {{ height:100vh; overflow:hidden;
        font-family:'Segoe UI', system-ui, Arial, sans-serif;
        background:#f0f4f8; display:flex; flex-direction:column; }}

/* ── Header ─────────────────────────────────────────────── */
.hdr {{
  background:#0E7490;
  border-bottom:none;
  padding:14px 28px; display:flex; align-items:center;
  flex-shrink:0; position:relative;
}}
.hdr-logo {{ font-size:1.25em; font-weight:700; color:#fff;
              letter-spacing:-.01em; }}
.hdr-logo span {{ color:#fff; }}
.hdr-sub  {{ font-size:0.78em; color:rgba(255,255,255,.75);
              margin-top:2px; }}
.hdr-right {{ position:absolute; right:20px; top:50%; transform:translateY(-50%); display:flex; align-items:center; gap:10px; }}
.settings-btn {{
  background:#ffffff !important; border:none;
  border-radius:8px; color:#0E7490 !important; font-size:.88em; font-weight:700;
  padding:7px 16px; display:inline-flex !important; align-items:center; gap:6px;
  cursor:pointer; transition:background .15s; white-space:nowrap;
  box-shadow:0 2px 8px rgba(0,0,0,.18);
}}
.settings-btn:hover {{ background:#e0f2f7 !important; }}

/* ── Settings Modal ──────────────────────────────────────── */
.modal-overlay {{
  display:none; position:fixed; inset:0; z-index:1000;
  background:rgba(0,0,0,.45); align-items:center; justify-content:center;
}}
.modal-overlay.open {{ display:flex; }}
.modal-card {{
  background:#fff; border-radius:18px; width:560px; max-width:95vw;
  max-height:88vh; overflow:hidden; display:flex; flex-direction:column;
  box-shadow:0 8px 40px rgba(14,116,144,.22);
}}
.modal-header {{
  background:#0E7490; padding:16px 22px;
  display:flex; align-items:center; justify-content:space-between;
}}
.modal-header h2 {{
  color:#fff; font-size:1.05em; font-weight:700; margin:0;
}}
.modal-close {{
  background:rgba(255,255,255,.15); border:none; color:#fff;
  font-size:1.2em; width:32px; height:32px; border-radius:6px;
  cursor:pointer; display:flex; align-items:center; justify-content:center;
}}
.modal-close:hover {{ background:rgba(255,255,255,.28); }}
.modal-body {{
  padding:20px 22px; overflow-y:auto; flex:1;
}}
.key-section {{ margin-bottom:18px; }}
.key-section-title {{
  font-size:.73em; font-weight:700; color:#0E7490;
  text-transform:uppercase; letter-spacing:.06em;
  margin-bottom:10px; padding-bottom:5px;
  border-bottom:1.5px solid #e2f0f5;
}}
.key-row {{
  display:flex; flex-direction:column; gap:4px; margin-bottom:12px;
}}
.key-row label {{
  font-size:.78em; font-weight:600; color:#374151;
}}
.key-row-input {{
  display:flex; gap:6px;
}}
.key-row input {{
  flex:1; padding:8px 12px; border:1.5px solid #d1d5db;
  border-radius:8px; font-size:.85em; font-family:monospace;
  color:#111; outline:none; transition:border-color .15s;
}}
.key-row input:focus {{ border-color:#0E7490; box-shadow:0 0 0 3px rgba(14,116,144,.1); }}
.eye-btn {{
  background:#f0f4f8; border:1.5px solid #d1d5db;
  border-radius:8px; padding:0 10px; cursor:pointer;
  font-size:1em; color:#6b7280; transition:background .15s;
}}
.eye-btn:hover {{ background:#e2f0f5; }}
.modal-footer {{
  padding:14px 22px; background:#f8fafc;
  border-top:1.5px solid #e5e7eb;
  display:flex; gap:10px; justify-content:flex-end;
}}
.btn-save {{
  background:#0E7490; color:#fff; border:none;
  padding:9px 28px; border-radius:9px; font-size:.9em;
  font-weight:600; cursor:pointer; transition:background .15s;
}}
.btn-save:hover {{ background:#0b6480; }}
.btn-cancel {{
  background:#f0f4f8; color:#374151; border:1.5px solid #d1d5db;
  padding:9px 20px; border-radius:9px; font-size:.9em;
  font-weight:600; cursor:pointer; transition:background .15s;
}}
.btn-cancel:hover {{ background:#e5e7eb; }}
.save-notice {{
  font-size:.78em; color:#059669; font-weight:600;
  display:none; align-items:center; gap:5px; margin-right:auto;
}}

/* ── Body split ─────────────────────────────────────────── */
.body {{ flex:1; display:flex; overflow:hidden; min-height:0; gap:0; background:#f0f4f8; padding:16px; }}

/* ── LEFT — drop zone ───────────────────────────────────── */
.left {{
  flex:0 0 55%;
  display:flex; flex-direction:column;
  align-items:stretch; justify-content:stretch;
  position:relative;
  background:transparent;
  overflow:hidden;
  margin-right:14px;
}}

/* Subtle grid background */
.left::before {{
  content:'';
  position:absolute; inset:0;
  background-image:
    linear-gradient(rgba(14,116,144,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(14,116,144,.05) 1px, transparent 1px);
  background-size:40px 40px;
  pointer-events:none; border-radius:20px;
}}

.drop-zone {{
  position:relative;
  width:100%; height:100%;
  border:2.5px dashed #93c5d4;
  border-radius:20px;
  display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  gap:10px;
  cursor:pointer;
  transition:all .25s;
  background:#fff;
  overflow:hidden;
  box-shadow:0 2px 20px rgba(14,116,144,.08);
}}
.drop-zone:hover, .drop-zone.drag-over {{
  border-color:#0e7490;
  background:#f6fdff;
  box-shadow:0 4px 32px rgba(14,116,144,.18);
}}
.drop-zone input[type=file] {{
  position:absolute; inset:0; opacity:0;
  cursor:pointer; width:100%; height:100%;
}}
.dz-icon-row {{ display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:4px; }}
.dz-icon-side {{ font-size:6.5em; opacity:.65; line-height:1; }}
.dz-icon {{ font-size:6.5em; opacity:.65; line-height:1; }}
.dz-title {{ font-size:1.25em; font-weight:700; color:#1e3a4a; margin-top:4px; text-align:center; }}
.dz-sub   {{ font-size:0.82em; color:#6b8a99; text-align:center; }}
.dz-badge {{
  background:#e0f2f7; border:1px solid #93c5d4;
  border-radius:20px; padding:5px 16px;
  font-size:0.75em; color:#0e7490; letter-spacing:.06em; margin-top:6px;
  display:inline-block; white-space:nowrap;
}}
/* Corner accents for drop zone */
.drop-zone::before, .drop-zone::after {{
  content:''; position:absolute;
  width:36px; height:36px;
  border-color:#b8dde8; border-style:solid;
  pointer-events:none; border-radius:4px;
}}
.drop-zone::before {{ top:10px; left:10px; border-width:2px 0 0 2px; }}
.drop-zone::after  {{ bottom:10px; right:10px; border-width:0 2px 2px 0; }}

/* Preview thumbnails */
#preview {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:center;
             max-width:580px; margin-top:8px; padding:0 8px; }}
.prev-thumb {{
  width:64px; height:64px; object-fit:cover;
  border-radius:6px; border:2px solid #93c5d4;
  box-shadow:0 2px 8px rgba(0,0,0,.15);
}}
.prev-name {{
  font-size:0.68em; color:#0e7490; text-align:center;
  max-width:64px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.prev-item {{ display:flex; flex-direction:column; align-items:center; gap:3px; }}

/* ── RIGHT panel ────────────────────────────────────────── */
.right {{
  flex:1;
  display:flex; flex-direction:column;
  padding:18px 18px; gap:0;
  background:#fff;
  border:2.5px solid #93c5d4;
  border-radius:20px;
  overflow:hidden;
  box-shadow:0 2px 20px rgba(14,116,144,.08);
}}

.right-title {{
  font-size:0.63em; font-weight:700; letter-spacing:.12em;
  text-transform:uppercase; color:#0E7490;
  margin-bottom:8px; padding-bottom:6px;
  border-bottom:2px solid #e0f2f7;
  flex-shrink:0;
}}

/* Model picker */
.model-label {{
  font-size:0.63em; font-weight:600; color:#6b8a99;
  text-transform:uppercase; letter-spacing:.08em; margin-bottom:4px;
  flex-shrink:0;
}}
.model-cards {{ display:flex; flex-direction:column; gap:8px; margin-bottom:10px; flex-shrink:0; }}
.model-card {{
  border:2px solid #ccdde3;
  border-radius:10px; padding:10px 14px;
  cursor:pointer; transition:all .2s;
  display:flex; align-items:center; gap:12px;
  background:#fff;
  box-shadow:0 1px 4px rgba(14,116,144,.07);
}}
.model-card:hover {{
  border-color:#0e7490;
  background:#f0f9fb;
  box-shadow:0 2px 10px rgba(14,116,144,.15);
}}
.model-card input[type=radio] {{ accent-color:#0e7490; margin:0; flex-shrink:0; width:16px; height:16px; }}
.mc-body {{ flex:1; }}
.mc-body .mc-name {{
  font-size:0.84em; font-weight:700; color:#1e3a4a;
  display:flex; align-items:center; gap:6px;
}}
.mc-body .mc-desc {{
  font-size:0.69em; color:#6b8a99; margin-top:3px; line-height:1.4;
}}
.mc-badge {{
  font-size:0.60em; font-weight:700; letter-spacing:.05em;
  padding:2px 6px; border-radius:4px;
  vertical-align:middle;
}}
.badge-best    {{ background:#d0f0f7; color:#0e7490; }}
.badge-fast    {{ background:#fef9c3; color:#a16207; }}
.badge-offline {{ background:#ede9fe; color:#6d28d9; }}

.model-card.selected {{
  border-color:#0e7490; border-width:2px;
  background:#e8f5f9;
  box-shadow:0 0 0 3px rgba(14,116,144,.12);
}}

/* Divider */
.divider {{ flex:1; min-height:4px; max-height:14px; }}

/* Info strip */
.info-strip {{
  background:#f0f9fb; border-radius:7px; border:1px solid #b8dde8;
  padding:6px 10px; margin-bottom:8px; flex-shrink:0;
  font-size:0.67em; color:#4a7080; line-height:1.45;
}}
.info-strip b {{ color:#0E7490; }}

/* Process button */
.process-btn {{
  width:100%; padding:11px;
  background:linear-gradient(135deg,#0e7490,#0891b2);
  color:white; border:none; border-radius:8px;
  font-size:0.92em; font-weight:700; cursor:pointer;
  transition:all .2s;
  box-shadow:0 3px 12px rgba(14,116,144,.3);
  letter-spacing:.01em; flex-shrink:0;
}}
.process-btn:hover {{
  background:linear-gradient(135deg,#0891b2,#06b6d4);
  box-shadow:0 5px 22px rgba(14,116,144,.55);
  transform:translateY(-1px);
}}
.process-btn:disabled {{
  background:#dde8ec; color:#7a9daa;
  cursor:not-allowed; box-shadow:none; transform:none;
}}
.process-btn .btn-sub {{
  display:block; font-size:0.68em; font-weight:400;
  color:rgba(255,255,255,.70); margin-top:2px;
}}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr" style="display:flex;align-items:center;justify-content:space-between;background:#0E7490;padding:14px 28px;flex-shrink:0;">
  <div>
    <div class="hdr-logo">CV&#8209;<span>Toposheet</span> / Maps</div>
    <div class="hdr-sub">Extracted Map Feature</div>
  </div>
  <button onclick="openSettings()" title="API Settings"
    style="background:#fff;border:none;border-radius:8px;color:#0E7490;font-size:.9em;font-weight:700;padding:8px 18px;cursor:pointer;display:flex;align-items:center;gap:6px;box-shadow:0 2px 8px rgba(0,0,0,.2);">
    &#9881; Settings
  </button>
</div>

<div class="body">

  <!-- Left: upload drop zone -->
  <div class="left">
    <form action="/upload" method="post" enctype="multipart/form-data"
          id="uploadForm" style="width:100%;height:100%;display:flex;flex-direction:column;position:relative;z-index:1;">
      <input type="hidden" name="model" id="modelInput" value="best">

      <div class="drop-zone" id="dropZone">
        <input type="file" name="map_file" id="fileInput"
               accept=".jpg,.jpeg,.png,.tif,.tiff" multiple required>
        <div id="dzDefault" style="display:flex;flex-direction:column;align-items:center;text-align:center;">
          <div class="dz-icon-row">
            <div class="dz-icon-side">&#127759;</div>
            <div class="dz-icon">&#128506;&#65039;</div>
            <div class="dz-icon-side">&#129517;</div>
          </div>
          <div class="dz-title">Upload Toposheet</div>
          <div class="dz-sub">Drag &amp; drop map images here, or click to browse</div>
          <div class="dz-badge">JPG &nbsp;·&nbsp; PNG &nbsp;·&nbsp; TIF &nbsp;·&nbsp; Up to 500 MB each</div>
        </div>
        <div id="preview"></div>
      </div>
    </form>
  </div>

  <!-- Right: model + process -->
  <div class="right">

    <div class="model-label">Choose Model</div>
    <div class="model-cards">

      <label class="model-card selected" id="card-best">
        <input type="radio" name="model_ui" value="best" checked
               onchange="selectModel('best')">
        <div class="mc-body">
          <div class="mc-name">
            Best Quality
            <span class="mc-badge badge-best">RECOMMENDED</span>
          </div>
          <div class="mc-desc">GCV OCR + Vertex AI Gemini 2.5 Flash<br>Highest accuracy, uses Google Cloud</div>
        </div>
      </label>

      <label class="model-card" id="card-fast">
        <input type="radio" name="model_ui" value="fast"
               onchange="selectModel('fast')">
        <div class="mc-body">
          <div class="mc-name">
            Fast
            <span class="mc-badge badge-fast">QUICK</span>
          </div>
          <div class="mc-desc">GCV OCR + Groq LLaMA 3.1<br>Faster processing, lower cost</div>
        </div>
      </label>

      <label class="model-card" id="card-offline">
        <input type="radio" name="model_ui" value="offline"
               onchange="selectModel('offline')">
        <div class="mc-body">
          <div class="mc-name">
            Offline OCR
            <span class="mc-badge badge-offline">NO GOOGLE</span>
          </div>
          <div class="mc-desc">EasyOCR + Groq LLaMA 3.1<br>No Google Cloud needed</div>
        </div>
      </label>

    </div>

    <div class="divider"></div>

    <div class="info-strip" id="infoStrip">
      <b>Best Quality:</b> Uses Google Cloud Vision for OCR
      and Vertex AI Gemini for feature extraction.
      Requires internet &amp; GCP credentials.
    </div>

    <button class="process-btn" id="processBtn" type="submit"
            form="uploadForm" disabled>
      &#9654;&nbsp; Process Map
      <span class="btn-sub" id="btnSub">Select a map file to continue</span>
    </button>
  </div>

</div>

<script>
const fileInput  = document.getElementById('fileInput');
const processBtn = document.getElementById('processBtn');
const modelInput = document.getElementById('modelInput');
const dropZone   = document.getElementById('dropZone');
const dzDefault  = document.getElementById('dzDefault');
const preview    = document.getElementById('preview');
const btnSub     = document.getElementById('btnSub');

const modelInfo = {{
  best:    'Best Quality: Uses Google Cloud Vision for OCR and Vertex AI Gemini for feature extraction. Requires internet & GCP credentials.',
  fast:    'Fast: Uses Google Cloud Vision OCR with Groq LLaMA for quicker, lower-cost processing.',
  offline: 'Offline OCR: Uses EasyOCR locally so no Google Cloud needed. First run downloads model files.',
}};

function selectModel(val) {{
  modelInput.value = val;
  ['best','fast','offline'].forEach(m => {{
    document.getElementById('card-' + m).classList.toggle('selected', m === val);
  }});
  document.getElementById('infoStrip').innerHTML =
    '<b>' + (val === 'best' ? 'Best Quality' : val === 'fast' ? 'Fast' : 'Offline OCR') + ':</b> '
    + modelInfo[val].split(':')[1];
}}

function showPreviews(files) {{
  preview.innerHTML = '';
  dzDefault.style.display = 'none';
  const n = Math.min(files.length, 8);
  for (let i = 0; i < n; i++) {{
    const f = files[i];
    const item = document.createElement('div');
    item.className = 'prev-item';
    const img = document.createElement('img');
    img.className = 'prev-thumb';
    img.src = URL.createObjectURL(f);
    const nm = document.createElement('div');
    nm.className = 'prev-name';
    nm.textContent = f.name;
    item.appendChild(img);
    item.appendChild(nm);
    preview.appendChild(item);
  }}
  if (files.length > 8) {{
    const more = document.createElement('div');
    more.className = 'prev-item';
    more.innerHTML = '<div style="width:64px;height:64px;border-radius:6px;background:rgba(34,211,238,.1);border:2px solid rgba(34,211,238,.3);display:flex;align-items:center;justify-content:center;font-size:1.2em;color:#22d3ee;">+' + (files.length-8) + '</div>';
    preview.appendChild(more);
  }}
}}

fileInput.addEventListener('change', function () {{
  if (this.files.length > 0) {{
    showPreviews(this.files);
    processBtn.disabled = false;
    const n = this.files.length;
    btnSub.textContent = n + ' file' + (n > 1 ? 's' : '') + ' ready to process';
  }}
}});

dropZone.addEventListener('dragover',  e => {{ e.preventDefault(); dropZone.classList.add('drag-over'); }});
dropZone.addEventListener('dragleave', ()  => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {{
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length > 0) {{
    fileInput.files = e.dataTransfer.files;
    fileInput.dispatchEvent(new Event('change'));
  }}
}});

document.getElementById('uploadForm').addEventListener('submit', function () {{
  processBtn.disabled    = true;
  processBtn.innerHTML   = '\\u23f3 Uploading...<span class="btn-sub">Please wait</span>';
}});

// ── Settings modal ────────────────────────────────────────
const KEYS = ['GROQ_API_KEY','GEMINI_API_KEY','GEMINI_API_KEY_2',
              'OPENAI_API_KEY','CLAUDE_API_KEY','GROK_API_KEY',
              'GOOGLE_API_KEY','GOOGLE_APPLICATION_CREDENTIALS',
              'VERTEX_PROJECT','VERTEX_LOCATION','VERTEX_MODEL'];

function openSettings() {{
  fetch('/settings').then(r => r.json()).then(data => {{
    KEYS.forEach(k => {{
      const el = document.getElementById('inp_' + k);
      if (el) el.value = data[k] || '';
    }});
    document.getElementById('settingsModal').classList.add('open');
    document.getElementById('saveNotice').style.display = 'none';
  }});
}}

function closeSettings() {{
  document.getElementById('settingsModal').classList.remove('open');
}}

function saveSettings() {{
  const payload = {{}};
  KEYS.forEach(k => {{
    const el = document.getElementById('inp_' + k);
    if (el) payload[k] = el.value.trim();
  }});
  fetch('/settings', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify(payload)
  }}).then(r => r.json()).then(() => {{
    const notice = document.getElementById('saveNotice');
    notice.style.display = 'flex';
    setTimeout(() => notice.style.display = 'none', 2500);
  }});
}}

function toggleEye(key) {{
  const inp = document.getElementById('inp_' + key);
  inp.type = inp.type === 'password' ? 'text' : 'password';
}}

// Close on backdrop click
document.getElementById('settingsModal').addEventListener('click', function(e) {{
  if (e.target === this) closeSettings();
}});
</script>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-card">
    <div class="modal-header">
      <h2>&#9881; API Key Settings</h2>
      <button class="modal-close" onclick="closeSettings()">&#x2715;</button>
    </div>
    <div class="modal-body">

      <div class="key-section">
        <div class="key-section-title">LLM Providers</div>
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

      <div class="key-section">
        <div class="key-section-title">Google Cloud</div>
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
            <input type="text" id="inp_GOOGLE_APPLICATION_CREDENTIALS" placeholder="C:\path\to\service_account.json">
          </div>
        </div>
      </div>

      <div class="key-section">
        <div class="key-section-title">Google Vertex AI</div>
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


# ── Processing page HTML ──────────────────────────────────────────────────────
def _process_html(filename: str, model: str = 'best', session_id: str = '') -> str:
    # URL-encode the filename for safe use in the SSE endpoint
    enc = url_quote(filename, safe='')
    model_enc = url_quote(model, safe='')
    session_enc = url_quote(session_id, safe='')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Processing – {filename}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',Arial,sans-serif; background:#f0f2f5;
       min-height:100vh; display:flex; flex-direction:column; }}

.hdr {{ background:#0E7490; color:white; padding:14px 28px; }}
.hdr-title {{ font-size:1.2em; font-weight:700; }}
.hdr-sub   {{ font-size:0.78em; color:rgba(255,255,255,.70); margin-top:2px; }}

.page {{ flex:1; display:flex; align-items:flex-start;
         justify-content:center; padding:32px 20px; }}
.card {{ background:white; border-radius:12px;
         box-shadow:0 4px 28px rgba(0,0,0,.10);
         padding:32px 36px; max-width:780px; width:100%; }}

/* Spinner row */
.status-row {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; }}
.spinner {{ width:24px; height:24px; border:3px solid #e0e0e0;
            border-top-color:#0E7490; border-radius:50%;
            animation:spin .8s linear infinite; flex-shrink:0; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
.status-text  {{ font-size:1em; font-weight:600; color:#333; }}
.filename-tag {{ background:#e8f5f9; color:#0E7490; padding:3px 9px;
                 border-radius:4px; font-size:0.82em; font-weight:600;
                 margin-left:8px; word-break:break-all; }}

/* Phase pills */
.phases {{ display:flex; gap:6px; margin-bottom:18px; flex-wrap:wrap; }}
.phase       {{ padding:4px 11px; border-radius:12px; font-size:0.74em;
                font-weight:600; background:#f0f0f0; color:#999; transition:all .3s; }}
.phase.active {{ background:#0E7490; color:white; }}
.phase.done   {{ background:#27ae60; color:white; }}

/* Log */
.log {{ background:#1a1a2e; color:#a8d8a8; border-radius:8px;
        padding:16px 18px; font-family:'Consolas','Courier New',monospace;
        font-size:0.77em; height:360px; overflow-y:auto; line-height:1.6;
        margin-bottom:20px; white-space:pre-wrap; word-break:break-all; }}
.line-phase {{ color:#67d8f0; font-weight:bold; }}
.line-done  {{ color:#67f0a0; font-weight:bold; }}
.line-warn  {{ color:#f0c040; }}
.line-err   {{ color:#f06060; }}

/* Buttons */
.btn-row  {{ display:flex; gap:10px; flex-wrap:wrap; }}
.view-btn {{ padding:11px 28px; background:#22c55e; color:white; border:none;
             border-radius:7px; font-size:0.95em; font-weight:700; cursor:pointer;
             text-decoration:none; display:inline-block; transition:background .2s;
             opacity:0.45; pointer-events:none; }}
.view-btn.ready       {{ opacity:1; pointer-events:auto; }}
.view-btn.ready:hover {{ background:#16a34a; }}
.back-btn {{ padding:11px 20px; background:#f0f0f0; color:#555; border:none;
             border-radius:7px; font-size:0.95em; cursor:pointer;
             text-decoration:none; display:inline-block; }}
.back-btn:hover {{ background:#e0e0e0; }}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-title">CV-Toposheet / Maps</div>
  <div class="hdr-sub">Pipeline running — please keep this tab open</div>
</div>

<div class="page">
  <div class="card">

    <div class="status-row">
      <div class="spinner" id="spinner"></div>
      <div class="status-text">
        Processing<span class="filename-tag">{filename}</span>
      </div>
    </div>

    <div class="phases">
      <div class="phase" id="p1">1 · Tiling</div>
      <div class="phase" id="p2">2 · OCR</div>
      <div class="phase" id="p3">3 · Grid Detection</div>
      <div class="phase" id="p4">4 · LLM Cleaning</div>
      <div class="phase" id="p5">5 · Database</div>
      <div class="phase" id="p6">Export</div>
    </div>

    <div class="log" id="logBox"></div>

    <div class="btn-row">
      <a href="/results" class="view-btn" id="viewBtn">&#128202;&nbsp; View Results</a>
      <a href="/"        class="back-btn">&#8592;&nbsp; Upload Another Map</a>
    </div>

  </div>
</div>

<script>
const logBox  = document.getElementById('logBox');
const spinner = document.getElementById('spinner');
const viewBtn = document.getElementById('viewBtn');

let currentPhase = null;

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

es.onmessage = function (e) {{
  const data = JSON.parse(e.data);
  const msg  = data.msg || '';

  // Detect phase from log text
  if      (/Phase 1|Tiling/i.test(msg))    {{ setPhase('p1'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 2|OCR/i.test(msg))       {{ setPhase('p2'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 3|Grid/i.test(msg))      {{ setPhase('p3'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 4|LLM/i.test(msg))       {{ setPhase('p4'); appendLine(msg, 'line-phase'); }}
  else if (/Phase 5|Database/i.test(msg))  {{ setPhase('p5'); appendLine(msg, 'line-phase'); }}
  else if (/[Ee]xport/i.test(msg))         {{ setPhase('p6'); appendLine(msg, 'line-phase'); }}
  else if (/ERROR|Error/i.test(msg))       {{ appendLine(msg, 'line-err');  }}
  else if (/warn/i.test(msg))              {{ appendLine(msg, 'line-warn'); }}
  else if (msg.startsWith('\\u2705'))       {{ appendLine(msg, 'line-done'); }}
  else                                     {{ appendLine(msg, '');          }}

  if (data.done) {{
    es.close();
    // Mark last phase done
    if (currentPhase) document.getElementById(currentPhase).className = 'phase done';
    // Update spinner to checkmark
    spinner.style.cssText = 'width:24px;height:24px;font-size:1.3em;color:#27ae60;line-height:1;';
    spinner.textContent   = '\\u2713';
    document.querySelector('.status-text').textContent = 'Processing complete!';
    // Use session-specific results URL sent by server
    if (data.redirect) viewBtn.href = data.redirect;
    viewBtn.classList.add('ready');
  }}

  if (data.error) es.close();
}};

es.onerror = function () {{
  if (viewBtn.classList.contains('ready')) return;
  appendLine('Connection lost. Check the terminal for errors.', 'line-err');
  es.close();
}};
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print()
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)   # hides the "development server" warning
    print('=' * 52)
    print('  CV-Toposheet Map Digitization Interface')
    print('  Open your browser: http://localhost:5000')
    print('=' * 52)
    print()
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True)
