# utils/metadata_utils.py  –  Parse SOI / Zenodo map filenames into structured metadata
#
# Zenodo (John Brown OSOIM collection) filename format:
#   "72 P]16 Birbhum District (1924).jpg"
#   "73 P]16 Birbhum & Murshidabad Districts (1922) 35MM-FILMED.jpg"
#   "72 B]04 Siwan District (1974) BW@.jpg"
#   Parts: <block> <letter>]<num> <district(s)> (<year>)[optional notes]
#
# SOI Sheet Reference System:
#   India is divided into 1° × 1° blocks numbered 1–260 (approx).
#   Each block is subdivided into a 4×4 grid of 16 sub-sheets labelled A–P
#   (letters A, B, C, D, E, F, G, H, J, K, L, M, N, O, P — I is skipped).
#   Sub-sheets are numbered 1–16 within each letter block.
#   Standard ref: "72 P/16"  →  block=72, letter=P, sub-num=16
#
# Internal grid on each printed sheet:
#   Old 1:63,360 (1-inch) maps  → 4 cols × 4 rows  (A,B,C,D  ×  1,2,3,4)
#   1:50,000 maps               → 4 cols × 4 rows  (same)
#   1:25,000 maps               → 4 cols × 4 rows  (further subdivided)

import re
from pathlib import Path

# ─── Filename regex ───────────────────────────────────────────────────────────
# Handles: "72 P]16 Birbhum District (1924) BW@.jpg"
_SOI_ZENODO = re.compile(
    r'^(?P<block>\d{1,3})\s+'            # block number
    r'(?P<letter>[A-HJ-P])\]'            # sheet letter (I skipped)
    r'(?P<num>\d{1,2})\s+'               # sheet sub-number
    r'(?P<district>[^(]+?)\s*'           # district name (greedy to before year)
    r'\((?P<year>\d{4})\)'               # year in parentheses
    r'(?P<suffix>.*)',                   # any trailing notes
    re.IGNORECASE,
)

# Also handle simpler patterns like "Bengal 73P16 (1922)"
_SOI_SHORT = re.compile(
    r'(?P<block>\d{1,3})\s*(?P<letter>[A-HJ-P])[/\]\s](?P<num>\d{1,2})',
    re.IGNORECASE,
)

# ─── Approximate SW-corner (lat, lon) for common block numbers ────────────────
# Full table would require the complete SOI index; these cover blocks 45–90
# Format: block_str → (lat_sw_deg, lon_sw_deg)
_BLOCK_CORNERS: dict[str, tuple[float, float]] = {
    '45': (21, 73),  '46': (21, 74),  '47': (21, 75),  '48': (21, 76),
    '49': (21, 77),  '50': (21, 78),  '51': (21, 79),  '52': (21, 80),
    '53': (21, 81),  '54': (21, 82),  '55': (22, 73),  '56': (22, 74),
    '57': (22, 75),  '58': (22, 76),  '63': (23, 73),  '64': (23, 74),
    '65': (23, 75),  '66': (23, 76),  '72': (23, 86),  '73': (23, 87),
    '74': (23, 88),  '75': (23, 89),  '76': (23, 90),  '83': (24, 86),
    '84': (24, 87),  '85': (24, 88),  '86': (24, 89),  '87': (25, 68),
    '88': (25, 69),  '89': (25, 73),  '90': (25, 74),
}

# Sub-sheet letter → row index within the block (A=0, B=1, ..., H=7, J=8, ...)
# (I is skipped, SOI convention)
_LETTER_ROW: dict[str, int] = {
    'A': 0, 'B': 1, 'C': 2, 'D': 3,
    'E': 4, 'F': 5, 'G': 6, 'H': 7,
    'J': 8, 'K': 9, 'L': 10, 'M': 11,
    'N': 12, 'O': 13, 'P': 14,
}


# ─── Public API ───────────────────────────────────────────────────────────────

