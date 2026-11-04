import logging
from collections import OrderedDict
from copy import deepcopy
from pymongo import UpdateOne
from app.db import clean, now, review_mode_enabled
from app.analytics.engine import (
    discount,
    quality,
    eligible,
    comparable,
    value,
    dt,
)

log = logging.getLogger('marketlens.summaries')
_FALLBACK_CACHE_LIMIT = 2
_fallback_cache = OrderedDict()

def compute_product_summary(db, product, cutoff=None):
    rows = list(db.observations.find({'product_id': product['id']}))
    return compute_product_summary_from_rows(product, rows, cutoff)


def compute_product_summary_from_rows(product, rows, cutoff=None):
    cutoff = cutoff or now()
    if not rows:
        d = discount([], cutoff)
        q = quality([], cutoff)
        return {
            **clean(product),
            'current': None,
            'days': 0,
            'advertised': None,
            'latest': None,
            'discount': d,
            'quality': q,
            'observations_count': 0,
            'updated_at': now()
        }

    rs = comparable(eligible(rows, cutoff))
    current_row = rs[-1] if rs else next(iter(rows), None)

    price = value(current_row) if current_row else None
    ref = current_row.get('reference_price') if current_row else None
    advertised = (
        100 * (ref - current_row['price']) / ref
        if ref and current_row and current_row.get('price')
        else None
    )

    days = len({r['observed_at'][:10] for r in rs}) if rs else 0

    d = discount(rows, cutoff)
    q = quality(rows, cutoff)

    return {
        **clean(product),
        'current': price,
        'days': days,
        'advertised': advertised,
        'latest': clean(current_row),
        'discount': d,
        'quality': q,
        'observations_count': len(rows),
        'updated_at': now()
    }


def clear_product_summary_cache(db=None):
    if db is None:
        _fallback_cache.clear()
    else:
        _fallback_cache.pop(id(db), None)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_number_or_none(value):
    return value is None or _is_number(value)


def _summary_is_complete(summary, product_id):
    required_fields = {
        'id',
        'title',
        'category',
        'brand',
        'currency',
        'marketplace',
        'condition',
        'current',
        'days',
        'advertised',
        'latest',
        'discount',
        'quality',
        'observations_count',
    }
    if not required_fields.issubset(summary) or summary['id'] != product_id:
        return False

    if (
        not isinstance(summary['title'], str)
        or not isinstance(summary['category'], str)
        or not isinstance(summary['brand'], str)
        or not isinstance(summary['currency'], str)
        or not isinstance(summary['marketplace'], str)
        or not isinstance(summary['condition'], str)
        or not isinstance(summary['days'], int)
        or isinstance(summary['days'], bool)
        or summary['days'] < 0
        or not _is_number_or_none(summary['current'])
        or not _is_number_or_none(summary['advertised'])
        or not isinstance(summary['observations_count'], int)
        or isinstance(summary['observations_count'], bool)
        or summary['observations_count'] < 0
    ):
        return False

    latest = summary['latest']
    if summary['observations_count'] == 0:
        if latest is not None or summary['current'] is not None:
            return False
    elif (
        not isinstance(latest, dict)
        or 'total' not in latest
        or not _is_number_or_none(latest['total'])
        or not _is_number(latest.get('price'))
        or summary['current'] != (
            latest['total']
            if latest['total'] is not None
            else latest['price']
        )
    ):
        return False

    discount_data = summary['discount']
    if not isinstance(discount_data, dict):
        return False
    if (
        not isinstance(discount_data.get('status'), str)
        or not _is_number_or_none(discount_data.get('score'))
    ):
        return False
    if set(discount_data) == {'status', 'score'}:
        if (
            discount_data['status'] != 'insufficient history'
            or discount_data['score'] is not None
        ):
            return False
    else:
        base_fields = {
            'window',
            'days',
            'coverage',
            'current',
            'basis',
            'version',
            'scope',
            'evidence',
        }
        if not base_fields.issubset(discount_data):
            return False
        if (
            not isinstance(discount_data['window'], int)
            or not isinstance(discount_data['days'], int)
            or not _is_number(discount_data['coverage'])
            or not 0 <= discount_data['coverage'] <= 1
            or not _is_number_or_none(discount_data['current'])
            or discount_data['current'] != summary['current']
            or not isinstance(discount_data['basis'], str)
            or not isinstance(discount_data['version'], str)
            or discount_data['scope'] not in (
                'observed Buy Box',
                'same seller',
            )
            or not isinstance(discount_data['evidence'], list)
            or not all(
                isinstance(key, str)
                for key in discount_data['evidence']
            )
        ):
            return False
        if discount_data['status'] == 'ready':
            ready_fields = {
                'median',
                'low',
                'high',
                'average',
                'percentile',
                'volatility',
                'historical_savings_pct',
                'advertised_discount',
                'claim_gap',
                'components',
                'explanation',
            }
            if not ready_fields.issubset(discount_data):
                return False
            if not _is_number(discount_data['score']) or not all(
                _is_number(discount_data[field])
                for field in (
                    'median',
                    'low',
                    'high',
                    'average',
                    'percentile',
                    'volatility',
                    'historical_savings_pct',
                )
            ) or not _is_number_or_none(discount_data['advertised_discount']):
                return False
            if not _is_number_or_none(discount_data['claim_gap']):
                return False
            if not isinstance(discount_data['components'], list):
                return False
            if not all(
                isinstance(component, dict)
                and isinstance(component.get('name'), str)
                and _is_number(component.get('weight'))
                and _is_number(component.get('value'))
                for component in discount_data['components']
            ):
                return False
            if not isinstance(discount_data['explanation'], str):
                return False
        elif discount_data['status'] != 'insufficient history':
            return False
        elif discount_data['score'] is not None:
            return False
        elif (
            discount_data['current'] != summary['current']
            or not _is_number(discount_data['days'])
            or not _is_number(discount_data['coverage'])
        ):
            return False

    quality_data = summary['quality']
    if not isinstance(quality_data, dict):
        return False
    quality_fields = {
        'observations',
        'distinct_days',
        'coverage',
        'span_days',
        'missing_days',
        'freshness_hours',
        'sellers',
        'missing_timestamps',
        'eligible',
    }
    if not quality_fields.issubset(quality_data):
        return False
    if not all(
        isinstance(quality_data[field], int)
        and not isinstance(quality_data[field], bool)
        and quality_data[field] >= 0
        for field in (
            'observations',
            'distinct_days',
            'span_days',
            'missing_days',
            'sellers',
            'missing_timestamps',
        )
    ):
        return False
    if (
        not _is_number(quality_data['coverage'])
        or not 0 <= quality_data['coverage'] <= 1
        or not _is_number_or_none(quality_data['freshness_hours'])
    ):
        return False
    eligible_data = quality_data['eligible']
    return (
        quality_data['distinct_days'] == summary['days']
        and isinstance(eligible_data, dict)
        and all(
            isinstance(eligible_data.get(field), bool)
            for field in ('discount', 'forecast', 'seller')
        )
    )


