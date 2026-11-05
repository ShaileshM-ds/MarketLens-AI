from datetime import datetime, timedelta, timezone
from decimal import Decimal

import random
import pytest

from app.services.history import (
    DEFAULT_DAYS,
    DEFAULT_SEED,
    HISTORY_RUN_ID,
    HISTORY_SOURCE_ID,
    MAX_CUMULATIVE_PRICE_CHANGE,
    _anchor_seed,
    _coerce_minor_units,
    _minor_to_major,
    _price_walk,
    extract_anchors,
    generate_history,
    iter_history,
    _parse_datetime,
)

from app.services.normalize import normalize, digest


# Product ID is derived from marketplace + external_id + variant only.
# Condition is NOT part of the product identity digest.
EXPECTED_PRODUCT_ID = digest([
    'amazon.com',
    'B0EXAMPLE01',
    {},
])[:24]


def make_observation(
    *,
    product_id=EXPECTED_PRODUCT_ID,
    seller_id='seller-123',
    price=2599,
    observed_at='2026-10-20T12:00:00+00:00',
    region='US',
    seller_name='Example Seller',
):
    return {
        'product_id': product_id,
        'seller_id': seller_id,
        'seller_name': seller_name,
        'price': price,
        'shipping': 0,
        'reference_price': 4999,
        'tax_treatment': 'included',
        'currency': 'USD',
        'buy_box': True,
        'region': region,
        'observed_at': observed_at,
        'delivery': 'Free delivery',
        'rating': 4.5,
        'review_count': 100,
        'seller_count': 2,
        'metadata': {
            'external_id': 'B0EXAMPLE01',
            'marketplace': 'amazon.com',
            'variant': {},
            'condition': 'new',
            'title': 'Example Product',
            'brand': 'Example Brand',
            'category': 'Electronics',
            'currency': 'USD',
            'product_url': (
                'https://www.amazon.com/dp/B0EXAMPLE01'
            ),
        },
    }


def make_anchor():
    return {
        'product_id': EXPECTED_PRODUCT_ID,
        'seller_id': 'seller-123',
        'seller_name': 'Example Seller',
        'currency': 'USD',
        'observed_at': '2026-10-20T12:00:00+00:00',
        'price': 2599,
        'shipping': 0,
        'tax_treatment': 'included',
        'reference_price': 4999,
        'buy_box': True,
        'region': 'US',
        'delivery': 'Free delivery',
        'rating': 4.5,
        'review_count': 100,
        'seller_count': 2,
        'seller_url': None,
        'external_id': 'B0EXAMPLE01',
        'marketplace': 'amazon.com',
        'variant': {},
        'condition': 'new',
        'title': 'Example Product',
        'brand': 'Example Brand',
        'category': 'Electronics',
        'product_url': (
            'https://www.amazon.com/dp/B0EXAMPLE01'
        ),
    }


# ---------------------------------------------------------
# Stable seeds
# ---------------------------------------------------------

def test_default_history_window_is_120_days():
    assert DEFAULT_DAYS == 120


def test_anchor_seed_is_deterministic():
    first = _anchor_seed(42, 'product-123', 'seller-123')
    second = _anchor_seed(42, 'product-123', 'seller-123')

    assert first == second


def test_anchor_seed_changes_with_global_seed():
    assert (
        _anchor_seed(42, 'product-123', 'seller-123')
        != _anchor_seed(43, 'product-123', 'seller-123')
    )


def test_anchor_seed_changes_with_product():
    assert (
        _anchor_seed(42, 'product-123', 'seller-123')
        != _anchor_seed(42, 'product-456', 'seller-123')
    )


def test_anchor_seed_changes_with_seller():
    assert (
        _anchor_seed(42, 'product-123', 'seller-123')
        != _anchor_seed(42, 'product-123', 'seller-456')
    )


# ---------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------

def test_minor_to_major_converts_usd():
    assert _minor_to_major(2599, 'USD') == '25.99'


def test_minor_to_major_converts_inr_without_value_change():
    assert _minor_to_major(259999, 'INR') == '2599.99'


