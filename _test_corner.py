"""Test the improved detect_map_number_from_ocr against the Ganjam map."""
import sys, json
sys.path.insert(0, r'c:\CV- Toposheet')

# Patch the function from 3_grid_detection.py
import importlib.util, types
spec = importlib.util.spec_from_file_location(
    "grid_det", r"c:\CV- Toposheet\3_grid_detection.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

detect = mod.detect_map_number_from_ocr

# Load the Ganjam map OCR from the identified folder
folder = r'c:\CV- Toposheet\logs\37f5220d282a'
with open(folder + r'\ocr_results_raw.json', encoding='utf-8') as f:
    ocr = json.load(f)
with open(folder + r'\tiles_manifest.json', encoding='utf-8') as f:
    manifest = json.load(f)

map_name = '72 A]10 Ganjam District (1936)'
detections = ocr[map_name]
info = manifest[map_name]
img_w, img_h = info['width'], info['height']

result = detect(detections, img_w, img_h)
print(f'Detected map number: {result!r}')
print('Expected: "74 A/10"')
print('PASS' if result == '74 A/10' else 'FAIL')
