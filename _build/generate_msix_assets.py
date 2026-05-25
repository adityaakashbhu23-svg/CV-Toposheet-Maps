"""
_build/generate_msix_assets.py
───────────────────────────────
Generates all required MSIX visual assets from _build/app_icon.png.

Required asset sizes (MSIX spec):
  Square44x44Logo.png    44 x  44   taskbar / small tile
  Square150x150Logo.png 150 x 150   medium tile
  Wide310x150Logo.png   310 x 150   wide tile
  Square310x310Logo.png 310 x 310   large tile
  StoreLogo.png          50 x  50   Store listing thumbnail
  SplashScreen.png      620 x 300   launch splash screen

Run from the project root:
    python _build/generate_msix_assets.py
"""

import os, sys

try:
    from PIL import Image
except ImportError:
    print("Installing Pillow...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow", "--quiet"])
    from PIL import Image

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
SOURCE_ICON = os.path.join(SCRIPT_DIR, "app_icon.png")
ASSETS_DIR  = os.path.join(SCRIPT_DIR, "Assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

BG_COLOR = (26, 26, 46, 255)   # #1a1a2e dark navy

ASSETS = [
    ("Square44x44Logo.png",    44,  44,  False),
    ("Square150x150Logo.png", 150, 150,  False),
    ("Wide310x150Logo.png",   310, 150,  True),
    ("Square310x310Logo.png", 310, 310,  False),
    ("StoreLogo.png",          50,  50,  False),
    ("SplashScreen.png",      620, 300,  True),
]


def make_square(icon, size):
    canvas = Image.new("RGBA", (size, size), BG_COLOR)
    pad    = max(int(size * 0.15), 2)
    inner  = size - 2 * pad
    thumb  = icon.copy()
    thumb.thumbnail((inner, inner), Image.LANCZOS)
    x = (size - thumb.width)  // 2
    y = (size - thumb.height) // 2
    canvas.paste(thumb, (x, y), thumb if thumb.mode == "RGBA" else None)
    return canvas


def make_wide(icon, w, h):
    canvas  = Image.new("RGBA", (w, h), BG_COLOR)
    pad     = int(h * 0.15)
    inner_h = h - 2 * pad
    thumb   = icon.copy()
    thumb.thumbnail((inner_h, inner_h), Image.LANCZOS)
    x = int(w * 0.10)
    y = (h - thumb.height) // 2
    canvas.paste(thumb, (x, y), thumb if thumb.mode == "RGBA" else None)
    return canvas


if not os.path.isfile(SOURCE_ICON):
    print(f"ERROR: {SOURCE_ICON} not found.")
    sys.exit(1)

icon = Image.open(SOURCE_ICON).convert("RGBA")
print(f"Source icon: {icon.width}x{icon.height}")

for filename, w, h, is_wide in ASSETS:
    img = make_wide(icon, w, h) if is_wide else make_square(icon, w)
    out = os.path.join(ASSETS_DIR, filename)
    img.save(out, "PNG")
    print(f"  {filename:35s} {w}x{h}")

print(f"\nAssets written to: {ASSETS_DIR}")
