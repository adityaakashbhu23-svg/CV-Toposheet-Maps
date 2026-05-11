import sys, json
sys.path.insert(0, r'c:\CV- Toposheet')
import importlib.util
spec = importlib.util.spec_from_file_location("gd", r"c:\CV- Toposheet\3_grid_detection.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
detect_year = mod.detect_survey_year_from_ocr

folder = r'c:\CV- Toposheet\logs\37f5220d282a'
with open(folder + r'\ocr_results_raw.json', encoding='utf-8') as f:
    ocr = json.load(f)
with open(folder + r'\tiles_manifest.json', encoding='utf-8') as f:
    manifest = json.load(f)

map_name = '72 A]10 Ganjam District (1936)'
detections = ocr[map_name]
info = manifest[map_name]
img_w, img_h = info['width'], info['height']

result = detect_year(detections, img_w, img_h)
print(f'Detected year: {result!r}')
print('Expected: "1934-35"')
print('PASS' if result == '1934-35' else 'FAIL')
