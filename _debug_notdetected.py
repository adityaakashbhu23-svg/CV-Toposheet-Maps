import sys, json, os, glob
sys.path.insert(0, r'c:\CV- Toposheet')

# Show top-margin tokens for each not-detected unique map (first occurrence only)
SHOW_MAPS = {
    '72 A]03 and A]02 Champaran District (1929) Preliminary',
    '72 A]04 Champaran District (1928) Preliminary',
    '72 L]16 Devghar District (1984)',
    '72 P]15 Birbhum District (1913)',
    '72 P]16 Birbhum District (1924)',
    '72 P_15 Birbhum District _1913_',
    '72 P_16 Birbhum District _1924_',
}

seen = set()
for ocr_path in sorted(glob.glob(r'c:\CV- Toposheet\logs\*\ocr_results_raw.json')):
    folder = os.path.dirname(ocr_path)
    manifest_path = os.path.join(folder, 'tiles_manifest.json')
    if not os.path.exists(manifest_path):
        continue
    with open(ocr_path, encoding='utf-8') as f:
        ocr = json.load(f)
    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    for map_name in ocr:
        if map_name not in SHOW_MAPS or map_name in seen:
            continue
        if map_name not in manifest:
            continue
        seen.add(map_name)
        info = manifest[map_name]
        img_h = info.get('height', 0)
        zone_y = img_h * 0.15
        top = []
        for d in ocr[map_name]:
            bx1, by1, bx2, by2 = d['bbox']
            cy = (by1 + by2) / 2
            if cy <= zone_y:
                top.append((bx1, d['text']))
        top.sort()
        print(f'\n{map_name}')
        print(f'  top-margin tokens: {[t for _, t in top]}')
