# config.py  –  Central configuration loader for CV-Toposheet pipeline
# Reads all settings from .env so no secrets are ever hardcoded.

import os
import sys
from utils.cpu_utils import throttler as _throttler  # starts background monitor on import
from pathlib import Path
from dotenv import load_dotenv

# In frozen (PyInstaller EXE) mode, __file__ is inside _internal/ which may be
# read-only (e.g. Program Files). Use the directory next to the EXE for all
# user-writable data: .env, service account JSONs, maps, results, logs.
if getattr(sys, 'frozen', False):
    _DATA_DIR = Path(sys.executable).parent   # install dir next to the EXE
else:
    _DATA_DIR = Path(__file__).parent          # project root (dev mode)

# Load .env from the data dir
load_dotenv(_DATA_DIR / '.env')

# ── API Keys ──────────────────────────────────────────────────
OPENAI_API_KEY  = os.getenv('OPENAI_API_KEY', '')
GEMINI_API_KEY  = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_KEY_2 = os.getenv('GEMINI_API_KEY_2', '')
CLAUDE_API_KEY  = os.getenv('CLAUDE_API_KEY', '')
GROQ_API_KEY    = os.getenv('GROQ_API_KEY',   '')
GROK_API_KEY    = os.getenv('GROK_API_KEY',   '')  # xAI Grok (Elon Musk)
GROK_MODEL      = os.getenv('GROK_MODEL',     'grok-3-mini')

# Which LLM to use: "vertex", "groq", "openai", "gemini", "claude", or "local"
LLM_PROVIDER    = os.getenv('LLM_PROVIDER', 'vertex').lower()

# Ensemble mode: run ALL configured LLMs in parallel and vote on results.
# Set ENSEMBLE_MODE=true in .env, or pass --ensemble flag to 4_llm_cleaning.py.
ENSEMBLE_MODE   = os.getenv('ENSEMBLE_MODE', 'false').lower() in ('1', 'true', 'yes')

# CPU workers — always start at full CPU count; ThermalThrottler reduces live if hot
# Override with LLM_ENSEMBLE_WORKERS=N in .env to cap ensemble parallelism
_default_workers = str(os.cpu_count() or 6)
LLM_ENSEMBLE_WORKERS = int(os.getenv('LLM_ENSEMBLE_WORKERS', _default_workers))

# Model names
OPENAI_MODEL    = os.getenv('OPENAI_MODEL',  'gpt-4o-mini')
GEMINI_MODEL    = os.getenv('GEMINI_MODEL',  'gemini-2.5-flash')
CLAUDE_MODEL    = os.getenv('CLAUDE_MODEL',  'claude-haiku-4-5')
GROQ_MODEL      = os.getenv('GROQ_MODEL',    'llama-3.3-70b-versatile')

# Vertex AI (Google Cloud — uses service_account.json, billed to GCP credit)
VERTEX_PROJECT  = os.getenv('VERTEX_PROJECT',  'cv-toposheet')
VERTEX_LOCATION = os.getenv('VERTEX_LOCATION', 'us-central1')
VERTEX_MODEL    = os.getenv('VERTEX_MODEL',    'gemini-2.5-flash')

# ── Google Cloud Vision ──────────────────────────────────────
# Priority: service_account.json → service_account2.json → Service_account_Backup.json
_gcv_creds_rel  = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
if _gcv_creds_rel:
    _gcv_creds_path = _DATA_DIR / _gcv_creds_rel
else:
    _primary_path  = _DATA_DIR / 'service_account.json'
    _legacy_path   = _DATA_DIR / 'service_account2.json'
    _backup_path   = _DATA_DIR / 'Service_account_Backup.json'
    if _primary_path.exists():
        _gcv_creds_path = _primary_path
    elif _legacy_path.exists():
        _gcv_creds_path = _legacy_path
    elif _backup_path.exists():
        _gcv_creds_path = _backup_path
    else:
        _gcv_creds_path = _primary_path  # will just not be set
