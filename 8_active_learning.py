# 8_active_learning.py  –  Active Learning Loop
#
# Implements the human-in-the-loop feedback cycle:
#
#   Step 1:  python 8_active_learning.py flag          → find low-confidence items → CSV
#   Step 2:  Human corrects the CSV (same columns as gold standard)
#   Step 3:  python 8_active_learning.py apply          → writes corrections back to DB
#   Step 4:  python 8_active_learning.py export-train   → append to fine-tuning JSONL
#
# The loop:  Extract → Flag → Review → Apply → Re-Train (repeat)

import sys
import csv
import json
import sqlite3
from pathlib import Path
from datetime import datetime

import config

DB_PATH        = config.RESULTS_FOLDER / 'toposheet.db'
FLAG_CSV       = config.RESULTS_FOLDER / 'active_learning_review.csv'
TRAIN_JSONL    = config.RESULTS_FOLDER / 'finetune_train.jsonl'
CORRECTIONS_LOG = config.RESULTS_FOLDER / 'corrections_log.json'

LOW_CONF_THRESHOLD = 0.70   # flag anything below this

FIELDNAMES = [
    'id', 'map_name', 'original_text', 'feature_name', 'feature_type',
    'grid_reference', 'confidence',
    'verified_correct',   # Y / N / SKIP
    'verified_name',
    'verified_type',
    'notes',
]


