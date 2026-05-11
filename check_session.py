"""Check session HTML vs global HTML."""
import os
session_html = r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html'
if os.path.exists(session_html):
    content = open(session_html, encoding='utf-8').read()
    lines = content.splitlines()
    print('Session HTML lines:', len(lines))
    print('File size:', len(content.encode('utf-8')), 'bytes')
    # Check DATA line
    for i, line in enumerate(lines, 1):
        if 'var DATA' in line:
            print(f'Line {i}: var DATA length={len(line)}, starts=[{line[:60]}], ends=[{line[-60:]}]')
    # Check init section
    print('\n--- Last 20 lines ---')
    for i, line in enumerate(lines[-20:], len(lines)-19):
        print(f'Line {i}: {line}')
else:
    print('Session HTML not found!')
    import os
    for f in os.listdir(r'C:\CV- Toposheet\results\cffa8f4df2bd'):
        print(' ', f)
