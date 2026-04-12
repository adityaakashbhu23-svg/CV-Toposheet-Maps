# utils/image_utils.py  –  Image loading, tiling, and preprocessing helpers

import cv2
import numpy as np
from pathlib import Path
from typing import List, Tuple, Generator


def load_image(image_path: str) -> np.ndarray:
    """Load an image from disk. Raises FileNotFoundError if missing."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f'Image not found: {image_path}')
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f'Could not decode image: {image_path}')
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


def preprocess_for_ocr(img: np.ndarray) -> np.ndarray:
    """
    Enhance an image/tile for better OCR accuracy on historical maps:
    - Convert to grayscale
    - Upscale if small
    - Apply CLAHE contrast enhancement
    - Threshold to clean up background
    """
    # Convert to grayscale
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Upscale small tiles
    h, w = gray.shape
    if h < 300 or w < 300:
        scale = max(2, 300 // min(h, w))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # CLAHE: improves contrast for faded historical maps
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    return enhanced


def tile_count(img: np.ndarray, tile_size: int = 1024, overlap: int = 50) -> int:
    """Return the total number of tiles that will be generated for an image."""
    h, w = img.shape[:2]
    step = tile_size - overlap
    cols = max(1, -(-w // step))  # ceiling division
    rows = max(1, -(-h // step))
    return cols * rows
