# utils/db_utils.py  –  SQLite database creation, insertion, and querying

import sqlite3
import csv
import json
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    map_name        TEXT NOT NULL,
    original_text   TEXT,
    feature_name    TEXT NOT NULL,
    feature_type    TEXT,
    grid_reference  TEXT,
    confidence      REAL,
    tile_x          INTEGER,
    tile_y          INTEGER,
    bbox_x1         INTEGER,
    bbox_y1         INTEGER,
    bbox_x2         INTEGER,
    bbox_y2         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_map        ON features(map_name);
CREATE INDEX IF NOT EXISTS idx_type       ON features(feature_type);
CREATE INDEX IF NOT EXISTS idx_grid       ON features(grid_reference);
CREATE INDEX IF NOT EXISTS idx_name       ON features(feature_name);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create the database and table schema if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript(DB_SCHEMA)
    conn.commit()
    conn.close()
    print(f'[DB] Initialized database: {db_path}')


def insert_features(db_path: str, features: List[Dict]) -> int:
    """Insert a list of feature dicts into the database. Returns count inserted."""
    if not features:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    inserted = 0
    for f in features:
        try:
            cursor.execute("""
                INSERT INTO features
                (map_name, original_text, feature_name, feature_type,
                 grid_reference, confidence, tile_x, tile_y,
                 bbox_x1, bbox_y1, bbox_x2, bbox_y2)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f.get('map_name', ''),
                f.get('original_text', ''),
                f.get('feature_name', ''),
                f.get('feature_type', ''),
                f.get('grid_reference', ''),
                f.get('confidence', 0.0),
                f.get('tile_x', 0),
                f.get('tile_y', 0),
                f.get('bbox_x1', 0),
                f.get('bbox_y1', 0),
                f.get('bbox_x2', 0),
                f.get('bbox_y2', 0),
            ))
            inserted += 1
        except Exception as e:
            print(f'[DB] Insert error: {e}')
    conn.commit()
    conn.close()
    return inserted


def export_csv(db_path: str, csv_path: str) -> None:
    """Export all non-noise features to a CSV file."""
    conn = get_connection(db_path)
    df = pd.read_sql_query("""
        SELECT map_name        AS Map_Name,
               feature_name   AS Feature_Name,
               feature_type   AS Feature_Type,
               grid_reference AS Grid_Reference,
               confidence     AS Confidence,
               original_text  AS Original_OCR
        FROM   features
        WHERE  feature_type != 'noise'
        ORDER  BY map_name, feature_type, feature_name
    """, conn)
    conn.close()
    df.to_csv(csv_path, index=False, encoding='utf-8')
    print(f'[DB] CSV exported: {csv_path}  ({len(df)} rows)')


def export_json(db_path: str, json_path: str) -> None:
    """Export all non-noise features to a structured JSON file."""
    conn = get_connection(db_path)
    df = pd.read_sql_query("""
        SELECT map_name, feature_name, feature_type,
               grid_reference, confidence, original_text
        FROM   features
        WHERE  feature_type != 'noise'
        ORDER  BY map_name, feature_type, feature_name
    """, conn)
    conn.close()

    # Group by map
    output = {}
    for _, row in df.iterrows():
        map_key = row['map_name']
        if map_key not in output:
            output[map_key] = {}
        ftype = row['feature_type'] or 'unknown'
        if ftype not in output[map_key]:
            output[map_key][ftype] = []
        output[map_key][ftype].append({
            'name':      row['feature_name'],
            'grid':      row['grid_reference'],
            'confidence': row['confidence'],
        })

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'[DB] JSON exported: {json_path}')


def query_features(
    db_path: str,
    map_name: Optional[str] = None,
    feature_type: Optional[str] = None,
    grid_ref: Optional[str] = None,
    min_confidence: float = 0.0,
    search_name: Optional[str] = None,
) -> List[Dict]:
    """
    Query the database with optional filters.
    Returns a list of feature dicts.
    """
    conn = get_connection(db_path)
    clauses = ["feature_type != 'noise'"]
    params = []

    if map_name:
        clauses.append('LOWER(map_name) LIKE ?')
        params.append(f'%{map_name.lower()}%')
    if feature_type:
        clauses.append('LOWER(feature_type) = ?')
        params.append(feature_type.lower())
    if grid_ref:
        clauses.append('LOWER(grid_reference) = ?')
        params.append(grid_ref.lower())
    if min_confidence > 0:
        clauses.append('confidence >= ?')
        params.append(min_confidence)
    if search_name:
        clauses.append('LOWER(feature_name) LIKE ?')
        params.append(f'%{search_name.lower()}%')

    where = ' AND '.join(clauses)
    sql = f"""
        SELECT map_name, feature_name, feature_type,
               grid_reference, confidence
        FROM   features
        WHERE  {where}
        ORDER  BY feature_name
    """
    cursor = conn.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
