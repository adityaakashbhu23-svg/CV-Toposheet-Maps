# CV-Toposheet: Historical Map Digitization Pipeline

## Project Vision
Automated extraction of geographic features from Survey of India topographical maps using:
- **Computer Vision + OCR**: Extract text from large, dense maps (7500x10000px+)
- **Multi-LLM Post-processing**: Clean OCR noise, classify features (settlement/river/mountain/landmark)
- **Grid Localization**: Detect alphanumeric grid overlays and assign coordinates
- **Web Interface**: Upload maps, watch live processing, view/export results as HTML table

---

## Current Status: FULLY OPERATIONAL ✅

All 8 pipeline phases implemented and working. Flask web interface live at `localhost:5000`.

---

## Architecture Overview

### Input
- JPG / PNG / TIF maps: up to 500 MB, any resolution
- Survey of India sheets (standard 4x4 grid), UK OS, USGS, German, French, Pakistan maps supported
- Multi-map upload: process multiple maps in one session

### Processing Pipeline

#### Phase 1 — Tiling Engine (`1_tile_maps.py`)
- Splits large maps into 1024x1024px tiles with 50px overlap
- No downsampling — preserves full text quality
- Generates `tiles_manifest.json` for incremental re-processing
- Incremental: skips maps already tiled

#### Phase 2 — OCR Extraction (`2_ocr_extraction.py`)
- **Primary**: Google Cloud Vision (GCV) — highest accuracy, parallel tile processing
- **Fallback 1**: EasyOCR — thread-safe via lock, GPU-accelerated if available
- **Fallback 2**: Tesseract — open-source offline fallback
- Parallel processing via `ThreadPoolExecutor` using all available CPU cores
- `ThermalThrottler` daemon monitors CPU temp/load, auto-reduces workers if system runs hot
- GCV client singleton — initialised once, reused across all tiles
- Output: raw per-tile detections with bounding boxes and confidence scores

#### Phase 3 — Grid Detection (`3_grid_detection.py`)
- Detects alphanumeric grid overlay (rows A/B/C..., columns 1/2/3...)
- Two methods: OCR-based grid text recognition + image Hough line scan
- Assigns each detected text to nearest grid cell
- Output: `grid_detection.json` with (text, grid_ref, bbox, confidence)

#### Phase 4 — LLM Cleaning (`4_llm_cleaning.py`) — 7 providers supported
- Input: raw OCR text chunks
- Tasks: fix misspellings, classify feature type, remove noise (contour labels, scale text), assign confidence
- **7 LLM providers** all wired and working:

| Provider | Model | Notes |
|----------|-------|-------|
| Vertex AI | gemini-2.5-flash | GCP service account, best quality |
| Gemini Direct (key 1) | gemini-2.5-flash | Direct API key |
| Gemini Direct (key 2) | gemini-2.5-flash | Second key for quota |
| OpenAI | gpt-4o-mini | Reliable paid API |
| Claude | claude-3-5-haiku | Anthropic API |
| Grok | grok-3-mini | xAI API |
| Groq | llama-3.1-8b-instant | Free tier, fast |

- **Ensemble mode**: all 7 run in parallel via `ThreadPoolExecutor`, results merged by majority vote
- Voting: `feature_type` = plurality vote, `cleaned` = most common text, `confidence` = avg × agreement ratio
- Country-specific prompts injected per map (India, UK, USA, Germany, France, Pakistan)
- Incremental: skips maps already cleaned

#### Phase 5 — Database Assembly (`5_database_assembly.py`)
- Combines: map_name + feature_name + grid_ref + feature_type + confidence
- Stores in SQLite (`toposheet.db`) — per-session, fully isolated
- Exports: `extracted_data.csv` + `extracted_data.json`

#### Export — HTML Results Table (`export_table.py`)
- Generates a styled HTML table from the session database
- Auto-runs at end of pipeline
- Also regenerates on-demand when user clicks View Results (if HTML is missing)
- Shows: map name, feature name, type, grid ref, confidence

#### Phase 6 — Query Interface (`6_query_interface.py`)
- Search by feature type, grid reference, map name
- Examples: "List all rivers", "Find settlements in grid B-3", "High confidence (>0.9) features"

#### Phase 7 — Gold Standard (`7_gold_standard.py`)
- Random sample of extracted features sent for human review
- Measures pipeline accuracy

#### Phase 8 — Active Learning (`8_active_learning.py`)
- Flags low-confidence items for targeted re-review
- Feeds corrected labels back to improve confidence thresholds

---

## Web Interface (`app.py` — Flask)

### User Flow
```
1. Open localhost:5000
2. Upload 1 or more map images (JPG/PNG/TIF)
3. Choose processing model (7 options)
4. Click "Process Map"
5. Watch live progress stream: Tiling → OCR → Grid → LLM → Database → Export
6. Click "View Results" → HTML table of all extracted features
```

