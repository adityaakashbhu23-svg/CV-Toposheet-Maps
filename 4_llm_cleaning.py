# 4_llm_cleaning.py  –  Phase 4: LLM-based OCR cleaning and feature classification
#
# Usage:
#   python 4_llm_cleaning.py              # uses LLM_PROVIDER from config / .env
#   python 4_llm_cleaning.py --ensemble   # override: run ALL LLMs in parallel and vote
#   python 4_llm_cleaning.py --single     # override: run single provider (fast, cheaper)
#
# Reads:   logs/grid_detection.json
# Output:  logs/llm_corrections.json

import json
import sys
from pathlib import Path
from tqdm import tqdm

import config
import utils.llm_utils as llm_utils
from utils.llm_utils import (
    clean_with_llm,
    clean_with_ensemble,
    apply_spelling_normalisation,
    _load_spelling_variants,
)

# ── Country-aware system prompt ──────────────────────────────────────────────
llm_utils.SYSTEM_PROMPT = llm_utils.build_system_prompt(config.MAP_COUNTRY)
print(f'[LLM] Country prompt loaded: {config.MAP_COUNTRY.upper()}')

# Pre-warm spelling variants cache so the first map doesn't pay load cost
_load_spelling_variants()

GRID_PATH    = config.LOGS_FOLDER / 'grid_detection.json'
LLM_OUT_PATH = config.LOGS_FOLDER / 'llm_corrections.json'


def _use_ensemble() -> bool:
    """
    Return True when ensemble mode is active.
    Priority: CLI flag (--ensemble / --single) > ENSEMBLE_MODE in config.
    """
    if '--ensemble' in sys.argv:
        return True
    if '--single' in sys.argv:
        return False
    return getattr(config, 'ENSEMBLE_MODE', False)


def run_llm_cleaning() -> dict:
    config.validate()

    if not GRID_PATH.exists():
        print(f'[LLM] Grid detection file not found: {GRID_PATH}')
        print('      Run 3_grid_detection.py first.')
        sys.exit(1)

    with open(GRID_PATH, encoding='utf-8') as f:
        grid_data = json.load(f)

    ensemble = _use_ensemble()
    if ensemble:
        workers = getattr(config, 'LLM_ENSEMBLE_WORKERS', 6)
        print(f'[LLM] Mode: ENSEMBLE (parallel, workers={workers})')
    else:
        print(f'[LLM] Mode: SINGLE ({config.LLM_PROVIDER.upper()})')

    llm_results = {}

    for map_name, map_data in tqdm(grid_data.items(), desc='Maps', unit='map'):
        detections = map_data.get('detections', [])
        print(f'\n[LLM] Map: {map_name}  ({len(detections)} OCR detections)')

        if not detections:
            llm_results[map_name] = []
            continue

        # Build index: raw text → detection metadata
        # (keeps the first occurrence so bbox/grid_reference come from earliest tile)
        raw_texts   = [d['text'] for d in detections]
        det_by_text = {}
        for d in detections:
            det_by_text.setdefault(d['text'], d)

        # ── LLM call ────────────────────────────────────────────────────────
        if ensemble:
            workers = getattr(config, 'LLM_ENSEMBLE_WORKERS', 6)
            cleaned = clean_with_ensemble(raw_texts, max_workers=workers)
        else:
            cleaned = clean_with_llm(raw_texts)

        # ── Historical spelling normalisation ────────────────────────────────
        # Runs AFTER LLM so it corrects any remaining colonial/era spellings
        # that the LLM may not have normalised (e.g. "Cawnpore" → "Kanpur").
        cleaned = apply_spelling_normalisation(cleaned)

        # ── Merge back with grid references / bboxes ─────────────────────────
        enriched = []
        for item in cleaned:
            if item.get('feature_type') == 'noise':
                continue  # Drop noise items

            original_text = item.get('original', '')
            source_det    = det_by_text.get(original_text, {})

            record = {
                'map_name':       map_name,
                'original_text':  original_text,
                'feature_name':   item.get('cleaned', original_text),
                'feature_type':   item.get('feature_type', 'unknown'),
                'confidence':     item.get('confidence', 0.0),
                'grid_reference': source_det.get('grid_reference', ''),
                'bbox':           source_det.get('bbox', []),
            }
            # Ensemble-only metadata (ignored silently by downstream if absent)
            if 'llm_count' in item:
                record['llm_count']  = item['llm_count']
                record['agreement']  = item['agreement']
            if 'spelling_source' in item:
                record['spelling_source'] = item['spelling_source']

            enriched.append(record)

        llm_results[map_name] = enriched
        kept    = len(enriched)
        dropped = len(cleaned) - kept
        print(f'  Features kept: {kept}  |  Noise dropped: {dropped}')

        if ensemble:
            agreed = sum(1 for e in enriched if e.get('agreement', 1.0) >= 0.75)
            print(f'  High-agreement (≥75 %): {agreed}/{kept}')

    with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(llm_results, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in llm_results.values())
    print(f'\n[LLM] Total clean features: {total}')
    print(f'[LLM] Saved: {LLM_OUT_PATH}')
    return llm_results


if __name__ == '__main__':
    run_llm_cleaning()
    print('[LLM] Phase 4 complete.')
