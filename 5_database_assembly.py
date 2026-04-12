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

LLM_PATH  = config.LOGS_FOLDER    / 'llm_corrections.json'
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

    # Initialize (or reset) database
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db(str(DB_PATH))

    total_inserted = 0

    for map_name, features in llm_data.items():
        if not features:
            continue

        # Flatten bbox into individual columns
        db_records = []
        for feat in features:
            bbox = feat.get('bbox', [0, 0, 0, 0])
            bbox = bbox if len(bbox) == 4 else [0, 0, 0, 0]
            db_records.append({
                'map_name':      feat.get('map_name', map_name),
                'original_text': feat.get('original_text', ''),
                'feature_name':  feat.get('feature_name', ''),
                'feature_type':  feat.get('feature_type', 'unknown'),
                'grid_reference':feat.get('grid_reference', ''),
                'confidence':    feat.get('confidence', 0.0),
                'tile_x':        0,
                'tile_y':        0,
                'bbox_x1': bbox[0], 'bbox_y1': bbox[1],
                'bbox_x2': bbox[2], 'bbox_y2': bbox[3],
            })

        n = insert_features(str(DB_PATH), db_records)
        total_inserted += n
        print(f'[DB] {map_name}: inserted {n} features')

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
