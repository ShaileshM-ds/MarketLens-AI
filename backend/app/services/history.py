
"""Deterministic historical observation generation.

Generated history is simulated from real imported anchor observations.
This module does not connect to MongoDB at import time.
"""

import hashlib
import random

from datetime import datetime, timedelta, timezone
from decimal import Decimal


HISTORY_RUN_ID = 'history-gen-v1'
HISTORY_SOURCE_ID = 'history-gen-v1'

DEFAULT_DAYS = 180
DEFAULT_SEED = 42

# Historical prices cannot drift more than 30% from their anchor price.
MAX_CUMULATIVE_PRICE_CHANGE = 0.30


def _anchor_seed(
    global_seed: int,
    product_id: str,
    seller_id: str,
) -> int:
    """Create a stable seed for one product/seller combination."""

    key = (
        f'{global_seed}:{product_id}:{seller_id}'
    ).encode('utf-8')

    return (
        int(hashlib.sha256(key).hexdigest(), 16)
        & 0xFFFFFFFF
    )


def _minor_to_major(
    minor: int | None,
    currency: str,
) -> str | None:
    """Convert normalized minor currency units to a decimal string."""

    if minor is None:
        return None

    if currency != 'USD':
        raise ValueError(
            f'Unsupported history currency: {currency}'
        )

    return format(
        Decimal(int(minor)) / Decimal(100),
        '.2f',
    )


def _price_walk(
    anchor_price_minor: int,
    days: int,
    rng: random.Random,
) -> list[int]:
    """Generate deterministic daily prices ending at the anchor price."""

    if days < 1:
        raise ValueError('days must be at least 1')

    if anchor_price_minor <= 0:
        raise ValueError('anchor price must be positive')

    anchor_price_minor = int(anchor_price_minor)

    lower_bound = max(
        1,
        round(
            anchor_price_minor
            * (1 - MAX_CUMULATIVE_PRICE_CHANGE)
        ),
    )

    upper_bound = round(
        anchor_price_minor
        * (1 + MAX_CUMULATIVE_PRICE_CHANGE)
    )

    prices = [0] * days
    current = anchor_price_minor

    for index in range(days - 1, -1, -1):
        prices[index] = current

        if index > 0:
            change = max(
                -0.06,
                min(0.06, rng.gauss(0, 0.012)),
            )

            previous = round(
                current / (1 + change)
            )

            current = max(
                lower_bound,
                min(upper_bound, previous),
            )

    return prices


def _parse_datetime(value):
    """Return a timezone-aware datetime or None."""

    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(
                str(value).replace('Z', '+00:00')
            )
        except (TypeError, ValueError):
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result


def extract_anchors(observations):
    """Select the latest eligible observation per product/seller/region.

    Non-USD observations are skipped; only USD is supported for
    synthetic history generation.
    """

    latest = {}

    for observation in observations:
        if not isinstance(observation, dict):
            continue

        metadata = observation.get('metadata') or {}

        product_id = observation.get('product_id')
        seller_id = observation.get('seller_id')

        if not product_id or not seller_id:
            continue

        observed_at = _parse_datetime(
            observation.get('observed_at')
        )

        if observed_at is None:
            continue

        currency = str(
            observation.get('currency')
            or metadata.get('currency')
            or ''
        ).upper()

        if currency != 'USD':
            continue

        price = observation.get('price')

        if price is None:
            continue

        try:
            price = int(price)
        except (TypeError, ValueError):
            continue

        if price <= 0:
            continue

        external_id = metadata.get('external_id')
        marketplace = metadata.get('marketplace')

        if not external_id or not marketplace:
            continue

        condition = metadata.get('condition') or 'unknown'
        variant = metadata.get('variant', {})
        region = observation.get('region')

        key = (
            str(product_id),
            str(seller_id),
            currency,
            str(condition),
            str(marketplace),
            str(region or ''),
        )

        anchor = {
            'product_id': product_id,
            'seller_id': seller_id,
            'seller_name': observation.get('seller_name'),
            'currency': currency,
            'observed_at': observed_at.isoformat(),
            'price': price,
            'shipping': observation.get('shipping'),
            'tax_treatment': observation.get(
                'tax_treatment',
                'unknown',
            ),
            'reference_price': observation.get(
                'reference_price'
            ),
            'buy_box': observation.get('buy_box'),
            'region': region,
            'delivery': observation.get('delivery'),
            'rating': observation.get('rating'),
            'review_count': observation.get('review_count'),
            'seller_count': observation.get('seller_count'),
            'seller_url': observation.get('seller_url'),
            'seller_offer': observation.get('seller_offer'),
            'seller_offer_details': observation.get(
                'seller_offer_details'
            ),
            'ships_from': observation.get('ships_from'),
            'external_id': external_id,
            'marketplace': marketplace,
            'variant': variant,
            'condition': condition,
            'title': metadata.get('title'),
            'brand': metadata.get('brand'),
            'category': metadata.get('category'),
            'product_url': metadata.get('product_url'),
            'image_url': metadata.get('image_url'),
            'country': metadata.get('country'),
            'parent_id': metadata.get('parent_id'),
        }

        previous = latest.get(key)

        if (
            previous is None
            or observed_at
            > _parse_datetime(previous['observed_at'])
        ):
            latest[key] = anchor

    anchors = list(latest.values())

    anchors.sort(
        key=lambda item: (
            str(item['product_id']),
            str(item['seller_id']),
            str(item.get('region') or ''),
        )
    )

    return anchors


