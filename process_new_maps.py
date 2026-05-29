# process_new_maps.py  –  Incremental processor for new maps only
#
# Detects which maps in maps/ are NOT yet in grid_detection.json,
# runs phases 1-3 on those maps only, merges results into existing
# log files, then runs phase 4 (LLM) and phase 5 (database).
#
# Usage: python process_new_maps.py

import json
import sys
import re
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm

import config
from utils.image_utils import load_image, get_image_info, generate_tiles, preprocess_for_ocr_stable, tile_count
from utils.ocr_utils import ocr_tile, translate_bbox_to_global, deduplicate
from utils.llm_utils import clean_with_llm
from utils.cpu_utils import throttler as _throttler

MANIFEST_PATH  = config.LOGS_FOLDER / 'tiles_manifest.json'
OCR_RAW_PATH   = config.LOGS_FOLDER / 'ocr_results_raw.json'
GRID_PATH      = config.LOGS_FOLDER / 'grid_detection.json'
LLM_OUT_PATH   = config.LOGS_FOLDER / 'llm_corrections.json'

ROW_LABELS    = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
COLUMN_LABELS = [str(i) for i in range(1, 30)]


# ─────────────────────────────────────────────────────────────
#  Helper: find which maps still need processing
# ─────────────────────────────────────────────────────────────

def find_new_maps() -> list:
    """Return list of Path objects for maps not yet in grid_detection.json."""
    processed = set()
    if GRID_PATH.exists():
        with open(GRID_PATH, encoding='utf-8') as _f:
            processed = set(json.load(_f).keys())

    map_files_raw = (
        list(config.MAPS_FOLDER.glob('*.jpg')) +
        list(config.MAPS_FOLDER.glob('*.jpeg')) +
        list(config.MAPS_FOLDER.glob('*.JPG')) +
        list(config.MAPS_FOLDER.glob('*.JPEG')) +
        list(config.MAPS_FOLDER.glob('*.png')) +
        list(config.MAPS_FOLDER.glob('*.PNG')) +
        list(config.MAPS_FOLDER.glob('*.tif')) +
        list(config.MAPS_FOLDER.glob('*.tiff')) +
        list(config.MAPS_FOLDER.glob('*.TIF')) +
        list(config.MAPS_FOLDER.glob('*.TIFF'))
    )

    seen_stems = set()
    new_maps = []
    for p in sorted(map_files_raw):
        key = p.stem.lower()
        if key in seen_stems:
            continue
        seen_stems.add(key)
        if p.stem not in processed:
            new_maps.append(p)

    return new_maps


# ─────────────────────────────────────────────────────────────
#  Phase 1 (incremental): tile new maps, merge into manifest
# ─────────────────────────────────────────────────────────────

def phase1_tile(new_maps: list) -> dict:
    print(f'\n{"#"*60}')
    print(f'#  Phase 1 (incremental): Tiling {len(new_maps)} new map(s)')
    print(f'{"#"*60}')

    manifest = {}
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, encoding='utf-8') as _f:
            manifest = json.load(_f)

    for map_path in new_maps:
        map_name = map_path.stem
        if map_name in manifest:
            print(f'[TILE] Already in manifest, skipping: {map_name}')
            continue

        print(f'\n[TILE] Processing: {map_path.name}')
        img  = load_image(str(map_path))
        info = get_image_info(img)
        total = tile_count(img, config.TILE_SIZE, config.TILE_OVERLAP)
        print(f'       Size: {info["width"]}x{info["height"]}px -> {total} tiles')

        manifest[map_name] = {
            'file':   str(map_path),
            'width':  info['width'],
            'height': info['height'],
            'tiles':  [],
        }

        with tqdm(total=total, desc='  Tiling', unit='tile') as pbar:
            for tile_img, x, y, x2, y2 in generate_tiles(img, config.TILE_SIZE, config.TILE_OVERLAP):
                manifest[map_name]['tiles'].append({'x': x, 'y': y, 'x2': x2, 'y2': y2})
                pbar.update(1)

        print(f'       Generated {len(manifest[map_name]["tiles"])} tiles')

    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f'\n[TILE] Manifest updated: {MANIFEST_PATH}')
    return manifest


# ─────────────────────────────────────────────────────────────
#  Phase 2 (incremental): OCR new maps, merge into ocr_results_raw
# ─────────────────────────────────────────────────────────────

