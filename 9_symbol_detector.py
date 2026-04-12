# 9_symbol_detector.py  –  Custom Map Symbol Detector
#
# Crops symbol images from map tiles to build an AutoML Vision training dataset.
# Also runs inference using a trained AutoML model (if available).
#
# WORKFLOW:
#
#   --- PHASE A: Build Training Dataset (one-time) ---
#   Step 1:  python 9_symbol_detector.py crop-symbols
#            → Crops candidate symbol patches around known landmark features
#            → Saves to results/symbol_dataset/<map_name>/<feature_type>/
#
#   Step 2:  Manually review results/symbol_dataset/ in Explorer
#            Move any bad crops to results/symbol_dataset/rejected/
#
#   Step 3:  python 9_symbol_detector.py export-automl
#            → Creates AutoML Vision CSV import file for Google Cloud
#
#   Step 4:  Upload to Google Cloud AutoML Vision (console.cloud.google.com)
#            → Vision → AutoML → Image Classification → Import CSV
#
#   --- PHASE B: Detect Symbols in New Maps (after model trains) ---
#   Step 5:  python 9_symbol_detector.py detect <map_image.jpg> <automl_endpoint_id>
#            → Scans every tile, calls AutoML, saves detected symbols to DB

import sys
import json
import csv
import sqlite3
import shutil
from pathlib import Path
from typing import List, Dict, Tuple

import cv2
import numpy as np

import config

DB_PATH        = config.RESULTS_FOLDER / 'toposheet.db'
TILES_DIR      = config.LOGS_FOLDER / 'tiles'
SYMBOL_DIR     = config.RESULTS_FOLDER / 'symbol_dataset'
AUTOML_CSV     = config.RESULTS_FOLDER / 'automl_import.csv'
SYMBOL_LOG     = config.RESULTS_FOLDER / 'symbol_detections.json'

# Symbol patch size around a feature's bounding box (pixels, added as padding)
SYMBOL_PADDING = 32

# Target symbol types to crop for training
SYMBOL_CLASSES = ['landmark', 'settlement', 'river', 'mountain', 'forest', 'road']


