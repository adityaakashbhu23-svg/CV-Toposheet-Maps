"""
_build/build_msix.py
─────────────────────
Builds CVToposheet_1.0.1.0_x64.msix from the PyInstaller dist folder.
No Windows SDK, no MSIX Packaging Tool, no admin rights required.

How it works:
  1. Generates MSIX visual assets (if missing) from app_icon.png
  2. Collects all files from dist\CVToposheet\ + Assets\
  3. Writes a ZIP (DEFLATE-compressed) containing every file + AppxManifest.xml
  4. Reads the ZIP back to get exact Local File Header sizes for each entry
  5. Computes SHA-256 block-map hashes from original (uncompressed) files
  6. Appends AppxBlockMap.xml and [Content_Types].xml to the ZIP
  The result is a standards-compliant MSIX accepted by the Microsoft Store.

Usage (from project root):
    python _build\\build_msix.py

Prerequisites:
    - Run _build\\build_exe.bat first  →  creates _build\\dist\\CVToposheet\\
    - _build\\AppxManifest.xml must exist (already present)
    - _build\\app_icon.png must exist (already present)

Output:
    _build\\CVToposheet_1.0.1.0_x64.msix  (~250 MB compressed)

Store submission:
    1. Update Publisher in AppxManifest.xml to your Partner Center CN.
    2. Delete the old Win32 submission in Partner Center (same app name).
    3. Upload this .msix — Store signs it automatically, no cert needed.
"""

import os, sys, struct, hashlib, base64, zipfile
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DIST_DIR   = SCRIPT_DIR / 'dist' / 'CVToposheet'
ASSETS_DIR = SCRIPT_DIR / 'Assets'
MANIFEST   = SCRIPT_DIR / 'AppxManifest.xml'
OUT_MSIX   = SCRIPT_DIR / 'CVToposheet_1.0.1.0_x64.msix'

BLOCK_SIZE = 65536   # 64 KB per block — required by the MSIX spec

# ── Content-type map ───────────────────────────────────────────────────────────
_CT = {
    'exe':'application/octet-stream', 'dll':'application/octet-stream',
    'pyd':'application/octet-stream', 'so': 'application/octet-stream',
    'png':'image/png',   'jpg':'image/jpeg', 'jpeg':'image/jpeg',
    'ico':'image/x-icon','bmp':'image/bmp',  'gif': 'image/gif',
    'xml':'application/xml',
    'json':'application/octet-stream',
    'py': 'application/octet-stream', 'pyc':'application/octet-stream',
    'txt':'text/plain',  'html':'text/html', 'css': 'text/css',
    'js': 'application/javascript',
    'db': 'application/octet-stream', 'sqlite':'application/octet-stream',
    'zip':'application/octet-stream', 'whl': 'application/octet-stream',
    'ttf':'application/octet-stream', 'woff':'application/octet-stream',
    'woff2':'application/octet-stream',
}

# These are never listed in AppxBlockMap.xml
_BLOCKMAP_EXCLUDE = {'AppxManifest.xml', 'AppxBlockMap.xml', '[Content_Types].xml',
                     'AppxSignature.p7x'}


# ── Helpers ────────────────────────────────────────────────────────────────────

def sha256_block_hashes(path: Path) -> list:
    """Base64-encoded SHA-256 of each 64 KB uncompressed chunk of the file."""
    hashes = []
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(BLOCK_SIZE)
            if not chunk:
                break
            hashes.append(base64.b64encode(hashlib.sha256(chunk).digest()).decode())
    return hashes


def read_lfh_sizes(msix_path: Path) -> dict:
    """
    Open the ZIP and read the actual Local File Header (LFH) size for every entry.
    LFH layout: 30 bytes fixed  +  filename_len  +  extra_field_len
    """
    lfh_map = {}
    with open(msix_path, 'rb') as raw:
        with zipfile.ZipFile(msix_path, 'r') as zf:
            for info in zf.infolist():
                raw.seek(info.header_offset + 26)   # jump to filename_length field
                fname_len = struct.unpack('<H', raw.read(2))[0]
                extra_len = struct.unpack('<H', raw.read(2))[0]
                lfh_map[info.filename] = 30 + fname_len + extra_len
    return lfh_map


def build_content_types(extensions: set) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
    ]
    for ext in sorted(extensions):
        ct = _CT.get(ext, 'application/octet-stream')
        lines.append(f'  <Default Extension="{ext}" ContentType="{ct}"/>')
    lines.append('  <Override PartName="/AppxManifest.xml" '
                 'ContentType="application/vnd.ms-appx.manifest+xml"/>')
    lines.append('  <Override PartName="/AppxBlockMap.xml" '
                 'ContentType="application/vnd.ms-appx.blockmap+xml"/>')
    lines.append('</Types>')
    return '\n'.join(lines)


