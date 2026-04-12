"""
resume_phase4.py — Run LLM cleaning on any maps in grid_detection.json
                   that are not yet in llm_corrections.json, then rebuild DB.
"""
import json, sys, time
from pathlib import Path
import config

GRID_PATH    = Path(config.LOGS_FOLDER) / 'grid_detection.json'
LLM_OUT_PATH = Path(config.LOGS_FOLDER) / 'llm_corrections.json'

# ── LLM helpers from process_new_maps ─────────────────────────────────────────
import os
from dotenv import load_dotenv
load_dotenv()

LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'groq').upper()

def clean_with_llm(texts: list) -> list:
    from utils.llm_utils import clean_with_llm as _clean
    return _clean(texts)


# ── Phase 4 ────────────────────────────────────────────────────────────────────
def run_phase4():
    print(f'\n{"#"*60}')
    print(f'#  Phase 4 (resume): LLM cleaning with {LLM_PROVIDER}')
    print(f'{"#"*60}')

    grid_data = json.load(open(GRID_PATH, encoding='utf-8'))

    llm_results = {}
    if LLM_OUT_PATH.exists():
        llm_results = json.load(open(LLM_OUT_PATH, encoding='utf-8'))

    pending = [k for k in grid_data if k not in llm_results]
    total   = len(grid_data)
    done    = total - len(pending)

    print(f'[LLM] {done}/{total} maps already done — {len(pending)} remaining\n')

    if not pending:
        print('[LLM] All maps already LLM-cleaned.')
        return llm_results

    for i, map_name in enumerate(pending, 1):
        map_data   = grid_data[map_name]
        detections = map_data.get('detections', [])
        print(f'\n[LLM] ({done+i}/{total}) Map: {map_name}  ({len(detections)} OCR detections)')

        if not detections:
            llm_results[map_name] = []
            with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
                json.dump(llm_results, f, ensure_ascii=False, indent=2)
            continue

        raw_texts   = [d['text'] for d in detections]
        det_by_text = {d['text']: d for d in detections}

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

        with open(LLM_OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(llm_results, f, ensure_ascii=False, indent=2)
        print(f'  [LLM] Progress saved.')

    total_feats = sum(len(v) for v in llm_results.values())
    print(f'\n[LLM] Done. Total clean features: {total_feats}')
    return llm_results


# ── Phase 5 ────────────────────────────────────────────────────────────────────
def run_phase5():
    print(f'\n{"#"*60}')
    print(f'#  Phase 5: Rebuilding database')
    print(f'{"#"*60}')
    from importlib import import_module
    mod = import_module('5_database_assembly')
    mod.build_database()


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    config.validate()
    print(f'[CONFIG OK] LLM={LLM_PROVIDER}  Maps={config.MAPS_FOLDER}')

    t0 = time.time()
    run_phase4()
    run_phase5()
    elapsed = time.time() - t0

    print(f'\n{"="*60}')
    print(f'Done in {elapsed:.0f}s')
    print(f'Results: {config.RESULTS_FOLDER}')
    print('Run "python 6_query_interface.py" to search the database.')
