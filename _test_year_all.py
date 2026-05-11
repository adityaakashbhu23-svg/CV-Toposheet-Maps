import sys, json, os, glob
sys.path.insert(0, r'c:\CV- Toposheet')
import importlib.util
spec = importlib.util.spec_from_file_location("gd", r"c:\CV- Toposheet\3_grid_detection.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
detect_year = mod.detect_survey_year_from_ocr

detected = []
not_detected = []

for ocr_path in glob.glob(r'c:\CV- Toposheet\logs\*\ocr_results_raw.json'):
    folder = os.path.dirname(ocr_path)
    manifest_path = os.path.join(folder, 'tiles_manifest.json')
    if not os.path.exists(manifest_path):
        continue
    with open(ocr_path, encoding='utf-8') as f:
        ocr = json.load(f)
    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    for map_name in ocr:
        if map_name not in manifest:
            continue
        info = manifest[map_name]
        img_w = info.get('width', 0)
        img_h = info.get('height', 0)
        if img_w == 0 or img_h == 0:
            continue
        year = detect_year(ocr[map_name], img_w, img_h)
        if year:
            detected.append((map_name, year))
        else:
            not_detected.append(map_name)

print(f'\n===  SURVEY YEAR OCR DETECTION RESULTS  ===')
print(f'Detected:     {len(detected)}')
print(f'Not detected: {len(not_detected)}')
print(f'\nDetected maps:')
for m, y in sorted(detected):
    print(f'  {y:10s}  {m}')
if not_detected:
    print(f'\nNot detected:')
    for m in sorted(not_detected):
        print(f'  {m}')
