# 3_grid_detection.py  –  Phase 3: Detect alphanumeric grid overlay and assign grid refs
#
# Usage: python 3_grid_detection.py
#
# Reads:   logs/ocr_results_raw.json + tiles_manifest.json
# Output:  logs/grid_detection.json  (detections + grid_reference assigned)
#
# SOI Grid format:
#   Columns (left→right) = A, B, C, D  (letters printed in RED at TOP/BOTTOM margin)
#   Rows    (top→bottom) = 1, 2, 3, 4  (numbers printed in RED at LEFT/RIGHT margin)
#   Grid reference = ROW_NUMBER + COLUMN_LETTER  e.g. "2B"
# The pipeline reads only RED-ink margin labels so body-text is never mistaken for grid labels.
#
# Improvements over v1:
#   - Wider margin search (15% of image edge instead of fixed 80px)
#   - Sequence-aware row/col label validation (must be consecutive A,B,C or 1,2,3)
#   - Adaptive Hough threshold based on image size
#   - Grid regularity check + interpolation of missing lines
#   - Per-map grid confidence score reported
#   - Configurable default grid size from config.py (SOI standard: 4 cols x 4 rows)
#   - Handles OCR misreads of grid labels (e.g. "0" → "O", "l" → "1")

import json
import sys
import re
import numpy as np
import cv2
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import Counter

import config
from utils.image_utils import load_image
from utils.coords_utils import extract_coord_bounds, assign_coords_to_grid


# ─────────────────────────────────────────────────────────────────────────────
def _is_red_region(img: np.ndarray, bbox: List[int], min_red_fraction: float = 0.04) -> bool:
    """
    Return True when the image region within bbox contains enough red pixels
    to be consistent with a red-ink SOI grid label.

    Survey of India toposheets print ALL grid margin labels (A/B/C and 1/2/3)
    in red ink so they stand out from the black body text.  This check filters
    out black body text that happens to be a single letter/digit and would
    otherwise be mistaken for a grid label.

    Parameters
    ----------
    img               : full BGR map image (may be None → check skipped)
    bbox              : [x1, y1, x2, y2] in image-pixel coordinates
    min_red_fraction  : minimum fraction of bbox pixels that must be red
    """
    if img is None:
        return True   # no image available → skip colour check, allow all

    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    h, w = img.shape[:2]
    bx1 = max(0, bx1);  by1 = max(0, by1)
    bx2 = min(w, bx2);  by2 = min(h, by2)
    if bx2 <= bx1 or by2 <= by1:
        return False

    region = img[by1:by2, bx1:bx2]
    if region.size == 0:
        return False

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    # Red wraps around 0 / 180 in the hue wheel
    mask1 = cv2.inRange(hsv, np.array([0,   70, 70]),  np.array([12,  255, 255]))
    mask2 = cv2.inRange(hsv, np.array([165, 70, 70]),  np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(mask1, mask2)

    total_px    = region.shape[0] * region.shape[1]
    red_px      = int(red_mask.sum()) // 255
    return (red_px / total_px) >= min_red_fraction

MANIFEST_PATH   = config.LOGS_FOLDER / 'tiles_manifest.json'
OCR_RAW_PATH    = config.LOGS_FOLDER / 'ocr_results_raw.json'
GRID_OUT_PATH   = config.LOGS_FOLDER / 'grid_detection.json'

# Row labels and column labels used on Survey of India topo sheets
ROW_LABELS    = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
COLUMN_LABELS = [str(i) for i in range(1, 30)]

# Survey of India standard grid: 4 columns x 4 rows (can be overridden in .env)
DEFAULT_COLS = int(getattr(config, 'GRID_DEFAULT_COLS', 4))
DEFAULT_ROWS = int(getattr(config, 'GRID_DEFAULT_ROWS', 4))

# Margin zone (fraction of image dimension) to search for grid boundary labels
MARGIN_FRACTION = 0.15

# Maximum character height (as fraction of image height) for a valid grid label.
# Grid labels (A/B/C, 1/2/3) are small annotation text. Large decorative area-name
# letters on SOI maps (e.g. "P U R H A T" spanning the map body) have a character
# height of 5-15% of the image. Reject anything taller than 4% of image height.
GRID_LABEL_MAX_HEIGHT_FRACTION = 0.04

# OCR commonly misreads these as grid labels — map them to correct value
OCR_CORRECTIONS = {
    '0': 'O',   # zero misread as letter O (row label)
    'l': '1',   # lowercase L misread as 1 (col label)
    'I': '1',   # capital I misread as 1
    'S': '5',   # S misread as 5
    'G': '6',   # G misread as 6
}

YEAR_VALUE_RE = re.compile(r'(?<!\d)(\d{4}(?:[-–]\d{2,4})?)(?!\d)')


def _normalize_year_value(raw: str) -> str:
    raw = (raw or '').strip().rstrip('.,;:').replace('–', '-')
    m = re.match(r'^(\d{4})-(\d{2,4})$', raw)
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw if len(end_raw) == 4 else f'{m.group(1)[:2]}{end_raw}')
        if 1500 <= start <= 2100 and 1500 <= end <= 2100 and start <= end <= start + 20:
            return f'{start}-{str(end)[2:]}'
        return ''

    m = re.match(r'^(\d{4})$', raw)
    if m:
        year = int(m.group(1))
        if 1500 <= year <= 2100:
            return m.group(1)
    return ''