if _gcv_creds_path.exists():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_gcv_creds_path)
    _which = _gcv_creds_path.name
    if 'backup' in _which.lower() or 'Backup' in _which:
        print(f'[Config/GCV] WARN: Primary service_account.json not found - using BACKUP: {_which}')
    else:
        print(f'[Config/GCV] OK: GCV credentials loaded: {_which}')
else:
    print(f'[Config/GCV] ERROR: No GCV service account found - GCV OCR will fail. Upload one in Settings.')

# ── OCR / Tiling ──────────────────────────────────────────────
OCR_ENGINE      = os.getenv('OCR_ENGINE', 'gcv').lower()   # gcv | easyocr | tesseract
OCR_CONFIDENCE  = float(os.getenv('OCR_CONFIDENCE_THRESHOLD', '0.3'))
# OCR workers — default to 8 for GCV (pure network I/O, no memory penalty).
# If OCR_ENGINE=easyocr lower this to 2-4 to avoid OpenBLAS OOM on large maps.
OCR_WORKERS     = int(os.getenv('OCR_WORKERS', '8'))   # parallel tile workers
# GCV batch size — how many tiles to send in one batch_annotate_images call.
# GCV allows up to 16 per synchronous request; 16 is optimal for throughput.
# Set GCV_BATCH_SIZE=1 in .env to disable batching (debug / single-tile mode).
GCV_BATCH_SIZE  = int(os.getenv('GCV_BATCH_SIZE', '16'))
TILE_SIZE       = int(os.getenv('TILE_SIZE',   '1024'))
TILE_OVERLAP    = int(os.getenv('TILE_OVERLAP', '50'))
# Gemini API sleep between batches (seconds).
# Free tier needs ~4s (15 RPM); paid tier is fine at 1-2s (60-1000 RPM).
GEMINI_BATCH_SLEEP = float(os.getenv('GEMINI_BATCH_SLEEP', '2.0'))

# ── Country / Map Origin ─────────────────────────────────────
# Controls which country-specific knowledge block is injected into the LLM prompt.
# Supported: india, uk, usa, germany, france, pakistan
# Set MAP_COUNTRY=usa in .env when processing USGS topo maps, etc.
MAP_COUNTRY     = os.getenv('MAP_COUNTRY', 'india').lower()

# ── Grid Detection ────────────────────────────────────────────
# Survey of India standard sheet = 4 cols x 4 rows.
# Override in .env if your maps use a different grid size.
GRID_DEFAULT_COLS = int(os.getenv('GRID_DEFAULT_COLS', '4'))
GRID_DEFAULT_ROWS = int(os.getenv('GRID_DEFAULT_ROWS', '4'))

# ── Paths ─────────────────────────────────────────────────────
BASE_DIR        = _DATA_DIR
MAPS_FOLDER     = BASE_DIR / os.getenv('MAPS_FOLDER',    'maps')
RESULTS_FOLDER  = BASE_DIR / os.getenv('RESULTS_FOLDER', 'results')
LOGS_FOLDER     = BASE_DIR / os.getenv('LOGS_FOLDER',    'logs')

# Create output dirs on import
RESULTS_FOLDER.mkdir(exist_ok=True)
LOGS_FOLDER.mkdir(exist_ok=True)

# ── Validation ────────────────────────────────────────────────
def validate():
    """Call this once at startup to confirm required keys are present."""
    errors = []
    if LLM_PROVIDER == 'openai' and not OPENAI_API_KEY:
        errors.append('OPENAI_API_KEY is missing in .env')
    if LLM_PROVIDER == 'gemini' and not GEMINI_API_KEY:
        errors.append('GEMINI_API_KEY is missing in .env')
    if not MAPS_FOLDER.exists():
        errors.append(f'Maps folder not found: {MAPS_FOLDER}')
    if errors:
        for e in errors:
            print(f'[CONFIG ERROR] {e}')
        raise SystemExit('Fix config errors before running the pipeline.')
    print(f'[CONFIG OK] LLM={LLM_PROVIDER.upper()}  Maps={MAPS_FOLDER}')

if __name__ == '__main__':
    validate()
