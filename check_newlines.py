"""Check for literal newlines inside the DATA JSON string in session HTML."""
with open(r'C:\CV- Toposheet\results\cffa8f4df2bd\table_export.html', 'rb') as f:
    raw = f.read()

print('File size (bytes):', len(raw))
print('CRLF count:', raw.count(b'\r\n'))
print('LF-only count:', raw.count(b'\n') - raw.count(b'\r\n'))

# Find the DATA section
data_start = raw.find(b'var DATA = [')
data_end = raw.find(b'\r\nvar PAGE_SIZE', data_start)
if data_end == -1:
    data_end = raw.find(b'\nvar PAGE_SIZE', data_start)
    
print(f'\nDATA section: offset {data_start} to {data_end} ({data_end - data_start} bytes)')

# Check for \n or \r inside the DATA section
data_section = raw[data_start:data_end]
lf_in_data = data_section.count(b'\n')
cr_in_data = data_section.count(b'\r')
print(f'\\n in DATA section: {lf_in_data}')
print(f'\\r in DATA section: {cr_in_data}')

if lf_in_data > 0:
    # Find where they are
    for i, b in enumerate(data_section):
        if b == 10:  # \n
            print(f'  Newline at offset {data_start+i}: context={repr(raw[data_start+i-30:data_start+i+30])}')
            if i > 500:  # Only show first few
                break
