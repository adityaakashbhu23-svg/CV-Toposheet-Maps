import pypdf, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

reader = pypdf.PdfReader(r'c:\Users\adity\Downloads\Dissertation\Books\Map vision pipe line paper.pdf')
print(f'Total pages: {len(reader.pages)}')
for i, page in enumerate(reader.pages):
    print(f'--- Page {i+1} ---')
    text = page.extract_text() or ''
    print(text)
    print()
