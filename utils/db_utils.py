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
    bbox_y2         INTEGER,
    lat_min         REAL,
    lat_max         REAL,
    lon_min         REAL,
    lon_max         REAL,
    sheet_ref       TEXT,    -- SOI canonical ref e.g. "72P/16"
    district        TEXT,    -- district name from filename
    survey_year     TEXT,    -- year map was surveyed/published (e.g. "1934-35" or "1972")
    map_scale       INTEGER, -- 63360 (1-inch) or 50000 or 25000
    verified        INTEGER DEFAULT 0   -- 0=unverified, 1=human-confirmed correct
);
CREATE INDEX IF NOT EXISTS idx_year      ON features(survey_year);
CREATE INDEX IF NOT EXISTS idx_district  ON features(district);
CREATE INDEX IF NOT EXISTS idx_sheet     ON features(sheet_ref);
CREATE INDEX IF NOT EXISTS idx_map        ON features(map_name);
CREATE INDEX IF NOT EXISTS idx_type       ON features(feature_type);
CREATE INDEX IF NOT EXISTS idx_grid       ON features(grid_reference);
CREATE INDEX IF NOT EXISTS idx_name       ON features(feature_name);
CREATE INDEX IF NOT EXISTS idx_conf       ON features(confidence);

-- FTS5 virtual table: searches feature_name  AND  original_text
-- so both the normalised name AND the exact text as written on the map are found.
CREATE VIRTUAL TABLE IF NOT EXISTS features_fts USING fts5(
    feature_name,
    original_text,
    map_name,
    content='features',
    content_rowid='id'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS features_ai AFTER INSERT ON features BEGIN
    INSERT INTO features_fts(rowid, feature_name, original_text, map_name)
    VALUES (new.id, new.feature_name, new.original_text, new.map_name);
END;
CREATE TRIGGER IF NOT EXISTS features_au AFTER UPDATE ON features BEGIN
    INSERT INTO features_fts(features_fts, rowid, feature_name, original_text, map_name)
    VALUES ('delete', old.id, old.feature_name, old.original_text, old.map_name);
    INSERT INTO features_fts(rowid, feature_name, original_text, map_name)
    VALUES (new.id, new.feature_name, new.original_text, new.map_name);
END;
CREATE TRIGGER IF NOT EXISTS features_ad AFTER DELETE ON features BEGIN
    INSERT INTO features_fts(features_fts, rowid, feature_name, original_text, map_name)
    VALUES ('delete', old.id, old.feature_name, old.original_text, old.map_name);
END;
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create the database and table schema if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript(DB_SCHEMA)
    conn.executescript(FTS_TRIGGERS)
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
                 bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                 lat_min, lat_max, lon_min, lon_max,
                 sheet_ref, district, survey_year, map_scale)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                f.get('lat_min'),
                f.get('lat_max'),
                f.get('lon_min'),
                f.get('lon_max'),
                f.get('sheet_ref'),
                f.get('district'),
                f.get('survey_year'),
                f.get('map_scale'),
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
            'name':           row['feature_name'],
            'original_text':  row['original_text'],
            'grid':           row['grid_reference'],
            'confidence':     row['confidence'],
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
        SELECT map_name, original_text, feature_name, feature_type,
               grid_reference, confidence
        FROM   features
        WHERE  {where}
        ORDER  BY feature_name
    """
    cursor = conn.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def search_fulltext(db_path: str, query: str, limit: int = 50) -> List[Dict]:
    """
    Full-text search across feature_name and map_name using the FTS5 index.
    Supports partial words and multi-word queries.
    Returns results ranked by relevance (BM25 score).
    Example: search_fulltext(db, "rampur river") finds all river entries
             with 'rampur' in the name across all maps.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("""
            SELECT f.map_name, f.original_text, f.feature_name, f.feature_type,
                   f.grid_reference, f.confidence,
                   rank AS relevance_score
            FROM   features_fts
            JOIN   features f ON features_fts.rowid = f.id
            WHERE  features_fts MATCH ?
              AND  f.feature_type != 'noise'
            ORDER  BY rank
            LIMIT  ?
        """, (query, limit))
        rows = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f'[DB/FTS] Full-text search error: {e}')
        # Fallback to LIKE search
        rows = query_features(db_path, search_name=query)
    conn.close()
    return rows


def get_stats(db_path: str) -> Dict:
    """
    Return a summary statistics dict for the whole database:
    - total features, per-type counts
    - per-map counts
    - confidence distribution
    - top 10 most common feature names
    """
    conn = get_connection(db_path)

    stats: Dict = {}

    # Total
    stats['total'] = conn.execute(
        "SELECT COUNT(*) FROM features WHERE feature_type != 'noise'"
    ).fetchone()[0]

    # Per type
    rows = conn.execute("""
        SELECT feature_type, COUNT(*) AS cnt
        FROM   features
        WHERE  feature_type != 'noise'
        GROUP  BY feature_type
        ORDER  BY cnt DESC
    """).fetchall()
    stats['by_type'] = {r['feature_type']: r['cnt'] for r in rows}

    # Per map
    rows = conn.execute("""
        SELECT map_name, COUNT(*) AS cnt
        FROM   features
        WHERE  feature_type != 'noise'
        GROUP  BY map_name
        ORDER  BY cnt DESC
    """).fetchall()
    stats['by_map'] = {r['map_name']: r['cnt'] for r in rows}

    # Confidence bands
    high   = conn.execute("SELECT COUNT(*) FROM features WHERE confidence >= 0.8 AND feature_type != 'noise'").fetchone()[0]
    medium = conn.execute("SELECT COUNT(*) FROM features WHERE confidence >= 0.5 AND confidence < 0.8 AND feature_type != 'noise'").fetchone()[0]
    low    = conn.execute("SELECT COUNT(*) FROM features WHERE confidence < 0.5  AND feature_type != 'noise'").fetchone()[0]
    avg    = conn.execute("SELECT AVG(confidence) FROM features WHERE feature_type != 'noise'").fetchone()[0]
    stats['confidence'] = {
        'high_0.8+':    high,
        'medium_0.5-0.8': medium,
        'low_under_0.5':  low,
        'average':        round(avg or 0, 3),
    }

    # Top 10 most common names (excluding grid-label noise)
    rows = conn.execute("""
        SELECT feature_name, COUNT(*) AS cnt
        FROM   features
        WHERE  feature_type != 'noise'
          AND  LENGTH(feature_name) > 2
        GROUP  BY LOWER(feature_name)
        ORDER  BY cnt DESC
        LIMIT  10
    """).fetchall()
    stats['top_names'] = [(r['feature_name'], r['cnt']) for r in rows]

    conn.close()
    return stats
