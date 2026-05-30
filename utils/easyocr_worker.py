import argparse
import json
import os
import sys

import cv2


def _configure_runtime():
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ.setdefault('MKL_NUM_THREADS', '1')
    os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
    os.environ.setdefault('TORCH_NUM_THREADS', '1')
    os.environ.setdefault('TORCH_NUM_INTEROP_THREADS', '1')
    os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')
    os.environ.setdefault('KMP_AFFINITY', 'disabled')
    os.environ.setdefault('KMP_BLOCKTIME', '0')
    os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
    os.environ.setdefault('OPENCV_OPENCL_RUNTIME', 'disabled')
    os.environ.setdefault('OPENBLAS_CORETYPE', 'GENERIC')
    os.environ.setdefault('MKL_THREADING_LAYER', 'SEQUENTIAL')
    os.environ.setdefault('MKL_ENABLE_INSTRUCTIONS', 'COMPATIBLE')
    os.environ.setdefault('OMP_DYNAMIC', 'FALSE')
    os.environ.setdefault('PYTORCH_JIT', '0')


def _run(input_path: str, output_path: str, threshold: float) -> int:
    _configure_runtime()

    try:
        import easyocr
        import torch
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception as e:
        print(f'[easyocr_worker] import error: {e}', file=sys.stderr)
        return 2

    img = cv2.imread(input_path)
    if img is None:
        print('[easyocr_worker] failed to read input image', file=sys.stderr)
        return 3

    try:
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        rows = reader.readtext(img, detail=1, paragraph=False, workers=0, batch_size=1)
    except Exception as e:
        print(f'[easyocr_worker] OCR error: {e}', file=sys.stderr)
        return 4

    detections = []
    for (bbox_pts, text, conf) in rows:
        if conf < threshold or not text.strip():
            continue
        xs = [pt[0] for pt in bbox_pts]
        ys = [pt[1] for pt in bbox_pts]
        detections.append({
            'text': text.strip(),
            'confidence': round(float(conf), 4),
            'bbox': [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
        })

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(detections, f, ensure_ascii=False)
    except Exception as e:
        print(f'[easyocr_worker] output error: {e}', file=sys.stderr)
        return 5

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--threshold', type=float, default=0.3)
    args = parser.parse_args()
    return _run(args.input, args.output, args.threshold)


if __name__ == '__main__':
    raise SystemExit(main())