def _group_ocr_lines(detections: List[Dict]) -> List[Dict]:
    items = []
    for d in detections:
        text = d.get('text', '').strip()
        bbox = d.get('bbox') or []
        if not text or len(bbox) != 4:
            continue
        bx1, by1, bx2, by2 = bbox
        cy = (by1 + by2) / 2
        h = max(by2 - by1, 1)
        items.append({
            'text': text,
            'bbox': [bx1, by1, bx2, by2],
            'x': bx1,
            'cy': cy,
            'h': h,
        })

    items.sort(key=lambda item: (item['cy'], item['x']))
    lines: List[Dict] = []
    for item in items:
        if not lines:
            lines.append({'items': [item], 'cy': item['cy'], 'avg_h': item['h']})
            continue

        last = lines[-1]
        tol = max(last['avg_h'], item['h']) * 0.8
        if abs(item['cy'] - last['cy']) <= tol:
            last['items'].append(item)
            count = len(last['items'])
            last['cy'] = ((last['cy'] * (count - 1)) + item['cy']) / count
            last['avg_h'] = ((last['avg_h'] * (count - 1)) + item['h']) / count
        else:
            lines.append({'items': [item], 'cy': item['cy'], 'avg_h': item['h']})

    grouped = []
    for line in lines:
        ordered = sorted(line['items'], key=lambda item: item['x'])
        grouped.append({
            'text': ' '.join(item['text'] for item in ordered),
            'cy': line['cy'],
            'avg_h': line['avg_h'],
        })
    return grouped


def _score_year_candidate(context: str, year_value: str, line_cy: float, img_height: int) -> int:
    context = context.lower()
    score = 0

    if re.search(r'\b(surv(?:ey(?:ed)?)?|season)\b', context):
        score += 12
    if re.search(r'\b(revis(?:ed)?|publish(?:ed)?|edition|compiled|drawn|sketch(?:ed)?|prepared)\b', context):
        score += 10
    if re.search(r'\b(date|dated|year|a\.?d\.?)\b', context):
        score += 8
    if re.search(r'\b(map|plate|plan|copy|title|sheet)\b', context):
        score += 4
    if '-' in year_value:
        score += 2

    if line_cy <= img_height * 0.20 or line_cy >= img_height * 0.80:
        score += 3

    if re.search(r'\b(scale|miles?|yards?|feet|foot|kilomet(?:er|re)s?|metres?|contours?|elevation|latitude|longitude|north|south|east|west|grid|legend)\b', context):
        score -= 8

    return score


