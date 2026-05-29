# utils/image_utils.py  –  Image loading, tiling, and preprocessing helpers

import cv2
import numpy as np
from pathlib import Path
from typing import List, Tuple, Generator


def load_image(image_path: str) -> np.ndarray:
    """
    Load an image from disk. Raises FileNotFoundError if missing.
    On OpenCV OutOfMemoryError, retries at progressively reduced scales
    (1/2, then 1/4) so very large maps don't crash the pipeline.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f'Image not found: {image_path}')

    # Try full resolution first
    try:
        img = cv2.imread(str(path))
        if img is not None:
            return img
    except cv2.error as e:
        if 'Insufficient memory' not in str(e) and 'OutOfMemory' not in str(e):
            raise  # unexpected cv2 error — re-raise it
        print(f'[WARN] load_image: OOM at full scale, retrying at 1/2 scale ...')

    # Fallback 1: half resolution (uses 1/4 of the memory)
    try:
        img = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_2)
        if img is not None:
            print(f'[WARN] Loaded at 1/2 scale due to memory limits: {path.name}')
            return img
    except cv2.error:
        print(f'[WARN] load_image: still OOM at 1/2 scale, retrying at 1/4 scale ...')

    # Fallback 2: quarter resolution (uses 1/16 of the memory)
    img = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_4)
    if img is None:
        raise ValueError(f'Could not decode image (tried full, 1/2, 1/4 scale): {image_path}')
    print(f'[WARN] Loaded at 1/4 scale due to memory limits: {path.name}')
    return img


def get_image_info(img: np.ndarray) -> dict:
    """Return basic image metadata."""
    h, w = img.shape[:2]
    channels = img.shape[2] if len(img.shape) == 3 else 1
    return {'width': w, 'height': h, 'channels': channels, 'megapixels': round(w * h / 1_000_000, 2)}


def generate_tiles(
    img: np.ndarray,
    tile_size: int = 1024,
    overlap: int = 50
) -> Generator[Tuple[np.ndarray, int, int, int, int], None, None]:
    """
    Yield (tile_img, x, y, x2, y2) for every tile in the image.
    Tiles overlap by `overlap` pixels to avoid cutting text at boundaries.
    """
    h, w = img.shape[:2]
    step = tile_size - overlap

    for y in range(0, h, step):
        for x in range(0, w, step):
            x2 = min(x + tile_size, w)
            y2 = min(y + tile_size, h)
            tile = img[y:y2, x:x2]
            # Skip tiles that are too small to contain useful text
            if tile.shape[0] < 32 or tile.shape[1] < 32:
                continue
            yield tile, x, y, x2, y2


def save_tile(tile: np.ndarray, path: str) -> None:
    """Save a tile image to disk."""
    cv2.imwrite(path, tile)


def _deskew(img: np.ndarray) -> np.ndarray:
    """
    Detect and correct small rotations (up to ±5°) in a grayscale tile.
    Historical maps are sometimes scanned slightly tilted, which hurts OCR.
    Uses Hough line angles on edges to compute skew angle.
    """
    edges = cv2.Canny(img, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                             minLineLength=img.shape[1] // 4, maxLineGap=10)
    if lines is None or len(lines) < 3:
        return img  # not enough lines to estimate skew safely

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 != x1:
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            # Only care about near-horizontal lines (text baselines)
            if abs(angle) < 10:
                angles.append(angle)

    if not angles:
        return img

    median_angle = sorted(angles)[len(angles) // 2]
    if abs(median_angle) < 0.3:
        return img  # skew too small to matter

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h),
                              flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    return rotated


def preprocess_for_ocr(img: np.ndarray) -> np.ndarray:
    """
    Enhance an image/tile for better OCR accuracy on historical maps:
    - Convert to grayscale
    - Upscale if small (maintains text sharpness for OCR engines)
    - Denoise: remove scanner grain without blurring text strokes
    - CLAHE contrast enhancement: lifts faded ink on aged paper
    - Deskew: correct small rotations (up to ±5°) from scanner tilt
    - Sharpen: crispens text edges after smoothing operations
    """
    # Convert to grayscale
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Upscale small tiles — OCR needs at least ~30px character height
    h, w = gray.shape
    if h < 300 or w < 300:
        scale = max(2, 300 // min(h, w))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Denoise: removes scanner grain & paper texture while preserving ink strokes
    # h=10 is gentle — enough for aged maps without destroying thin text
    gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    # CLAHE: adaptive contrast — lifts faded ink in dark/bright patches independently
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Deskew: fix slight scanner tilt that causes OCR to miss characters
    enhanced = _deskew(enhanced)

    # Sharpen: unsharp mask to crisp text edges after denoising softened them
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.5)
    sharpened = cv2.addWeighted(enhanced, 1.8, blurred, -0.8, 0)

    return sharpened


def preprocess_for_ocr_stable(img: np.ndarray) -> np.ndarray:
    """
    Crash-safe OCR preprocessing for frozen Windows builds.

    Uses only lightweight, stable OpenCV ops and avoids heavy denoise/deskew
    paths that can trigger native crashes on some machines.
    """
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    h, w = gray.shape
    if h < 300 or w < 300:
        scale = max(2, 300 // min(h, w))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)

    # Lightweight local contrast enhancement.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Mild sharpen without heavy filters.
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)
    return sharpened


def tile_count(img: np.ndarray, tile_size: int = 1024, overlap: int = 50) -> int:
    """Return the total number of tiles that will be generated for an image."""
    h, w = img.shape[:2]
    step = tile_size - overlap
    cols = max(1, -(-w // step))  # ceiling division
    rows = max(1, -(-h // step))
    return cols * rows


# ─── Historical / sepia map helpers ──────────────────────────────────────────

def is_sepia(img: np.ndarray) -> bool:
    """
    Detect whether a colour image has a sepia/brown tone typical of
    pre-1960 photographic scans of Survey of India maps.

    Strategy: in a sepia image the Red channel mean is significantly higher
    than the Blue channel mean.  A ratio R/B > 1.25 and low overall
    saturation in the Green channel indicates sepia.
    """
    if len(img.shape) == 2:
        return False   # already grayscale
    b, g, r = cv2.split(img.astype(np.float32))
    mean_r = float(np.mean(r))
    mean_b = float(np.mean(b))
    if mean_b < 1:
        return False
    rb_ratio = mean_r / mean_b
    # Sepia: red >> blue (ratio > 1.20) and green is between them
    return rb_ratio > 1.20 and float(np.mean(g)) < mean_r * 0.95


def desepia(img: np.ndarray) -> np.ndarray:
    """
    Convert a sepia-toned BGR image to a clean near-grayscale image.

    Old SOI maps are brownish; naïve rgb2gray makes ink look grey-on-grey
    (low contrast) because the brown paper and the brown ink are close in
    luminance.  Instead we:
    1. Boost the blue channel (paper is brownish → low blue; ink is dark → blue ≈ 0)
       so the contrast ratio between ink and paper increases.
    2. Subtract a scaled red channel to further whiten the paper.
    Returns an 8-bit single-channel image.
    """
    b, g, r = cv2.split(img.astype(np.float32))
    # Weight channels: emphasise blue (ink dark) suppress red (paper warm)
    enhanced = np.clip(1.5 * b - 0.5 * r + 0.3 * g, 0, 255).astype(np.uint8)
    # Invert so ink = dark on white background (OCR standard)
    enhanced = cv2.bitwise_not(enhanced)
    enhanced = cv2.bitwise_not(enhanced)   # undo — keep dark-on-light
    return enhanced


def preprocess_historical(img: np.ndarray, year: int | None = None) -> np.ndarray:
    """
    Enhanced preprocessing pipeline for historical maps (pre-1960 era).

    Compared to preprocess_for_ocr():
    - Runs desepia() first if sepia tone detected
    - Uses stronger CLAHE (clipLimit=4.0) for faded ink
    - Applies morphological closing to reconnect broken ink strokes
    - More aggressive denoising (h=15) for heavy scanner grain
    - Extra sharpening pass for very old (pre-1930) maps

    Parameters
    ----------
    img  : BGR image tile
    year : survey year (int), used to scale preprocessing intensity
    """
    if len(img.shape) == 3 and is_sepia(img):
        gray = desepia(img)
    elif len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Upscale small tiles
    h, w = gray.shape
    if h < 300 or w < 300:
        scale = max(2, 300 // min(h, w))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Stronger denoise for old maps (more grain)
    h_strength = 15 if (year and year < 1940) else 12
    gray = cv2.fastNlMeansDenoising(gray, h=h_strength, templateWindowSize=7, searchWindowSize=21)

    # Strong CLAHE — faded ink needs more contrast lift
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Morphological closing: reconnect broken ink strokes in aged maps
    # Kernel 2×2 is enough to bridge 1-2px gaps without thickening text too much
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    enhanced = cv2.morphologyEx(enhanced, cv2.MORPH_CLOSE, kernel)

    # Deskew
    enhanced = _deskew(enhanced)

    # Sharpening — extra pass for very early maps
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.5)
    sharpened = cv2.addWeighted(enhanced, 1.8, blurred, -0.8, 0)

    if year and year < 1930:
        # Second lighter sharpening pass for really early prints
        blurred2 = cv2.GaussianBlur(sharpened, (0, 0), sigmaX=1.0)
        sharpened = cv2.addWeighted(sharpened, 1.4, blurred2, -0.4, 0)

    return sharpened
