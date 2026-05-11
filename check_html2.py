"""Check end of DATA line."""
content = open('results/table_export.html', encoding='utf-8').read()
lines = content.splitlines()
# Line 239 = index 238
data_line = lines[238]
print('Line 239 length:', len(data_line))
print('End of line 239 (last 100 chars):', repr(data_line[-100:]))
print()
# Show lines 238-243 
for i in range(238, 245):
    print(f'Line {i+1} (len={len(lines[i])}): {lines[i][:80]}')