# ─────────────────────────────────────────────────────────────────────────────
def detect_survey_year_from_ocr(
    detections: List[Dict],
    img_width: int,
    img_height: int,
) -> str:
    """
    Detect the printed year from OCR text.

    Common SOI formats printed on the map:
        "Surveyed 1934-35."        → "1934-35"
        "Surveyed 1936."           → "1936"
        "Revised 1972."            → "1972"
        "Surveyed 1928-29, published 1935."  → "1928-29"

    Non-SOI maps may print a year in a title, plate caption, date note,
    or bottom margin. Those are also accepted when OCR can read them.

    Returns a string like "1934-35", "1972", etc., or '' if not found.
    """
    lines = _group_ocr_lines(detections)
    if not lines:
        return ''

    candidates = []
    for line in lines:
        text = line['text']
        for match in YEAR_VALUE_RE.finditer(text):
            year_value = _normalize_year_value(match.group(1))
            if not year_value:
                continue

            start, end = match.span(1)
            context = text[max(0, start - 30): min(len(text), end + 30)]
            score = _score_year_candidate(context, year_value, line['cy'], img_height)
            candidates.append({
                'value': year_value,
                'score': score,
                'line_cy': line['cy'],
            })

    if candidates:
        strong = [c for c in candidates if c['score'] >= 8]
        pool = strong or [
            c for c in candidates
            if c['line_cy'] <= img_height * 0.20 or c['line_cy'] >= img_height * 0.80
        ]
        if pool:
            pool.sort(
                key=lambda c: (
                    c['score'],
                    1 if '-' in c['value'] else 0,
                    1 if (c['line_cy'] <= img_height * 0.20 or c['line_cy'] >= img_height * 0.80) else 0,
                ),
                reverse=True,
            )
            return pool[0]['value']

    return ''


# ─────────────────────────────────────────────────────────────────────────────
def detect_map_number_from_ocr(
    detections: List[Dict],
    img_width: int,
    img_height: int,
) -> str:
    """
    Scan OCR detections for the official SOI sheet number printed in the
    top-right corner of every Survey of India toposheet.

    Format on the map:   No. 74  A
                                ──     ← fraction line
                                10
    OCR may return this as:
      - Single token:  "No. 74 A/10"  or  "No. 74 A"  or  "74 A/10"
      - Two tokens:    "No. 74 A"  +  "10" just below
      - Three tokens:  "No."  +  "74"  +  "A"  (with "10" potentially
                       overlapping the "A" token vertically as a fraction)
      - Four tokens:   "No."  +  "74"  +  "A"  +  "10" fully separate

    Returns canonical string like "74 A/10", or '' if not detected.
    """
    # Search zone: top 30 % of height, right-most 45 % of width
    zone_x = img_width  * 0.55   # right 45 % of image
    zone_y = img_height * 0.30   # top 30 % of image

    # Pre-filter detections to the zone and cache cx/cy
    zone_dets = []
    for d in detections:
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2
        if cx >= zone_x and cy <= zone_y:
            zone_dets.append({
                'text': d['text'].strip(),
                'bbox': [bx1, by1, bx2, by2],
                'cx': cx, 'cy': cy,
            })

    # ── Helper: find a 1-2 digit sub-number near/below a reference bbox ──
    def _find_subnum_below(ref_bbox, candidates, exclude_text=''):
        rbx1, rby1, rbx2, rby2 = ref_bbox
        main_cx = (rbx1 + rbx2) / 2
        h = max(rby2 - rby1, 1)
        # Search from the lower half of the ref bbox downward
        y_start = rby1 + h * 0.5
        y_end   = rby2 + h * 5
        for d2 in candidates:
            txt = d2['text']
            if (re.match(r'^\d{1,2}$', txt)
                    and txt != exclude_text
                    and abs(d2['cx'] - main_cx) < h * 3
                    and y_start <= d2['cy'] <= y_end):
                return txt
        return None

    # ── Pass 1: Single-token patterns ─────────────────────────────────────
    # Pattern: "No. 74 A/10"  or  "No. 74 A"  (with optional sub-number)
    no_pattern = re.compile(
        r'No\.?\s*(\d{1,3})\s+([A-HJ-P])\s*(?:[/\\](\d{1,2}))?',
        re.I,
    )
    # Pattern: bare "74 A/10"  or  "74 A"
    bare_pattern = re.compile(
        r'^(\d{1,3})\s+([A-HJ-P])\s*(?:[/\\](\d{1,2}))?$',
        re.I,
    )

    for d in zone_dets:
        text = d['text']
        m = no_pattern.search(text) or bare_pattern.match(text)
        if m:
            block  = m.group(1)
            letter = m.group(2).upper()
            subnum = m.group(3) if (m.lastindex and m.lastindex >= 3) else None
            if not subnum:
                subnum = _find_subnum_below(d['bbox'], zone_dets, exclude_text=block)
            if block and letter and subnum:
                return f'{block} {letter}/{subnum}'
            if block and letter:
                return f'{block} {letter}'

    # ── Pass 2: Multi-token matching ──────────────────────────────────────
    # Step 1: Find "No." token in zone (optional anchor)
    no_token = None
    for d in zone_dets:
        if re.match(r'^No\.?$', d['text'], re.I):
            no_token = d
            break

    # Step 2: Collect candidate number tokens (1-3 digits)
    num_candidates = []
    for d in zone_dets:
        if not re.match(r'^\d{1,3}$', d['text']):
            continue
        if no_token is not None:
            # Anchor: number must be to the right of "No." at similar height
            no_h = max(no_token['bbox'][3] - no_token['bbox'][1], 1)
            if (d['cx'] > no_token['cx']
                    and abs(d['cy'] - no_token['cy']) < no_h * 1.5
                    and d['cx'] - no_token['cx'] < no_h * 15):
                num_candidates.append(d)
        else:
            num_candidates.append(d)

    # Step 3: For each candidate number, look for a letter to its right
    for num_d in sorted(num_candidates, key=lambda x: x['cx']):
        num_cx = num_d['cx']
        num_cy = num_d['cy']
        num_h  = max(num_d['bbox'][3] - num_d['bbox'][1], 1)
        block  = num_d['text']

        letter_token = None
        for d in zone_dets:
            if not re.match(r'^[A-HJ-P]$', d['text'], re.I):
                continue
            if (d['cx'] > num_cx
                    and abs(d['cy'] - num_cy) < num_h * 1.5
                    and d['cx'] - num_cx < num_h * 15):
                if letter_token is None or d['cx'] < letter_token['cx']:
                    letter_token = d

        if letter_token is None:
            continue

        letter = letter_token['text'].upper()

        # Step 4: Find sub-number below the letter (or below the number)
        subnum = _find_subnum_below(letter_token['bbox'], zone_dets, exclude_text=block)
        if not subnum:
            subnum = _find_subnum_below(num_d['bbox'], zone_dets, exclude_text=block)

        if block and letter and subnum:
            return f'{block} {letter}/{subnum}'
        if block and letter:
            return f'{block} {letter}'

    return ''