def generate_history(
    anchors,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
):
    """Generate deterministic simulated observations before each anchor."""

    if days < 1:
        raise ValueError('days must be at least 1')

    rows = []

    for anchor in anchors:
        anchor_dt = _parse_datetime(
            anchor.get('observed_at')
        )

        if anchor_dt is None:
            raise ValueError(
                'Anchor must have a valid observed_at timestamp'
            )

        product_id = str(anchor['product_id'])
        seller_id = str(anchor['seller_id'])

        rng = random.Random(
            _anchor_seed(
                seed,
                product_id,
                seller_id,
            )
        )

        prices = _price_walk(
            anchor['price'],
            days,
            rng,
        )

        for index, price in enumerate(prices):
            observed_at = (
                anchor_dt
                - timedelta(days=days - index)
            )

            row = {
                'asin': anchor.get('external_id'),
                'marketplace': anchor.get('marketplace'),
                'variant': anchor.get('variant', {}),
                'condition': anchor.get('condition', 'unknown'),
                'parent_asin': anchor.get('parent_id'),

                'seller_id': anchor.get('seller_id'),
                'seller_name': anchor.get('seller_name'),

                'currency': anchor.get('currency'),
                'price': _minor_to_major(
                    price,
                    anchor.get('currency'),
                ),

                'shipping': _minor_to_major(
                    anchor.get('shipping'),
                    anchor.get('currency'),
                ) if anchor.get('shipping') is not None else None,

                'tax_treatment': anchor.get(
                    'tax_treatment',
                    'unknown',
                ),

                'reference_price': _minor_to_major(
                    anchor.get('reference_price'),
                    anchor.get('currency'),
                ) if anchor.get('reference_price') is not None else None,

                'buy_box': anchor.get('buy_box'),

                'region': anchor.get('region'),
                'delivery': anchor.get('delivery'),

                'rating': anchor.get('rating'),
                'review_count': anchor.get('review_count'),
                'seller_count': anchor.get('seller_count'),

                'seller_url': anchor.get('seller_url'),
                'seller_offer': anchor.get('seller_offer'),
                '_is_additional_seller': bool(
                    anchor.get('seller_offer')
                ),
                '_offer_details': (
                    anchor.get('seller_offer_details') or {}
                ),

                'ships_from': anchor.get('ships_from'),

                'title': anchor.get('title'),
                'brand': anchor.get('brand'),
                'category': anchor.get('category'),

                'url': anchor.get('product_url'),
                'product_url': anchor.get('product_url'),

                'image_url': anchor.get('image_url'),
                'country': anchor.get('country'),

                'observed_at': observed_at.isoformat(),

                'source_id': HISTORY_SOURCE_ID,
                '_generated': True,
            }

            rows.append(row)

    return rows


