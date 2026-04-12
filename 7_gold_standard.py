# 7_gold_standard.py  –  Build a "Gold Standard" verification dataset
#
# Samples 500 random entries from toposheet.db and exports them to
# results/gold_standard_review.csv for manual human verification.
#
# Workflow:
#   Step 1:  python 7_gold_standard.py sample       → creates gold_standard_review.csv
#   Step 2:  Open CSV in Excel, fill in the 'verified_*' columns
#   Step 3:  python 7_gold_standard.py stats         → accuracy stats on your corrections
#   Step 4:  python 7_gold_standard.py export-train  → exports fine-tune JSONL

import sys
import csv
import json
import random
import sqlite3
from pathlib import Path

import config

DB_PATH     = config.RESULTS_FOLDER / 'toposheet.db'
REVIEW_CSV  = config.RESULTS_FOLDER / 'gold_standard_review.csv'
TRAIN_JSONL = config.RESULTS_FOLDER / 'finetune_train.jsonl'
SAMPLE_SIZE = 500

FIELDNAMES = [
    'id', 'map_name', 'original_text', 'feature_name', 'feature_type',
    'grid_reference', 'confidence',
    # ── Human fills these columns ──
    'verified_correct',   # Y / N
    'verified_name',      # corrected name (leave blank if already correct)
    'verified_type',      # corrected type (leave blank if already correct)
    'notes',              # optional free-text notes
]


# ─────────────────────────────────────────────────────────────────────────────
def cmd_sample():
    """Sample SAMPLE_SIZE random rows from the DB and write review CSV."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM features")
    total = cur.fetchone()[0]
    print(f"[Gold Standard] Total features in DB: {total}")

    cur.execute("""
        SELECT id, map_name, original_text, feature_name, feature_type,
               grid_reference, confidence
        FROM features
        ORDER BY RANDOM()
        LIMIT ?
    """, (SAMPLE_SIZE,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    with open(REVIEW_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            r['verified_correct'] = ''  # human fills: Y or N
            r['verified_name']    = ''  # corrected name if N
            r['verified_type']    = ''  # corrected type if N
            r['notes']            = ''
            writer.writerow(r)

    print(f"[Gold Standard] Wrote {len(rows)} rows → {REVIEW_CSV}")
    print()
    print("  NEXT STEPS:")
    print("  1. Open results/gold_standard_review.csv in Excel")
    print("  2. For each row fill in:")
    print("       verified_correct = Y  (AI was correct)")
    print("       verified_correct = N  (AI was wrong — fill verified_name / verified_type)")
    print("  3. Save CSV and run:  python 7_gold_standard.py stats")


# ─────────────────────────────────────────────────────────────────────────────
def cmd_stats():
    """Read the completed review CSV and print accuracy statistics."""
    if not REVIEW_CSV.exists():
        print("[Gold Standard] review CSV not found. Run 'sample' first.")
        return

    rows = []
    with open(REVIEW_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    verified = [r for r in rows if r['verified_correct'].strip().upper() in ('Y', 'N')]
    if not verified:
        print("[Gold Standard] No verified rows yet (verified_correct column is empty).")
        return

    correct   = [r for r in verified if r['verified_correct'].strip().upper() == 'Y']
    incorrect = [r for r in verified if r['verified_correct'].strip().upper() == 'N']
    accuracy  = len(correct) / len(verified) * 100

    print(f"\n{'='*55}")
    print(f"  GOLD STANDARD ACCURACY REPORT")
    print(f"{'='*55}")
    print(f"  Total reviewed   : {len(verified)}")
    print(f"  Correct (Y)      : {len(correct)}")
    print(f"  Incorrect (N)    : {len(incorrect)}")
    print(f"  Accuracy         : {accuracy:.1f}%")

    # Break down errors by type
    type_errors: dict = {}
    for r in incorrect:
        t = r['feature_type'] or 'unknown'
        type_errors[t] = type_errors.get(t, 0) + 1
    if type_errors:
        print(f"\n  Errors by feature type:")
        for t, n in sorted(type_errors.items(), key=lambda x: -x[1]):
            print(f"    {t:<20} {n}")

    # Common error patterns
    print(f"\n  Sample of errors (first 10):")
    fmt = '    {:<30} → {:<30} | type: {} → {}'
    for r in incorrect[:10]:
        print(fmt.format(
            r['feature_name'][:29],
            (r['verified_name'] or '(same)')[:29],
            r['feature_type'],
            r['verified_type'] or '(same)'
        ))
    print()


# ─────────────────────────────────────────────────────────────────────────────
def cmd_export_train():
    """Export verified corrections as Vertex AI fine-tuning JSONL."""
    if not REVIEW_CSV.exists():
        print("[Gold Standard] review CSV not found. Run 'sample' first.")
        return

    rows = []
    with open(REVIEW_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    corrections = [r for r in rows if r['verified_correct'].strip().upper() == 'N'
                   and r['original_text'].strip()]

    if not corrections:
        print("[Gold Standard] No corrections found to export.")
        return

    from utils.llm_utils import SYSTEM_PROMPT

    count = 0
    with open(TRAIN_JSONL, 'w', encoding='utf-8') as f:
        for r in corrections:
            verified_name = r['verified_name'].strip() or r['feature_name'].strip()
            verified_type = r['verified_type'].strip() or r['feature_type'].strip()
            if not verified_name:
                continue

            # Vertex AI supervised fine-tune format (chat)
            entry = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Clean and classify these OCR-extracted map labels:\n\n1. {r['original_text']}"},
                    {"role": "model",  "content": json.dumps([{
                        "original":     r['original_text'],
                        "cleaned":      verified_name,
                        "feature_type": verified_type,
                        "confidence":   1.0
                    }])}
                ]
            }
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            count += 1

    print(f"[Gold Standard] Exported {count} correction examples → {TRAIN_JSONL}")
    print()
    print("  NEXT STEPS FOR FINE-TUNING:")
    print("  1. Upload results/finetune_train.jsonl to a GCS bucket:")
    print("     gsutil cp results/finetune_train.jsonl gs://YOUR_BUCKET/finetune/")
    print("  2. Go to Vertex AI → Training → Create Custom Training Job")
    print("     Model: gemini-2.0-flash-001")
    print("     Training data: gs://YOUR_BUCKET/finetune/finetune_train.jsonl")


# ─────────────────────────────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'sample'

    if cmd == 'sample':
        cmd_sample()
    elif cmd == 'stats':
        cmd_stats()
    elif cmd == 'export-train':
        cmd_export_train()
    else:
        print("Usage: python 7_gold_standard.py [sample | stats | export-train]")
        print()
        print("  sample        Sample 500 random rows for manual review")
        print("  stats         Show accuracy stats from your completed review")
        print("  export-train  Export corrections as Vertex AI JSONL for fine-tuning")


if __name__ == '__main__':
    main()
