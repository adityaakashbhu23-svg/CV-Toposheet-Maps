# 2_ocr_extraction.py  –  Phase 2: Run OCR on every tile and collect raw detections
#
# Usage:
#   python 2_ocr_extraction.py           # full run
#   python 2_ocr_extraction.py --resume  # skip maps already checkpointed
#
# Reads:   logs/tiles_manifest.json
# Output:  logs/ocr_results_raw.json
#          logs/ocr_checkpoint/  (per-map intermediate saves for crash-recovery)
#
# Tiles within each map are processed in parallel using ThreadPoolExecutor.
# Set OCR_WORKERS=N in .env to control parallelism (default: 4).

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm

import config
from utils.image_utils import load_image, generate_tiles, preprocess_for_ocr
from utils.ocr_utils import ocr_tile, translate_bbox_to_global, deduplicate

MANIFEST_PATH    = config.LOGS_FOLDER / 'tiles_manifest.json'
OCR_RAW_PATH     = config.LOGS_FOLDER / 'ocr_results_raw.json'
CHECKPOINT_DIR   = config.LOGS_FOLDER / 'ocr_checkpoint'
OCR_WORKERS      = int(getattr(config, 'OCR_WORKERS', 4))


def _checkpoint_path(map_name: str) -> Path:
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    safe = map_name.replace('/', '_').replace('\\', '_')
    return CHECKPOINT_DIR / f'{safe}.json'


def _save_checkpoint(map_name: str, detections: list) -> None:
    path = _checkpoint_path(map_name)
    path.write_text(json.dumps(detections, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_checkpoint(map_name: str) -> list | None:
    path = _checkpoint_path(map_name)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def _process_tile(args):
    """Worker: preprocess + OCR one tile, return globally-shifted detections."""
    tile_img, x, y, x2, y2 = args
    processed = preprocess_for_ocr(tile_img)
    detections = ocr_tile(processed, config.OCR_CONFIDENCE)
    return translate_bbox_to_global(detections, x, y)


def run_ocr(resume: bool = False) -> dict:
    """
    For each map in the manifest, re-generate tiles on-the-fly and run OCR.
    Tiles are processed in parallel (OCR_WORKERS threads).
    If resume=True, maps that already have a checkpoint file are skipped.
    Stores global-coordinate detections in ocr_results_raw.json.
    """
    config.validate()

    if not MANIFEST_PATH.exists():
        print(f'[OCR] Manifest not found: {MANIFEST_PATH}')
        print('      Run 1_tile_maps.py first.')
        sys.exit(1)

    with open(MANIFEST_PATH, encoding='utf-8') as f:
        manifest = json.load(f)

    # Deduplicate maps that appear with both .jpg and .JPG (Windows case issue)
    seen = set()
    deduped = {}
    for k, v in manifest.items():
        key = k.lower()
        if key not in seen:
            seen.add(key)
            deduped[k] = v
    manifest = deduped

    all_results = {}

    for map_name, map_info in manifest.items():
        # ── Checkpoint: skip maps already done ──────────────────
        if resume:
            cached = _load_checkpoint(map_name)
            if cached is not None:
                print(f'[OCR] SKIP (checkpointed): {map_name}  ({len(cached)} detections)')
                all_results[map_name] = cached
                continue

        print(f'\n[OCR] Map: {map_name}  (workers={OCR_WORKERS})')
        img = load_image(map_info['file'])

        # Collect all tile args first (generate_tiles is a generator)
        tile_args = list(generate_tiles(img, config.TILE_SIZE, config.TILE_OVERLAP))
        total_tiles = len(tile_args)

        all_detections = []

        with ThreadPoolExecutor(max_workers=OCR_WORKERS) as pool:
            futures = {pool.submit(_process_tile, args): i for i, args in enumerate(tile_args)}
            with tqdm(total=total_tiles, desc='  OCR tiles', unit='tile') as pbar:
                for future in as_completed(futures):
                    try:
                        global_dets = future.result()
                        all_detections.extend(global_dets)
                    except Exception as exc:
                        tile_idx = futures[future]
                        print(f'  [OCR] Tile {tile_idx} error: {exc}')
                    pbar.update(1)

        # Remove duplicates caused by tile overlap
        before = len(all_detections)
        all_detections = deduplicate(all_detections, iou_threshold=0.4)
        after = len(all_detections)
        print(f'  Raw detections: {before}  ->  after dedup: {after}')

        # ── Save checkpoint immediately ──────────────────────────
        _save_checkpoint(map_name, all_detections)
        all_results[map_name] = all_detections

    # Merge all maps into final output
    with open(OCR_RAW_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in all_results.values())
    print(f'\n[OCR] Total detections across all maps: {total}')
    print(f'[OCR] Raw OCR saved: {OCR_RAW_PATH}')
    print(f'[OCR] Checkpoints in: {CHECKPOINT_DIR}')
    return all_results


if __name__ == '__main__':
    resume_mode = '--resume' in sys.argv
    if resume_mode:
        print('[OCR] Resume mode — skipping already-checkpointed maps')
    run_ocr(resume=resume_mode)
    print('[OCR] Phase 2 complete.')
