# _build/make_icon.py
# Converts app_icon.png  →  app_icon.ico  (multi-size: 16,32,48,64,128,256)
# Run from project root OR from _build\:
#   python _build\make_icon.py
#
import sys
from pathlib import Path
from PIL import Image

# Resolve paths whether run from project root or from _build\
_here = Path(__file__).resolve().parent   # _build\
src  = _here / 'app_icon.png'
dest = _here / 'app_icon.ico'

if not src.exists():
    print(f'[ERROR] File not found: {src}')
    print('  → Save the PNG icon as  _build\\app_icon.png  then re-run this script.')
    sys.exit(1)

img = Image.open(src).convert('RGBA')
sizes = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
img.save(dest, format='ICO', sizes=sizes)
print(f'[OK] Created: {dest}')
print(f'     Sizes  : {[f"{s[0]}x{s[1]}" for s in sizes]}')