# ─────────────────────────────────────────────────────────────────────────────
def _cluster(positions: List[float], gap: int = 30) -> List[float]:
    """Cluster nearby positions by taking the mean of each group."""
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


def _regularity_score(positions: List[float]) -> float:
    """
    Score 0-1 for how evenly spaced a list of line positions is.
    1.0 = perfectly regular grid, 0.0 = completely irregular.
    """
    if len(positions) < 2:
        return 0.0
    gaps = [positions[i+1] - positions[i] for i in range(len(positions) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap == 0:
        return 0.0
    variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
    cv = (variance ** 0.5) / mean_gap   # coefficient of variation
    return max(0.0, 1.0 - cv)           # 0 variance → score 1.0


def _interpolate_grid(positions: List[float], img_size: int, expected: int) -> List[float]:
    """
    If we detected fewer lines than expected, fill in missing ones by
    extrapolating from the median spacing. Works for both rows and cols.
    """
    if len(positions) < 2 or len(positions) >= expected:
        return positions

    gaps = [positions[i+1] - positions[i] for i in range(len(positions) - 1)]
    median_gap = sorted(gaps)[len(gaps) // 2]

    # Extend backward from first detected line
    result = list(positions)
    while result[0] - median_gap > 0:
        result.insert(0, result[0] - median_gap)

    # Extend forward from last detected line
    while result[-1] + median_gap < img_size:
        result.append(result[-1] + median_gap)

    # Keep only positions inside image
    result = [p for p in result if 0 <= p <= img_size]
    return result


# ─────────────────────────────────────────────────────────────────────────────
def detect_grid_from_ocr(
    detections: List[Dict],
    img_width: int,
    img_height: int,
    img: np.ndarray = None,
) -> Tuple[List[float], List[float]]:
    """
    Find grid line positions by looking for column markers (A/B/C at top/bottom)
    and row markers (1/2/3 at left/right) in the OCR detections.

    SOI convention
    --------------
    - Letters A, B, C … printed in RED at TOP and BOTTOM margins → mark COLUMN boundaries
    - Numbers 1, 2, 3 … printed in RED at LEFT and RIGHT margins → mark ROW boundaries
    - Grid reference format: ROW_NUMBER + COLUMN_LETTER  → "2B"

    Improvements
    ------------
    - Red-ink colour filter (via _is_red_region) keeps only genuine red margin labels
    - Uses MARGIN_FRACTION (15%) instead of fixed 80 px → resolution-independent
    - Validates that found labels form a consecutive sequence (A,B,C not random)
    - Applies OCR correction map for common misreads
    - Returns sorted grid line positions
    """
    margin_x = img_width  * MARGIN_FRACTION
    margin_y = img_height * MARGIN_FRACTION

    row_pattern = re.compile(r'^[A-Z]$')
    col_pattern = re.compile(r'^\d{1,2}$')

    row_candidates = {}   # label → y position
    col_candidates = {}   # label → x position

    for d in detections:
        raw_text = d['text'].strip()
        text = OCR_CORRECTIONS.get(raw_text, raw_text).upper()
        bx1, by1, bx2, by2 = d['bbox']
        cx = (bx1 + bx2) / 2
        cy = (by1 + by2) / 2

        # ── COLUMN label: single letter in TOP or BOTTOM margin ──────────────
        # SOI prints A, B, C... in red at top/bottom to mark column divisions.
        if row_pattern.match(text):
            in_top    = cy < margin_y
            in_bottom = cy > img_height - margin_y
            if in_top or in_bottom:
                # Reject large decorative area-name letters (e.g. "P U R H A T")
                char_h = abs(by2 - by1)
                if char_h > img_height * GRID_LABEL_MAX_HEIGHT_FRACTION:
                    continue
                # Reject if not red ink (body text can be single letters too)
                if not _is_red_region(img, [bx1, by1, bx2, by2]):
                    continue
                if text not in row_candidates or abs(cy - img_height/2) < abs(row_candidates[text] - img_height/2):
                    row_candidates[text] = cx   # x position → vertical column boundary

        # ── ROW label: 1-2 digit number in LEFT or RIGHT margin ──────────────
        # SOI prints 1, 2, 3... in red at left/right to mark row divisions.
        if col_pattern.match(text):
            in_left  = cx < margin_x
            in_right = cx > img_width - margin_x
            if in_left or in_right:
                char_h = abs(by2 - by1)
                if char_h > img_height * GRID_LABEL_MAX_HEIGHT_FRACTION:
                    continue
                if not _is_red_region(img, [bx1, by1, bx2, by2]):
                    continue
                if text not in col_candidates or abs(cx - img_width/2) < abs(col_candidates[text] - img_width/2):
                    col_candidates[text] = cy   # y position → horizontal row boundary

    # Validate: only keep labels that form consecutive runs (A,B,C or 1,2,3)
    def consecutive_positions(candidates: dict, is_numeric: bool) -> List[float]:
        if not candidates:
            return []
        if is_numeric:
            items = sorted(candidates.items(), key=lambda x: int(x[0]))
            # Check consecutive integers
            nums = [int(k) for k, _ in items]
            valid = []
            for i, (k, v) in enumerate(items):
                if i == 0 or int(k) == nums[i-1] + 1:
                    valid.append(v)
                else:
                    break   # stop at first gap
        else:
            items = sorted(candidates.items(), key=lambda x: ROW_LABELS.index(x[0]) if x[0] in ROW_LABELS else 99)
            letters = [k for k, _ in items]
            valid = []
            for i, (k, v) in enumerate(items):
                if i == 0 or (k in ROW_LABELS and ROW_LABELS.index(k) == ROW_LABELS.index(letters[i-1]) + 1):
                    valid.append(v)
                else:
                    break
        return sorted(valid)

    col_xs = consecutive_positions(row_candidates, is_numeric=False)
    row_ys = consecutive_positions(col_candidates, is_numeric=True)

    return _cluster(col_xs, gap=50), _cluster(row_ys, gap=50)


# ─────────────────────────────────────────────────────────────────────────────
def detect_grid_from_image(img: np.ndarray) -> Tuple[List[float], List[float]]:
    """
    Detect grid lines from the image using adaptive Hough line transform.

    Improvements:
    - Adaptive threshold based on image size (instead of fixed 200)
    - Bilateral filter before edge detection to preserve lines but reduce text noise
    - Minimum line length = 60% of image dimension (grid lines span full map)
    - Cluster gap scales with image size
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    h, w = gray.shape

    # Bilateral filter — reduces noise while keeping sharp lines
    gray = cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)

    # Adaptive edge detection
    median_val = float(np.median(gray))
    low  = int(max(0,   0.6 * median_val))
    high = int(min(255, 1.4 * median_val))
    edges = cv2.Canny(gray, low, high, apertureSize=3)

    # Adaptive Hough parameters
    min_dim        = min(w, h)
    hough_thresh   = max(80, int(min_dim * 0.06))   # scales with image size
    min_line_len   = min_dim * 0.60                 # must span 60% of map
    max_line_gap   = int(min_dim * 0.02)            # allow small breaks in line

    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=hough_thresh,
        minLineLength=min_line_len,
        maxLineGap=max_line_gap
    )

    col_xs = []
    row_ys = []

    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1)))

            if angle < 3:     # nearly horizontal → row boundary
                row_ys.append((y1 + y2) / 2)
            elif angle > 87:  # nearly vertical → col boundary
                col_xs.append((x1 + x2) / 2)

    cluster_gap = max(20, int(min_dim * 0.01))
    return _cluster(col_xs, gap=cluster_gap), _cluster(row_ys, gap=cluster_gap)


# ─────────────────────────────────────────────────────────────────────────────
def build_final_grid(
    col_xs: List[float],
    row_ys: List[float],
    img_width: int,
    img_height: int
) -> Tuple[List[float], List[float], float]:
    """
    Validate detected grid lines, attempt interpolation of missing lines,
    and fall back to equal-division if detection was too poor.

    Returns (final_col_xs, final_row_ys, confidence_0_to_1).
    """
    # If we have enough lines, check regularity and interpolate missing ones
    if len(col_xs) >= 2:
        col_xs = _interpolate_grid(col_xs, img_width, DEFAULT_COLS + 1)
    else:
        # Full fallback: divide image into equal sections
        col_xs = [img_width * i / DEFAULT_COLS for i in range(1, DEFAULT_COLS)]

    if len(row_ys) >= 2:
        row_ys = _interpolate_grid(row_ys, img_height, DEFAULT_ROWS + 1)
    else:
        row_ys = [img_height * i / DEFAULT_ROWS for i in range(1, DEFAULT_ROWS)]

    # Regularity scores for confidence reporting
    col_score = _regularity_score(col_xs)
    row_score = _regularity_score(row_ys)
    confidence = round((col_score + row_score) / 2, 3)

    return col_xs, row_ys, confidence


# ─────────────────────────────────────────────────────────────────────────────
def assign_grid_ref(
    bbox: List[int],
    col_xs: List[float],
    row_ys: List[float],
    img_width: int,
    img_height: int
) -> str:
    """
    Given a detection bounding box and grid line positions,
    return the grid reference in SOI format: ROW_NUMBER + COLUMN_LETTER
    e.g. "2B"  (row 2, column B).

    col_xs  → vertical lines separating columns A | B | C | D  (from left)
    row_ys  → horizontal lines separating rows   1 | 2 | 3 | 4  (from top)
    """
    bx1, by1, bx2, by2 = bbox
    cx = (bx1 + bx2) / 2
    cy = (by1 + by2) / 2

    # col_idx: how many vertical lines are left of cx → 0=col A, 1=col B, 2=col C …
    col_idx = sum(1 for x in col_xs if x < cx)
    # row_idx: how many horizontal lines are above cy → 0=row 1, 1=row 2, 2=row 3 …
    row_idx = sum(1 for y in row_ys if y < cy)

    # SOI notation: columns use letters, rows use numbers
    col_letter = ROW_LABELS[col_idx]  if col_idx < len(ROW_LABELS)    else f'C{col_idx}'
    row_num    = str(row_idx + 1)     # 1-based

    return f'{row_num}{col_letter}'   # e.g. "2B"


# ─────────────────────────────────────────────────────────────────────────────
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

        # ── Step 1: Load the image (needed for red-ink colour check + Hough fallback)
        img = None
        map_file = map_info.get('file', '')
        if map_file:
            try:
                img = load_image(map_file)
            except Exception as _e:
                print(f'  Warning: could not load image for colour check: {_e}')

        # ── Step 2: OCR-based label detection with red-ink colour filtering
        col_xs, row_ys = detect_grid_from_ocr(detections, img_width, img_height, img=img)
        method = 'ocr-labels (red-ink filtered)'

        # ── Step 3: If OCR gave too few lines, try image Hough detection
        if len(col_xs) < 2 or len(row_ys) < 2:
            print(f'  OCR label method: {len(col_xs)} cols, {len(row_ys)} rows — trying Hough lines...')
            try:
                if img is None and map_file:
                    img = load_image(map_file)
                col_xs_img, row_ys_img = detect_grid_from_image(img)
                # Take whichever method found more lines
                if len(col_xs_img) > len(col_xs):
                    col_xs = col_xs_img
                    method = 'hough-image'
                if len(row_ys_img) > len(row_ys):
                    row_ys = row_ys_img
                    method = 'hough-image'
            except Exception as e:
                print(f'  Hough detection failed: {e}')

        # ── Step 3: Validate, interpolate missing lines, compute confidence
        col_xs, row_ys, grid_confidence = build_final_grid(col_xs, row_ys, img_width, img_height)

        if grid_confidence < 0.4:
            method += ' (fallback-equal-division)'

        print(f'  Method: {method}')
        print(f'  Grid lines: {len(col_xs)} col boundaries, {len(row_ys)} row boundaries')
        print(f'  Grid confidence: {grid_confidence:.2f}')

        # Sample a few grid refs so operator can verify format looks like "2B"
        sample_refs = []
        for det in detections[:5]:
            sample_refs.append(assign_grid_ref(det['bbox'], col_xs, row_ys, img_width, img_height))
        if sample_refs:
            print(f'  Sample grid refs: {sample_refs}  (format: rowNumber+colLetter, e.g. "2B")')

        # Assign grid references to every detection
        enriched = []
        for det in detections:
            grid_ref = assign_grid_ref(det['bbox'], col_xs, row_ys, img_width, img_height)
            enriched.append({**det, 'grid_reference': grid_ref})

        unique_cells = len(set(d['grid_reference'] for d in enriched))
        print(f'  Detections: {len(enriched)} items across {unique_cells} unique grid cells')

        # ── Step 4: Extract lat/lon bounds from margin labels → assign to cells ──
        coord_bounds = extract_coord_bounds(detections)
        col_labels = [ROW_LABELS[i] for i in range(max(len(col_xs) + 1, DEFAULT_COLS))]
        row_labels = [COLUMN_LABELS[i] for i in range(max(len(row_ys) + 1, DEFAULT_ROWS))]
        cell_coords = assign_coords_to_grid(coord_bounds, col_labels, row_labels)
        if coord_bounds.get('found'):
            print(f'  Coordinates: lat {coord_bounds["lat_min"]:.4f}–{coord_bounds["lat_max"]:.4f} '
                  f'  lon {coord_bounds["lon_min"]:.4f}–{coord_bounds["lon_max"]:.4f}')
        else:
            print('  Coordinates: not found in OCR text')

        # ── Step 5: Detect official map number from top-right corner ──────────
        ocr_map_number = detect_map_number_from_ocr(detections, img_width, img_height)
        if ocr_map_number:
            print(f'  Map number (OCR top-right corner): {ocr_map_number}')
        else:
            print('  Map number: not detected in top-right corner (will use filename)')

        # ── Step 6: Detect survey year from top margin ───────────────────────
        ocr_survey_year = detect_survey_year_from_ocr(detections, img_width, img_height)
        if ocr_survey_year:
            print(f'  Survey year (OCR top margin): {ocr_survey_year}')
        else:
            print('  Survey year: not detected in top margin')

        grid_results[map_name] = {
            'col_lines':        col_xs,
            'row_lines':        row_ys,
            'grid_confidence':  grid_confidence,
            'detection_method': method,
            'coord_bounds':     coord_bounds,
            'cell_coords':      cell_coords,
            'detections':       enriched,
            'ocr_map_number':   ocr_map_number,   # e.g. "74 A/10" from printed corner
            'ocr_survey_year':  ocr_survey_year,  # e.g. "1934-35" from top margin
        }

    with open(GRID_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(grid_results, f, ensure_ascii=False, indent=2)
    print(f'\n[GRID] Saved: {GRID_OUT_PATH}')
    return grid_results


if __name__ == '__main__':
    assign_all_grid_refs()
    print('[GRID] Phase 3 complete.')