### Model Options (7)
| # | Option | LLM | OCR | Notes |
|---|--------|-----|-----|-------|
| 1 | Best Quality | Vertex AI Gemini 2.5 Flash | GCV | Default, highest accuracy |
| 2 | Fast | Groq LLaMA 3.1 8B | GCV | Free, fastest |
| 3 | All LLMs Together | All 7 in parallel | GCV | Majority vote, most confident |
| 4 | OpenAI GPT-4o | GPT-4o-mini | GCV | Reliable paid API |
| 5 | Claude Haiku | Claude 3.5 Haiku | GCV | Anthropic API |
| 6 | Grok (xAI) | Grok-3-mini | GCV | xAI API |
| 7 | Offline OCR | Groq LLaMA | EasyOCR | No Google Cloud needed |

### Session Isolation
- Each upload = unique 12-char session ID (`uuid4().hex[:12]`)
- All data stored under `maps/<session_id>/`, `results/<session_id>/`, `logs/<session_id>/`
- Upload 1 map → View Results shows that map's features only
- Upload 3 maps together → same session → all 3 processed → results show all 3 combined
- Never mixed with other upload sessions

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Web Framework | Flask 2.x (threaded, SSE streaming) |
| Image Tiling | OpenCV (cv2) |
| OCR Primary | Google Cloud Vision API (singleton client) |
| OCR Fallback | EasyOCR (thread-safe) + Tesseract |
| Grid Detection | CV2 contour detection + Hough lines |
| LLM Providers | Vertex AI, Gemini, OpenAI, Claude, Grok, Groq |
| Ensemble | ThreadPoolExecutor + majority vote |
| CPU Throttle | ThermalThrottler daemon (`utils/cpu_utils.py`) |
| Database | SQLite (per-session) |
| Output | CSV + JSON + styled HTML table |
| Country Prompts | India, UK, USA, Germany, France, Pakistan |

---

## File Structure

```
C:\CV- Toposheet\
├── app.py                  Flask web interface (upload, SSE stream, results)
├── config.py               Central config — loads .env, API keys, paths
├── process_new_maps.py     Main pipeline runner (phases 1-5)
├── export_table.py         HTML/CSV results table generator
├── run_pipeline.py         CLI pipeline runner
├── 1_tile_maps.py          Phase 1: tiling
├── 2_ocr_extraction.py     Phase 2: OCR
├── 3_grid_detection.py     Phase 3: grid detection
├── 4_llm_cleaning.py       Phase 4: LLM cleaning
├── 5_database_assembly.py  Phase 5: database
├── 6_query_interface.py    Phase 6: search CLI
├── 7_gold_standard.py      Phase 7: human review
├── 8_active_learning.py    Phase 8: active learning
├── 9_symbol_detector.py    Symbol/icon detection
├── utils/
│   ├── cpu_utils.py        ThermalThrottler daemon
│   ├── ocr_utils.py        GCV singleton, EasyOCR thread lock
│   ├── llm_utils.py        All 7 LLM providers + ensemble voting
│   └── db_utils.py         SQLite helpers
├── prompts/
│   ├── india.txt           Country-specific LLM knowledge prompts
│   ├── uk.txt
│   ├── usa.txt
│   ├── germany.txt
│   ├── france.txt
│   ├── pakistan.txt
│   └── spelling_variants.json
├── maps/
│   └── <session_id>/       One folder per upload session
├── results/
│   └── <session_id>/
│       ├── toposheet.db
│       ├── extracted_data.csv
│       ├── extracted_data.json
│       └── table_export.html
├── logs/
│   └── <session_id>/
│       ├── tiles_manifest.json
│       ├── ocr_results_raw.json
│       ├── grid_detection.json
│       └── llm_corrections.json
├── .env                    API keys (not in git)
├── service_account.json    GCP service account — GCV OCR
├── service_account2.json   GCP service account — Vertex AI
└── requirements.txt
```

---

## API Keys Configured

| Service | Status |
|---------|--------|
| GROQ_API_KEY | ✅ |
| OPENAI_API_KEY | ✅ |
| GEMINI_API_KEY | ✅ |
| GEMINI_API_KEY_2 | ✅ |
| CLAUDE_API_KEY | ✅ |
| GROK_API_KEY | ✅ |
| VERTEX_PROJECT (service_account2.json) | ✅ |
| GCV OCR (service_account.json) | ✅ |

---

## Key Design Decisions

1. **Session isolation**: every upload is fully independent — own maps, DB, results, logs
2. **Ensemble voting**: 7 LLMs in parallel, majority vote per feature = highest accuracy
3. **Tiling not resizing**: preserves full text quality on large maps
4. **GCV singleton**: initialised once, reused — avoids repeated auth overhead
5. **ThermalThrottler**: auto-scales workers based on CPU temperature and load
6. **Incremental pipeline**: each phase skips already-processed maps — safe to re-run after failure
7. **PYTHONIOENCODING=utf-8**: set in all subprocess calls — prevents Windows encoding crashes
8. **Country prompts**: injected per map into LLM for region-specific spelling and feature knowledge
