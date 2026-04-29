import json
m = json.load(open('logs/tiles_manifest.json', encoding='utf-8'))
total_tiles = 0
for name, info in m.items():
    tc = len(info.get('tiles', []))
    w = info.get('width','?')
    h = info.get('height','?')
    total_tiles += tc
    print(f'{name}: {w}x{h}px -> {tc} tiles')
print(f'\nTotal maps: {len(m)}  |  Avg tiles/map: {total_tiles//max(1,len(m))}')
