"""Check session HTML renderTable and applyFilters functions."""
content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()

# Find renderTable function
for i, line in enumerate(lines, 1):
    if 'function renderTable' in line or 'function applyFilters' in line or 'function getChecked' in line or 'tBody' in line or 'filteredData' in line:
        print(f'Line {i}: {line[:100]}')

print()
# Find script section
script_start = content.find('<script>') + 8
script_end = content.rfind('</script>')
script = content[script_start:script_end]
print('Script size:', len(script), 'bytes')

# Check for any </script in DATA
import re
close_scripts = list(re.finditer(r'</\s*script', script, re.IGNORECASE))
print(f'</script occurrences: {len(close_scripts)}')

# Check for bad control chars
bad = [(i,c) for i,c in enumerate(script) if ord(c) < 32 and c not in '\n\r\t']
print(f'Bad control chars: {len(bad)}')
if bad:
    for i,c in bad[:3]:
        print(f'  offset {i}: {repr(c)} context: {repr(script[max(0,i-20):i+20])}')
