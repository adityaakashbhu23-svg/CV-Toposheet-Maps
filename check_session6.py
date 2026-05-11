"""Read the end of the session HTML to see the init section."""
content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()
total = len(lines)
print(f'Total lines: {total}')
print()
print('=== Last 30 lines ===')
for i in range(max(0, total-30), total):
    print(f'Line {i+1}: {repr(lines[i][:120])}')
