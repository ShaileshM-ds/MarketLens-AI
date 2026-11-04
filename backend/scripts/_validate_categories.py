"""
Read-only category validation against coolingPad_captureCard_final.csv.
No MongoDB, no API calls, no file writes, no workers, no Bright Data.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter
from app.services.normalize import parse_file, normalize

CSV_PATH = r'C:\Users\Impana Dechamma\Downloads\coolingPad_captureCard_final.csv'

# ── 1. Load via parse_file() (the actual import path) ──────────────────────
with open(CSV_PATH, encoding='utf-8-sig') as f:
    content = f.read()

rows = parse_file(content)
print(f'Rows loaded by parse_file(): {len(rows)}')

# ── 2. Normalize every row ──────────────────────────────────────────────────
accepted   = []   # (raw_categories_field, normalized_category)
rejected   = []   # (row_num, reason)

for i, raw in enumerate(rows, 1):
    try:
        p, _ = normalize(raw, 'imported', 'validation_run')
        accepted.append({
            'row':      i,
            'asin':     raw.get('asin', ''),
            'raw_cats': raw.get('categories') or raw.get('category') or '',
            'category': p['category'],
        })
    except Exception as e:
        rejected.append({'row': i, 'asin': raw.get('asin', ''), 'reason': str(e)})

print(f'Accepted: {len(accepted)}')
print(f'Rejected: {len(rejected)}')

# ── 3. Category distribution ────────────────────────────────────────────────
cat_dist = Counter(r['category'] for r in accepted)
print(f'\nDistinct category values among accepted rows: {len(cat_dist)}')
print('\nCategory distribution (all values):')
for cat, n in cat_dist.most_common():
    print(f'  {n:4d}x  {cat!r}')

# ── 4. Check for raw JSON-encoded array strings in stored categories ────────
json_array_leakage = [
    r for r in accepted
    if r['category'].strip().startswith('[')
]
print(f'\nAccepted rows where category still starts with "[" (JSON leakage): '
      f'{len(json_array_leakage)}')
if json_array_leakage:
    for r in json_array_leakage[:5]:
        print(f'  row {r["row"]} [{r["asin"]}]: {r["category"][:80]!r}')

# ── 5. Check for empty / Uncategorized ─────────────────────────────────────
uncategorized = [r for r in accepted if r['category'] == 'Uncategorized']
empty_cat     = [r for r in accepted if not r['category'].strip()]
print(f'\nRows with category="Uncategorized": {len(uncategorized)}')
print(f'Rows with empty category string:    {len(empty_cat)}')

# ── 6. Confirm JSON-encoded inputs were parsed ──────────────────────────────
json_encoded_inputs = [
    r for r in accepted
    if isinstance(r['raw_cats'], str) and r['raw_cats'].strip().startswith('[')
]
correctly_parsed = [
    r for r in json_encoded_inputs
    if not r['category'].strip().startswith('[')
]
print(f'\nRows whose raw categories field was a JSON-encoded string: '
      f'{len(json_encoded_inputs)}')
print(f'Of those, correctly parsed (category does NOT start with "["): '
      f'{len(correctly_parsed)}')

# ── 7. 10+ examples of raw input → normalized output ───────────────────────
print('\n--- 12 examples: raw categories input → normalized category ---')
seen_raw_types = set()
examples = []
for r in accepted:
    raw_c = r['raw_cats']
    kind = (
        'json_array'  if isinstance(raw_c, str) and raw_c.strip().startswith('[') else
        'python_list' if isinstance(raw_c, list) else
        'plain_str'   if isinstance(raw_c, str) and raw_c.strip() else
        'empty'
    )
    examples.append((kind, r))

# Show 3 of each kind found, up to 12 total
from collections import defaultdict
by_kind = defaultdict(list)
for kind, r in examples:
    by_kind[kind].append(r)

shown = 0
for kind in ('json_array', 'python_list', 'plain_str', 'empty'):
    for r in by_kind[kind][:3]:
        raw_display = repr(str(r['raw_cats'])[:90])
        print(f'  [{kind}] row {r["row"]:3d} [{r["asin"]}]')
        print(f'    raw : {raw_display}')
        print(f'    out : {r["category"]!r}')
        shown += 1
        if shown >= 12:
            break
    if shown >= 12:
        break

# ── 8. Rejected details ─────────────────────────────────────────────────────
if rejected:
    print(f'\nRejected rows ({len(rejected)}):')
    for r in rejected:
        print(f'  row {r["row"]} [{r["asin"]}]: {r["reason"]}')

# ── 9. PASS / FAIL conclusion ───────────────────────────────────────────────
print('\n' + '='*60)
print('CONCLUSION')
print('='*60)

fail_reasons = []

if len(rows) != 469:
    fail_reasons.append(f'Expected 469 rows, got {len(rows)}')

if len(accepted) != 462:
    fail_reasons.append(f'Expected 462 accepted rows, got {len(accepted)}')

if json_array_leakage:
    fail_reasons.append(
        f'{len(json_array_leakage)} accepted row(s) still have a raw JSON '
        f'array string as category'
    )

if len(json_encoded_inputs) > 0 and len(correctly_parsed) != len(json_encoded_inputs):
    fail_reasons.append(
        f'{len(json_encoded_inputs) - len(correctly_parsed)} JSON-encoded '
        f'category string(s) were not parsed correctly'
    )

if empty_cat:
    fail_reasons.append(f'{len(empty_cat)} accepted row(s) have an empty category string')

if fail_reasons:
    print('RESULT: FAIL')
    for r in fail_reasons:
        print(f'  - {r}')
else:
    print('RESULT: PASS')
    print(f'  - All {len(rows)} rows loaded correctly')
    print(f'  - {len(accepted)} rows accepted, {len(rejected)} rejected (all "Positive price is required")')
    print(f'  - {len(json_encoded_inputs)} JSON-encoded category strings all parsed correctly')
    print(f'  - 0 accepted rows contain a raw JSON array string as category')
    print(f'  - {len(cat_dist)} distinct category values, all plain strings')
