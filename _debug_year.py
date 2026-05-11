import json

folder = r'c:\CV- Toposheet\logs\37f5220d282a'
with open(folder + r'\ocr_results_raw.json', encoding='utf-8') as f:
    ocr = json.load(f)
with open(folder + r'\tiles_manifest.json', encoding='utf-8') as f:
    manifest = json.load(f)

map_name = '72 A]10 Ganjam District (1936)'
detections = ocr[map_name]
info = manifest[map_name]
img_w, img_h = info['width'], info['height']

zone_y = img_h * 0.15
print(f'Top margin zone: y < {zone_y:.0f} (total height: {img_h})')
print(f'\nAll tokens in top 15%:')
tokens = []
for d in detections:
    bx1, by1, bx2, by2 = d['bbox']
    cy = (by1 + by2) / 2
    if cy <= zone_y:
        tokens.append((bx1, d['text'], d['bbox']))

tokens.sort()
for x, txt, bb in tokens:
    print(f'  {txt!r:30s}  x={x}  bbox={bb}')

print(f'\nTotal: {len(tokens)} tokens')
print(f'\nCombined top line: {" ".join(t for _, t, _ in tokens)}')
