"""Compare checkbox values vs DATA r[0] keys in session HTML."""
import re
import json

content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()

# Extract checkbox values
cb_values = re.findall(r'<input[^>]+class="map-cb"[^>]*value="([^"]*)"', content)
print(f'Checkbox values ({len(cb_values)}):')
for v in cb_values:
    print(f'  {repr(v)}')

# Extract DATA r[0] values (unique map keys)
data_line = lines[188]  # line 189
# Extract all r[0] values using a pattern
# DATA = [[key, ...], [key, ...], ...]
r0_values = set(re.findall(r'\[\"([^\"]+)\"', data_line[:5000]))
print(f'\nFirst few DATA r[0] values (from first 5000 chars):')
for v in list(r0_values)[:5]:
    print(f'  {repr(v)}')

# More specific: get the actual first DATA item r[0]
first_match = re.search(r'var DATA = \[\[\"([^\"]+)\"', data_line)
if first_match:
    data_key = first_match.group(1)
    print(f'\nFirst DATA r[0]: {repr(data_key)}')
    
    # Compare
    if cb_values:
        cb_val = cb_values[0]
        print(f'First checkbox value: {repr(cb_val)}')
        print(f'Are they equal? {cb_val == data_key}')
        print(f'cb_val bytes: {cb_val.encode("utf-8").hex()}')
        print(f'data_key bytes: {data_key.encode("utf-8").hex()}')