def phase2_ocr(new_maps: list, manifest: dict) -> dict:
    print(f'\n{"#"*60}')
    print(f'#  Phase 2 (incremental): OCR on {len(new_maps)} new map(s)')
    print(f'{"#"*60}')

    all_results = {}
    if OCR_RAW_PATH.exists():
        with open(OCR_RAW_PATH, encoding='utf-8') as _f:
            all_results = json.load(_f)

    for map_path in new_maps:
        map_name = map_path.stem
        if map_name in all_results:
            print(f'[OCR] Already processed, skipping: {map_name}')
            continue

        print(f'\n[OCR] Map: {map_name}')
        map_info   = manifest.get(map_name, {})
        total_tiles = len(map_info.get('tiles', []))

        img = load_image(map_info['file'])
        all_detections = []

        # Pre-collect all tiles so we know the total upfront
        tiles_list = list(generate_tiles(img, config.TILE_SIZE, config.TILE_OVERLAP))

        def _process_tile(args):
            tile_img, x, y, x2, y2 = args
            processed = preprocess_for_ocr_stable(tile_img)
            detections = ocr_tile(processed, config.OCR_CONFIDENCE)
            return translate_bbox_to_global(detections, x, y)

        # EasyOCR is not thread-safe – serialize for offline mode.
        # GCV is a remote cloud API — no local CPU/RAM strain, so allow more parallelism.
        # For local engines (easyocr/tesseract) cap workers to prevent OOM.
        _ocr_engine = getattr(config, 'OCR_ENGINE', 'gcv')
        workers = _throttler.get_workers(_ocr_engine)
        tile_count_n = len(tiles_list)
        if _ocr_engine == 'gcv':
            # GCV: cap at 8 concurrent requests (safe for free/paid GCV quota)
            workers = min(workers, 8)
        else:
            # Local engines: cap to avoid OpenBLAS/numpy OOM on large maps
            if tile_count_n > 60:
                workers = min(workers, 2)
            elif tile_count_n > 30:
                workers = min(workers, 4)
        print(f'  [CPU] {_throttler.status()}  ocr_workers={workers}')
        # For local OCR engines (especially EasyOCR), running inside a worker
        # thread can trigger native crashes in frozen builds. If workers resolve
        # to 1, process tiles on the main thread.
        if _ocr_engine != 'gcv' and workers <= 1:
            with tqdm(total=len(tiles_list), desc='  OCR tiles', unit='tile') as pbar:
                for t in tiles_list:
                    try:
                        all_detections.extend(_process_tile(t))
                    except Exception as e:
                        print(f'  [OCR] Tile error: {e}')
                    pbar.update(1)
        else:
            with tqdm(total=len(tiles_list), desc='  OCR tiles', unit='tile') as pbar:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_process_tile, t): t for t in tiles_list}
                    for fut in as_completed(futures):
                        try:
                            all_detections.extend(fut.result())
                        except Exception as e:
                            print(f'  [OCR] Tile error: {e}')
                        pbar.update(1)

        before = len(all_detections)
        all_detections = deduplicate(all_detections, iou_threshold=0.4)
        after = len(all_detections)
        print(f'  Raw detections: {before}  ->  after dedup: {after}')

        all_results[map_name] = all_detections

        # Save after each map so progress is kept on crash/interrupt
        with open(OCR_RAW_PATH, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f'  [OCR] Saved progress ({map_name})')

    total = sum(len(v) for v in all_results.values())
    print(f'\n[OCR] Total detections in file: {total}')
    return all_results


# ─────────────────────────────────────────────────────────────
#  Phase 3 (incremental): Grid detection, merge into grid_detection
# ─────────────────────────────────────────────────────────────

def _detect_grid_from_ocr(detections, img_width, img_height):
    import re as _re
    col_xs, row_ys = [], []
    row_pat = _re.compile(r'^[A-Z]$')
    col_pat = _re.compile(r'^\d{1,2}$')
    for d in detections:
        text = d['text'].strip().upper()
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2
        if row_pat.match(text) and (cx < 80 or cx > img_width - 80):
            row_ys.append(cy)
        if col_pat.match(text) and (cy < 80 or cy > img_height - 80):
            col_xs.append(cx)
    return sorted(set(col_xs)), sorted(set(row_ys))


def _detect_grid_from_image(img):
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    h, w = gray.shape
    edges = cv2.Canny(gray, 30, 100, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=200,
                             minLineLength=min(w, h) * 0.5, maxLineGap=20)
    col_xs, row_ys = [], []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1)))
            if angle < 5:
                row_ys.append((y1 + y2) / 2)
            elif angle > 85:
                col_xs.append((x1 + x2) / 2)

    def cluster(positions, gap=30):
        if not positions:
            return []
        positions = sorted(set(positions))
        clusters = [[positions[0]]]
        for p in positions[1:]:
            if p - clusters[-1][-1] < gap:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [sum(c) / len(c) for c in clusters]

    return cluster(col_xs), cluster(row_ys)