def test_minor_to_major_converts_zero_decimal_currency():
    assert _minor_to_major(2599, 'JPY') == '2599'


def test_minor_to_major_preserves_none():
    assert _minor_to_major(None, 'USD') is None


def test_coerce_minor_units_handles_major_and_minor_values():
    assert _coerce_minor_units('246548.84', 'INR') == 24654884
    assert _coerce_minor_units(2599, 'INR') == 2599


def test_minor_to_major_rejects_unknown_currency():
    with pytest.raises(ValueError):
        _minor_to_major(2599, 'XYZ')


# ---------------------------------------------------------
# Price generation
# ---------------------------------------------------------

def test_price_walk_has_requested_length():
    prices = _price_walk(2599, 30, random.Random(42))

    assert len(prices) == 30


def test_price_walk_ends_at_anchor_price():
    prices = _price_walk(2599, 30, random.Random(42))

    assert prices[-1] == 2599


def test_price_walk_prices_are_positive():
    prices = _price_walk(2599, 180, random.Random(42))

    assert all(price > 0 for price in prices)


def test_price_walk_is_reproducible():
    first = _price_walk(2599, 30, random.Random(42))
    second = _price_walk(2599, 30, random.Random(42))

    assert first == second


def test_price_walk_stays_within_cumulative_bounds():
    anchor_price = 2599

    prices = _price_walk(
        anchor_price,
        180,
        random.Random(42),
    )

    lower = max(
        1,
        round(
            anchor_price
            * (1 - MAX_CUMULATIVE_PRICE_CHANGE)
        ),
    )

    upper = round(
        anchor_price
        * (1 + MAX_CUMULATIVE_PRICE_CHANGE)
    )

    assert all(
        lower <= price <= upper
        for price in prices
    )

    assert prices[-1] == anchor_price


def test_price_walk_rejects_invalid_days():
    with pytest.raises(ValueError):
        _price_walk(2599, 0, random.Random(42))


def test_price_walk_rejects_nonpositive_anchor():
    with pytest.raises(ValueError):
        _price_walk(0, 30, random.Random(42))


# ---------------------------------------------------------
# Anchor extraction
# ---------------------------------------------------------

def test_extract_anchors_returns_latest_observation():
    older = make_observation(
        price=2000,
        observed_at='2026-10-19T12:00:00+00:00',
    )

    newer = make_observation(
        price=2599,
        observed_at='2026-10-20T12:00:00+00:00',
    )

    anchors = extract_anchors([older, newer])

    assert len(anchors) == 1
    assert anchors[0]['price'] == 2599


def test_extract_anchors_keeps_different_sellers():
    first = make_observation(seller_id='seller-A')
    second = make_observation(seller_id='seller-B')

    anchors = extract_anchors([first, second])

    assert len(anchors) == 2


def test_extract_anchors_keeps_different_regions():
    first = make_observation(region='US')
    second = make_observation(region='CA')

    anchors = extract_anchors([first, second])

    assert len(anchors) == 2


def test_extract_anchors_skips_missing_seller():
    observation = make_observation(seller_id=None)

    assert extract_anchors([observation]) == []


def test_extract_anchors_skips_missing_timestamp():
    observation = make_observation(observed_at=None)

    assert extract_anchors([observation]) == []


def test_extract_anchors_skips_missing_product_metadata():
    observation = make_observation()
    observation['metadata'] = {}

    assert extract_anchors([observation]) == []


def test_extract_anchors_accepts_inr():
    observation = make_observation()
    observation['currency'] = 'INR'
    observation['metadata'] = dict(observation['metadata'], currency='INR')

    anchors = extract_anchors([observation])
    assert len(anchors) == 1
    assert anchors[0]['currency'] == 'INR'


def test_extract_anchors_converts_naive_ist_to_utc():
    observation = make_observation(observed_at='2026-10-20T12:00:00')
    anchors = extract_anchors(
        [observation],
        naive_timezone='Asia/Kolkata',
    )

    assert anchors[0]['observed_at'] == '2026-10-20T06:30:00+00:00'


