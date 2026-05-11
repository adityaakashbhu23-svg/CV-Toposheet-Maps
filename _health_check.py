import os

base = r'C:\CV- Toposheet\results'
issues = []
checked = 0

for d in sorted(os.listdir(base)):
    p = os.path.join(base, d, 'table_export.html')
    if not os.path.isfile(p):
        continue
    checked += 1
    txt = open(p, encoding='utf-8').read()

    if 'var DATA =' not in txt:
        issues.append((d, 'old DOM format - no DATA array'))
    elif "/* init */\nonToggle();" in txt:
        issues.append((d, 'old onToggle() init'))
    elif "lines.join('\n')" in txt:
        issues.append((d, 'literal newline in CSV join'))

if issues:
    print(f"PROBLEMS found in {len(issues)}/{checked} files:")
    for d, msg in issues:
        print(f"  BAD: {d} - {msg}")
else:
    print(f"ALL OK - {checked} HTML files checked, no issues found")
