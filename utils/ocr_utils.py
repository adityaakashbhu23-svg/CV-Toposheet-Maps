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


def ocr_tile_gcv(tile: np.ndarray, confidence_threshold: float = 0.3) -> List[Dict]:
    """
    Run Google Cloud Vision TEXT_DETECTION on a single tile.

    Authentication is handled via the GOOGLE_APPLICATION_CREDENTIALS env-var,
    which config.py sets to the absolute path of service_account.json before
    any OCR code runs.

    Returns a list of dicts: {text, confidence, bbox: [x1,y1,x2,y2]}
    """
    try:
        from google.cloud import vision
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
        client = vision.ImageAnnotatorClient()
        image  = vision.Image(content=image_bytes)
        response = client.text_detection(image=image)
    except Exception as e:
        print(f'[OCR/GCV] API error: {e}')
        return []

    if response.error.message:
        print(f'[OCR/GCV] Response error: {response.error.message}')
        return []

    annotations = response.text_annotations
    if not annotations:
        return []

    detections = []
    # annotations[0] is the full-page block; skip it and use per-word entries
    for ann in annotations[1:]:
        text = ann.description.strip()
        if not text:
            continue
        verts = ann.bounding_poly.vertices
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        # GCV doesn't give per-word confidence; use 0.9 as a high-trust default
        conf = 0.9
        if conf < confidence_threshold:
            continue
        detections.append({
            'text':       text,
            'confidence': conf,
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


def deduplicate(detections: List[Dict], iou_threshold: float = 0.5) -> List[Dict]:
    """
    Remove duplicate detections (from overlapping tiles) by IoU overlap check.
    Keeps the detection with higher confidence when two overlap significantly.
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
        return inter / (area_a + area_b - inter)

    # Sort by confidence descending
    detections = sorted(detections, key=lambda d: d['confidence'], reverse=True)
    kept = []
    suppressed = set()

    for i, d in enumerate(detections):
        if i in suppressed:
            continue
        kept.append(d)
        for j in range(i + 1, len(detections)):
            if j in suppressed:
                continue
            if iou(d['bbox'], detections[j]['bbox']) > iou_threshold:
                suppressed.add(j)

    return kept
