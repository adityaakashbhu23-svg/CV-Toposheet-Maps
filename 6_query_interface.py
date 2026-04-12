# 6_query_interface.py  –  Phase 6: Interactive search over the extracted database
#
# Usage:
#   python 6_query_interface.py                        (interactive menu)
#   python 6_query_interface.py rivers                 (list all rivers)
#   python 6_query_interface.py grid B-3               (features in grid B-3)
#   python 6_query_interface.py map "Palamau" rivers   (rivers in a specific map)

import sys
from pathlib import Path

import config
from utils.db_utils import query_features, export_csv

DB_PATH = config.RESULTS_FOLDER / 'toposheet.db'


def print_results(results: list, title: str = '') -> None:
    if title:
        print(f'\n{"="*60}')
        print(f'  {title}')
        print(f'{"="*60}')
    if not results:
        print('  (no results found)\n')
        return
    print(f'  Found {len(results)} feature(s):\n')
    fmt = '  {:<30} {:<15} {:<8} {:<6}'
    print(fmt.format('Feature Name', 'Type', 'Grid', 'Conf'))
    print('  ' + '-' * 60)
    for r in results:
        print(fmt.format(
            r['feature_name'][:29],
            r['feature_type'][:14],
            r['grid_reference'][:7],
            f"{r['confidence']:.2f}",
        ))
    print()


def interactive_menu() -> None:
    print('\n' + '='*60)
    print('  CV-Toposheet: Map Feature Search')
    print('='*60)
    print('  Commands:')
    print('    1  List all features (alphabetical)')
    print('    2  Filter by feature type')
    print('    3  Filter by grid reference')
    print('    4  Search by name')
    print('    5  Filter by map')
    print('    6  Export current search to CSV')
    print('    q  Quit')
    print('='*60 + '\n')

    last_results = []

    while True:
        cmd = input('> Command: ').strip().lower()

        if cmd == 'q':
            break

        elif cmd == '1':
            last_results = query_features(str(DB_PATH), min_confidence=0.3)
            print_results(last_results, 'All Features (A→Z)')

        elif cmd == '2':
            ftype = input('  Feature type (settlement/river/mountain/lake/forest/road/landmark): ').strip()
            last_results = query_features(str(DB_PATH), feature_type=ftype, min_confidence=0.3)
            print_results(last_results, f'Features of type: {ftype}')

        elif cmd == '3':
            grid = input('  Grid reference (e.g. B-3): ').strip().upper()
            last_results = query_features(str(DB_PATH), grid_ref=grid)
            print_results(last_results, f'Features in grid {grid}')

        elif cmd == '4':
            name = input('  Search name (partial match): ').strip()
            last_results = query_features(str(DB_PATH), search_name=name, min_confidence=0.3)
            print_results(last_results, f'Features matching "{name}"')

        elif cmd == '5':
            map_name = input('  Map name (partial match): ').strip()
            last_results = query_features(str(DB_PATH), map_name=map_name, min_confidence=0.3)
            print_results(last_results, f'Features in map: {map_name}')

        elif cmd == '6':
            if not last_results:
                print('  No results to export. Run a query first.\n')
                continue
            out = config.RESULTS_FOLDER / 'search_export.csv'
            import csv
            with open(out, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['feature_name', 'feature_type', 'grid_reference', 'confidence', 'map_name'])
                writer.writeheader()
                writer.writerows(last_results)
            print(f'  Exported {len(last_results)} rows to: {out}\n')

        else:
            print('  Unknown command. Enter 1-6 or q.\n')


def cli_mode(args: list) -> None:
    """Handle quick single-command usage from the terminal."""
    if not args:
        return

    if args[0] in ('rivers', 'settlements', 'mountains', 'lakes', 'forests', 'roads', 'landmarks'):
        results = query_features(str(DB_PATH), feature_type=args[0].rstrip('s'))
        print_results(results, f'All {args[0].title()}')

    elif args[0] == 'grid' and len(args) > 1:
        grid = args[1].upper()
        results = query_features(str(DB_PATH), grid_ref=grid)
        print_results(results, f'Features in grid {grid}')

    elif args[0] == 'map' and len(args) > 1:
        map_name = args[1]
        ftype = args[2] if len(args) > 2 else None
        results = query_features(str(DB_PATH), map_name=map_name, feature_type=ftype)
        print_results(results, f'{map_name} → {ftype or "all types"}')

    elif args[0] == 'search' and len(args) > 1:
        results = query_features(str(DB_PATH), search_name=args[1])
        print_results(results, f'Search: {args[1]}')

    else:
        print(f'Unknown command: {" ".join(args)}')
        print('Usage: python 6_query_interface.py [rivers|grid B-3|map "Name" rivers|search "term"]')


if __name__ == '__main__':
    if not DB_PATH.exists():
        print(f'[QUERY] Database not found: {DB_PATH}')
        print('         Run 5_database_assembly.py first.')
        sys.exit(1)

    args = sys.argv[1:]
    if args:
        cli_mode(args)
    else:
        interactive_menu()