def _cached_fallback(db, cutoff):
    entry = _fallback_cache.get(id(db))
    if entry is None or entry['db'] is not db:
        return None

    requested_cutoff = dt(cutoff)
    if entry['cutoff'] == requested_cutoff:
        summaries = deepcopy(entry['summaries'])
        for summary in summaries:
            summary['updated_at'] = now()
        return summaries
    if (
        entry['eligibility_boundary'] is not None
        and (
            requested_cutoff < entry['eligibility_boundary']
            or entry['cutoff'] < entry['eligibility_boundary']
        )
    ):
        return None

    summaries = deepcopy(entry['summaries'])
    delta_hours = (
        requested_cutoff - entry['cutoff']
    ).total_seconds() / 3600
    for summary in summaries:
        freshness = summary['quality']['freshness_hours']
        if freshness is not None:
            summary['quality']['freshness_hours'] = freshness + delta_hours
        summary['updated_at'] = now()
    return summaries


def read_product_summaries(db, cutoff=None):
    """Read complete summaries or calculate missing ones without writing."""
    cutoff = cutoff or now()
    products = list(db.products.find({}))
    summaries = list(db.product_summaries.find({}, {'_id': 0}))
    product_ids = {product['id'] for product in products}
    summaries_by_id = {}
    summary_ids_valid = True
    for summary in summaries:
        if (
            not isinstance(summary, dict)
            or not isinstance(summary.get('id'), str)
            or summary['id'] in summaries_by_id
        ):
            summary_ids_valid = False
            continue
        summaries_by_id[summary['id']] = summary
    complete = (
        summary_ids_valid
        and len(summaries) == len(products)
        and all(
            _summary_is_complete(
                summaries_by_id.get(product['id'], {}),
                product['id'],
            )
            for product in products
        )
        and set(summaries_by_id) == product_ids
    )
    if complete:
        _fallback_cache.pop(id(db), None)
        return summaries

    cached = _cached_fallback(db, cutoff)
    if cached is not None:
        _fallback_cache.move_to_end(id(db))
        return cached

    observations_by_product = {}
    eligibility_boundary = None
    cacheable = True
    for observation in db.observations.find({}):
        product_id = observation.get('product_id')
        if product_id is not None:
            observations_by_product.setdefault(product_id, []).append(
                observation
            )
        for field in ('observed_at', 'ingested_at'):
            timestamp = observation.get(field)
            if timestamp is None:
                continue
            try:
                timestamp = dt(timestamp)
            except (TypeError, ValueError):
                cacheable = False
            else:
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    cacheable = False
                elif (
                    eligibility_boundary is None
                    or timestamp > eligibility_boundary
                ):
                    eligibility_boundary = timestamp

    result = [
        compute_product_summary_from_rows(
            product,
            observations_by_product.get(product['id'], []),
            cutoff,
        )
        for product in products
    ]
    if cacheable:
        _fallback_cache[id(db)] = {
            'db': db,
            'cutoff': dt(cutoff),
            'eligibility_boundary': eligibility_boundary,
            'summaries': deepcopy(result),
        }
        _fallback_cache.move_to_end(id(db))
        while len(_fallback_cache) > _FALLBACK_CACHE_LIMIT:
            _fallback_cache.popitem(last=False)

    return result


