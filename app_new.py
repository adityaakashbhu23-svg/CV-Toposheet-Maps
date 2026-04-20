# app.py  –  CV-Toposheet Web Interface
#
# A simple Flask cover page: upload a map → pipeline runs → view results.
#

import sys
import threading
import json
import os
import re
import uuid
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

_MODEL_ENVS = {
    'claude':   {'LLM_PROVIDER': 'claude',  'OCR_ENGINE': 'gcv'},
    'grok':     {'LLM_PROVIDER': 'grok',    'OCR_ENGINE': 'gcv'},
    'ensemble': {'LLM_PROVIDER': 'groq',    'OCR_ENGINE': 'gcv', 'ENSEMBLE_MODE': 'true'},
    'best':     {'LLM_PROVIDER': 'gemini',  'OCR_ENGINE': 'gcv'},
    'openai':   {'LLM_PROVIDER': 'openai',  'OCR_ENGINE': 'gcv'},
    'fast':     {'LLM_PROVIDER': 'groq',    'OCR_ENGINE': 'gcv'},
    'offline':  {'LLM_PROVIDER': 'groq',    'OCR_ENGINE': 'easyocr'},
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

    env_overrides = _MODEL_ENVS.get(model, _MODEL_ENVS['best'])

    def generate():
        if not _process_lock.acquire(blocking=False):
            yield f'data: {json.dumps({"msg": "Another map is already being processed. Please wait and try again."})}\n\n'
            yield f'data: {json.dumps({"error": True})}\n\n'
            return

        try:
            env = os.environ.copy()
            env.update(env_overrides)

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
                    bufsize=1,
                    env=env,
                )
                for line in iter(proc.stdout.readline, ''):
                    line = line.rstrip()
                    if line:
                        yield f'data: {json.dumps({"msg": line})}\n\n'
                proc.wait()

            yield f'data: {json.dumps({"done": True, "redirect": results_url, "msg": "Done! Click View Results."})}\n\n'

        finally:
            _process_lock.release()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


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
    yield f'data: {json.dumps({"msg": f"Starting processing for {filename}..."})}\n\n'


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


