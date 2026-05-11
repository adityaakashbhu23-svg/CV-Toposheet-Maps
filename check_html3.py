"""Check checkboxes in the HTML."""
import re
content = open('results/table_export.html', encoding='utf-8').read()

# Find all map-cb checkboxes
cbs = re.findall(r'<input[^>]+class="map-cb"[^>]*>', content)
print(f'Total checkboxes: {len(cbs)}')
print('First 3:')
for cb in cbs[:3]:
    print(' ', cb)
print('Last 1:')
print(' ', cbs[-1])

# Extract values  
vals = re.findall(r'<input[^>]+class="map-cb"[^>]*value="([^"]*)"', content)
print(f'\nCheckbox values ({len(vals)} total):')
for v in vals[:5]:
    print(f'  {repr(v)}')

# Check first DATA row key
lines = content.splitlines()
data_line = lines[238]  # line 239
# Extract first key from DATA array
import json
# Find first element
first_bracket = data_line.index('[[') + 1
# find the end of first record
first_end = data_line.index('],[', first_bracket)
first_item_str = data_line[first_bracket:first_end+1]
print('\nFirst DATA item:')
try:
    first_item = json.loads(first_item_str)
    print('  r[0] (map key):', repr(first_item[0]))
    print('  r[1] (feat type):', repr(first_item[1]))
except Exception as e:
    print('  Parse error:', e)
    print('  Raw:', first_item_str[:200])

# Compare first checkbox value to first DATA r[0]
if vals and first_item:
    print(f'\nFirst checkbox value: {repr(vals[0])}')
    print(f'First DATA r[0]: {repr(first_item[0])}')
    print(f'Match: {vals[0] == first_item[0]}')
