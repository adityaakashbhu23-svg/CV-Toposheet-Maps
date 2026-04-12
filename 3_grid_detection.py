# 3_grid_detection.py  –  Phase 3: Detect alphanumeric grid overlay and assign grid refs
#
# Usage: python 3_grid_detection.py
#
# Reads:   logs/ocr_results_raw.json + tiles_manifest.json
# Output:  logs/grid_detection.json  (detections + grid_reference assigned)
#
# Grid format: Rows = A, B, C, D...  Columns = 1, 2, 3, 4...
# A detection at row B, column 3 → grid_reference = "B-3"

import json
import sys
import re
import numpy as np
import cv2
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import config
from utils.image_utils import load_image

MANIFEST_PATH   = config.LOGS_FOLDER / 'tiles_manifest.json'
OCR_RAW_PATH    = config.LOGS_FOLDER / 'ocr_results_raw.json'
GRID_OUT_PATH   = config.LOGS_FOLDER / 'grid_detection.json'

# Row labels and column labels used on Survey of India topo sheets
ROW_LABELS    = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
COLUMN_LABELS = [str(i) for i in range(1, 30)]


def detect_grid_from_ocr(
    detections: List[Dict],
    img_width: int,
    img_height: int
) -> Tuple[List[float], List[float]]:
    """
    Find grid line positions by looking for single-letter row markers (A/B/C)
    and numeric column markers (1/2/3) in the OCR detections themselves.

    Returns (col_x_positions, row_y_positions) sorted ascending.
    """
    col_xs = []
    row_ys = []

    row_pattern = re.compile(r'^[A-Z]$')
    col_pattern = re.compile(r'^\d{1,2}$')

    for d in detections:
        text = d['text'].strip().upper()
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2

        if row_pattern.match(text) and (cy < 80 or cy > img_height - 80):
            row_ys.append(cy)
        if col_pattern.match(text) and (cx < 80 or cx > img_width - 80):
            col_xs.append(cx)

    return sorted(set(col_xs)), sorted(set(row_ys))


def detect_grid_from_image(img: np.ndarray) -> Tuple[List[float], List[float]]:
    """
    Detect grid lines from the image using Hough line transform.
    Returns (col_x_positions, row_y_positions).
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    h, w = gray.shape

    # Edge detection
    edges = cv2.Canny(gray, 30, 100, apertureSize=3)

    # Probabilistic Hough line detection
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=200,
                             minLineLength=min(w, h) * 0.5, maxLineGap=20)

    col_xs = []  # vertical lines
    row_ys = []  # horizontal lines

    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            angle = np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1)))

            if angle < 5:    # nearly horizontal → row line
                row_ys.append((y1 + y2) / 2)
            elif angle > 85: # nearly vertical → col line
                col_xs.append((x1 + x2) / 2)

    # Cluster nearby lines (within 20px) by taking means
    def cluster(positions, gap=30):
        if not positions:
            return []
        positions = sorted(set(positions))
        clusters = [[positions[0]]]
        for p in positions[1:]:
            if p - clusters[-1][-1] < gap:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [sum(c) / len(c) for c in clusters]

    return cluster(col_xs), cluster(row_ys)


def assign_grid_ref(
    bbox: List[int],
    col_xs: List[float],
    row_ys: List[float],
    img_width: int,
    img_height: int
) -> str:
    """
    Given a detection bounding box and grid line positions,
    return the grid reference string (e.g. "B-3").
    Falls back to dividing the image into equal sections if no grid lines detected.
    """
    bx1, by1, bx2, by2 = bbox
    cx = (bx1 + bx2) / 2
    cy = (by1 + by2) / 2

    # Determine column index
    if col_xs:
        col_idx = sum(1 for x in col_xs if x < cx)
    else:
        # Fallback: divide image width into sections
        n_cols = 4
        col_idx = min(int(cx / img_width * n_cols), n_cols - 1)

    # Determine row index
    if row_ys:
        row_idx = sum(1 for y in row_ys if y < cy)
    else:
        n_rows = 4
        row_idx = min(int(cy / img_height * n_rows), n_rows - 1)

    row_label = ROW_LABELS[row_idx] if row_idx < len(ROW_LABELS) else f'R{row_idx}'
    col_label = COLUMN_LABELS[col_idx] if col_idx < len(COLUMN_LABELS) else str(col_idx + 1)

    return f'{row_label}-{col_label}'


def assign_all_grid_refs() -> dict:
    config.validate()

    for path, name in [(MANIFEST_PATH, 'tiles manifest'), (OCR_RAW_PATH, 'OCR results')]:
        if not path.exists():
            print(f'[GRID] Missing {name}: {path}')
            print('       Run earlier pipeline phases first.')
            sys.exit(1)

    with open(MANIFEST_PATH, encoding='utf-8') as f:
        manifest = json.load(f)
    with open(OCR_RAW_PATH, encoding='utf-8') as f:
        ocr_results = json.load(f)

    grid_results = {}

    for map_name, detections in ocr_results.items():
        print(f'\n[GRID] Map: {map_name}')

        map_info   = manifest.get(map_name, {})
        img_width  = map_info.get('width', 5000)
        img_height = map_info.get('height', 7000)

        # Try to detect grid from OCR markers first
        col_xs, row_ys = detect_grid_from_ocr(detections, img_width, img_height)

        # If OCR-based detection found very few lines, try image-based
        if len(col_xs) < 2 or len(row_ys) < 2:
            print(f'  OCR grid detection: {len(col_xs)} cols, {len(row_ys)} rows — trying image scan...')
            try:
                img = load_image(map_info['file'])
                col_xs_img, row_ys_img = detect_grid_from_image(img)
                if len(col_xs_img) > len(col_xs):
                    col_xs = col_xs_img
                if len(row_ys_img) > len(row_ys):
                    row_ys = row_ys_img
            except Exception as e:
                print(f'  Image grid detection failed: {e}. Using fallback equal-division.')

        print(f'  Grid lines detected: {len(col_xs)} columns, {len(row_ys)} rows')

        enriched = []
        for det in detections:
            grid_ref = assign_grid_ref(det['bbox'], col_xs, row_ys, img_width, img_height)
            enriched.append({**det, 'grid_reference': grid_ref})

        grid_results[map_name] = {
            'col_lines': col_xs,
            'row_lines': row_ys,
            'detections': enriched,
        }
        print(f'  Assigned grid refs to {len(enriched)} detections')

    with open(GRID_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(grid_results, f, ensure_ascii=False, indent=2)
    print(f'\n[GRID] Saved: {GRID_OUT_PATH}')
    return grid_results


if __name__ == '__main__':
    assign_all_grid_refs()
    print('[GRID] Phase 3 complete.')