def _assign_grid_ref(bbox, col_xs, row_ys, img_width, img_height):
    bx1, by1, bx2, by2 = bbox
    cx = (bx1 + bx2) / 2
    cy = (by1 + by2) / 2
    if col_xs:
        col_idx = sum(1 for x in col_xs if x < cx)
    else:
        col_idx = min(int(cx / img_width * 4), 3)
    if row_ys:
        row_idx = sum(1 for y in row_ys if y < cy)
    else:
        row_idx = min(int(cy / img_height * 4), 3)
    row_label = ROW_LABELS[row_idx] if row_idx < len(ROW_LABELS) else f'R{row_idx}'
    col_label = COLUMN_LABELS[col_idx] if col_idx < len(COLUMN_LABELS) else str(col_idx + 1)
    return f'{row_label}-{col_label}'


def phase3_grid(new_maps: list, manifest: dict, ocr_results: dict) -> dict:
    print(f'\n{"#"*60}')
    print(f'#  Phase 3 (incremental): Grid detection on {len(new_maps)} new map(s)')
    print(f'{"#"*60}')

    grid_results = {}
    if GRID_PATH.exists():
        with open(GRID_PATH, encoding='utf-8') as _f:
            grid_results = json.load(_f)

    for map_path in new_maps:
        map_name = map_path.stem
        if map_name in grid_results:
            print(f'[GRID] Already processed, skipping: {map_name}')
            continue

        detections = ocr_results.get(map_name, [])
        map_info   = manifest.get(map_name, {})
        img_width  = map_info.get('width', 5000)
        img_height = map_info.get('height', 7000)

        print(f'\n[GRID] Map: {map_name}  ({len(detections)} detections)')

        col_xs, row_ys = _detect_grid_from_ocr(detections, img_width, img_height)
        if len(col_xs) < 2 or len(row_ys) < 2:
            print(f'  OCR grid: {len(col_xs)} cols, {len(row_ys)} rows — trying image scan...')
            try:
                img = load_image(map_info['file'])
                col_xs_img, row_ys_img = _detect_grid_from_image(img)
                if len(col_xs_img) > len(col_xs):
                    col_xs = col_xs_img
                if len(row_ys_img) > len(row_ys):
                    row_ys = row_ys_img
            except Exception as e:
                print(f'  Image grid detection failed: {e}. Using fallback.')

        print(f'  Grid lines: {len(col_xs)} cols, {len(row_ys)} rows')

        enriched = []
        for det in detections:
            grid_ref = _assign_grid_ref(det['bbox'], col_xs, row_ys, img_width, img_height)
            enriched.append({**det, 'grid_reference': grid_ref})

        grid_results[map_name] = {
            'col_lines':  col_xs,
            'row_lines':  row_ys,
            'detections': enriched,
        }
        print(f'  Assigned grid refs to {len(enriched)} detections')

    with open(GRID_PATH, 'w', encoding='utf-8') as f:
        json.dump(grid_results, f, ensure_ascii=False, indent=2)
    print(f'\n[GRID] grid_detection.json updated: {GRID_PATH}')
    return grid_results


# ─────────────────────────────────────────────────────────────
#  Phase 4 (incremental): LLM cleaning on new maps only
# ─────────────────────────────────────────────────────────────

