import os
import glob
import cv2
import warnings
import sys

# Disable warnings
warnings.filterwarnings('ignore')

# Disable oneDNN and set up environment for stable CPU inference
os.environ['PADDLE_DISABLE_FAST_MATH'] = '1'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
os.environ['OMP_NUM_THREADS'] = '1'
# Disable MKL-DNN (oneDNN alternative)
os.environ['MKL_THREADING_LAYER'] = 'GNU'
os.environ['FLAGS_use_mkldnn'] = '0'

from paddleocr import PaddleOCR

# Initialize OCR
try:
    print("Initializing PaddleOCR...")
    ocr = PaddleOCR(lang='en')
    print("PaddleOCR initialized successfully")
except Exception as e:
    print(f"Error initializing PaddleOCR: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

maps_folder = 'maps'
output_file = 'all_map_text.txt'

def process_image(image_path):
    """
    Process an image and extract text.
    """
    try:
        print(f"  Reading image...")
        result = ocr.ocr(image_path)
        
        lines = []
        if result and result[0]:
            for line in result[0]:
                text = line[1][0]
                conf = line[1][1]
                if conf > 0.3:  # Include text with 30% confidence or higher
                    lines.append(text)
        
        return lines
    
    except Exception as e:
        print(f"  Error processing {image_path}: {type(e).__name__}: {str(e)[:100]}")
        return []

with open(output_file, 'w', encoding='utf-8') as f:
    # Find all JPG/JPEG files (case-insensitive)
    img_files = glob.glob(os.path.join(maps_folder, '*.jpg')) + glob.glob(os.path.join(maps_folder, '*.jpeg')) + glob.glob(os.path.join(maps_folder, '*.JPG')) + glob.glob(os.path.join(maps_folder, '*.JPEG'))
    img_files = list(set(img_files))  # Remove duplicates
    img_files.sort()
    
    print(f'Found {len(img_files)} image file(s) to process')
    
    for img_path in img_files:
        print(f'Processing {os.path.basename(img_path)}...')
        lines = process_image(img_path)
        
        f.write(f'\n=== {os.path.basename(img_path)} ===\n')
        if lines:
            for text in lines:
                f.write(text + '\n')
        else:
            f.write('(No text detected)\n')
        
        print(f'Found {len(lines)} lines')

print(f'All text saved to {output_file}')
