"""Find the hash folder for the Ganjam map and check its OCR corner detections."""
import os, json

logs_root = r'c:\CV- Toposheet\logs'
maps_root = r'c:\CV- Toposheet\maps'

# Find hash folders that have a tiles_manifest.json referencing Ganjam
for d in os.listdir(logs_root):
    folder = os.path.join(logs_root, d)
    if not os.path.isdir(folder):
        continue
    manifest_path = os.path.join(folder, 'tiles_manifest.json')
    if not os.path.exists(manifest_path):
        continue
    try:
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        # Check if any map_name in manifest references Ganjam
        for map_name, info in manifest.items():
            if 'Ganjam' in map_name or 'AJ10' in map_name or ('72' in map_name and '10' in map_name):
                print(f'Found in folder: {d}')
                print(f'  map_name: {map_name}')
                print(f'  info keys: {list(info.keys())}')
    except:
        pass

# Also check maps hash folders for Ganjam
print('\nChecking maps hash folders:')
for d in os.listdir(maps_root):
    folder = os.path.join(maps_root, d)
    if not os.path.isdir(folder):
        continue
    meta_path = os.path.join(folder, 'meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as f:
                meta = json.load(f)
            if 'Ganjam' in str(meta) or 'AJ10' in str(meta):
                print(f'Found in maps folder: {d}')
                print(f'  meta: {meta}')
        except:
            pass
