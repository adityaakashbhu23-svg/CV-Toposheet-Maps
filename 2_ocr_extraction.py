# 2_ocr_extraction.py  –  Phase 2: Run OCR on every tile and collect raw detections
#
# Usage: python 2_ocr_extraction.py
#
# Reads:   logs/tiles_manifest.json
# Output:  logs/ocr_results_raw.json

import json
import sys
from pathlib import Path
from tqdm import tqdm

import config
from utils.image_utils import load_image, generate_tiles, preprocess_for_ocr
from utils.ocr_utils import ocr_tile, translate_bbox_to_global, deduplicate

MANIFEST_PATH  = config.LOGS_FOLDER / 'tiles_manifest.json'
OCR_RAW_PATH   = config.LOGS_FOLDER / 'ocr_results_raw.json'


def run_ocr() -> dict:
    """
    For each map in the manifest, re-generate tiles on-the-fly and run OCR.
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
        print(f'\n[OCR] Map: {map_name}')
        img = load_image(map_info['file'])
        total_tiles = len(map_info['tiles'])

        all_detections = []

        with tqdm(total=total_tiles, desc='  OCR tiles', unit='tile') as pbar:
            for tile_img, x, y, x2, y2 in generate_tiles(
                img, config.TILE_SIZE, config.TILE_OVERLAP
            ):
                processed = preprocess_for_ocr(tile_img)
                detections = ocr_tile(processed, config.OCR_CONFIDENCE)

                # Shift bounding boxes to full-image coordinates
                global_dets = translate_bbox_to_global(detections, x, y)
                all_detections.extend(global_dets)
                pbar.update(1)

        # Remove duplicates caused by tile overlap
        before = len(all_detections)
        all_detections = deduplicate(all_detections, iou_threshold=0.4)
        after = len(all_detections)

        print(f'  Raw detections: {before}  ->  after dedup: {after}')
        all_results[map_name] = all_detections

    # Save
    with open(OCR_RAW_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in all_results.values())
    print(f'\n[OCR] Total detections across all maps: {total}')
    print(f'[OCR] Raw OCR saved: {OCR_RAW_PATH}')
    return all_results


if __name__ == '__main__':
    run_ocr()
    print('[OCR] Phase 2 complete.')
