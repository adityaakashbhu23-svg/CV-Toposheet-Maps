"""Parse and validate DATA from session HTML."""
import json
import re

content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()

# DATA is on line 189 (index 188)
data_line = lines[188]
print('DATA line length:', len(data_line))

# Extract the JSON array
match = re.match(r'var DATA = (\[.*\]);?\s*$', data_line)
if match:
    data_json = match.group(1)
    print('Extracted JSON length:', len(data_json))
    try:
        data = json.loads(data_json)
        print(f'Parsed successfully: {len(data)} items')
        print('First item:', data[0])
        print('Last item:', data[-1])
        # Check for any None/null values
        none_count = sum(1 for row in data for v in row if v is None)
        print(f'None values in DATA: {none_count}')
        # Check r[0] values (map keys)
        r0_values = set(row[0] for row in data)
        print(f'Unique r[0] (map keys): {r0_values}')
    except json.JSONDecodeError as e:
        print('JSON error:', e)
        print('Error position context:', repr(data_json[max(0, e.pos-30):e.pos+30]))
else:
    print('Could not extract JSON array!')
    print('Line starts with:', repr(data_line[:80]))
    print('Line ends with:', repr(data_line[-80:]))