# ─────────────────────────────────────────────────────────────────────────────
def cmd_crop_symbols():
    """
    Crop symbol image patches from tiles, grouped by feature_type.
    Uses bbox coordinates stored in toposheet.db to locate each feature on its tile.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute("""
        SELECT id, map_name, feature_name, feature_type,
               tile_x, tile_y, bbox_x1, bbox_y1, bbox_x2, bbox_y2, confidence
        FROM features
        WHERE feature_type IN ({})
          AND confidence >= 0.7
          AND bbox_x1 > 0 AND bbox_y1 > 0
        ORDER BY feature_type, confidence DESC
    """.format(','.join('?' * len(SYMBOL_CLASSES))), SYMBOL_CLASSES)

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    print(f"[Symbol Detector] Found {len(rows)} high-confidence features with bbox data")

    # Create output directories
    SYMBOL_DIR.mkdir(parents=True, exist_ok=True)
    (SYMBOL_DIR / 'rejected').mkdir(exist_ok=True)
    for cls in SYMBOL_CLASSES:
        (SYMBOL_DIR / cls).mkdir(exist_ok=True)

    cropped = 0
    skipped = 0
    tile_cache: Dict[str, np.ndarray] = {}

    for r in rows:
        # Find the tile image file
        tile_path = _find_tile(r['map_name'], r['tile_x'], r['tile_y'])
        if tile_path is None:
            skipped += 1
            continue

        # Load tile (cache to avoid re-reading same file)
        if str(tile_path) not in tile_cache:
            img = cv2.imread(str(tile_path))
            if img is None:
                skipped += 1
                continue
            tile_cache[str(tile_path)] = img
        img = tile_cache[str(tile_path)]

        # Crop the symbol patch with padding
        h, w = img.shape[:2]
        x1 = max(0, r['bbox_x1'] - SYMBOL_PADDING)
        y1 = max(0, r['bbox_y1'] - SYMBOL_PADDING)
        x2 = min(w, r['bbox_x2'] + SYMBOL_PADDING)
        y2 = min(h, r['bbox_y2'] + SYMBOL_PADDING)

        if x2 <= x1 or y2 <= y1:
            skipped += 1
            continue

        patch = img[y1:y2, x1:x2]
        if patch.shape[0] < 16 or patch.shape[1] < 16:
            skipped += 1
            continue

        # Save as: results/symbol_dataset/<type>/<id>_<feature_name>.jpg
        safe_name = _safe_filename(r['feature_name'])
        out_path  = SYMBOL_DIR / r['feature_type'] / f"{r['id']}_{safe_name}.jpg"
        cv2.imwrite(str(out_path), patch)
        cropped += 1

    print(f"[Symbol Detector] Cropped {cropped} symbol patches → {SYMBOL_DIR}")
    print(f"[Symbol Detector] Skipped {skipped} (no tile file or invalid bbox)")
    print()
    print("  NEXT STEPS:")
    print("  1. Open results/symbol_dataset/ in Windows Explorer")
    print("  2. Subfolders = classes: landmark/, settlement/, river/, etc.")
    print("  3. Delete any bad crops (wrong symbol, too blurry)")
    print("  4. Run: python 9_symbol_detector.py export-automl")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_export_automl():
    """
    Create a CSV file for importing into Google Cloud AutoML Vision.
    Format: gs://BUCKET/path/to/image.jpg,label
    """
    if not SYMBOL_DIR.exists():
        print("[Symbol Detector] No symbol dataset found. Run 'crop-symbols' first.")
        return

    rows = []
    for cls_dir in SYMBOL_DIR.iterdir():
        if not cls_dir.is_dir() or cls_dir.name == 'rejected':
            continue
        label = cls_dir.name
        for img_file in cls_dir.glob('*.jpg'):
            # AutoML CSV can use local paths for reference; user replaces with gs:// URIs
            rows.append({
                'set':   'TRAIN',  # can be TRAIN/VALIDATE/TEST
                'path':  str(img_file),
                'label': label,
            })

    if not rows:
        print("[Symbol Detector] No images found in symbol_dataset/.")
        return

    # Split 80% train / 10% validate / 10% test
    import random
    random.shuffle(rows)
    n = len(rows)
    for i, r in enumerate(rows):
        if i < int(n * 0.8):
            r['set'] = 'TRAIN'
        elif i < int(n * 0.9):
            r['set'] = 'VALIDATE'
        else:
            r['set'] = 'TEST'

    with open(AUTOML_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['set', 'path', 'label'])
        writer.writeheader()
        writer.writerows(rows)

    counts: dict = {}
    for r in rows:
        counts[r['label']] = counts.get(r['label'], 0) + 1

    print(f"[Symbol Detector] Exported {len(rows)} images → {AUTOML_CSV}")
    print()
    print("  Class distribution:")
    for label, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {label:<20} {n:>4} images")
    print()
    print("  NEXT STEPS FOR AutoML Vision:")
    print("  1. Create a GCS bucket:  gsutil mb gs://cv-toposheet-symbols")
    print("  2. Upload symbol images: gsutil -m cp -r results/symbol_dataset gs://cv-toposheet-symbols/")
    print("  3. Edit automl_import.csv — replace local paths with  gs://cv-toposheet-symbols/...")
    print("  4. Go to: console.cloud.google.com → Vision → AutoML Image Classification")
    print("     → New Dataset → Import → CSV file → Upload automl_import.csv")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_detect(map_image_path: str, endpoint_id: str):
    """
    Run inference on a new map using a trained AutoML Vision endpoint.
    Scans every tile and highlights detected symbol positions.
    """
    try:
        from google.cloud import automl_v1
    except ImportError:
        print("[Symbol Detector] google-cloud-automl not installed.")
        print("  Run: pip install google-cloud-automl")
        return

    map_path = Path(map_image_path)
    if not map_path.exists():
        print(f"[Symbol Detector] Map not found: {map_image_path}")
        return

    img = cv2.imread(str(map_path))
    if img is None:
        print(f"[Symbol Detector] Could not read image: {map_image_path}")
        return

    print(f"[Symbol Detector] Scanning {map_path.name} for symbols...")

    from utils.image_utils import generate_tiles

    client      = automl_v1.PredictionServiceClient()
    project_id  = config.VERTEX_PROJECT
    results: List[Dict] = []

    tile_count = 0
    detect_count = 0

    for tile, x, y, x2, y2 in generate_tiles(img):
        tile_count += 1
        # Encode tile to JPEG bytes
        _, buf = cv2.imencode('.jpg', tile)
        payload = automl_v1.ExamplePayload(
            image=automl_v1.Image(image_bytes=buf.tobytes())
        )
        name = f"projects/{project_id}/locations/us-central1/models/{endpoint_id}"
        response = client.predict(name=name, payload=payload)

        for annotation in response.payload:
            label = annotation.display_name
            score = annotation.classification.score
            if score >= 0.5:
                results.append({
                    'map_name':      map_path.stem,
                    'feature_type':  label,
                    'confidence':    round(score, 3),
                    'tile_x':        x,
                    'tile_y':        y,
                })
                detect_count += 1

    # Save results
    with open(SYMBOL_LOG, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"[Symbol Detector] Scanned {tile_count} tiles → {detect_count} symbols detected")
    print(f"[Symbol Detector] Results → {SYMBOL_LOG}")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_stats():
    """Show stats on the current symbol dataset."""
    if not SYMBOL_DIR.exists():
        print("[Symbol Detector] No symbol dataset. Run 'crop-symbols' first.")
        return

    total = 0
    print(f"\n{'='*45}")
    print(f"  SYMBOL DATASET STATS")
    print(f"{'='*45}")
    for cls_dir in sorted(SYMBOL_DIR.iterdir()):
        if not cls_dir.is_dir() or cls_dir.name == 'rejected':
            continue
        n = len(list(cls_dir.glob('*.jpg')))
        total += n
        bar = '█' * min(n // 5, 40)
        print(f"  {cls_dir.name:<20} {n:>4}  {bar}")
    rejected = len(list((SYMBOL_DIR / 'rejected').glob('*.jpg'))) if (SYMBOL_DIR / 'rejected').exists() else 0
    print(f"  {'rejected':<20} {rejected:>4}")
    print(f"  {'─'*40}")
    print(f"  {'TOTAL':<20} {total:>4}")
    print()
    if total < 100:
        print(f"  ⚠ Need at least 100 images per class for AutoML training.")
    else:
        print(f"  Ready for AutoML. Run: python 9_symbol_detector.py export-automl")
    print()


# ─────────────────────────────────────────────────────────────────────────────
def _find_tile(map_name: str, tile_x: int, tile_y: int) -> Path | None:
    """Locate a saved tile file from Phase 1."""
    # Try common naming patterns used by Phase 1
    for pattern in [
        f"{map_name}_tile_{tile_x}_{tile_y}.jpg",
        f"{map_name}_tile_{tile_x}_{tile_y}.png",
        f"tile_{tile_x}_{tile_y}.jpg",
    ]:
        candidate = TILES_DIR / map_name / pattern
        if candidate.exists():
            return candidate
        candidate = TILES_DIR / pattern
        if candidate.exists():
            return candidate
    return None


def _safe_filename(name: str) -> str:
    """Remove characters not safe for filenames."""
    return ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name).strip()[:50]


# ─────────────────────────────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'

    if cmd == 'crop-symbols':
        cmd_crop_symbols()
    elif cmd == 'export-automl':
        cmd_export_automl()
    elif cmd == 'detect':
        if len(sys.argv) < 4:
            print("Usage: python 9_symbol_detector.py detect <map.jpg> <automl_endpoint_id>")
        else:
            cmd_detect(sys.argv[2], sys.argv[3])
    elif cmd == 'stats':
        cmd_stats()
    else:
        print("Usage: python 9_symbol_detector.py [crop-symbols | export-automl | stats | detect <map> <endpoint>]")
        print()
        print("  crop-symbols     Crop symbol patches from tiles using DB bbox data")
        print("  stats            Show symbol dataset statistics")
        print("  export-automl    Export AutoML Vision import CSV")
        print("  detect <map> <endpoint_id>   Run symbol detection on a new map")


if __name__ == '__main__':
    main()