def parse_soi_filename(filename: str) -> dict:
    """
    Parse a Zenodo-style SOI map filename into structured metadata.

    Parameters
    ----------
    filename : str
        Filename or full path, e.g. "72 P]16 Birbhum District (1924) BW@.jpg"

    Returns
    -------
    dict with keys:
        block         – SOI block number string, e.g. "72"
        sheet_letter  – letter sub-block, e.g. "P"
        sheet_num     – sub-number int, e.g. 16
        sheet_ref     – canonical ref string, e.g. "72P/16"
        district      – district name(s), e.g. "Birbhum"
        year          – survey/edition year int, e.g. 1924
        scale_guess   – 63360 for pre-1960 maps, 50000 otherwise
        notes         – BW/Preliminary/Partial flags
        approx_lat    – approx latitude of SW corner (None if block unknown)
        approx_lon    – approx longitude of SW corner (None if block unknown)
    If filename doesn't match, returns minimal dict with raw_name only.
    """
    stem = Path(filename).stem  # strip extension & path

    m = _SOI_ZENODO.match(stem)
    if not m:
        return {'raw_name': stem, 'sheet_ref': stem, 'district': '', 'year': None,
                'scale_guess': 50000, 'notes': ''}

    block   = m.group('block')
    letter  = m.group('letter').upper()
    num     = int(m.group('num'))
    raw_dist = m.group('district').strip()
    year    = int(m.group('year'))
    suffix  = m.group('suffix').strip()

    # Clean district: remove trailing "District(s)", "Dist." etc.
    district = re.sub(r'\s+(Districts?|Dist\.?|Pargana|Parganas)$', '', raw_dist,
                      flags=re.I).strip().rstrip('&').rstrip(',').strip()

    # Parse suffix for quality notes
    notes = []
    if re.search(r'\bBW\b', suffix, re.I):
        notes.append('Black & White scan')
    if re.search(r'Prelim', suffix, re.I):
        notes.append('Preliminary edition')
    if '@' in suffix:
        notes.append('Partial/damaged sheet')
    if re.search(r'35\s*[Mm][Mm]', suffix):
        notes.append('35mm film scan')

    # Canonical sheet reference
    sheet_ref = f'{block}{letter}/{num:02d}'

    # Scale heuristic: 1-inch (63k) maps dominated before ~1960
    scale_guess = 63360 if year < 1960 else 50000

    # Approximate geographic corner from block lookup
    corner = _BLOCK_CORNERS.get(block)
    approx_lat = corner[0] if corner else None
    approx_lon = corner[1] if corner else None

    return {
        'block':        block,
        'sheet_letter': letter,
        'sheet_num':    num,
        'sheet_ref':    sheet_ref,
        'district':     district,
        'year':         year,
        'scale_guess':  scale_guess,
        'notes':        '; '.join(notes),
        'approx_lat':   approx_lat,
        'approx_lon':   approx_lon,
    }


def sheet_ref_to_coords(block: str, letter: str, num: int) -> dict | None:
    """
    Convert a sheet reference to an approximate lat/lon bounding box.
    Each standard SOI block is 1° × 1°.
    The 16 sub-sheets per letter group (A–P) each cover 0.25° × 0.25°.

    Returns {lat_min, lat_max, lon_min, lon_max} or None if block unknown.
    """
    corner = _BLOCK_CORNERS.get(str(block))
    if not corner:
        return None

    lat_sw, lon_sw = corner
    row_idx = _LETTER_ROW.get(letter.upper())
    if row_idx is None:
        return None

    # Within a letter group (4 sub-sheets per row, 4 rows per letter block)
    # num goes 1-16: row 0-3, col 0-3 within the letter block
    # Each of the 16 sub-letters covers 4°÷16 = 0.25° in each direction
    # Letter row within block (A=top-left, P=bottom-right, row-major)
    block_col = row_idx % 4    # 0-3 (E/W within block)
    block_row = row_idx // 4   # 0-3 (N/S within block, 0=north)

    sub_col = (num - 1) % 4    # 0-3 within letter block
    sub_row = (num - 1) // 4   # 0-3 within letter block

    cell_size = 0.25  # degrees per sub-sheet

    lon_min = lon_sw + (block_col * 4 + sub_col) * cell_size
    lat_max = (lat_sw + 1.0) - (block_row * 4 + sub_row) * cell_size
    lon_max = lon_min + cell_size
    lat_min = lat_max - cell_size

    return {
        'lat_min': round(lat_min, 4),
        'lat_max': round(lat_max, 4),
        'lon_min': round(lon_min, 4),
        'lon_max': round(lon_max, 4),
    }


def normalize_map_name(filename: str) -> str:
    """Return a short, filesystem-safe identifier for a map based on its filename."""
    meta = parse_soi_filename(filename)
    if meta.get('year') and meta.get('sheet_ref', '') != Path(filename).stem:
        return f"{meta['sheet_ref'].replace('/', '_')}_{meta['year']}"
    return Path(filename).stem
