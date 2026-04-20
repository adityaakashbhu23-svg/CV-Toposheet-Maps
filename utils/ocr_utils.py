# utils/ocr_utils.py  –  OCR extraction: Google Cloud Vision (primary), EasyOCR, Tesseract

import os
import json
import tempfile
import numpy as np
import cv2
from pathlib import Path
from typing import List, Dict, Optional

# EasyOCR (secondary) – initialized lazily to avoid slow startup
_easyocr_reader = None

def _get_easyocr():
    """Return a cached EasyOCR reader (initializes on first call)."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        print('[OCR] Initializing EasyOCR (first run may download models)...')
        _easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        print('[OCR] EasyOCR ready.')
    return _easyocr_reader


# Google Cloud Vision client – singleton to avoid per-tile auth overhead
_gcv_client = None
_gcv_client_lock = None  # will be set to threading.Lock on first use

# EasyOCR is not thread-safe – serialize all calls behind a lock
import threading as _threading
_easyocr_lock = _threading.Lock()

def _get_gcv_client():
    """Return a cached GCV ImageAnnotatorClient (initializes once per process)."""
    global _gcv_client, _gcv_client_lock
    import threading
    if _gcv_client_lock is None:
        _gcv_client_lock = threading.Lock()
    with _gcv_client_lock:
        if _gcv_client is None:
            from google.cloud import vision
            print('[OCR/GCV] Initializing Google Cloud Vision client...')
            _gcv_client = vision.ImageAnnotatorClient()
            print('[OCR/GCV] GCV client ready.')
    return _gcv_client


def ocr_tile_gcv(tile: np.ndarray, confidence_threshold: float = 0.3) -> List[Dict]:
    """
    Run Google Cloud Vision DOCUMENT_TEXT_DETECTION on a single tile.

    Uses DOCUMENT_TEXT_DETECTION instead of TEXT_DETECTION because:
    - Better at dense, multi-orientation text (like historical topo maps)
    - Returns per-symbol confidence scores → we use block-level confidence
    - Handles curved and overlapping text labels more accurately

    Authentication is handled via the GOOGLE_APPLICATION_CREDENTIALS env-var,
    which config.py sets to the absolute path of service_account.json before
    any OCR code runs.

    Returns a list of dicts: {text, confidence, bbox: [x1,y1,x2,y2]}
    """
    try:
        from google.cloud import vision as _vision_check  # noqa: just verify installed
    except ImportError:
        print('[OCR/GCV] google-cloud-vision not installed. Run: pip install google-cloud-vision')
        return []

    # Encode tile as PNG bytes (in-memory, no disk write)
    success, buf = cv2.imencode('.png', tile)
    if not success:
        print('[OCR/GCV] Failed to encode tile as PNG')
        return []
    image_bytes = buf.tobytes()

    try:
        from google.cloud import vision as _vision
        client   = _get_gcv_client()
        image    = _vision.Image(content=image_bytes)
        response = client.document_text_detection(image=image)
    except Exception as e:
        print(f'[OCR/GCV] API error: {e}')
        return []

    if response.error.message:
        print(f'[OCR/GCV] Response error: {response.error.message}')
        return []

    full_text = response.full_text_annotation
    if not full_text or not full_text.pages:
        return []

    detections = []

    # Walk pages → blocks → paragraphs → words
    for page in full_text.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                # Build word-level detections from paragraph words
                for word in para.words:
                    word_text = ''.join(s.text for s in word.symbols).strip()
                    if not word_text:
                        continue

                    # Compute average per-symbol confidence for this word
                    confidences = [s.confidence for s in word.symbols if s.confidence > 0]
                    conf = sum(confidences) / len(confidences) if confidences else 0.85

                    if conf < confidence_threshold:
                        continue

                    verts = word.bounding_box.vertices
                    xs = [v.x for v in verts]
                    ys = [v.y for v in verts]
                    if not xs or not ys:
                        continue

                    detections.append({
                        'text':       word_text,
                        'confidence': round(float(conf), 4),
                        'bbox':       [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                    })

    return detections


def ocr_tile_easyocr(tile: np.ndarray, confidence_threshold: float = 0.3) -> List[Dict]:
    """
    Run EasyOCR on a single tile.
    Returns a list of dicts: {text, confidence, bbox: [x1,y1,x2,y2]}
    """
    reader = _get_easyocr()
    try:
        with _easyocr_lock:
            results = reader.readtext(tile, detail=1, paragraph=False)
    except Exception as e:
        print(f'[OCR] EasyOCR error: {e}')
        return []

    detections = []
    for (bbox_pts, text, conf) in results:
        if conf < confidence_threshold or not text.strip():
            continue
        # bbox_pts is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        xs = [pt[0] for pt in bbox_pts]
        ys = [pt[1] for pt in bbox_pts]
        detections.append({
            'text':       text.strip(),
            'confidence': round(float(conf), 4),
            'bbox':       [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
        })
    return detections


def ocr_tile_tesseract(tile: np.ndarray, confidence_threshold: float = 0.3) -> List[Dict]:
    """
    Fallback OCR using pytesseract.
    Returns a list of dicts: {text, confidence, bbox: [x1,y1,x2,y2]}
    """
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        print('[OCR] pytesseract not installed. Install it with: pip install pytesseract')
        return []

    try:
        data = pytesseract.image_to_data(tile, output_type=Output.DICT, config='--psm 11')
    except Exception as e:
        print(f'[OCR] Tesseract error: {e}')
        return []

    detections = []
    n = len(data['text'])
    for i in range(n):
        text = data['text'][i].strip()
        conf_raw = data['conf'][i]
        if not text or conf_raw == -1:
            continue
        conf = float(conf_raw) / 100.0
        if conf < confidence_threshold:
            continue
        x = data['left'][i]
        y = data['top'][i]
        w = data['width'][i]
        h = data['height'][i]
        detections.append({
            'text':       text,
            'confidence': round(conf, 4),
            'bbox':       [x, y, x + w, y + h],
        })
    return detections


def ocr_tile(
    tile: np.ndarray,
    confidence_threshold: float = 0.3,
    use_tesseract_fallback: bool = True
) -> List[Dict]:
    """
    Run OCR on a tile using the engine configured in config.OCR_ENGINE:
      - 'gcv'        → Google Cloud Vision (primary, highest accuracy)
      - 'easyocr'    → EasyOCR
      - 'tesseract'  → Tesseract directly

    Falls back to EasyOCR then Tesseract if the primary returns nothing.
    """
    try:
        import config
        engine = config.OCR_ENGINE
    except Exception:
        engine = 'easyocr'

    results: List[Dict] = []

    if engine == 'gcv':
        results = ocr_tile_gcv(tile, confidence_threshold)
        if not results:
            print('[OCR] GCV returned nothing, falling back to EasyOCR...')
            results = ocr_tile_easyocr(tile, confidence_threshold)
    elif engine == 'easyocr':
        results = ocr_tile_easyocr(tile, confidence_threshold)
    elif engine == 'tesseract':
        results = ocr_tile_tesseract(tile, confidence_threshold)
    else:
        print(f'[OCR] Unknown OCR_ENGINE="{engine}", defaulting to EasyOCR')
        results = ocr_tile_easyocr(tile, confidence_threshold)

    # Final fallback to Tesseract if still empty
    if not results and use_tesseract_fallback and engine != 'tesseract':
        print('[OCR] All engines returned nothing, trying Tesseract...')
        results = ocr_tile_tesseract(tile, confidence_threshold)

    return results


def translate_bbox_to_global(
    detections: List[Dict],
    tile_x: int,
    tile_y: int
) -> List[Dict]:
    """
    Shift all bounding boxes from tile-local coordinates to full-image coordinates.
    """
    translated = []
    for d in detections:
        bx1, by1, bx2, by2 = d['bbox']
        translated.append({
            **d,
            'bbox': [bx1 + tile_x, by1 + tile_y, bx2 + tile_x, by2 + tile_y],
        })
    return translated


def deduplicate(detections: List[Dict], iou_threshold: float = 0.4) -> List[Dict]:
    """
    Remove duplicate detections caused by overlapping tiles.

    Two detections are considered duplicates if EITHER:
    - Their bounding boxes overlap above iou_threshold (spatial duplicate)
    - OR their bounding boxes overlap > 0.2 AND they share the same normalised text
      (same word detected in two slightly offset tiles)

    Keeps the higher-confidence detection in each duplicate pair.
    """
    if not detections:
        return detections

    def iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        area_a = (ax2 - ax1) * (ay2 - ay1)
        area_b = (bx2 - bx1) * (by2 - by1)
        union  = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def normalise(text: str) -> str:
        """Lowercase + strip punctuation for text comparison."""
        return ''.join(c for c in text.lower() if c.isalnum())

    # Sort by confidence descending so we always keep the best version
    detections = sorted(detections, key=lambda d: d['confidence'], reverse=True)
    kept = []
    suppressed = set()

    for i, d in enumerate(detections):
        if i in suppressed:
            continue
        kept.append(d)
        norm_i = normalise(d['text'])

        for j in range(i + 1, len(detections)):
            if j in suppressed:
                continue
            overlap = iou(d['bbox'], detections[j]['bbox'])
            if overlap > iou_threshold:
                suppressed.add(j)
            elif overlap > 0.2 and norm_i == normalise(detections[j]['text']):
                # Same word detected in adjacent tile — suppress the lower-confidence one
                suppressed.add(j)

    return kept


# ─────────────────────────────────────────────────────────────
#  Interior-text extraction helpers
# ─────────────────────────────────────────────────────────────

def detect_legend_zone(img: np.ndarray, min_fraction: float = 0.75) -> int:
    """
    Return the y-pixel where the legend/margin strip begins.

    Strategy
    --------
    Topo maps have a legend box at the bottom bordered by dense horizontal
    lines.  We look for the topmost long horizontal Hough line in the bottom
    25 % of the image.  If nothing is found we fall back to 88 % of height
    (a safe conservative estimate that keeps most of the map body).

    Parameters
    ----------
    img          : full map image (BGR or grayscale)
    min_fraction : minimum fraction of map height to protect as map body
                   (default 0.75 → legend can only occupy bottom 25 %)

    Returns
    -------
    Integer y-coordinate (inclusive upper bound of map body).
    """
    h, w = img.shape[:2]
    search_top = int(h * 0.70)      # only scan bottom 30 %

    region = img[search_top:, :]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if len(region.shape) == 3 else region.copy()

    edges = cv2.Canny(gray, 40, 120)
    # Line must span at least 40 % of the width to be a legend border
    min_len = int(w * 0.40)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=min_len // 2,
        minLineLength=min_len,
        maxLineGap=30,
    )

    if lines is None or len(lines) == 0:
        # Conservative fallback: protect bottom 12 % as potential legend
        return int(h * 0.88)

    # Take the topmost horizontal-ish line (angle within 5° of horizontal)
    candidate_ys = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = abs(np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1))))
        if angle < 5:
            candidate_ys.append(y1)

    if not candidate_ys:
        return int(h * 0.88)

    legend_start = search_top + min(candidate_ys)
    # Never cut map body shorter than min_fraction
    return max(legend_start, int(h * min_fraction))


def preprocess_interior(
    img: np.ndarray,
    year: int = None,
) -> np.ndarray:
    """
    Enhanced preprocessing pipeline for map-body (interior) tiles.

    Differences from standard preprocess():
    - Desepia + historical enhancement for pre-1960 / sepia maps
    - Bilateral denoising (edge-preserving) instead of Gaussian
    - Adaptive thresholding tuned for small scattered settlement names
    - Mild unsharp-mask sharpening to bring out fine ink strokes

    Returns a 3-channel BGR image ready for GCV / EasyOCR.
    """
    try:
        from utils.image_utils import is_sepia, desepia, preprocess_historical
        if year is not None and year < 1960:
            img = preprocess_historical(img, year)
        elif is_sepia(img):
            img = desepia(img)
            img = preprocess_historical(img, year or 1950)
    except ImportError:
        pass  # image_utils optional dependency

    # Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()

    # CLAHE – moderate clip so we don't over-enhance noise
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Edge-preserving bilateral filter — smooths uniform areas, sharpens text edges
    gray = cv2.bilateralFilter(gray, d=9, sigmaColor=60, sigmaSpace=60)

    # Adaptive threshold — block size 21 works well for 1024×1024 tile + ~12 pt text
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=21, C=10,
    )

    # Unsharp mask — thin ink strokes become crisper after thresholding
    blurred = cv2.GaussianBlur(thresh, (0, 0), sigmaX=1.2)
    sharp   = cv2.addWeighted(thresh, 1.5, blurred, -0.5, 0)

    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)


def ocr_map_interior(
    full_map: np.ndarray,
    tile_size: int = 1024,
    overlap: int = 50,
    confidence_threshold: float = 0.3,
    year: int = None,
) -> List[Dict]:
    """
    OCR all tiles in the map body only (skips legend zone at bottom).

    Workflow
    --------
    1. detect_legend_zone() → find where legend starts
    2. Crop the map body (rows 0 … legend_top)
    3. Tile the body with preprocess_interior() applied to each tile
    4. Run ocr_tile() on each enhanced tile
    5. translate_bbox_to_global() to restore original coordinates
    6. deduplicate() across all tiles

    Parameters
    ----------
    full_map             : complete map image (BGR)
    tile_size            : width/height of each tile in pixels
    overlap              : pixel overlap between adjacent tiles
    confidence_threshold : minimum OCR confidence to keep a detection
    year                 : approximate map survey year (enables historical preprocessing)

    Returns
    -------
    List of dicts: {text, confidence, bbox (in full-map coordinates)}
    """
    h, w = full_map.shape[:2]

    # Step 1 — find non-legend region
    legend_top = detect_legend_zone(full_map)
    map_body   = full_map[:legend_top, :]
    bh, bw     = map_body.shape[:2]

    print(f'[OCR-Interior] Map body: {bw}×{bh}px  (legend starts at y={legend_top})')

    all_detections: List[Dict] = []
    step = tile_size - overlap

    y = 0
    while y < bh:
        x = 0
        while x < bw:
            tile_raw = map_body[y: y + tile_size, x: x + tile_size]
            if tile_raw.size == 0:
                x += step
                continue

            # Preprocess for interior small text
            tile_proc = preprocess_interior(tile_raw, year=year)

            # OCR
            dets = ocr_tile(tile_proc, confidence_threshold=confidence_threshold)

            # Translate to full-map coords
            global_dets = translate_bbox_to_global(dets, tile_x=x, tile_y=y)
            all_detections.extend(global_dets)

            x += step
        y += step

    # Remove duplicates from overlapping tile regions
    unique = deduplicate(all_detections, iou_threshold=0.4)
    print(f'[OCR-Interior] {len(all_detections)} raw → {len(unique)} after dedup')
    return unique
