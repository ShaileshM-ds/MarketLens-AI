"""
Dry-run CSV import validator for MarketLens AI.

Reads a Bright Data Amazon CSV, runs every row through the actual
normalization and seller-expansion functions, and prints a validation
report.  No MongoDB connection is made.  No data is written anywhere.

Usage:
    python -m scripts.dry_run_import <path/to/file.csv>

or from the backend/ directory:
    python scripts/dry_run_import.py <path/to/file.csv>
"""

import csv
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Ensure the backend package is importable when run directly.
# ---------------------------------------------------------------------------
import os
_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Import only pure-Python helpers — never db.py or ingest().
from app.services.normalize import (
    expand_additional_sellers,
    money,
    normalize,
    parse_coupon,
    seller_id_from_url,
    seller_id_type,
    timestamp,
)

SUPPORTED_CURRENCIES = {'INR', 'USD', 'GBP', 'EUR', 'JPY', 'CAD', 'AUD'}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_csv(path: str) -> list[dict]:
    """Read the entire CSV without any row limit."""
    with open(path, newline='', encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def _safe_timestamp(value) -> tuple[str | None, str | None]:
    """Return (iso_string, error_message).  error_message is None on success."""
    try:
        return timestamp(value), None
    except Exception as exc:
        return None, str(exc)


def _safe_money(value, currency: str) -> tuple[int | None, str | None]:
    """Return (minor_units, error_message).  error_message is None on success."""
    if value in (None, ''):
        return None, 'missing'
    try:
        result = money(value, currency)
        if result is None or result <= 0:
            return None, f'non-positive result for {value!r}'
        return result, None
    except Exception as exc:
        return None, str(exc)


def _currency(row: dict) -> str:
    return str(row.get('currency', '')).strip().upper()


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------

def validate(path: str) -> dict:
    rows = _read_csv(path)
    original_count = len(rows)

    # ---- per-row accumulators ----
    accepted_products: list[dict] = []          # (p, o) pairs
    accepted_additional: list[dict] = []        # expanded offer observations
    rejected: list[dict] = []                   # {row_num, reason, row}

    unique_products: set[str] = set()
    unique_sellers: set[str] = set()
    verified_seller_ids: set[str] = set()
    name_derived_ids: set[str] = set()

    currency_dist: Counter = Counter()
    condition_dist: Counter = Counter()
    seller_count_per_row: list[int] = []        # derived seller count per row
    missing_seller_name: int = 0
    missing_seller_id: int = 0
    invalid_prices: list[dict] = []
    invalid_timestamps: list[dict] = []
    unsupported_currencies: list[dict] = []
    duplicate_obs_keys: Counter = Counter()
    duplicate_offer_keys: Counter = Counter()
    capture_timestamps: set[str] = set()
    missing_fields_dist: Counter = Counter()

    # Track observation keys for duplicate detection
    obs_key_set: set[str] = set()

    for row_num, raw in enumerate(rows, start=1):
        currency = _currency(raw)

        # ---- currency check ----
        if currency not in SUPPORTED_CURRENCIES:
            unsupported_currencies.append({
                'row': row_num,
                'currency': currency or '(empty)',
            })

        # ---- missing field tracking ----
        for field in ('asin', 'seller_name', 'seller_id', 'final_price',
                      'currency', 'timestamp', 'url'):
            if not raw.get(field):
                missing_fields_dist[field] += 1

        if not raw.get('seller_name'):
            missing_seller_name += 1
        if not raw.get('seller_id') and not seller_id_from_url(raw.get('seller_url', '')):
            missing_seller_id += 1

        # ---- price check ----
        price_val = raw.get('final_price') or raw.get('price')
        _, price_err = _safe_money(price_val, currency or 'USD')
        if price_err:
            invalid_prices.append({
                'row': row_num,
                'value': price_val,
                'reason': price_err,
            })

        # ---- timestamp check ----
        ts_val = raw.get('timestamp') or raw.get('observed_at')
        ts_iso, ts_err = _safe_timestamp(ts_val)
        if ts_err:
            invalid_timestamps.append({
                'row': row_num,
                'value': ts_val,
                'reason': ts_err,
            })
        elif ts_iso:
            capture_timestamps.add(ts_iso)

        # ---- normalize the Buy Box row ----
        # Provide the fields normalize() expects from a Bright Data CSV row.
        norm_input = dict(raw)
        norm_input.setdefault('marketplace',
            'amazon.com' if not raw.get('marketplace') else raw['marketplace'])
        # Map Bright Data field names to normalize() field names.
        if not norm_input.get('price') and norm_input.get('final_price'):
            norm_input['price'] = norm_input['final_price']
        if not norm_input.get('observed_at') and norm_input.get('timestamp'):
            norm_input['observed_at'] = norm_input['timestamp']
        if not norm_input.get('reference_price') and norm_input.get('initial_price'):
            norm_input['reference_price'] = norm_input['initial_price']

        # ---- per-row seller identity set (normalized observations only) ----
        row_seller_ids: set[str] = set()

        # ---- normalize the Buy Box row (independent of additional sellers) ----
        buybox_ok = False
        buybox_o = None
        try:
            p, buybox_o = normalize(norm_input, 'imported', 'dry_run')
            buybox_ok = True
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({
                'row': row_num,
                'reason': str(exc),
                'asin': raw.get('asin', ''),
            })

        if buybox_ok and buybox_o is not None:
            obs_key = buybox_o.get('key', '')
            if obs_key in obs_key_set:
                duplicate_obs_keys[obs_key] += 1
            else:
                obs_key_set.add(obs_key)
                accepted_products.append(buybox_o)

            unique_products.add(p['external_id'])
            currency_dist[buybox_o['currency']] += 1
            condition_dist[str(buybox_o['metadata'].get('condition', 'unknown'))] += 1

            sid = buybox_o.get('seller_id')
            if sid:
                row_seller_ids.add(sid)
                unique_sellers.add(sid)
                id_t = seller_id_type(norm_input)
                if id_t == 'verified':
                    verified_seller_ids.add(sid)
                else:
                    name_derived_ids.add(sid)

        # ---- expand additional sellers (always, even if Buy Box failed) ----
        expanded = expand_additional_sellers(norm_input)
        offer_seen: set[tuple] = set()

        for exp_raw in expanded:
            try:
                _, eo = normalize(exp_raw, 'imported', 'dry_run')
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append({
                    'row': row_num,
                    'reason': f'additional seller: {exc}',
                    'asin': raw.get('asin', ''),
                })
                continue

            offer_key = (
                eo.get('seller_id'),
                eo.get('price'),
                str(eo.get('metadata', {}).get('condition', '')).lower(),
                str((eo.get('seller_offer_details') or {}).get('ships_from', '')),
            )
            if offer_key in offer_seen:
                duplicate_offer_keys[str(offer_key)] += 1
                continue
            offer_seen.add(offer_key)

            ekey = eo.get('key', '')
            if ekey in obs_key_set:
                duplicate_obs_keys[ekey] += 1
            else:
                obs_key_set.add(ekey)
                accepted_additional.append(eo)

            esid = eo.get('seller_id')
            if esid:
                row_seller_ids.add(esid)
                unique_sellers.add(esid)
                offer_details = eo.get('seller_offer_details') or {}
                if offer_details.get('seller_id_type') == 'verified':
                    verified_seller_ids.add(esid)
                else:
                    name_derived_ids.add(esid)

        # ---- derived seller count: distinct normalized seller IDs this row ----
        seller_count_per_row.append(len(row_seller_ids))

    # ---- seller count distribution ----
    seller_count_dist = dict(Counter(seller_count_per_row))

    # ---- timezone check ----
    all_tz_aware = True
    for ts in capture_timestamps:
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                all_tz_aware = False
                break
        except ValueError:
            all_tz_aware = False
            break

    total_potential = len(accepted_products) + len(accepted_additional)

    return {
        'source_file': os.path.basename(path),
        'original_row_count': original_count,
        'accepted_product_observations': len(accepted_products),
        'accepted_additional_seller_observations': len(accepted_additional),
        'total_potential_observations': total_potential,
        'rejected_count': len(rejected),
        'unique_products': len(unique_products),
        'unique_seller_identities': len(unique_sellers),
        'verified_seller_ids': len(verified_seller_ids),
        'name_derived_seller_ids': len(name_derived_ids),
        'seller_count_distribution': dict(sorted(seller_count_dist.items())),
        'missing_seller_name_rows': missing_seller_name,
        'missing_seller_id_rows': missing_seller_id,
        'invalid_prices': invalid_prices,
        'invalid_timestamps': invalid_timestamps,
        'unsupported_currencies': unsupported_currencies,
        'duplicate_observation_keys': len(duplicate_obs_keys),
        'duplicate_offer_keys': len(duplicate_offer_keys),
        'rejected_records': rejected,
        'missing_fields': dict(missing_fields_dist.most_common()),
        'condition_distribution': dict(condition_dist.most_common()),
        'currency_distribution': dict(currency_dist.most_common()),
        'all_timestamps_tz_aware': all_tz_aware,
        'distinct_capture_timestamps': len(capture_timestamps),
        'multiple_capture_timestamps': len(capture_timestamps) > 1,
        'capture_timestamps_sample': sorted(capture_timestamps)[:5],
    }


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print('=' * 60)


def print_report(r: dict) -> None:
    _section('DRY-RUN IMPORT VALIDATION REPORT')
    print(f"  Source file : {r['source_file']}")

    _section('Row counts')
    print(f"  Original CSV rows                : {r['original_row_count']}")
    print(f"  Accepted Buy Box observations    : {r['accepted_product_observations']}")
    print(f"  Accepted additional-seller obs.  : {r['accepted_additional_seller_observations']}")
    print(f"  Total potential observations     : {r['total_potential_observations']}")
    print(f"  Rejected records                 : {r['rejected_count']}")

    _section('Products and sellers')
    print(f"  Unique products (ASIN)           : {r['unique_products']}")
    print(f"  Unique seller identities         : {r['unique_seller_identities']}")
    print(f"  Verified Amazon seller IDs       : {r['verified_seller_ids']}")
    print(f"  Name-derived fallback IDs        : {r['name_derived_seller_ids']}")
    print(f"  Missing seller name rows         : {r['missing_seller_name_rows']}")
    print(f"  Missing seller ID rows           : {r['missing_seller_id_rows']}")

    _section('Seller count distribution (derived, not number_of_sellers)')
    for count, freq in sorted(r['seller_count_distribution'].items()):
        print(f"  {count} seller(s) : {freq} row(s)")

    _section('Currency distribution')
    for cur, n in r['currency_distribution'].items():
        print(f"  {cur} : {n}")
    if r['unsupported_currencies']:
        print(f"\n  Unsupported currencies ({len(r['unsupported_currencies'])}):")
        for item in r['unsupported_currencies'][:10]:
            print(f"    row {item['row']}: {item['currency']!r}")

    _section('Condition distribution')
    for cond, n in r['condition_distribution'].items():
        print(f"  {cond} : {n}")

    _section('Timestamps')
    print(f"  Distinct capture timestamps      : {r['distinct_capture_timestamps']}")
    print(f"  Multiple capture timestamps      : {r['multiple_capture_timestamps']}")
    print(f"  All timestamps timezone-aware    : {r['all_timestamps_tz_aware']}")
    if r['capture_timestamps_sample']:
        print(f"  Sample timestamps:")
        for ts in r['capture_timestamps_sample']:
            print(f"    {ts}")
    if r['invalid_timestamps']:
        print(f"\n  Invalid timestamps ({len(r['invalid_timestamps'])}):")
        for item in r['invalid_timestamps'][:10]:
            print(f"    row {item['row']}: {item['value']!r} — {item['reason']}")

    _section('Prices')
    if r['invalid_prices']:
        print(f"  Invalid/missing prices ({len(r['invalid_prices'])}):")
        for item in r['invalid_prices'][:10]:
            print(f"    row {item['row']}: {item['value']!r} — {item['reason']}")
    else:
        print('  All prices valid.')

    _section('Duplicates')
    print(f"  Duplicate observation keys       : {r['duplicate_observation_keys']}")
    print(f"  Duplicate offer keys             : {r['duplicate_offer_keys']}")

    _section('Missing fields (rows where field is absent or empty)')
    if r['missing_fields']:
        for field, n in r['missing_fields'].items():
            print(f"  {field} : {n}")
    else:
        print('  No missing fields detected.')

    _section('Rejected records')
    if r['rejected_records']:
        for item in r['rejected_records'][:20]:
            print(f"  row {item['row']} [{item['asin']}]: {item['reason']}")
        if len(r['rejected_records']) > 20:
            print(f"  ... and {len(r['rejected_records']) - 20} more.")
    else:
        print('  No records rejected.')

    _section('Summary')
    total = r['original_row_count']
    accepted = r['accepted_product_observations']
    additional = r['accepted_additional_seller_observations']
    rejected = r['rejected_count']
    print(f"  {accepted}/{total} Buy Box rows accepted "
          f"({100*accepted/total:.1f}%)" if total else "  0 rows.")
    print(f"  {additional} additional-seller observations extracted.")
    print(f"  {rejected} rows rejected.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python scripts/dry_run_import.py <path/to/file.csv>',
              file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f'File not found: {path}', file=sys.stderr)
        sys.exit(1)

    print(f'Validating: {path}')
    report = validate(path)
    print_report(report)


if __name__ == '__main__':
    main()
