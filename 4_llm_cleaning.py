# 4_llm_cleaning.py  –  Phase 4: LLM-based OCR cleaning and feature classification
#
# Usage: python 4_llm_cleaning.py
#
# Reads:   logs/grid_detection.json
# Output:  logs/llm_corrections.json

import json
import sys
from pathlib import Path
from tqdm import tqdm

import config
from utils.llm_utils import clean_with_llm

GRID_PATH    = config.LOGS_FOLDER / 'grid_detection.json'
LLM_OUT_PATH = config.LOGS_FOLDER / 'llm_corrections.json'


def run_llm_cleaning() -> dict:
    config.validate()

    if not GRID_PATH.exists():
        print(f'[LLM] Grid detection file not found: {GRID_PATH}')
        print('      Run 3_grid_detection.py first.')
        sys.exit(1)

    with open(GRID_PATH, encoding='utf-8') as f:
        grid_data = json.load(f)

    llm_results = {}

    for map_name, map_data in grid_data.items():
        detections = map_data.get('detections', [])
        print(f'\n[LLM] Map: {map_name}  ({len(detections)} OCR detections)')

        if not detections:
            llm_results[map_name] = []
            continue

        # Build index: raw text → detection metadata
        raw_texts = [d['text'] for d in detections]
        det_by_text = {d['text']: d for d in detections}  # stores last if duplicate text

        # Send all raw texts to LLM in one call (batching handled inside llm_utils)
        cleaned = clean_with_llm(raw_texts)

        # Merge LLM output back with grid references and bounding boxes
        enriched = []
        for item in cleaned:
            if item.get('feature_type') == 'noise':
                continue  # Drop noise items

            original_text = item.get('original', '')
            source_det = det_by_text.get(original_text, {})

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
        kept = len(enriched)
        dropped = len(cleaned) - kept
        print(f'  Features kept: {kept}  |  Noise dropped: {dropped}')

    with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(llm_results, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in llm_results.values())
    print(f'\n[LLM] Total clean features: {total}')
    print(f'[LLM] Saved: {LLM_OUT_PATH}')
    return llm_results


if __name__ == '__main__':
    run_llm_cleaning()
    print('[LLM] Phase 4 complete.')
