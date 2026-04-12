# resume_llm.py  –  Resume Phase 4 LLM cleaning for all remaining maps
#
# Finds maps in grid_detection.json that are NOT yet in llm_corrections.json
# and runs Gemini 2.5 Flash on them. Falls back to Key 2 if Key 1 is exhausted.
# Saves progress after each map, then rebuilds the database.
#
# Usage: python resume_llm.py

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import config
from utils.llm_utils import clean_with_vertex, clean_with_gemini, QuotaExhaustedError

GRID_PATH   = config.LOGS_FOLDER / 'grid_detection.json'
LLM_PATH    = config.LOGS_FOLDER / 'llm_corrections.json'

VERTEX_PROJECT  = config.VERTEX_PROJECT
VERTEX_LOCATION = config.VERTEX_LOCATION
VERTEX_MODEL    = config.VERTEX_MODEL

GEMINI_KEY1  = os.getenv('GEMINI_API_KEY', '')
GEMINI_KEY2  = os.getenv('GEMINI_API_KEY_2', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')

# ─────────────────────────────────────────────────────────────
#  Load data
# ─────────────────────────────────────────────────────────────

print('Loading grid_detection.json ...')
with open(GRID_PATH, encoding='utf-8') as f:
    grid_data = json.load(f)

llm_results = {}
if LLM_PATH.exists():
    with open(LLM_PATH, encoding='utf-8') as f:
        llm_results = json.load(f)

already_done = set(llm_results.keys())
remaining    = [k for k in grid_data if k not in already_done]

total_maps   = len(grid_data)
print(f'  Total maps  : {total_maps}')
print(f'  Already done: {len(already_done)}')
print(f'  Remaining   : {len(remaining)}')

if not remaining:
    print('\nAll maps already LLM-cleaned! Running Phase 5 ...')
else:
    print(f'\nProcessing {len(remaining)} maps — Vertex AI (GCP credit) with free Gemini fallback ...')

# ─────────────────────────────────────────────────────────────
#  Helper: call Gemini with Key 1, fall back to Key 2
# ─────────────────────────────────────────────────────────────

active_key   = GEMINI_KEY1
key_label    = 'Vertex AI (GCP credit)'
use_vertex   = True

def call_llm(raw_texts):
    global active_key, key_label, use_vertex
    # Try Vertex AI first (GCP credit, no quota limits)
    if use_vertex and VERTEX_PROJECT:
        try:
            return clean_with_vertex(raw_texts, project=VERTEX_PROJECT,
                                     location=VERTEX_LOCATION, model=VERTEX_MODEL)
        except QuotaExhaustedError:
            print('  [Vertex AI] Quota exhausted — falling back to free Gemini ...')
            use_vertex = False
        except Exception as e:
            print(f'  [Vertex AI] Error: {e} — falling back to free Gemini ...')
            use_vertex = False
    # Fallback: free Gemini AI Studio (Key 1 → Key 2)
    key_label = f'Gemini free ({("Key 1" if active_key == GEMINI_KEY1 else "Key 2")})'
    try:
        return clean_with_gemini(raw_texts, api_key=active_key, model=GEMINI_MODEL)
    except QuotaExhaustedError:
        if active_key == GEMINI_KEY1 and GEMINI_KEY2:
            print('  [Gemini] Key 1 quota exhausted — switching to Key 2 ...')
            active_key = GEMINI_KEY2
            key_label  = 'Gemini free (Key 2)'
            return clean_with_gemini(raw_texts, api_key=active_key, model=GEMINI_MODEL)
        else:
            print('  [Gemini] Both keys exhausted. Stopping.')
            raise

# ─────────────────────────────────────────────────────────────
#  Phase 4 loop
# ─────────────────────────────────────────────────────────────

t0 = time.time()
maps_done = 0

for idx, map_name in enumerate(remaining, 1):
    map_data   = grid_data[map_name]
    detections = map_data.get('detections', [])

    print(f'\n[{idx}/{len(remaining)}] {map_name}  ({len(detections)} detections)  [{key_label}]')
    if not detections:
        llm_results[map_name] = []
        print('  No detections — skipping LLM call.')
    else:
        raw_texts   = [d['text'] for d in detections]
        det_by_text = {d['text']: d for d in detections}

        try:
            cleaned = call_llm(raw_texts)
        except QuotaExhaustedError:
            print(f'\nQuota exhausted on both keys. Stopping at map {idx}/{len(remaining)}.')
            print(f'Progress saved. Re-run this script after quota resets.')
            break
        except Exception as e:
            print(f'  Unexpected error: {e}  — skipping this map.')
            continue

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

        kept    = len(enriched)
        dropped = len(cleaned) - kept
        print(f'  Features kept: {kept}  |  Noise dropped: {dropped}')
        llm_results[map_name] = enriched

    # Save after every map
    with open(LLM_PATH, 'w', encoding='utf-8') as f:
        json.dump(llm_results, f, ensure_ascii=False, indent=2)

    maps_done += 1
    elapsed = time.time() - t0
    total_feats = sum(len(v) for v in llm_results.values())
    print(f'  Saved. Total maps done: {len(llm_results)}/{total_maps}  |  Total features: {total_feats}  |  Elapsed: {elapsed:.0f}s')

# ─────────────────────────────────────────────────────────────
#  Phase 5 – Rebuild database
# ─────────────────────────────────────────────────────────────

remaining_after = [k for k in grid_data if k not in llm_results]
if remaining_after:
    print(f'\n[INFO] {len(remaining_after)} maps still need LLM cleaning.')
    print('       Re-run this script when quota resets to finish.')
else:
    print(f'\n{"="*60}')
    print(f'Phase 4 complete! All {total_maps} maps cleaned.')
    print(f'Running Phase 5: Database assembly ...')
    print(f'{"="*60}')
    from importlib import import_module
    mod = import_module('5_database_assembly')
    mod.build_database()
    print('\nAll done! Results in results/ folder.')
    print('  extracted_data.csv / extracted_data.json / toposheet.db')
