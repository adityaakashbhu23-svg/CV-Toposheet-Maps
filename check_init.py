"""Verify init section in new HTML."""
content = open('results/table_export.html', encoding='utf-8').read()
lines = content.splitlines()
# Find init section
for i, line in enumerate(lines, 1):
    if 'init' in line or 'filteredData = DATA' in line or 'renderTable()' in line or '_btn' in line:
        print(f'Line {i}: {line}')