def test_parse_datetime_interprets_naive_ist_and_returns_utc():
    parsed = _parse_datetime(
        '2026-10-20T12:00:00',
        naive_timezone='Asia/Kolkata',
    )

    assert parsed == datetime(
        2026, 10, 20, 6, 30, tzinfo=timezone.utc
    )


# ---------------------------------------------------------
# Generated history
# ---------------------------------------------------------

def test_generate_history_creates_exact_number_of_rows():
    rows = generate_history(
        [make_anchor()],
        days=10,
        seed=42,
    )

    assert len(rows) == 10


def test_generate_history_does_not_duplicate_anchor_date():
    anchor = make_anchor()
    rows = generate_history([anchor], days=10)

    timestamps = [
        row['observed_at']
        for row in rows
    ]

    assert anchor['observed_at'] not in timestamps


def test_generate_history_ends_one_day_before_anchor():
    anchor = make_anchor()
    rows = generate_history([anchor], days=10)

    last = datetime.fromisoformat(rows[-1]['observed_at'])
    anchor_date = datetime.fromisoformat(anchor['observed_at'])

    assert last == anchor_date - timedelta(days=1)


def test_generate_history_starts_requested_days_before_anchor():
    anchor = make_anchor()
    rows = generate_history([anchor], days=10)

    first = datetime.fromisoformat(rows[0]['observed_at'])
    anchor_date = datetime.fromisoformat(anchor['observed_at'])

    assert first == anchor_date - timedelta(days=10)


def test_generate_history_is_chronological():
    rows = generate_history([make_anchor()], days=10)

    timestamps = [
        datetime.fromisoformat(row['observed_at'])
        for row in rows
    ]

    assert timestamps == sorted(timestamps)


def test_generate_history_preserves_seller_identity():
    rows = generate_history([make_anchor()], days=5)

    assert all(
        row['seller_id'] == 'seller-123'
        for row in rows
    )


def test_generate_history_preserves_product_identity():
    anchor = make_anchor()
    rows = generate_history([anchor], days=5)

    for row in rows:
        product, observation = normalize(
            row,
            'imported',
            HISTORY_RUN_ID,
        )

        assert product['id'] == anchor['product_id']
        assert observation['seller_id'] == anchor['seller_id']


def test_generate_history_preserves_region():
    rows = generate_history([make_anchor()], days=5)

    assert all(
        row['region'] == 'US'
        for row in rows
    )


def test_generate_history_preserves_product_url():
    anchor = make_anchor()
    rows = generate_history([anchor], days=5)

    assert all(
        row['url'] == anchor['product_url']
        for row in rows
    )


def test_generate_history_supports_inr_and_preserves_listing_identity():
    anchor = make_anchor()
    anchor.update({
        'currency': 'INR',
        'price': 259999,
        'original_price': 299999,
        'listing_id': 'stable-listing-1',
        'observation_id': 'source-observation-1',
        'source_record': {'coupon_discount': '500'},
        'source_record_ref': 'source-record-1',
    })
    rows = generate_history([anchor], days=3)

    assert [row['price'] for row in rows] == [
        _minor_to_major(price, 'INR')
        for price in _price_walk(
            259999,
            3,
            random.Random(_anchor_seed(
                DEFAULT_SEED,
                anchor['product_id'],
                anchor['seller_id'],
                'stable-listing-1',
            )),
        )
    ]
    assert all(row['currency'] == 'INR' for row in rows)
    assert all(row['listing_id'] == 'stable-listing-1' for row in rows)
    assert all(row['source_record'] == anchor['source_record'] for row in rows)
    assert all(row['source_observation_id'] == 'source-observation-1' for row in rows)
    assert len({row['observation_id'] for row in rows}) == len(rows)