def build_block_map(entries: list) -> str:
    """
    entries: list of (win_path_str, disk_Path, lfh_size_int)
    Block hashes are computed from the original uncompressed disk file content.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<BlockMap xmlns="http://schemas.microsoft.com/appx/2010/blockmap"',
        '          HashMethod="http://www.w3.org/2001/04/xmlenc#sha256">',
    ]
    total = len(entries)
    for i, (win_path, disk_path, lfh_size) in enumerate(entries, 1):
        size   = disk_path.stat().st_size
        hashes = sha256_block_hashes(disk_path)
        lines.append(f'  <File Name="{win_path}" Size="{size}" LfhSize="{lfh_size}">')
        for h in hashes:
            lines.append(f'    <Block Hash="{h}"/>')
        lines.append('  </File>')
        if i % 100 == 0 or i == total:
            print(f'    Block map: {i}/{total} files hashed...', end='\r')
    print()
    lines.append('</BlockMap>')
    return '\n'.join(lines)


def collect_files() -> list:
    """Returns list of (archive_name, disk_Path) in write order."""
    if not DIST_DIR.exists():
        print(f'\nERROR: dist folder not found:\n  {DIST_DIR}')
        print('Run  _build\\build_exe.bat  first.\n')
        sys.exit(1)
    if not MANIFEST.exists():
        print(f'\nERROR: AppxManifest.xml not found:\n  {MANIFEST}\n')
        sys.exit(1)

    files = []

    # App files (dist folder)
    for root, dirs, fnames in os.walk(DIST_DIR):
        dirs.sort()   # deterministic order
        for fname in sorted(fnames):
            disk = Path(root) / fname
            arc  = disk.relative_to(DIST_DIR).as_posix()
            files.append((arc, disk))

    # Visual assets
    if ASSETS_DIR.exists():
        for f in sorted(ASSETS_DIR.glob('*.png')):
            files.append((f'Assets/{f.name}', f))

    # Manifest (must be in the ZIP; excluded from block map)
    files.append(('AppxManifest.xml', MANIFEST))

    return files


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print()
    print('=' * 62)
    print('  CV-Toposheet  MSIX Builder  (no Windows SDK required)')
    print('=' * 62)

    # Step 1 — Generate assets if missing
    if not ASSETS_DIR.exists() or not (ASSETS_DIR / 'StoreLogo.png').exists():
        print('\n[1/5] Generating MSIX visual assets...')
        import subprocess
        subprocess.check_call([sys.executable,
                               str(SCRIPT_DIR / 'generate_msix_assets.py')])
    else:
        print(f'\n[1/5] Assets folder found: {ASSETS_DIR.name}\\')

    # Step 2 — Collect files
    print('\n[2/5] Collecting files...')
    files = collect_files()
    print(f'      {len(files)} files from dist + assets.')

    # Step 3 — Write first-pass ZIP (all files, no blockmap/content_types yet)
    print(f'\n[3/5] Writing compressed package (700 MB → ~250 MB)...')
    print('      This takes several minutes — please wait.')
    if OUT_MSIX.exists():
        OUT_MSIX.unlink()

    written_for_bm = []   # (arc_name, disk_Path) — for block map
    extensions     = set()

    with zipfile.ZipFile(OUT_MSIX, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        for i, (arc_name, disk_path) in enumerate(files, 1):
            ext = Path(arc_name).suffix.lstrip('.').lower()
            if ext:
                extensions.add(ext)
            zf.write(disk_path, arc_name)
            if arc_name not in _BLOCKMAP_EXCLUDE:
                written_for_bm.append((arc_name, disk_path))
            if i % 250 == 0 or i == len(files):
                pct = int(i / len(files) * 100)
                print(f'      {i}/{len(files)} files  ({pct}%)...', end='\r')

    print(f'      {len(files)}/{len(files)} files  (100%) — done.     ')

    # Step 4 — Read back actual LFH sizes from the written ZIP
    print('\n[4/5] Reading Local File Header sizes from ZIP...')
    lfh_map = read_lfh_sizes(OUT_MSIX)
    print(f'      {len(lfh_map)} entries read.')

    # Step 5 — Build block map + content types, append to ZIP
    print('\n[5/5] Computing SHA-256 block hashes and finalising...')
    bm_entries = []
    for arc_name, disk_path in written_for_bm:
        win_path = arc_name.replace('/', '\\')
        lfh      = lfh_map.get(arc_name, 30 + len(arc_name.encode('utf-8')))
        bm_entries.append((win_path, disk_path, lfh))

    block_map_xml = build_block_map(bm_entries)
    content_types = build_content_types(extensions)

    with zipfile.ZipFile(OUT_MSIX, 'a', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        zf.writestr('AppxBlockMap.xml',    block_map_xml)
        zf.writestr('[Content_Types].xml', content_types)

    size_mb = OUT_MSIX.stat().st_size / 1024 / 1024

    print()
    print('=' * 62)
    print(f'  Done!  {OUT_MSIX.name}  ({size_mb:.0f} MB)')
    print(f'  Path:  {OUT_MSIX}')
    print('=' * 62)
    print()
    print('  Next steps for Microsoft Store:')
    print('  1. Open _build\\AppxManifest.xml')
    print('     Set Publisher = your Partner Center CN exactly.')
    print('     Re-run this script after changing the manifest.')
    print('  2. In Partner Center: delete the old Win32 submission.')
    print('  3. Create new submission → upload this .msix file.')
    print('     The Store signs it — no certificate needed.')
    print()


if __name__ == '__main__':
    if '--allow-legacy-builder' not in sys.argv:
        print('ERROR: _build/build_msix.py is a legacy experimental packer and can produce Store-rejected packages.')
        print('Use _build/rebuild_msix.py (MakeAppx) for Microsoft Partner Center uploads.')
        print('If you still want to run this script intentionally, pass: --allow-legacy-builder')
        sys.exit(1)
    main()
