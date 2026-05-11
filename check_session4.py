"""Read renderTable and surrounding area of session HTML."""
content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()

# Print lines 195-250
print('=== Lines 195-250 ===')
for i in range(194, min(250, len(lines))):
    print(f'Line {i+1}: {lines[i]}')
