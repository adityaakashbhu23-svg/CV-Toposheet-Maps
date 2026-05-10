# config.py  –  Central configuration loader for CV-Toposheet pipeline
# Reads all settings from .env so no secrets are ever hardcoded.

import os
from utils.cpu_utils import throttler as _throttler  # starts background monitor on import
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / '.env')

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
CLAUDE_MODEL    = os.getenv('CLAUDE_MODEL',  'claude-3-5-haiku-20241022')
GROQ_MODEL      = os.getenv('GROQ_MODEL',    'llama-3.3-70b-versatile')

# Vertex AI (Google Cloud — uses service_account2.json, billed to GCP credit)
VERTEX_PROJECT  = os.getenv('VERTEX_PROJECT',  'cv-toposheet')
VERTEX_LOCATION = os.getenv('VERTEX_LOCATION', 'us-central1')
VERTEX_MODEL    = os.getenv('VERTEX_MODEL',    'gemini-2.5-flash')

# ── Google Cloud Vision ──────────────────────────────────────
_gcv_creds_rel  = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service_account.json')
# Resolve relative paths against the project root and set the env-var that
# the Google Cloud client library reads automatically.
_gcv_creds_path = Path(__file__).parent / _gcv_creds_rel
if _gcv_creds_path.exists():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_gcv_creds_path)

# ── OCR / Tiling ──────────────────────────────────────────────
OCR_ENGINE      = os.getenv('OCR_ENGINE', 'gcv').lower()   # gcv | easyocr | tesseract
OCR_CONFIDENCE  = float(os.getenv('OCR_CONFIDENCE_THRESHOLD', '0.3'))
# OCR workers — default to 4; override with OCR_WORKERS=N in .env
# Keeping this at 4 (not cpu_count) avoids OpenBLAS OOM on large maps
OCR_WORKERS     = int(os.getenv('OCR_WORKERS', '4'))  # parallel tile workers
TILE_SIZE       = int(os.getenv('TILE_SIZE',   '1024'))
TILE_OVERLAP    = int(os.getenv('TILE_OVERLAP', '50'))

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
BASE_DIR        = Path(__file__).parent
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
