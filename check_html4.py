"""Deep check for issues in table_export.html"""
content = open('results/table_export.html', encoding='utf-8').read()
lines = content.splitlines()

script_start = content.find('<script>') + 8
script_end = content.rfind('</script>')
script = content[script_start:script_end]

# Check for </script in any case
import re
close_scripts = list(re.finditer(r'</\s*script', script, re.IGNORECASE))
print(f'</script occurrences in DATA/script: {len(close_scripts)}')
for m in close_scripts[:5]:
    pos = m.start()
    print(f'  offset {pos}: {repr(script[max(0,pos-30):pos+30])}')

# Check for any unmatched quotes in the DATA line that could break JS
data_line = lines[238]
# Count quotes within the var DATA = [...]; statement
# This is complex — instead check if DATA line ends correctly
data_stripped = data_line.strip()
print(f'\nDATA line ends with: {repr(data_stripped[-20:])}')
print(f'DATA line starts with: {repr(data_stripped[:20])}')

# Check for \r\n or other weird line endings in the HTML file
with open('results/table_export.html', 'rb') as f:
    raw = f.read()
crlf_count = raw.count(b'\r\n')
cr_count = raw.count(b'\r') - crlf_count
print(f'\nLine endings: CRLF={crlf_count}, CR-only={cr_count}')
print(f'File size: {len(raw)} bytes')

# Check for UTF-8 BOM  
print(f'BOM at start: {raw[:3] == b"\\xef\\xbb\\xbf"}')
print(f'First bytes: {raw[:6].hex()}')

# Check script section for any </  followed by word chars
close_tags = list(re.finditer(r'</\w', script))
print(f'\nAll </ patterns in script: {len(close_tags)}')
unique_starts = set()
for m in close_tags:
    tag = script[m.start():m.start()+15]
    unique_starts.add(re.match(r'</\w+', tag).group(0) if re.match(r'</\w+', tag) else tag[:6])
print('Unique closing tags found:', sorted(unique_starts))
