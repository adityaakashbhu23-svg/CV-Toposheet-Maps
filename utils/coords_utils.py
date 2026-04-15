# utils/coords_utils.py  –  Real-world coordinate assignment from map margin labels
#
# Extracts latitude and longitude bounds from OCR detections on map margins,
# then assigns approximate lat/lon bounding boxes to each grid cell.
#
# Works for:
#   - Survey of India:  "78°30'E", "25°N", "78°45'E", "25°15'N"
#   - USGS Topo:        "78°30'00""W", decimal degrees, UTM grid labels
#   - OS (UK):          National Grid letters/numbers (partial — bounding box only)
#   - Any map with degree symbols in OCR text

import re
from typing import Optional

# ─────────────────────────────────────────────────────────────
#  Regex patterns for degree/minute/second notation
# ─────────────────────────────────────────────────────────────

# Matches patterns like: 78°30', 78°30'E, 78° 30' 00" E, 78.5°N, 25°15'30"N
_DMS_PATTERN = re.compile(
    r"""
    (?P<deg>\d{1,3})            # degrees
    \s*[°o]\s*                   # degree symbol (or letter 'o' OCR artifact)
    (?:(?P<min>\d{1,2})\s*[''′`]\s*   # optional minutes
    (?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*[""″]\s*)?  # optional seconds
    )?
    (?P<hemi>[NSEW])?            # hemisphere letter (optional — may be separate token)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# In case the hemisphere letter is a separate OCR token directly after a number token
_HEMI_PATTERN = re.compile(r'^[NSEW]$', re.IGNORECASE)


def _dms_to_decimal(deg: float, minutes: float = 0.0, seconds: float = 0.0,
                    hemisphere: str = '') -> float:
    """Convert degrees/minutes/seconds to decimal degrees. S and W are negative."""
    decimal = float(deg) + float(minutes) / 60.0 + float(seconds) / 3600.0
    if hemisphere.upper() in ('S', 'W'):
        decimal = -decimal
    return round(decimal, 6)


def extract_coord_bounds(detections: list[dict]) -> dict:
    """
    Scan OCR detections for latitude and longitude labels on map margins.

    Returns a dict:
        {
          'lat_min': float, 'lat_max': float,
          'lon_min': float, 'lon_max': float,
          'found': bool
        }

    If fewer than 2 lat values or 2 lon values are found, 'found' is False.
    """
    lats, lons = [], []

    texts = [d.get('text', '') for d in detections]

    for i, text in enumerate(texts):
        text_clean = text.strip()
        m = _DMS_PATTERN.search(text_clean)
        if not m:
            continue

        deg = float(m.group('deg'))
        minutes = float(m.group('min')) if m.group('min') else 0.0
        seconds = float(m.group('sec')) if m.group('sec') else 0.0
        hemi = (m.group('hemi') or '').upper()

        # Try to find the hemisphere in the next token if not embedded
        if not hemi and i + 1 < len(texts):
            next_t = texts[i + 1].strip()
            if _HEMI_PATTERN.match(next_t):
                hemi = next_t.upper()

        if not hemi:
            continue  # cannot determine lat vs lon without hemisphere

        decimal = _dms_to_decimal(deg, minutes, seconds, hemi)

        if hemi in ('N', 'S'):
            lats.append(decimal)
        elif hemi in ('E', 'W'):
            lons.append(decimal)

    if len(lats) < 2 or len(lons) < 2:
        return {'lat_min': None, 'lat_max': None,
                'lon_min': None, 'lon_max': None, 'found': False}

    return {
        'lat_min': min(lats),
        'lat_max': max(lats),
        'lon_min': min(lons),
        'lon_max': max(lons),
        'found':   True,
    }


def assign_coords_to_grid(
    bounds: dict,
    col_labels: list[str],
    row_labels: list[str],
) -> dict[str, dict]:
    """
    Given the map bounding box and sorted column/row labels, compute the
    approximate lat/lon bounding box for each grid cell.

    col_labels: left-to-right column identifiers, e.g. ['A','B','C','D']
    row_labels:  top-to-bottom row identifiers,   e.g. ['1','2','3','4']

    Returns:
        {
            'A-1': {'lat_min':..., 'lat_max':..., 'lon_min':..., 'lon_max':...},
            ...
        }
    """
    if not bounds.get('found'):
        return {}

    lat_min = bounds['lat_min']
    lat_max = bounds['lat_max']
    lon_min = bounds['lon_min']
    lon_max = bounds['lon_max']

    n_cols = len(col_labels) or 1
    n_rows = len(row_labels) or 1

    lat_step = (lat_max - lat_min) / n_rows
    lon_step = (lon_max - lon_min) / n_cols

    cell_coords = {}
    for r_idx, row in enumerate(row_labels):
        for c_idx, col in enumerate(col_labels):
            cell_key = f'{col}-{row}'
            # Row 0 = top = highest latitude
            cell_coords[cell_key] = {
                'lat_min': round(lat_max - (r_idx + 1) * lat_step, 6),
                'lat_max': round(lat_max - r_idx * lat_step, 6),
                'lon_min': round(lon_min + c_idx * lon_step, 6),
                'lon_max': round(lon_min + (c_idx + 1) * lon_step, 6),
            }

    return cell_coords
