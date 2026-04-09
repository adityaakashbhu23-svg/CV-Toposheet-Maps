# CV-Toposheet: Historical Map OCR + LLM Pipeline

## Project Vision
Automating geographic data extraction from historical Survey of India topographical maps using:
- **Computer Vision + OCR**: Extract text from large, dense maps with curved/overlapping typography
- **LLM Post-processing**: Clean OCR noise, classify features (city/river/mountain), maintain accuracy 95%+
- **Grid Localization**: Detect alphanumeric grid overlays (A-1, B-2, etc.) and assign coordinates
- **Searchable Database**: Query results (e.g., "List all rivers in grid B-3")

## Architecture Overview

### Input
- JPG maps: 2-6MB, 5000x7000px (Survey of India historical sheets)

### Processing Pipeline

#### 1. **Tiling Engine** (Local)
- Split large maps into 1024x1024px tiles with 50px overlap
- No downsampling; preserves text quality
- Handles maps of any size

#### 2. **OCR Extraction** (Local)
- **Primary**: EasyOCR (Google-backed, more stable than PaddleOCR)
- **Fallback**: Tesseract (open-source, reliable)
- Extract: text, bounding boxes, confidence scores
- Output: Raw per-tile OCR results

#### 3. **Grid Detection** (Local)
- Identify alphanumeric grid overlay (rows: A/B/C/..., columns: 1/2/3/...)
- Map each OCR text to nearest grid cell
- Output: (text, grid_ref, bbox, confidence)

#### 4. **LLM Post-Processing** (Cloud: OpenAI OR Gemini)
- Input: Raw OCR text chunks per grid
- Tasks:
  - Fix OCR misspellings (e.g., "Rlver" → "River")
  - Classify feature type (settlement/waterway/mountain/landmark)
  - Remove noise (contour labels, scale text, etc.)
  - Assign final confidence (0-1)
- Output: Cleaned features with classification

#### 5. **Database Assembly** (Local)
- Combine: map_name + feature_name + grid_ref + feature_type + confidence
- Generate CSV: Map_Name, Feature_Name, Grid_Reference, Feature_Type, Confidence
- Generate JSON: Nested structure for API integration
- Store in SQLite for searchable queries

#### 6. **Query Interface** (Local)
- Search examples:
  - "List all rivers alphabetically across all maps"
  - "Find features in grid B-3 of map 63-P-14"
  - "High-confidence settlements (>0.9) in Palamau region"

### Output Files
```
maps/
  63-P-14-Palamau-1918-Preliminary-1.jpg
  ...

results/
  extracted_data.csv
  extracted_data.json
  toposheet.db (SQLite searchable database)
  
logs/
  ocr_results_raw.json (per-tile OCR debug)
  grid_detection.json (detected grid mappings)
  llm_corrections.json (LLM cleaning log)
```

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Image Tiling** | OpenCV (cv2) | Fast, reliable tile generation |
| **OCR** | EasyOCR (primary) / Tesseract (fallback) | Proven on historical docs, handles curved text |
| **Grid Detection** | CV2 contour detection + Hough lines | Identifies printed grid overlays |
| **LLM** | OpenAI GPT-4 / Google Gemini | Both APIs supported for flexibility |
| **Database** | SQLite | Lightweight, searchable, no server needed |
| **Output** | CSV + JSON | Excel-compatible + API-ready |

## Phase 1: Foundation (Immediate)
- ✅ Project structure setup
- ✅ Tiling engine (robust tile generation)
- ⏳ Dual OCR system (EasyOCR + Tesseract with fallback)
- ⏳ Grid detection (detect A-1, B-2 layout)
- ⏳ LLM integration (OpenAI + Gemini wrappers)

## Phase 2: Integration (This week)
- Combine OCR + Grid mapping
- LLM post-processing pipeline
- Database assembly and export (CSV/JSON)

## Phase 3: Query Interface (Next)
- Search database by feature type, grid, map
- Export results to spreadsheet

## File Map

```
C:\CV-Toposheet/
├── PROJECT_PLAN.md (this file)
├── config.py (API keys, settings)
├── 1_tile_maps.py (tiling engine)
├── 2_ocr_extraction.py (local OCR)
├── 3_grid_detection.py (detect grid overlay)
├── 4_llm_cleaning.py (cloud LLM post-processing)
├── 5_database_assembly.py (build database)
├── 6_query_interface.py (search database)
├── utils/
│   ├── image_utils.py
│   ├── ocr_utils.py
│   ├── llm_utils.py
│   └── db_utils.py
├── maps/
│   ├── 63-P-14-Palamau-1918-Preliminary-1.jpg
│   ├── 63-P-14-Palamau-District-1955.jpg
│   └── ... (sample maps)
├── results/
│   ├── extracted_data.csv
│   ├── extracted_data.json
│   └── toposheet.db
└── logs/
    ├── ocr_results_raw.json
    ├── grid_detection.json
    └── llm_corrections.json
```

## Key Design Decisions

1. **Hybrid Processing**: Local OCR (no per-API costs) + cloud LLM (best accuracy)
2. **Dual LLM Support**: OpenAI AND Gemini interchangeable - use env vars to switch
3. **Tiling (not resizing)**: Preserves text quality; no "squeeze" artifacts
4. **Modular pipeline**: Each phase can run independently for debugging
5. **Grid-first approach**: All features tagged with grid before database storage

## Next Steps

1. Install robust OCR dependencies
2. Build tiling engine
3. Implement grid detection
4. Set up LLM wrappers (OpenAI + Gemini)
5. Test on sample maps
