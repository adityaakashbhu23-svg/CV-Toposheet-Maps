# 1_tile_maps.py  –  Phase 1: Split large maps into tiles
#
# Usage: python 1_tile_maps.py
#
# Output: logs/tiles_manifest.json  (map → list of tiles with coordinates)

import json
import os
import sys
import tempfile
from pathlib import Path
from tqdm import tqdm

import config
from utils.image_utils import (
    load_image, get_image_info, generate_tiles,
    preprocess_for_ocr, save_tile, tile_count
)

MANIFEST_PATH = config.LOGS_FOLDER / 'tiles_manifest.json'
TILES_DIR     = config.LOGS_FOLDER / 'tiles'


def tile_all_maps(save_tiles_to_disk: bool = False) -> dict:
    """
    Tile every JPG in the maps folder.
    If save_tiles_to_disk=True, writes each tile as a file (useful for debugging).
    Returns the full manifest dict.
    """
    config.validate()
    TILES_DIR.mkdir(exist_ok=True)

    # Collect all JPG/PNG files case-insensitively, deduplicate by stem
    seen_stems = set()
    map_files_raw = (
        list(config.MAPS_FOLDER.glob('*.jpg')) +
        list(config.MAPS_FOLDER.glob('*.jpeg')) +
        list(config.MAPS_FOLDER.glob('*.JPG')) +
        list(config.MAPS_FOLDER.glob('*.JPEG')) +
        list(config.MAPS_FOLDER.glob('*.png')) +
        list(config.MAPS_FOLDER.glob('*.PNG'))
    )
    map_files = []
    for p in sorted(map_files_raw):
        key = p.stem.lower()
        if key not in seen_stems:
            seen_stems.add(key)
            map_files.append(p)

    if not map_files:
        print(f'[TILE] No JPG/PNG files found in {config.MAPS_FOLDER}')
        sys.exit(1)

    print(f'[TILE] Found {len(map_files)} map(s)')
    manifest = {}

    for map_path in map_files:
        map_name = map_path.stem
        print(f'\n[TILE] Processing: {map_path.name}')

        img = load_image(str(map_path))
        info = get_image_info(img)
        total = tile_count(img, config.TILE_SIZE, config.TILE_OVERLAP)
        print(f'       Size: {info["width"]}x{info["height"]}px ({info["megapixels"]} MP) → {total} tiles')

        manifest[map_name] = {
            'file':   str(map_path),
            'width':  info['width'],
            'height': info['height'],
            'tiles':  [],
        }

        with tqdm(total=total, desc='  Tiling', unit='tile') as pbar:
            for tile_img, x, y, x2, y2 in generate_tiles(img, config.TILE_SIZE, config.TILE_OVERLAP):
                tile_info = {'x': x, 'y': y, 'x2': x2, 'y2': y2}

                if save_tiles_to_disk:
                    tile_filename = f'{map_name}__{x}_{y}.jpg'
                    tile_path = TILES_DIR / tile_filename
                    save_tile(preprocess_for_ocr(tile_img), str(tile_path))
                    tile_info['file'] = str(tile_path)

                manifest[map_name]['tiles'].append(tile_info)
                pbar.update(1)

        print(f'       Generated {len(manifest[map_name]["tiles"])} tiles')

    # Save manifest
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f'\n[TILE] Manifest saved: {MANIFEST_PATH}')
    return manifest


if __name__ == '__main__':
    # Pass --save-tiles to also write tiles to disk (large, for debugging only)
    save = '--save-tiles' in sys.argv
    tile_all_maps(save_tiles_to_disk=save)
    print('[TILE] Phase 1 complete.')
