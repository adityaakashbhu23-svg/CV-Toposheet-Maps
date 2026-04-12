# run_pipeline.py  –  Master script: run all 5 phases end-to-end
#
# Usage:
#   python run_pipeline.py              (run all phases)
#   python run_pipeline.py --from 3     (start from phase 3)
#   python run_pipeline.py --only 4     (run a single phase)

import sys
import time

def run_phase(n: int, desc: str) -> None:
    print(f'\n{"#"*60}')
    print(f'#  Phase {n}: {desc}')
    print(f'{"#"*60}')
    start = time.time()

    if n == 1:
        from importlib import import_module
        mod = import_module('1_tile_maps')
        mod.tile_all_maps()
    elif n == 2:
        from importlib import import_module
        mod = import_module('2_ocr_extraction')
        mod.run_ocr()
    elif n == 3:
        from importlib import import_module
        mod = import_module('3_grid_detection')
        mod.assign_all_grid_refs()
    elif n == 4:
        from importlib import import_module
        mod = import_module('4_llm_cleaning')
        mod.run_llm_cleaning()
    elif n == 5:
        from importlib import import_module
        mod = import_module('5_database_assembly')
        mod.build_database()

    elapsed = time.time() - start
    print(f'\n  Phase {n} done in {elapsed:.1f}s')


PHASES = [
    (1, 'Tile Maps'),
    (2, 'OCR Extraction'),
    (3, 'Grid Detection'),
    (4, 'LLM Cleaning'),
    (5, 'Database Assembly'),
]

if __name__ == '__main__':
    args = sys.argv[1:]

    start_from = 1
    only_phase = None

    if '--from' in args:
        idx = args.index('--from')
        start_from = int(args[idx + 1])
    if '--only' in args:
        idx = args.index('--only')
        only_phase = int(args[idx + 1])

    print('\nCV-Toposheet Pipeline')
    print('='*60)

    total_start = time.time()

    for phase_num, phase_desc in PHASES:
        if only_phase is not None and phase_num != only_phase:
            continue
        if phase_num < start_from:
            print(f'[SKIP] Phase {phase_num}: {phase_desc}')
            continue
        run_phase(phase_num, phase_desc)

    total_elapsed = time.time() - total_start
    print(f'\n{"="*60}')
    print(f'Pipeline complete in {total_elapsed:.1f}s')
    print(f'Results: {__import__("config").RESULTS_FOLDER}')
    print('Run "python 6_query_interface.py" to search the database.')
    print('='*60)
