# 5_database_assembly.py  –  Phase 5: Build SQLite DB + export CSV and JSON
#
# Usage: python 5_database_assembly.py
#
# Reads:   logs/llm_corrections.json
# Output:  results/toposheet.db
#          results/extracted_data.csv
#          results/extracted_data.json

import json
import sys
from pathlib import Path

import config
from utils.db_utils import init_db, insert_features, export_csv, export_json
from utils.metadata_utils import parse_soi_filename

LLM_PATH   = config.LOGS_FOLDER    / 'llm_corrections.json'
GRID_PATH  = config.LOGS_FOLDER    / 'grid_detection.json'   # for cell coordinates
DB_PATH   = config.RESULTS_FOLDER / 'toposheet.db'
CSV_PATH  = config.RESULTS_FOLDER / 'extracted_data.csv'
JSON_PATH = config.RESULTS_FOLDER / 'extracted_data.json'


def build_database() -> None:
    config.validate()

    if not LLM_PATH.exists():
        print(f'[DB] LLM corrections not found: {LLM_PATH}')
        print('     Run 4_llm_cleaning.py first.')
        sys.exit(1)

    with open(LLM_PATH, encoding='utf-8') as f:
        llm_data = json.load(f)

    # Load cell coordinates from grid detection if available
    cell_coords_all: dict = {}   # map_name → {grid_ref → {lat_min,...}}
    if GRID_PATH.exists():
        with open(GRID_PATH, encoding='utf-8') as f:
            grid_data = json.load(f)
        for mn, gd in grid_data.items():
            cell_coords_all[mn] = gd.get('cell_coords', {})

    # Initialize (or reset) database
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db(str(DB_PATH))

    total_inserted = 0

    for map_name, features in llm_data.items():
        if not features:
            continue

        cell_coords = cell_coords_all.get(map_name, {})

        # Parse sheet metadata (block, district, year, scale) from filename
        meta = parse_soi_filename(map_name)
        sheet_ref   = meta.get('sheet_ref')
        district    = meta.get('district') or ''
        survey_year = meta.get('year')
        map_scale   = meta.get('scale_guess')

        # Flatten bbox into individual columns + attach coordinates per cell
        db_records = []
        for feat in features:
            bbox = feat.get('bbox', [0, 0, 0, 0])
            bbox = bbox if len(bbox) == 4 else [0, 0, 0, 0]
            grid_ref = feat.get('grid_reference', '')
            cell = cell_coords.get(grid_ref, {})
            db_records.append({
                'map_name':      feat.get('map_name', map_name),
                'original_text': feat.get('original_text', ''),
                'feature_name':  feat.get('feature_name', ''),
                'feature_type':  feat.get('feature_type', 'unknown'),
                'grid_reference': grid_ref,
                'confidence':    feat.get('confidence', 0.0),
                'tile_x':        0,
                'tile_y':        0,
                'bbox_x1': bbox[0], 'bbox_y1': bbox[1],
                'bbox_x2': bbox[2], 'bbox_y2': bbox[3],
                'lat_min': cell.get('lat_min'),
                'lat_max': cell.get('lat_max'),
                'lon_min': cell.get('lon_min'),
                'lon_max': cell.get('lon_max'),
                'sheet_ref':   sheet_ref,
                'district':    district,
                'survey_year': survey_year,
                'map_scale':   map_scale,
            })

        n = insert_features(str(DB_PATH), db_records)
        total_inserted += n
        coords_found = sum(1 for r in db_records if r.get('lat_min') is not None)
        meta_str = f'{district} {survey_year}' if district and survey_year else map_name
        print(f'[DB] {meta_str} ({sheet_ref or map_name}): {n} features  ({coords_found} with coords)')

    print(f'\n[DB] Total features in database: {total_inserted}')

    # Export to CSV and JSON
    export_csv(str(DB_PATH), str(CSV_PATH))
    export_json(str(DB_PATH), str(JSON_PATH))

    print('\n[DB] Results summary:')
    print(f'     Database : {DB_PATH}')
    print(f'     CSV      : {CSV_PATH}')
    print(f'     JSON     : {JSON_PATH}')


if __name__ == '__main__':
    build_database()
    print('[DB] Phase 5 complete.')