def test_generate_history_keeps_original_price_and_discount_consistent():
    anchor = make_anchor()
    anchor['original_price'] = anchor['price'] + 1000
    rows = generate_history([anchor], days=5)

    for row in rows:
        selling = int(Decimal(row['price']) * 100)
        original = int(Decimal(row['original_price']) * 100)
        assert original >= selling
        expected = (
            Decimal(original - selling) * 100 / Decimal(original)
        ).quantize(Decimal('0.01'))
        assert Decimal(row['discount']) == expected


def test_generate_history_accepts_decimal_major_unit_original_prices():
    anchor = make_anchor()
    anchor['price'] = 2599
    anchor['original_price'] = '2999.99'
    anchor['reference_price'] = '2999.99'
    anchor['observation_id'] = 'source-observation-1'

    rows = generate_history([anchor], days=3)

    assert all(row['source_observation_id'] == 'source-observation-1' for row in rows)
    assert all(float(row['original_price']) > float(row['price']) for row in rows)


def test_iter_history_yields_rows_without_changing_wrapper():
    anchor = make_anchor()
    assert list(iter_history([anchor], days=2)) == generate_history(
        [anchor],
        days=2,
    )


def test_generated_product_url_survives_normalization():
    anchor = make_anchor()
    rows = generate_history([anchor], days=1)

    product, _ = normalize(
        rows[0],
        'imported',
        HISTORY_RUN_ID,
    )

    assert product['product_url'] == anchor['product_url']


def test_generate_history_marks_generated_records():
    rows = generate_history([make_anchor()], days=5)

    assert all(
        row['_generated'] is True
        for row in rows
    )

    assert all(
        row['source_id'] == HISTORY_SOURCE_ID
        for row in rows
    )


def test_generated_normalization_provenance():
    row = generate_history([make_anchor()], days=1)[0]

    _, observation = normalize(
        row,
        'imported',
        HISTORY_RUN_ID,
    )

    assert observation['source_type'] == 'generated_history'
    assert observation['synthetic'] is True
    assert observation['timestamp_quality'] == 'generated'
    assert '_generated' not in observation


def test_ordinary_imported_provenance_remains_unchanged():
    row = generate_history([make_anchor()], days=1)[0]
    row.pop('_generated')
    row['source_id'] = 'real-import-test'

    _, observation = normalize(
        row,
        'imported',
        'ordinary-import-test',
    )

    assert observation['source_type'] == 'imported'
    assert observation['synthetic'] is False
    assert observation['timestamp_quality'] == 'explicit'


def test_generate_history_is_reproducible():
    first = generate_history(
        [make_anchor()],
        days=30,
        seed=DEFAULT_SEED,
    )

    second = generate_history(
        [make_anchor()],
        days=30,
        seed=DEFAULT_SEED,
    )

    assert first == second


def test_generate_history_changes_with_seed():
    first = generate_history(
        [make_anchor()],
        days=30,
        seed=42,
    )

    second = generate_history(
        [make_anchor()],
        days=30,
        seed=99,
    )

    first_prices = [
        row['price']
        for row in first
    ]

    second_prices = [
        row['price']
        for row in second
    ]

    assert first_prices != second_prices


def test_generate_history_rejects_invalid_days():
    with pytest.raises(ValueError):
        generate_history([make_anchor()], days=0)


# ---------------------------------------------------------
# seed_history database-mode guard
# ---------------------------------------------------------

class _FakeDB:
    """Minimal stand-in for a PyMongo database object."""

    def __init__(self, name):
        self.name = name


def test_seed_history_rejects_demo_database():
    from app.services.history import seed_history

    with pytest.raises(ValueError, match='imported'):
        seed_history(_FakeDB('marketlens_demo'))


def test_seed_history_rejects_live_database():
    from app.services.history import seed_history

    with pytest.raises(ValueError, match='imported'):
        seed_history(_FakeDB('marketlens_live'))


def test_seed_history_rejects_unknown_database():
    from app.services.history import seed_history

    with pytest.raises(ValueError, match='imported'):
        seed_history(_FakeDB('some_other_db'))


def test_seed_history_rejects_zero_days():
    from app.services.history import seed_history

    with pytest.raises(ValueError, match='days'):
        seed_history(_FakeDB('marketlens_imported'), days=0)