"""Debug what OCR text is in the top-right corner zone for a specific map."""
import json

ocr_path = r'c:\CV- Toposheet\logs\ocr_results_raw.json'
target = '72 A]10 Ganjam District (1936)'

with open(ocr_path, encoding='utf-8') as f:
    ocr = json.load(f)

if target not in ocr:
    print(f'Map not found: {target}')
    print('Available keys containing Ganjam or 72 A:', [k for k in ocr if 'Ganjam' in k or '72 A' in k][:10])
else:
    detections = ocr[target]
    # Get image size from tiles_manifest
    try:
        with open(r'c:\CV- Toposheet\logs\tiles_manifest.json', encoding='utf-8') as f:
            manifest = json.load(f)
        info = manifest.get(target, {})
        img_w = info.get('width', 5000)
        img_h = info.get('height', 7000)
    except:
        img_w, img_h = 5000, 7000

    zone_x = img_w * 0.60
    zone_y = img_h * 0.25

    print(f'Image: {img_w} x {img_h}')
    print(f'Search zone: x > {zone_x:.0f}, y < {zone_y:.0f}')
    print(f'\nAll detections in top-right zone:')
    in_zone = []
    for d in detections:
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2
        if cx > zone_x and cy < zone_y:
            in_zone.append(d)
            print(f'  text={d["text"]!r:30s}  cx={cx:.0f} cy={cy:.0f}  bbox={d["bbox"]}')

    print(f'\nTotal in zone: {len(in_zone)}')

    # Also show top-right quadrant even if outside current zone
    print(f'\nTop 15% height, right 30% width (wider search):')
    for d in detections:
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2
        if cx > img_w * 0.70 and cy < img_h * 0.15:
            print(f'  text={d["text"]!r:30s}  cx={cx:.0f} cy={cy:.0f}')
