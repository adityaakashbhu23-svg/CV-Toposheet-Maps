"""Check OCR corner detections for the Ganjam map in the most recent run folder."""
import os, json, re

# d749eede1dfa is likely the most recent run — check all 4 and pick the one with ocr_results
logs_root = r'c:\CV- Toposheet\logs'
folders = ['37f5220d282a', '56c0c488f4dd', 'cdd6149f6ddf', 'd749eede1dfa']

for folder_name in folders:
    folder = os.path.join(logs_root, folder_name)
    ocr_path = os.path.join(folder, 'ocr_results_raw.json')
    gd_path = os.path.join(folder, 'grid_detection.json')
    manifest_path = os.path.join(folder, 'tiles_manifest.json')

    print(f'\n=== Folder: {folder_name} ===')
    print(f'  Has OCR: {os.path.exists(ocr_path)}  Has GD: {os.path.exists(gd_path)}')

    if not os.path.exists(ocr_path):
        continue

    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    with open(ocr_path, encoding='utf-8') as f:
        ocr = json.load(f)

    map_name = '72 A]10 Ganjam District (1936)'
    if map_name not in ocr:
        print(f'  map_name not in OCR')
        continue

    detections = ocr[map_name]
    info = manifest.get(map_name, {})
    img_w = info.get('width', 5000)
    img_h = info.get('height', 7000)

    print(f'  Image size: {img_w}x{img_h}, detections: {len(detections)}')

    # Show everything in top 20% height, right 30% width
    zone_x = img_w * 0.70
    zone_y = img_h * 0.20
    print(f'  Zone: x>{zone_x:.0f} y<{zone_y:.0f}')
    in_zone = [(d['text'], d['bbox']) for d in detections
               if (d['bbox'][0]+d['bbox'][2])/2 > zone_x and (d['bbox'][1]+d['bbox'][3])/2 < zone_y]
    for txt, bb in in_zone:
        print(f'    {txt!r:30s}  bbox={bb}')

    if os.path.exists(gd_path):
        with open(gd_path, encoding='utf-8') as f:
            gd = json.load(f)
        ocr_mn = gd.get(map_name, {}).get('ocr_map_number', 'NOT FOUND')
        print(f'  ocr_map_number in GD: {ocr_mn}')
