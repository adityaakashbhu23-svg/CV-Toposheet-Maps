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
from utils.db_utils import query_features, export_csv, search_fulltext, get_stats

DB_PATH = config.RESULTS_FOLDER / 'toposheet.db'


def print_results(results: list, title: str = '') -> None:
    if title:
        print(f'\n{"="*72}')
        print(f'  {title}')
        print(f'{"="*72}')
    if not results:
        print('  (no results found)\n')
        return
    print(f'  Found {len(results)} feature(s):\n')
    fmt = '  {:<28} {:<28} {:<12} {:<8} {:<5}'
    print(fmt.format('As Written on Map', 'Normalised Name', 'Type', 'Grid', 'Conf'))
    print('  ' + '-' * 72)
    for r in results:
        raw     = (r.get('original_text') or r['feature_name'])[:27]
        cleaned = r['feature_name'][:27]
        same    = raw.lower() == cleaned.lower()
        # Show arrow only when normalization actually changed the text
        name_col = cleaned if same else f'{raw} → {cleaned}'
        print(fmt.format(
            raw[:27],
            cleaned[:27],
            r['feature_type'][:11],
            r['grid_reference'][:7],
            f"{r['confidence']:.2f}",
        ))
    print()


def print_stats(stats: dict) -> None:
    print(f'\n{"="*60}')
    print('  DATABASE SUMMARY')
    print(f'{"="*60}')
    print(f'  Total features  : {stats["total"]}')
    print(f'\n  By feature type:')
    for ftype, cnt in stats['by_type'].items():
        print(f'    {ftype:<20} {cnt}')
    print(f'\n  By map:')
    for mname, cnt in stats['by_map'].items():
        print(f'    {mname[:40]:<42} {cnt}')
    c = stats['confidence']
    print(f'\n  Confidence:')
    print(f'    High  (≥0.8)   : {c["high_0.8+"]}')
    print(f'    Medium (0.5-0.8): {c["medium_0.5-0.8"]}')
    print(f'    Low   (<0.5)   : {c["low_under_0.5"]}')
    print(f'    Average        : {c["average"]}')
    print(f'\n  Top 10 most common names:')
    for name, cnt in stats['top_names']:
        print(f'    {name:<35} ×{cnt}')
    print()


def interactive_menu() -> None:
    print('\n' + '='*60)
    print('  CV-Toposheet: Map Feature Search')
    print('='*60)
    print('  Commands:')
    print('    1  Database summary & statistics')
    print('    2  List all features (alphabetical)')
    print('    3  Filter by feature type')
    print('    4  Filter by grid reference')
    print('    5  Search by name (full-text)')
    print('    6  Filter by map')
    print('    7  High-confidence only (≥0.8)')
    print('    8  Export current search to CSV')
    print('    q  Quit')
    print('='*60 + '\n')

    last_results = []

    while True:
        cmd = input('> Command: ').strip().lower()

        if cmd == 'q':
            break

        elif cmd == '1':
            stats = get_stats(str(DB_PATH))
            print_stats(stats)

        elif cmd == '2':
            last_results = query_features(str(DB_PATH), min_confidence=0.3)
            print_results(last_results, 'All Features (A→Z)')

        elif cmd == '3':
            ftype = input('  Feature type (settlement/river/mountain/lake/forest/road/landmark): ').strip()
            last_results = query_features(str(DB_PATH), feature_type=ftype, min_confidence=0.3)
            print_results(last_results, f'Features of type: {ftype}')

        elif cmd == '4':
            grid = input('  Grid reference (e.g. B-3): ').strip().upper()
            last_results = query_features(str(DB_PATH), grid_ref=grid)
            print_results(last_results, f'Features in grid {grid}')

        elif cmd == '5':
            query = input('  Search (e.g. "rampur" or "nala river"): ').strip()
            last_results = search_fulltext(str(DB_PATH), query)
            print_results(last_results, f'Full-text search: "{query}"')

        elif cmd == '6':
            map_name = input('  Map name (partial match): ').strip()
            last_results = query_features(str(DB_PATH), map_name=map_name, min_confidence=0.3)
            print_results(last_results, f'Features in map: {map_name}')

        elif cmd == '7':
            ftype = input('  Feature type (blank = all): ').strip() or None
            last_results = query_features(str(DB_PATH), feature_type=ftype, min_confidence=0.8)
            print_results(last_results, f'High-confidence features (≥0.8){" — " + ftype if ftype else ""}')

        elif cmd == '8':
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
            print('  Unknown command. Enter 1-8 or q.\n')


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
