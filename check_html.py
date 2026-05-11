"""Check table_export.html for issues."""
content = open('results/table_export.html', encoding='utf-8').read()
lines = content.splitlines()
print('Total lines:', len(lines))

for i, line in enumerate(lines, 1):
    if 'var DATA' in line or 'DATA =' in line:
        print(f'Line {i}: {line[:120]}')

# Find where script starts
script_start_line = None
for i, line in enumerate(lines, 1):
    if '<script>' in line:
        script_start_line = i
        print(f'Script tag at line {i}')
        break

# Show lines 237-245
print('\n--- Lines around script start ---')
for i in range(max(0, script_start_line-2), min(len(lines), script_start_line+10)):
    print(f'Line {i+1}: {lines[i][:120]}')

# Find getChecked function
print('\n--- getChecked function ---')
for i, line in enumerate(lines, 1):
    if 'getChecked' in line or 'function getChecked' in line:
        print(f'Line {i}: {line[:120]}')