@app.route('/results')
@app.route('/results/<session_id>')
def results(session_id=None):
    if session_id:
        out = BASE_DIR / 'results' / session_id / 'table_export.html'
    else:
        out = RESULTS_DIR / 'table_export.html'
    if out.exists():
        return send_file(str(out))
    return ('<h2 style="font-family:sans-serif;padding:40px;color:#888">'
            'No results yet – please upload and process a map first.'
            ' <a href="/">&#8592; Go back</a></h2>'), 404


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
body { min-height:100vh; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; display:flex; flex-direction:column; }
.hdr { display:flex;align-items:center;justify-content:space-between;background:#0E7490;padding:14px 28px;flex-shrink:0; }
.hdr-logo { font-size:1.25em; font-weight:700; color:#fff; letter-spacing:-.01em; }
.hdr-logo span { color:#fff; }
.hdr-sub  { font-size:0.78em; color:rgba(255,255,255,.75); margin-top:2px; }
.hdr-actions { display:flex; align-items:center; gap:10px; }
.settings-btn { background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); color:#fff; border-radius:7px; padding:7px 14px; font-size:0.85em; cursor:pointer; transition:background .2s; }
.settings-btn:hover { background:rgba(255,255,255,.25); }
.body { flex:1; display:flex; flex-direction:row; gap:32px; align-items:flex-start; justify-content:center; padding:38px 28px 0 28px; min-height:400px; }
.left { width:420px; min-width:320px; max-width:480px; }
.right { flex:1; display:flex; flex-direction:column; padding:18px; gap:0; background:#fff; border:2.5px solid #93c5d4; border-radius:20px; min-height:400px; box-shadow:0 2px 20px rgba(14,116,144,.08); overflow:auto; }
.right-title { font-size:0.63em; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:#0E7490; margin-bottom:8px; padding-bottom:6px; border-bottom:2px solid #e0f2f7; flex-shrink:0; }
.model-label { font-size:0.63em; font-weight:600; color:#6b8a99; text-transform:uppercase; letter-spacing:.08em; margin-bottom:4px; flex-shrink:0; }
.model-cards { display:flex; flex-direction:column; gap:5px; margin-bottom:8px; flex:1; overflow-y:auto; min-height:0; padding-right:2px; }
.model-card { border:2px solid #ccdde3; border-radius:10px; padding:7px 12px; cursor:pointer; transition:all .2s; display:flex; align-items:center; gap:10px; background:#fff; box-shadow:0 1px 4px rgba(14,116,144,.07); flex-shrink:0; }
.model-card:hover { border-color:#0e7490; background:#f0f9fb; box-shadow:0 2px 10px rgba(14,116,144,.15); }
.model-card input[type=radio] { accent-color:#0e7490; margin:0; flex-shrink:0; width:16px; height:16px; }
.mc-body { flex:1; }
.mc-body .mc-name { font-size:0.82em; font-weight:700; color:#1e3a4a; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.mc-body .mc-desc { font-size:0.67em; color:#6b8a99; margin-top:2px; line-height:1.3; }
.badge { display:inline-block; border-radius:4px; padding:1px 7px; font-size:0.7em; font-weight:700; }
.badge-best    { background:#d0f0f7; color:#0e7490; }
.badge-fast    { background:#fef9c3; color:#a16207; }
.badge-offline { background:#ede9fe; color:#6d28d9; }
.model-card.selected { border-color:#0e7490; border-width:2px; background:#e8f5f9; box-shadow:0 0 0 3px rgba(14,116,144,.12); }
.divider { flex-shrink:0; min-height:4px; max-height:8px; }
.info-strip { background:#f0f9fb; border-radius:7px; border:1px solid #b8dde8; padding:6px 10px; margin-bottom:8px; flex-shrink:0; font-size:0.67em; color:#4a7080; line-height:1.45; }
.info-strip b { color:#0E7490; }
.process-btn { width:100%; padding:11px; background:linear-gradient(135deg,#0e7490,#0891b2); color:white; border:none; border-radius:8px; font-size:0.92em; font-weight:700; cursor:pointer; transition:all .2s; box-shadow:0 3px 12px rgba(14,116,144,.3); letter-spacing:.01em; flex-shrink:0; }
.process-btn:hover { background:linear-gradient(135deg,#0891b2,#06b6d4); box-shadow:0 5px 22px rgba(14,116,144,.55); transform:translateY(-1px); }
.process-btn:disabled { background:#dde8ec; color:#7a9daa; cursor:not-allowed; box-shadow:none; transform:none; }
.process-btn .btn-sub { display:block; font-size:0.68em; font-weight:400; color:rgba(255,255,255,.70); margin-top:2px; }
.drop-zone { position:relative; border:2.5px dashed #93c5d4; border-radius:18px; background:#fff; min-height:220px; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:32px 18px 18px 18px; margin-bottom:18px; transition:border .18s,background .18s; cursor:pointer; }
.drop-zone.drag-over { border-color:#0e7490; background:#f6fdff; box-shadow:0 4px 32px rgba(14,116,144,.18); }
.drop-zone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; }
.dz-icon-row { display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:4px; }
.dz-icon-side { font-size:2.8em; opacity:.65; line-height:1; }
.dz-icon { font-size:3.2em; opacity:.65; line-height:1; }
.dz-title { font-size:1.25em; font-weight:700; color:#1e3a4a; margin-top:4px; text-align:center; }
.dz-sub   { font-size:0.82em; color:#6b8a99; text-align:center; }
.dz-badge { background:#e0f2f7; border:1px solid #93c5d4; border-radius:20px; padding:5px 16px; font-size:0.75em; color:#0e7490; letter-spacing:.06em; margin-top:6px; display:inline-block; white-space:nowrap; }
#preview { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; max-width:580px; margin-top:8px; padding:0 8px; }
.prev-thumb { width:64px; height:64px; object-fit:cover; border-radius:6px; border:2px solid #93c5d4; box-shadow:0 2px 8px rgba(0,0,0,.15); }
.prev-name { font-size:0.68em; color:#0e7490; text-align:center; max-width:64px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.prev-item { display:flex; flex-direction:column; align-items:center; gap:3px; }
@media (max-width:1100px) { .body { flex-direction:column; align-items:center; gap:18px; } .left, .right { width:100%; max-width:600px; } }
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
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-logo">CV-<span>Toposheet</span></div>
    <div class="hdr-sub">Topographic Map Digitization</div>
  </div>
  <div class="hdr-actions">
    <button class="settings-btn" onclick="openSettings()">&#9881; API Keys</button>
  </div>
</div>

<div class="body">
  <div class="left">
    <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data">
      <div class="drop-zone" id="dropZone">
        <input type="file" name="map_file" id="fileInput" accept=".jpg,.jpeg,.png,.tif,.tiff" multiple>
        <div class="dz-icon-row">
          <span class="dz-icon-side">&#128506;</span>
          <span class="dz-icon">&#128228;</span>
          <span class="dz-icon-side">&#128205;</span>
        </div>
        <div class="dz-title">Drop map image here</div>
        <div class="dz-sub">or click to browse</div>
        <div class="dz-badge">JPG &middot; PNG &middot; TIF</div>
        <div id="preview"></div>
      </div>

      <div class="right" style="min-height:unset;margin-bottom:14px;">
        <div class="right-title">Select AI Model</div>
        <div class="model-label">Choose extraction engine</div>
        <div class="model-cards" id="modelCards">
          <label class="model-card selected" onclick="selectCard(this)">
            <input type="radio" name="model" value="best" checked>
            <div class="mc-body">
              <div class="mc-name">Gemini Pro <span class="badge badge-best">Best</span></div>
              <div class="mc-desc">Highest accuracy, recommended for production maps</div>
            </div>
          </label>
          <label class="model-card" onclick="selectCard(this)">
            <input type="radio" name="model" value="fast">
            <div class="mc-body">
              <div class="mc-name">Groq <span class="badge badge-fast">Fast</span></div>
              <div class="mc-desc">Fastest processing, good for quick previews</div>
            </div>
          </label>
          <label class="model-card" onclick="selectCard(this)">
            <input type="radio" name="model" value="claude">
            <div class="mc-body">
              <div class="mc-name">Claude</div>
              <div class="mc-desc">Anthropic Claude – excellent at structured data</div>
            </div>
          </label>
          <label class="model-card" onclick="selectCard(this)">
            <input type="radio" name="model" value="openai">
            <div class="mc-body">
              <div class="mc-name">OpenAI GPT-4</div>
              <div class="mc-desc">Strong general-purpose extraction</div>
            </div>
          </label>
          <label class="model-card" onclick="selectCard(this)">
            <input type="radio" name="model" value="offline">
            <div class="mc-body">
              <div class="mc-name">Offline <span class="badge badge-offline">No API</span></div>
              <div class="mc-desc">EasyOCR + local model, no API keys needed</div>
            </div>
          </label>
        </div>
        <div class="divider"></div>
        <div class="info-strip">
          <b>__FEAT_COUNT__</b> features &middot; <b>__MAP_COUNT__</b> maps processed
        </div>
        <button type="submit" class="process-btn" id="processBtn" disabled>
          &#128202; Digitize Map
          <span class="btn-sub">Upload a map image to begin</span>
        </button>
      </div>
    </form>
  </div>

  <div class="right">
    <div class="right-title">How It Works</div>
    <p style="font-size:0.82em;color:#4a7080;line-height:1.6;margin-bottom:10px;">
      <b>1. Upload</b> a topographic map image (JPG, PNG, or TIF).<br>
      <b>2. Select</b> an AI model for text extraction.<br>
      <b>3. Click Digitize</b> – the pipeline runs automatically.<br>
      <b>4. View Results</b> – download the structured data table.
    </p>
    <div style="border-top:1px solid #e0f2f7;margin:10px 0;"></div>
    <div style="font-size:0.75em;color:#6b8a99;line-height:1.6;">
      Supports Survey of India, UK OS, USGS, and other national grid toposheets.
      Extracts place names, elevations, grid references, and geographic features.
    </div>
  </div>
</div>

<script>
function selectCard(el) {
  document.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
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

function showPreviews() {
  preview.innerHTML = '';
  const files = fileInput.files;
  if (!files.length) { processBtn.disabled = true; return; }
  processBtn.disabled = false;
  processBtn.querySelector('.btn-sub').textContent = files.length + ' file(s) ready';
  for (const f of files) {
    const item = document.createElement('div');
    item.className = 'prev-item';
    const img = document.createElement('img');
    img.className = 'prev-thumb';
    img.src = URL.createObjectURL(f);
    const name = document.createElement('div');
    name.className = 'prev-name';
    name.textContent = f.name;
    item.appendChild(img);
    item.appendChild(name);
    preview.appendChild(item);
  }
}

// Settings modal
const SECTION_IDS = ['llm','google','vertex'];

function openSettings() {
  fetch('/get_env').then(r => r.json()).then(data => {
    for (const [k, v] of Object.entries(data)) {
      const inp = document.getElementById('inp_' + k);
      if (inp) inp.value = v || '';
    }
  }).catch(() => {});
  document.getElementById('settingsModal').classList.add('open');
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

document.getElementById('settingsModal').addEventListener('click', function(e) {
  if (e.target === this) closeSettings();
});
</script>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-card">
    <div class="modal-header">
      <h2>&#9881; API Key Settings</h2>
      <button class="modal-close" onclick="closeSettings()">&#x2715;</button>
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
body {{ min-height:100vh; font-family:'Segoe UI', system-ui, Arial, sans-serif; background:#f0f4f8; display:flex; flex-direction:column; }}
.hdr {{ display:flex;align-items:center;justify-content:space-between;background:#0E7490;padding:14px 28px;flex-shrink:0; }}
.hdr-logo {{ font-size:1.25em; font-weight:700; color:#fff; letter-spacing:-.01em; }}
.hdr-logo span {{ color:#fff; }}
.page {{ flex:1; display:flex; justify-content:center; align-items:flex-start; padding:40px 20px; }}
.card {{ background:#fff; border-radius:14px; box-shadow:0 2px 20px rgba(14,116,144,.10); padding:28px; max-width:520px; width:100%; }}
.status-row {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; }}
.spinner {{ width:24px; height:24px; border:3px solid #e0e0e0; border-top-color:#0E7490; border-radius:50%; animation:spin .8s linear infinite; flex-shrink:0; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
.status-text {{ font-size:1em; font-weight:600; color:#333; }}
.filename-tag {{ background:#e8f5f9; color:#0E7490; padding:3px 9px; border-radius:4px; font-size:0.82em; font-weight:600; margin-left:8px; word-break:break-all; }}
.phases {{ display:flex; gap:6px; margin-bottom:18px; flex-wrap:wrap; }}
.phase {{ padding:4px 11px; border-radius:12px; font-size:0.74em; font-weight:600; background:#f0f0f0; color:#999; transition:all .3s; }}
.phase.active {{ background:#0E7490; color:white; }}
.phase.done   {{ background:#27ae60; color:white; }}
.log {{ background:#1a1a2e; color:#a8d8a8; border-radius:8px; padding:10px 12px; font-family:'Consolas','Courier New',monospace; font-size:0.75em; min-height:80px; max-height:180px; overflow-y:auto; line-height:1.5; margin-bottom:18px; white-space:pre-wrap; word-break:break-all; }}
.line-phase {{ color:#67d8f0; font-weight:bold; }}
.line-done  {{ color:#67f0a0; font-weight:bold; }}
.line-warn  {{ color:#f0c040; }}
.line-err   {{ color:#f06060; }}
.btn-row {{ display:flex; gap:10px; flex-wrap:wrap; }}
.view-btn {{ padding:11px 28px; background:#22c55e; color:white; border:none; border-radius:7px; font-size:0.95em; font-weight:700; cursor:pointer; text-decoration:none; display:inline-block; transition:background .2s; opacity:0.45; pointer-events:none; }}
.view-btn.ready       {{ opacity:1; pointer-events:auto; }}
.view-btn.ready:hover {{ background:#16a34a; }}
.back-btn {{ padding:11px 20px; background:#f0f0f0; color:#555; border:none; border-radius:7px; font-size:0.95em; cursor:pointer; text-decoration:none; display:inline-block; }}
.back-btn:hover {{ background:#e0e0e0; }}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-logo">CV-<span>Toposheet</span></div>
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
    viewBtn.classList.add('ready');
    if (data.redirect) viewBtn.href = data.redirect;
    es.close();
  }}
  if (data.error) {{
    spinner.style.display = 'none';
    es.close();
  }}
}};
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print()
    print('=' * 52)
    print('  CV-Toposheet Map Digitization Interface')
    print('  Open your browser: http://localhost:5000')
    print('=' * 52)
    print()
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True)