def ensure_summary_indexes(db):
    if review_mode_enabled():
        raise RuntimeError('Summary index creation is disabled in review mode')

    coll = db.product_summaries
    coll.create_index('id', unique=True)
    coll.create_index([('current', 1)])
    coll.create_index([('days', -1)])
    coll.create_index([('category', 1)])
    coll.create_index([('brand', 1)])
    coll.create_index([('marketplace', 1)])
    coll.create_index([('currency', 1)])
    coll.create_index([('discount.score', -1)])
    coll.create_index([('quality.distinct_days', -1)])
    coll.create_index([('title', 1)])

def build_summaries_batch(db, products, cutoff=None):
    if review_mode_enabled():
        raise RuntimeError('Summary writes are disabled in review mode')

    ops = []
    for p in products:
        summary = compute_product_summary(db, p, cutoff=cutoff)
        ops.append(
            UpdateOne(
                {'id': p['id']},
                {'$set': summary},
                upsert=True
            )
        )
    if ops:
        db.product_summaries.bulk_write(ops, ordered=False)
    return len(ops)

def sync_product_summaries(db, product_ids=None, cutoff=None, batch_size=50):
    if review_mode_enabled():
        raise RuntimeError('Summary writes are disabled in review mode')

    clear_product_summary_cache(db)
    ensure_summary_indexes(db)
    if product_ids is not None:
        pids = list(product_ids)
        products = list(db.products.find({'id': {'$in': pids}}))
        return build_summaries_batch(db, products, cutoff=cutoff)

    # Full sync - process in small batches to preserve memory
    cursor = db.products.find({})
    batch = []
    total = 0
    for p in cursor:
        batch.append(p)
        if len(batch) >= batch_size:
            build_summaries_batch(db, batch, cutoff=cutoff)
            total += len(batch)
            batch = []
    if batch:
        build_summaries_batch(db, batch, cutoff=cutoff)
        total += len(batch)
    return total

def verify_summaries(db, sample_size=10):
    """
    Verifies that values in product_summaries exactly match
    the original raw observations in db.observations.
    """
    summaries = list(db.product_summaries.aggregate([{'$sample': {'size': sample_size}}]))
    if not summaries:
        return {'verified': True, 'count': 0, 'details': 'No summaries to verify'}

    mismatches = []
    for s in summaries:
        pid = s['id']
        p = db.products.find_one({'id': pid})
        expected = compute_product_summary(db, p, cutoff=s.get('updated_at'))

        diffs = []
        if s.get('current') != expected.get('current'):
            diffs.append(f"current: got {s.get('current')}, expected {expected.get('current')}")
        if s.get('days') != expected.get('days'):
            diffs.append(f"days: got {s.get('days')}, expected {expected.get('days')}")
        if s.get('observations_count') != expected.get('observations_count'):
            diffs.append(f"obs_count: got {s.get('observations_count')}, expected {expected.get('observations_count')}")
        if s.get('discount', {}).get('score') != expected.get('discount', {}).get('score'):
            diffs.append(f"discount.score: got {s.get('discount',{}).get('score')}, expected {expected.get('discount',{}).get('score')}")

        if diffs:
            mismatches.append({'product_id': pid, 'diffs': diffs})

    return {
        'verified': len(mismatches) == 0,
        'checked': len(summaries),
        'mismatches': mismatches
    }