def phase4_llm(new_maps: list, grid_results: dict) -> dict:
    print(f'\n{"#"*60}')
    print(f'#  Phase 4 (incremental): LLM cleaning on {len(new_maps)} new map(s)')
    print(f'{"#"*60}')

    # Auto-detect map country: Stage 1 = Gemini Vision on image, Stage 2 = OCR scoring
    import utils.llm_utils as llm_utils
    try:
        _ocr_data = {}
        if OCR_RAW_PATH.exists():
            with open(OCR_RAW_PATH, encoding='utf-8') as _f:
                _ocr_data = json.load(_f)
        _map_name_hint = ' '.join(p.stem for p in new_maps)
        # Use the first new map image for vision scan
        _map_path_hint = new_maps[0] if new_maps else None
        _country = llm_utils.detect_country_smart(
            _ocr_data,
            map_name=_map_name_hint,
            map_path=_map_path_hint,
            fallback=config.MAP_COUNTRY,
        )
        llm_utils.SYSTEM_PROMPT = llm_utils.build_system_prompt(_country)
        print(f'[LLM] Country auto-detected: {_country.upper()}')
    except Exception as _e:
        print(f'[LLM] Country detection error: {_e}')

    llm_results = {}
    if LLM_OUT_PATH.exists():
        with open(LLM_OUT_PATH, encoding='utf-8') as f:
            llm_results = json.load(f)

    new_map_names = {p.stem for p in new_maps}

    for map_name, map_data in grid_results.items():
        if map_name not in new_map_names:
            continue
        if map_name in llm_results:
            print(f'[LLM] Already cleaned, skipping: {map_name}')
            continue

        detections = map_data.get('detections', [])
        print(f'\n[LLM] Map: {map_name}  ({len(detections)} OCR detections)')

        if not detections:
            llm_results[map_name] = []
            # Save so LLM_OUT_PATH always exists even when all maps have 0 detections
            with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
                json.dump(llm_results, f, ensure_ascii=False, indent=2)
            continue

        raw_texts   = [d['text'] for d in detections]
        det_by_text = {}
        for d in detections:
            det_by_text.setdefault(d['text'], d)

        cleaned = clean_with_llm(raw_texts)

        enriched = []
        for item in cleaned:
            if item.get('feature_type') == 'noise':
                continue
            original_text = item.get('original', '')
            source_det    = det_by_text.get(original_text, {})
            enriched.append({
                'map_name':       map_name,
                'original_text':  original_text,
                'feature_name':   item.get('cleaned', original_text),
                'feature_type':   item.get('feature_type', 'unknown'),
                'confidence':     item.get('confidence', 0.0),
                'grid_reference': source_det.get('grid_reference', ''),
                'bbox':           source_det.get('bbox', []),
            })

        llm_results[map_name] = enriched
        kept    = len(enriched)
        dropped = len(cleaned) - kept
        print(f'  Features kept: {kept}  |  Noise dropped: {dropped}')

        # Save after each map
        with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(llm_results, f, ensure_ascii=False, indent=2)
        print(f'  [LLM] Progress saved.')

    total = sum(len(v) for v in llm_results.values())
    print(f'\n[LLM] Total clean features in file: {total}')

    # Always ensure the file exists so phase5 / build_database never sees a missing file
    with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(llm_results, f, ensure_ascii=False, indent=2)

    return llm_results


# ─────────────────────────────────────────────────────────────
#  Phase 5: Rebuild database
# ─────────────────────────────────────────────────────────────

def phase5_database():
    print(f'\n{"#"*60}')
    print(f'#  Phase 5: Rebuilding database')
    print(f'{"#"*60}')
    from importlib import import_module
    mod = import_module('5_database_assembly')
    mod.build_database()


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    config.validate()

    new_maps = find_new_maps()

    if not new_maps:
        print('\n[INFO] All maps already processed. Nothing to do.')
        print('       Run python 5_database_assembly.py to rebuild the database.')
        sys.exit(0)

    print(f'\n[INFO] Found {len(new_maps)} map(s) to process:')
    for p in new_maps:
        print(f'  {p.name}')

    t0 = time.time()

    # Phase 1 – Tile
    try:
        manifest = phase1_tile(new_maps)
    except Exception as e:
        print(f'\n[ERROR] Phase 1 (tiling) crashed: {e}')
        print('[ERROR] Cannot continue without tiles. Exiting.')
        sys.exit(1)

    # Phase 2 – OCR
    try:
        ocr_results = phase2_ocr(new_maps, manifest)
    except Exception as e:
        print(f'\n[ERROR] Phase 2 (OCR) crashed: {e}')
        print('[WARN]  Continuing to Phase 3 with empty OCR results.')
        ocr_results = {}

    # Phase 3 – Grid detection
    try:
        grid_results = phase3_grid(new_maps, manifest, ocr_results)
    except Exception as e:
        print(f'\n[ERROR] Phase 3 (grid detection) crashed: {e}')
        print('[WARN]  Continuing to Phase 4 with empty grid results.')
        grid_results = {}

    # Phase 4 – LLM cleaning
    try:
        phase4_llm(new_maps, grid_results)
    except Exception as e:
        print(f'\n[ERROR] Phase 4 (LLM cleaning) crashed: {e}')
        print('[WARN]  Continuing to Phase 5 (database will have 0 features).')
        # Ensure LLM output file exists so phase5 / build_database doesn't crash
        if not LLM_OUT_PATH.exists():
            with open(LLM_OUT_PATH, 'w', encoding='utf-8') as _f:
                json.dump({}, _f)

    # Phase 5 – Rebuild full database
    try:
        phase5_database()
    except Exception as e:
        print(f'\n[ERROR] Phase 5 (database build) crashed: {e}')

    elapsed = time.time() - t0
    print(f'\n{"="*60}')
    print(f'Done! Processed {len(new_maps)} new maps in {elapsed:.0f}s')
    print(f'Results: {config.RESULTS_FOLDER}')
    print('Run "python 6_query_interface.py" to search the database.')
