"""One-time patch: fix _merge_session_to_global in app.py and wire it into _stream_gen."""
import re

path = r'c:\CV- Toposheet\app.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# ── Fix 1: rename the bare `env` variables inside _merge_session_to_global
# so they don't shadow the outer `env` dict in _stream_gen.
# The merge function is the one that contains 'export_table.py' subprocess call.
old_block = (
    "            env = os.environ.copy()\n"
    "            env['RESULTS_FOLDER'] = str(RESULTS_DIR)\n"
    "            env['PYTHONIOENCODING'] = 'utf-8'\n"
    "            subprocess.run(\n"
    "                [sys.executable, 'export_table.py'],\n"
    "                cwd=str(BASE_DIR), env=env,\n"
)
new_block = (
    "            genv = os.environ.copy()\n"
    "            genv['RESULTS_FOLDER'] = str(RESULTS_DIR)\n"
    "            genv['PYTHONIOENCODING'] = 'utf-8'\n"
    "            subprocess.run(\n"
    "                [sys.executable, 'export_table.py'],\n"
    "                cwd=str(BASE_DIR), env=genv,\n"
)
if old_block in content:
    content = content.replace(old_block, new_block, 1)
    print('[patch] Fixed env→genv inside _merge_session_to_global')
else:
    print('[patch] env→genv already done or not found — skipping')

# ── Fix 2: insert the merge call just before the "Done! Click View Results." yield.
done_yield = '        yield f\'data: {json.dumps({"done": True, "redirect": results_url, "msg": "Done! Click View Results."})}\\'+'\\n\\n\'\n'

# Find the exact done yield line
marker = '"Done! Click View Results."'
# Build insertion block
insert = (
    "        # Merge this session into the global DB (powers Map Database button)\n"
    "        yield 'data: {\"msg\": \"Saving to Map Database...\"}\\n\\n'\n"
    "        _merge_session_to_global(session_id)\n"
    "\n"
)

# Find position in content
idx = content.find(marker)
if idx == -1:
    print('[patch] Could not find done yield marker — aborting fix 2')
else:
    # Walk back to find start of that line
    line_start = content.rfind('\n', 0, idx) + 1
    # Check if merge call already inserted right before this line
    preceding = content[max(0, line_start - 200):line_start]
    if '_merge_session_to_global(session_id)' in preceding:
        print('[patch] Merge call already present — skipping fix 2')
    else:
        content = content[:line_start] + insert + content[line_start:]
        print('[patch] Inserted merge call before done yield')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('[patch] app.py written successfully.')