# ─────────────────────────────────────────────────────────────────────────────
def cmd_flag(threshold: float = LOW_CONF_THRESHOLD):
    """Export all low-confidence rows to a review CSV."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, map_name, original_text, feature_name, feature_type,
               grid_reference, confidence
        FROM features
        WHERE confidence < ?
        ORDER BY confidence ASC
    """, (threshold,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print(f"[Active Learning] No features below confidence {threshold}. Nothing to flag.")
        return

    # Skip rows already in corrections_log
    corrected_ids = set()
    if CORRECTIONS_LOG.exists():
        with open(CORRECTIONS_LOG, encoding='utf-8') as f:
            corrected_ids = {entry['id'] for entry in json.load(f)}
    rows = [r for r in rows if r['id'] not in corrected_ids]

    with open(FLAG_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            r['verified_correct'] = ''
            r['verified_name']    = ''
            r['verified_type']    = ''
            r['notes']            = ''
            writer.writerow(r)

    print(f"[Active Learning] Flagged {len(rows)} low-confidence features (< {threshold})")
    print(f"  → {FLAG_CSV}")
    print()
    print("  NEXT STEPS:")
    print("  1. Open results/active_learning_review.csv in Excel")
    print("  2. For each row:")
    print("       verified_correct = Y   (AI was right — keep it)")
    print("       verified_correct = N   (AI was wrong — fill verified_name / verified_type)")
    print("       verified_correct = SKIP (unsure — ignore)")
    print("  3. Save and run:  python 8_active_learning.py apply")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_apply():
    """Write human corrections back into toposheet.db and log them."""
    if not FLAG_CSV.exists():
        print("[Active Learning] Review CSV not found. Run 'flag' first.")
        return

    rows = []
    with open(FLAG_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    corrections = [
        r for r in rows
        if r['verified_correct'].strip().upper() == 'N'
        and r['id'].strip()
    ]
    verified_ok = [
        r for r in rows
        if r['verified_correct'].strip().upper() == 'Y'
        and r['id'].strip()
    ]

    if not corrections and not verified_ok:
        print("[Active Learning] No verified rows found. Fill in verified_correct column first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur  = conn.cursor()
    applied = 0

    # Apply corrections to DB
    for r in corrections:
        new_name = r['verified_name'].strip() or r['feature_name'].strip()
        new_type = r['verified_type'].strip() or r['feature_type'].strip()
        cur.execute("""
            UPDATE features
            SET feature_name = ?, feature_type = ?, confidence = 1.0
            WHERE id = ?
        """, (new_name, new_type, int(r['id'])))
        applied += 1

    # Boost confidence of confirmed-correct items
    for r in verified_ok:
        cur.execute("""
            UPDATE features SET confidence = MIN(confidence + 0.15, 1.0)
            WHERE id = ?
        """, (int(r['id']),))

    conn.commit()
    conn.close()

    # Append to corrections log
    log_entries = []
    if CORRECTIONS_LOG.exists():
        with open(CORRECTIONS_LOG, encoding='utf-8') as f:
            log_entries = json.load(f)

    timestamp = datetime.now().isoformat()
    for r in corrections:
        log_entries.append({
            'id':            int(r['id']),
            'map_name':      r['map_name'],
            'original_text': r['original_text'],
            'old_name':      r['feature_name'],
            'new_name':      r['verified_name'].strip() or r['feature_name'],
            'old_type':      r['feature_type'],
            'new_type':      r['verified_type'].strip() or r['feature_type'],
            'timestamp':     timestamp,
        })
    for r in verified_ok:
        log_entries.append({'id': int(r['id']), 'verified_correct': True, 'timestamp': timestamp})

    with open(CORRECTIONS_LOG, 'w', encoding='utf-8') as f:
        json.dump(log_entries, f, indent=2, ensure_ascii=False)

    print(f"[Active Learning] Applied {applied} corrections to database")
    print(f"[Active Learning] Confirmed {len(verified_ok)} correct items (confidence boosted)")
    print(f"[Active Learning] Corrections logged → {CORRECTIONS_LOG}")
    print()
    print("  Run 'python 8_active_learning.py export-train' to add to fine-tune dataset.")

    # Re-export updated CSV and JSON
    _reexport_db()


# ─────────────────────────────────────────────────────────────────────────────
def cmd_export_train():
    """Append active-learning corrections to the fine-tuning JSONL."""
    if not CORRECTIONS_LOG.exists():
        print("[Active Learning] No corrections log found. Run 'apply' first.")
        return

    with open(CORRECTIONS_LOG, encoding='utf-8') as f:
        log_entries = json.load(f)

    real_corrections = [e for e in log_entries if e.get('new_name') and e.get('old_name') != e.get('new_name')]
    if not real_corrections:
        print("[Active Learning] No new corrections to export.")
        return

    from utils.llm_utils import SYSTEM_PROMPT

    # Append to existing JSONL (don't overwrite gold standard data)
    with open(TRAIN_JSONL, 'a', encoding='utf-8') as f:
        for e in real_corrections:
            entry = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Clean and classify these OCR-extracted map labels:\n\n1. {e['original_text']}"},
                    {"role": "model",  "content": json.dumps([{
                        "original":     e['original_text'],
                        "cleaned":      e['new_name'],
                        "feature_type": e['new_type'],
                        "confidence":   1.0
                    }])}
                ]
            }
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"[Active Learning] Appended {len(real_corrections)} examples → {TRAIN_JSONL}")
    print()
    print("  Fine-tune dataset is growing. Upload to GCS when ready:")
    print("  gsutil cp results/finetune_train.jsonl gs://YOUR_BUCKET/finetune/")


# ─────────────────────────────────────────────────────────────────────────────
def _reexport_db():
    """Re-export the updated DB to CSV and JSON after corrections."""
    import csv as csv_mod
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM features ORDER BY map_name, feature_name")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    csv_path  = config.RESULTS_FOLDER / 'extracted_data.csv'
    json_path = config.RESULTS_FOLDER / 'extracted_data.json'

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        if rows:
            writer = csv_mod.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"[Active Learning] Re-exported {len(rows)} rows → CSV + JSON")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_summary():
    """Print a summary of all active-learning sessions so far."""
    if not CORRECTIONS_LOG.exists():
        print("[Active Learning] No corrections log yet.")
        return

    with open(CORRECTIONS_LOG, encoding='utf-8') as f:
        log = json.load(f)

    corrections = [e for e in log if 'new_name' in e]
    confirmed   = [e for e in log if e.get('verified_correct') is True]

    print(f"\n{'='*55}")
    print(f"  ACTIVE LEARNING SUMMARY")
    print(f"{'='*55}")
    print(f"  Total corrections applied : {len(corrections)}")
    print(f"  Total confirmed correct   : {len(confirmed)}")

    # Type distribution of corrections
    type_counts: dict = {}
    for e in corrections:
        t = e.get('new_type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1
    if type_counts:
        print(f"\n  Corrections by type:")
        for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t:<20} {n}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'flag'
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else LOW_CONF_THRESHOLD

    if cmd == 'flag':
        cmd_flag(threshold)
    elif cmd == 'apply':
        cmd_apply()
    elif cmd == 'export-train':
        cmd_export_train()
    elif cmd == 'summary':
        cmd_summary()
    else:
        print("Usage: python 8_active_learning.py [flag [threshold] | apply | export-train | summary]")
        print()
        print("  flag [0.7]    Export low-confidence features for human review (default threshold: 0.7)")
        print("  apply         Write corrections back to database")
        print("  export-train  Append corrections to fine-tuning JSONL")
        print("  summary       Show all corrections made so far")


if __name__ == '__main__':
    main()
