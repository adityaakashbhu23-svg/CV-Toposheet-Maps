"""Check PAGE_SIZE and other variables in session HTML."""
content = open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', encoding='utf-8').read()
lines = content.splitlines()

# Find PAGE_SIZE line
for i, line in enumerate(lines, 1):
    if 'PAGE_SIZE' in line:
        print(f'Line {i}: {line[:120]}')

print()
# Show lines 190-195 (around var declarations)
print('=== Lines 188-200 ===')
for i in range(187, 200):
    print(f'Line {i+1}: {repr(lines[i][:100])}')
