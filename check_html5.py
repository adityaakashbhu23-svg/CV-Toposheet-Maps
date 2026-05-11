"""Check for multiple script tags and other issues."""
content = open('results/table_export.html', encoding='utf-8').read()
import re

# Count script tags
script_opens = [m.start() for m in re.finditer(r'<script', content, re.IGNORECASE)]
script_closes = [m.start() for m in re.finditer(r'</script', content, re.IGNORECASE)]
print(f'Opening <script tags: {len(script_opens)} at positions: {script_opens}')
print(f'Closing </script tags: {len(script_closes)} at positions: {script_closes}')

# Show lines near each script tag
lines = content.splitlines()
for pos in script_opens:
    line_num = content[:pos].count('\n') + 1
    print(f'  <script at line {line_num}: {repr(lines[line_num-1][:80])}')

for pos in script_closes:
    line_num = content[:pos].count('\n') + 1
    print(f'  </script at line {line_num}: {repr(lines[line_num-1][:80])}')

# Check DATA for unescaped backslashes (which could cause JS parse issues)
data_line = lines[238]
backslash_in_data = data_line.count('\\')
print(f'\nBackslashes in DATA line: {backslash_in_data}')
# Check for \u sequences that might cause issues
bad_u = re.findall(r'\\u(?![0-9a-fA-F]{4})', data_line)
print(f'Malformed \\uXXXX sequences: {len(bad_u)}')

# Sample a few values from DATA to check encoding
# Find first few complete items
# Quick check: does json.loads work on a truncated data?
import json
# Extract just first 3 items from DATA
sample = re.search(r'var DATA = (\[.*?\])\s*;', data_line[:5000])
if not sample:
    # Try manually
    start = data_line.index('[[')
    # Find the third item boundary - just take first 2000 chars
    sample_str = data_line[start:start+2000]
    # Try to find a valid JSON array prefix
    for end in range(len(sample_str), 10, -1):
        try:
            parsed = json.loads(sample_str[:end] + ']]')
            print(f'\nSuccessfully parsed first partial DATA: {len(parsed)} items')
            if parsed:
                print('First item r[0]:', repr(parsed[0][0]))
                print('First item r[1]:', repr(parsed[0][1]))
            break
        except:
            pass
    else:
        print('Could not parse any DATA subset')
