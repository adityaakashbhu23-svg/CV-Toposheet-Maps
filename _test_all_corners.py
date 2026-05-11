"""Run detect_map_number_from_ocr on ALL available OCR data and report results."""
import sys, os, json
sys.path.insert(0, r'c:\CV- Toposheet')

import importlib.util
spec = importlib.util.spec_from_file_location(
    "grid_det", r"c:\CV- Toposheet\3_grid_detection.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
detect = mod.detect_map_number_from_ocr

logs_root = r'c:\CV- Toposheet\logs'
results = []

# Check per-map hash folders
for folder_name in os.listdir(logs_root):
    folder = os.path.join(logs_root, folder_name)
    if not os.path.isdir(folder):
        continue
    ocr_path = os.path.join(folder, 'ocr_results_raw.json')
    manifest_path = os.path.join(folder, 'tiles_manifest.json')
    gd_path = os.path.join(folder, 'grid_detection.json')
    if not os.path.exists(ocr_path) or not os.path.exists(manifest_path):
        continue
    try:
        with open(ocr_path, encoding='utf-8') as f:
            ocr = json.load(f)
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        old_gd = {}
        if os.path.exists(gd_path):
            with open(gd_path, encoding='utf-8') as f:
                old_gd = json.load(f)
        for map_name, detections in ocr.items():
            info = manifest.get(map_name, {})
            img_w = info.get('width', 5000)
            img_h = info.get('height', 7000)
            new = detect(detections, img_w, img_h)
            old = old_gd.get(map_name, {}).get('ocr_map_number', 'N/A')
            results.append((map_name, old, new, folder_name))
    except Exception as e:
        print(f'Error in {folder_name}: {e}')

# Also check global ocr_results_raw.json
global_ocr = r'c:\CV- Toposheet\logs\ocr_results_raw.json'
global_manifest = r'c:\CV- Toposheet\logs\tiles_manifest.json'
global_gd = r'c:\CV- Toposheet\logs\grid_detection.json'
if os.path.exists(global_ocr):
    try:
        with open(global_ocr, encoding='utf-8') as f:
            ocr = json.load(f)
        with open(global_manifest, encoding='utf-8') as f:
            manifest = json.load(f)
        old_gd = {}
        if os.path.exists(global_gd):
            with open(global_gd, encoding='utf-8') as f:
                old_gd = json.load(f)
        for map_name, detections in ocr.items():
            info = manifest.get(map_name, {})
            img_w = info.get('width', 5000)
            img_h = info.get('height', 7000)
            new = detect(detections, img_w, img_h)
            old = old_gd.get(map_name, {}).get('ocr_map_number', 'N/A')
            results.append((map_name, old, new, 'global'))
    except Exception as e:
        print(f'Error in global: {e}')

# Deduplicate (same map may appear in multiple run folders)
seen = {}
for map_name, old, new, folder in results:
    if map_name not in seen or (new and not seen[map_name][1]):
        seen[map_name] = (old, new, folder)

print(f'\n{"Map Name":<50} {"Old":>15} {"New":>15}')
print('-' * 85)
found = lost = unchanged = blank = 0
for map_name, (old, new, folder) in sorted(seen.items()):
    changed = ' ← NEW' if new != old else ''
    status = ''
    if new and not old or (old == 'N/A' and new):
        found += 1; status = ' [NOW FOUND]'
    elif not new and old and old != 'N/A':
        lost += 1; status = ' [LOST!]'
    elif not new:
        blank += 1; status = ' [blank]'
    else:
        unchanged += 1
    print(f'{map_name[:49]:<50} {str(old):>15} {str(new):>15}{changed}{status}')

print(f'\nSummary: {found} now-found, {lost} regressions, {unchanged} unchanged, {blank} still blank')