def seed_history(
    db,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    reset: bool = False,
):
    """Generate or resume simulated history in the imported database.

    Existing generated observations are checked against deterministic
    expected keys before ingestion. An interrupted run can be resumed
    with the same days and seed.
    """

    from app.services.normalize import (
        digest,
        ingest,
        normalize,
        queue_analysis_jobs,
    )
    from app.db import review_mode_enabled

    if review_mode_enabled():
        raise RuntimeError('History generation is disabled in review mode')

    db_name = db.name if hasattr(db, 'name') else ''

    if db_name != 'marketlens_imported':
        raise ValueError(
            f'seed_history() must only be called with the imported '
            f'database; got "{db_name}". '
            f'Use database("imported") explicitly.'
        )

    if days < 1:
        raise ValueError('days must be at least 1')

    if reset:
        existing = db.observations.find(
            {'run_id': HISTORY_RUN_ID},
            {'product_id': 1},
        )

        product_ids = {
            item['product_id']
            for item in existing
            if item.get('product_id')
        }

        job_ids = [
            digest([
                'analysis',
                HISTORY_RUN_ID,
                product_id,
            ])
            for product_id in product_ids
        ]

        db.observations.delete_many({
            'run_id': HISTORY_RUN_ID
        })

        db.raw.delete_many({
            'run_id': HISTORY_RUN_ID
        })

        db.quarantine.delete_many({
            'run_id': HISTORY_RUN_ID
        })

        db.runs.delete_many({
            'id': HISTORY_RUN_ID
        })

        if job_ids:
            db.jobs.delete_many({
                'id': {'$in': job_ids}
            })

    existing_run = db.runs.find_one({
        'id': HISTORY_RUN_ID
    })

    if existing_run:
        previous_days = existing_run.get('days')
        previous_seed = existing_run.get('seed')

        if (
            previous_days is not None
            and previous_days != days
        ):
            raise ValueError(
                'Existing history uses different days. '
                'Recovery stopped without changing data.'
            )

        if (
            previous_seed is not None
            and previous_seed != seed
        ):
            raise ValueError(
                'Existing history uses a different seed. '
                'Recovery stopped without changing data.'
            )

    # Only real observations may be used as anchors.
    source_observations = db.observations.find({
        'synthetic': {'$ne': True},
        'source_id': {'$ne': HISTORY_SOURCE_ID},
    })

    anchors = extract_anchors(source_observations)

    if not anchors:
        raise ValueError(
            'No valid real USD anchor observations were found.'
        )

    rows = generate_history(
        anchors,
        days=days,
        seed=seed,
    )

    # Calculate all expected deterministic observation keys.
    # This also validates the generated rows before database writes.
    expected_keys = set()

    for row in rows:
        _, observation = normalize(
            row,
            'imported',
            HISTORY_RUN_ID,
        )
        expected_keys.add(observation['key'])

    # Validate the existing partial run.
    existing_keys = set()

    for item in db.observations.find(
        {'run_id': HISTORY_RUN_ID},
        {'key': 1},
    ):
        key = item.get('key')

        if not key:
            raise ValueError(
                'An existing generated observation has no key. '
                'Recovery stopped without changing data.'
            )

        existing_keys.add(key)

    unexpected_keys = existing_keys - expected_keys

    if unexpected_keys:
        raise ValueError(
            f'Found {len(unexpected_keys)} existing generated keys '
            'that do not match the expected history. '
            'Recovery stopped without deleting anything.'
        )

    existing_count = len(existing_keys)

    # Create or update run metadata BEFORE ingestion.
    now = datetime.now(timezone.utc).isoformat()

    db.runs.update_one(
        {'id': HISTORY_RUN_ID},
        {
            '$set': {
                'id': HISTORY_RUN_ID,
                'mode': 'imported',
                'synthetic': True,
                'source_type': 'generated_history',
                'status': 'in_progress',
                'days': days,
                'seed': seed,
                'anchor_count': len(anchors),
                'generated_count': len(rows),
                'updated_at': now,
            },
            '$setOnInsert': {
                'created_at': now,
            },
        },
        upsert=True,
    )

    # Re-ingestion is safe for existing keys: ingest() catches
    # DuplicateKeyError on the unique observations.key index.
    counts = ingest(
        db,
        rows,
        'imported',
        HISTORY_RUN_ID,
        queue_analysis=False,
    )

    # Do not mark the run complete if any generated rows were rejected.
    if counts.get('rejected', 0):
        db.runs.update_one(
            {'id': HISTORY_RUN_ID},
            {
                '$set': {
                    'status': 'incomplete',
                    'synthetic': True,
                    'source_type': 'generated_history',
                    'updated_at': datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            },
        )

        raise RuntimeError(
            f'{counts["rejected"]} generated records were rejected. '
            'Analysis jobs were not queued. '
            'The run can be inspected and retried.'
        )

    # Verify that every expected key now exists under this run.
    final_keys = set()

    for item in db.observations.find(
        {'run_id': HISTORY_RUN_ID},
        {'key': 1},
    ):
        if item.get('key'):
            final_keys.add(item['key'])

    missing_keys = expected_keys - final_keys

    if missing_keys:
        db.runs.update_one(
            {'id': HISTORY_RUN_ID},
            {
                '$set': {
                    'status': 'incomplete',
                    'updated_at': datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            },
        )

        raise RuntimeError(
            f'{len(missing_keys)} expected observations are still '
            'missing. Analysis jobs were not queued.'
        )

    # Mark the run synthetic and completed before queuing analysis.
    db.runs.update_one(
        {'id': HISTORY_RUN_ID},
        {
            '$set': {
                'synthetic': True,
                'source_type': 'generated_history',
                'status': 'completed',
                'days': days,
                'seed': seed,
                'anchor_count': len(anchors),
                'generated_count': len(rows),
                'updated_at': datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        },
    )

    # Align ingested_at with observed_at for historical cutoff queries.
    db.observations.update_many(
        {'run_id': HISTORY_RUN_ID},
        [
            {
                '$set': {
                    'ingested_at': '$observed_at'
                }
            }
        ],
    )

    affected_product_ids = {
        row['product_id']
        for row in rows
        if row.get('product_id')
    }

    queue_analysis_jobs(
        db,
        affected_product_ids,
        'imported',
        HISTORY_RUN_ID,
    )

    return {
        **counts,
        'anchors': len(anchors),
        'generated': len(rows),
        'days': days,
        'seed': seed,
        'existing_before_resume': existing_count,
        'remaining_inserted': counts.get('accepted', 0),
        'already_present': counts.get('duplicates', 0),
    }
