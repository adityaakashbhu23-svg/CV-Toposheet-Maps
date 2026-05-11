"""
Batch-regenerate all stale session table_export.html files by running export_table.py
for each session directory that contains a toposheet.db.
Sessions whose HTML already uses the 'DATA' virtual-render pattern are skipped.
"""
import os
import subprocess
import sys

BASE     = r'C:\CV- Toposheet'
RESULTS  = os.path.join(BASE, 'results')
SCRIPT   = os.path.join(BASE, 'export_table.py')

regenerated = []
skipped     = []
errors      = []

for d in sorted(os.listdir(RESULTS)):
    session_dir = os.path.join(RESULTS, d)
    db_path     = os.path.join(session_dir, 'toposheet.db')
    html_path   = os.path.join(session_dir, 'table_export.html')

    if not os.path.isfile(db_path):
        continue  # no real data

    # Check if HTML already has the modern DATA virtual-render pattern
    needs_regen = True
    if os.path.isfile(html_path):
        snippet = open(html_path, encoding='utf-8').read(4000)
        if 'var DATA =' in snippet and 'filteredData = DATA.slice()' in snippet:
            # Check no literal newlines inside JS strings (the old bug)
            full = open(html_path, encoding='utf-8').read()
            if "lines.join('\n')" not in full and "join('\n')" not in full:
                skipped.append(d)
                needs_regen = False

    if needs_regen:
        env = dict(os.environ)
        env['RESULTS_FOLDER'] = session_dir
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(
            [sys.executable, SCRIPT],
            cwd=BASE,
            env=env,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            regenerated.append(d)
            print(f"  Regenerated: {d}")
        else:
            errors.append((d, result.stderr.strip()[-300:]))
            print(f"  ERROR: {d}\n    {result.stderr.strip()[-200:]}")

print(f"\nDone. Regenerated: {len(regenerated)}, Skipped (already OK): {len(skipped)}, Errors: {len(errors)}")
if errors:
    print("Errors:")
    for name, msg in errors:
        print(f"  {name}: {msg}")
