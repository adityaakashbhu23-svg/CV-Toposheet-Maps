import ast, sys
files = ['3_grid_detection.py','5_database_assembly.py','export_table.py']
ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f, '- OK')
    except SyntaxError as e:
        print(f, '- SYNTAX ERROR:', e)
        ok = False
sys.exit(0 if ok else 1)
