"""
Tests for app.services.normalize — pure-Python, no MongoDB required.

Covers:
  1.  Currency-prefixed price parsing
  2.  Invalid price rejection
  3.  Seller ID extraction from seller URLs
  4.  Verified / name-derived / unknown identity classification
  5.  Case-insensitive name-derived identity normalisation
  6.  No collision between name-derived and verified IDs
  7.  Additional seller extraction from JSON strings
  8.  Additional seller extraction from Python lists
  9.  Duplicate offer handling
  10. Same seller, different conditions
  11. Same seller, different fulfillment details
  12. Seller rating separate from product rating
  13. Seller review count separate from product review count
  14. Derived seller count uses distinct identities
  15. Original observation timestamp preserved for expanded offers
  16. Invalid / missing timestamps
  17. Importing normalize does not instantiate a MongoDB client
  18. Coupon handling
  19. dry_run_import additional-seller processing
  20. reviews_count alias for review_count
  21. Categories field handling
  22. Buy Box seller cleared on expanded offers
  23. Seller-count consistency
  24. Buy Box seller classification
  25. JSON-encoded category strings
  26. Import preparation preserves original source records
  27. Import preparation maps Buy Box fields before seller expansion
  28. Additional seller prices are not overwritten by Buy Box mapping
"""

import importlib
import json
import sys
import os
import csv
import tempfile

import pytest

from app.services.normalize import (
    _normalise_name,
    expand_additional_sellers,
    money,
    normalize,
    parse_additional_sellers,
    parse_coupon,
    seller_id_from_url,
    seller_id_type,
    seller_identifier,
    timestamp,
)


# ---------------------------------------------------------------------------
# Minimal record builders
# ---------------------------------------------------------------------------

def _raw(
    asin='B000TEST01',
    marketplace='amazon.com',
    currency='USD',
    price='19.99',
    seller_id='ATVPDKIKX0DER',
    seller_name='Test Seller',
    seller_url=None,
    observed_at='2026-01-01T12:00:00+00:00',
    rating='4.5',
    review_count='1234',
    other_sellers_prices=None,
    **kwargs,
):
    r = dict(
        asin=asin,
        marketplace=marketplace,
        currency=currency,
        price=price,
        seller_id=seller_id,
        seller_name=seller_name,
        observed_at=observed_at,
        rating=rating,
        review_count=review_count,
    )
    if seller_url:
        r['seller_url'] = seller_url
    if other_sellers_prices is not None:
        r['other_sellers_prices'] = other_sellers_prices
    r.update(kwargs)
    return r


def _offer(
    seller_name=None,
    seller_url=None,
    price='15.99',
    condition='new',
    ships_from='Amazon',
    seller_rating=None,
    num_of_ratings=None,
):
    o = {
        'price': price,
        'condition': condition,
        'ships_from': ships_from,
    }
    if seller_name:
        o['seller_name'] = seller_name
    if seller_url:
        o['seller_url'] = seller_url
    if seller_rating is not None:
        o['seller_rating'] = seller_rating
    if num_of_ratings is not None:
        o['num_of_ratings'] = num_of_ratings
    return o


# ---------------------------------------------------------------------------
# 1. Currency-prefixed price parsing
# ---------------------------------------------------------------------------

def test_money_dollar_prefix():
    assert money('$19.99', 'USD') == 1999


def test_money_currency_code_prefix():
    assert money('USD 19.99', 'USD') == 1999


def test_money_inr_prefix():
    assert money('INR 1299.00', 'INR') == 129900


def test_money_comma_thousands():
    assert money('1,299.00', 'USD') == 129900


def test_money_plain_string():
    assert money('19.99', 'USD') == 1999


def test_money_jpy_no_minor():
    assert money('1500', 'JPY') == 1500


# ---------------------------------------------------------------------------
# 2. Invalid price rejection
# ---------------------------------------------------------------------------

def test_money_nan_raises():
    with pytest.raises(ValueError):
        money('NaN', 'USD')


def test_money_negative_raises():
    with pytest.raises(ValueError):
        money('-1', 'USD')


def test_money_text_raises():
    with pytest.raises(ValueError):
        money('not a price', 'USD')


def test_money_none_returns_none():
    assert money(None) is None


def test_money_empty_returns_none():
    assert money('', 'USD') is None


# ---------------------------------------------------------------------------
# 3. Seller ID extraction from seller URLs
# ---------------------------------------------------------------------------

def test_seller_id_from_url_standard():
    url = 'https://www.amazon.com/sp?seller=A1B2C3D4E5F6G7'
    assert seller_id_from_url(url) == 'A1B2C3D4E5F6G7'


def test_seller_id_from_url_with_extra_params():
    url = 'https://www.amazon.com/sp?foo=bar&seller=XSELLER1&baz=1'
    assert seller_id_from_url(url) == 'XSELLER1'


def test_seller_id_from_url_missing_param():
    assert seller_id_from_url(
        'https://www.amazon.com/sp?foo=bar'
    ) is None


def test_seller_id_from_url_none():
    assert seller_id_from_url(None) is None


def test_seller_id_from_url_empty():
    assert seller_id_from_url('') is None


# ---------------------------------------------------------------------------
# 4. Verified / name-derived / unknown identity classification
# ---------------------------------------------------------------------------

def test_seller_id_type_verified_from_field():
    assert seller_id_type(
        {'seller_id': 'ATVPDKIKX0DER'}
    ) == 'verified'


def test_seller_id_type_verified_from_url():
    r = {
        'seller_url':
        'https://www.amazon.com/sp?seller=A1B2C3'
    }
    assert seller_id_type(r) == 'verified'


def test_seller_id_type_name_derived():
    assert seller_id_type(
        {'seller_name': 'Acme Corp'}
    ) == 'name_derived'


def test_seller_id_type_unknown():
    assert seller_id_type({}) == 'unknown'


# ---------------------------------------------------------------------------
# 5. Case-insensitive name-derived identity normalisation
# ---------------------------------------------------------------------------

def test_normalise_name_casefold():
    assert _normalise_name('Acme Corp') == _normalise_name('ACME CORP')
    assert _normalise_name('  Acme Corp  ') == 'acme corp'


def test_seller_identifier_name_derived_casefolded():
    r1 = {'seller_name': 'Acme Corp'}
    r2 = {'seller_name': 'ACME CORP'}
    assert seller_identifier(r1) == seller_identifier(r2)


def test_seller_identifier_verified_not_casefolded():
    r = {'seller_id': 'ATVPDKIKX0DER'}
    assert seller_identifier(r) == 'ATVPDKIKX0DER'


# ---------------------------------------------------------------------------
# 6. No collision between name-derived and verified IDs
# ---------------------------------------------------------------------------

def test_no_collision_verified_vs_name_derived():
    verified = seller_identifier({'seller_id': 'Acme Corp'})
    name_der = seller_identifier({'seller_name': 'Acme Corp'})

    assert verified == 'Acme Corp'
    assert name_der == 'acme corp'
    assert verified != name_der


# ---------------------------------------------------------------------------
# 7. Additional seller extraction from JSON strings
# ---------------------------------------------------------------------------

def test_parse_additional_sellers_json_string():
    offers = [
        {'seller_name': 'SellerA', 'price': '12.99'}
    ]
    result = parse_additional_sellers(json.dumps(offers))

    assert len(result) == 1
    assert result[0]['seller_name'] == 'SellerA'


def test_parse_additional_sellers_invalid_json():
    assert parse_additional_sellers('not json') == []


def test_parse_additional_sellers_empty_string():
    assert parse_additional_sellers('') == []


# ---------------------------------------------------------------------------
# 8. Additional seller extraction from Python lists
# ---------------------------------------------------------------------------

def test_parse_additional_sellers_list():
    offers = [
        {'seller_name': 'B', 'price': '9.99'},
        {'seller_name': 'C', 'price': '8.00'},
    ]
    result = parse_additional_sellers(offers)

    assert len(result) == 2


def test_expand_additional_sellers_from_list():
    raw = _raw(
        other_sellers_prices=[
            _offer(seller_name='Extra Seller', price='14.99'),
        ]
    )

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1
    assert expanded[0]['seller_name'] == 'Extra Seller'


# ---------------------------------------------------------------------------
# 9. Duplicate offer handling
# ---------------------------------------------------------------------------

def test_expand_duplicate_offer_deduplicated():
    offer = _offer(
        seller_name='DupSeller',
        price='10.00',
        condition='new',
        ships_from='Amazon',
    )

    raw = _raw(other_sellers_prices=[offer, offer])
    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1


# ---------------------------------------------------------------------------
# 10. Same seller, different conditions
# ---------------------------------------------------------------------------

def test_expand_same_seller_different_conditions():
    raw = _raw(
        other_sellers_prices=[
            _offer(
                seller_name='MultiSeller',
                price='10.00',
                condition='new',
            ),
            _offer(
                seller_name='MultiSeller',
                price='7.00',
                condition='used',
            ),
        ]
    )

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 2
    assert {e['condition'] for e in expanded} == {'new', 'used'}


# ---------------------------------------------------------------------------
# 11. Same seller, different fulfillment details
# ---------------------------------------------------------------------------

def test_expand_same_seller_different_ships_from():
    raw = _raw(
        other_sellers_prices=[
            _offer(
                seller_name='FulfillSeller',
                price='10.00',
                ships_from='Amazon',
            ),
            _offer(
                seller_name='FulfillSeller',
                price='10.00',
                ships_from='Seller',
            ),
        ]
    )

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 2


# ---------------------------------------------------------------------------
# 12. Seller rating separate from product rating
# ---------------------------------------------------------------------------

def test_product_rating_not_overwritten_by_seller_rating():
    raw = _raw(
        rating='4.7',
        review_count='500',
        other_sellers_prices=[
            _offer(
                seller_name='RatingSeller',
                price='12.00',
                seller_rating='98%',
                num_of_ratings='200',
            ),
        ],
    )

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1
    exp = expanded[0]

    assert exp.get('rating') == '4.7'
    assert exp['_offer_details']['seller_rating'] == '98%'


# ---------------------------------------------------------------------------
# 13. Seller review count separate from product review count
# ---------------------------------------------------------------------------

def test_product_review_count_not_overwritten():
    raw = _raw(
        rating='4.2',
        review_count='999',
        other_sellers_prices=[
            _offer(
                seller_name='ReviewSeller',
                price='11.00',
                num_of_ratings='50',
            ),
        ],
    )

    expanded = expand_additional_sellers(raw)
    exp = expanded[0]

    assert exp.get('review_count') == '999'
    assert exp['_offer_details']['seller_review_count'] == '50'


# ---------------------------------------------------------------------------
# 14. Derived seller count uses distinct identities
# ---------------------------------------------------------------------------

def test_derived_seller_count_distinct_not_offer_count():
    raw = _raw(
        other_sellers_prices=[
            _offer(
                seller_name='OneSeller',
                price='10.00',
                condition='new',
            ),
            _offer(
                seller_name='OneSeller',
                price='8.00',
                condition='used',
            ),
        ]
    )

    expanded = expand_additional_sellers(raw)

    for exp in expanded:
        assert exp['_derived_seller_count'] == 2


def test_derived_seller_count_name_normalisation():
    raw = _raw(
        other_sellers_prices=[
            _offer(seller_name='OneSeller', price='10.00'),
            _offer(seller_name='ONESELLER', price='9.00'),
        ]
    )

    expanded = expand_additional_sellers(raw)

    for exp in expanded:
        assert exp['_derived_seller_count'] == 2


# ---------------------------------------------------------------------------
# 15. Original observation timestamp preserved for expanded offers
# ---------------------------------------------------------------------------

def test_expanded_offers_preserve_original_timestamp():
    ts = '2026-06-15T08:30:00+00:00'

    raw = _raw(
        observed_at=ts,
        other_sellers_prices=[
            _offer(seller_name='TsSeller', price='20.00'),
        ],
    )

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1
    assert expanded[0].get('observed_at') == ts


# ---------------------------------------------------------------------------
# 16. Invalid / missing timestamps
# ---------------------------------------------------------------------------

def test_timestamp_none_returns_none():
    assert timestamp(None) is None


def test_timestamp_empty_returns_none():
    assert timestamp('') is None


def test_timestamp_no_timezone_raises():
    with pytest.raises(ValueError, match='timezone'):
        timestamp('2026-01-01T12:00:00')


def test_timestamp_utc_z_accepted():
    result = timestamp('2026-01-01T12:00:00Z')

    assert result is not None
    assert '+00:00' in result


def test_timestamp_offset_normalised_to_utc():
    result = timestamp('2026-01-01T17:30:00+05:30')

    assert result == '2026-01-01T12:00:00+00:00'


# ---------------------------------------------------------------------------
# 17. Importing normalize does not instantiate a MongoDB client
# ---------------------------------------------------------------------------

def test_import_normalize_no_mongodb_client():
    import app.services.normalize as norm_module
    import inspect

    try:
        from pymongo import MongoClient

        for name, obj in vars(norm_module).items():
            assert not isinstance(obj, MongoClient), (
                'normalize.py must not hold a MongoClient instance '
                f'(found {name!r})'
            )
    except ImportError:
        pass

    source = inspect.getsource(norm_module)

    assert 'from app.db import' not in source
    assert 'import app.db' not in source


# ---------------------------------------------------------------------------
# 18. Coupon handling
# ---------------------------------------------------------------------------

def test_coupon_percentage_text_not_parsed_as_money():
    val, desc = parse_coupon('Apply 10% coupon', 'USD')

    assert val is None
    assert desc == 'Apply 10% coupon'


def test_coupon_save_percent_not_parsed_as_money():
    val, desc = parse_coupon('Save 6%, Save 10%', 'USD')

    assert val is None
    assert desc == 'Save 6%, Save 10%'


def test_coupon_fixed_amount_description_not_parsed():
    val, desc = parse_coupon('Apply $9 coupon', 'USD')

    assert val is None
    assert desc == 'Apply $9 coupon'


def test_coupon_up_to_percent_not_parsed():
    val, desc = parse_coupon('Up to 15% off', 'USD')

    assert val is None
    assert desc == 'Up to 15% off'


def test_coupon_generic_savings_text_not_parsed():
    val, desc = parse_coupon('Savings', 'USD')

    assert val is None
    assert desc == 'Savings'


def test_coupon_numeric_string_parsed_correctly():
    val, desc = parse_coupon('9.00', 'USD')

    assert val == 900
    assert desc is None


def test_coupon_dollar_prefixed_numeric_parsed():
    val, desc = parse_coupon('$9.00', 'USD')

    assert val == 900
    assert desc is None


def test_coupon_none_returns_none_none():
    assert parse_coupon(None, 'USD') == (None, None)


def test_coupon_empty_returns_none_none():
    assert parse_coupon('', 'USD') == (None, None)


def test_normalize_percentage_coupon_accepted():
    raw = _raw(coupon='Apply 10% coupon')

    _, observation = normalize(raw, 'imported', 'run1')

    assert observation['coupon'] is None
    assert observation['coupon_description'] == 'Apply 10% coupon'


def test_normalize_numeric_coupon_preserved():
    raw = _raw(
        coupon='5.00',
        coupon_conditions='with Prime',
    )

    _, observation = normalize(raw, 'imported', 'run1')

    assert observation['coupon'] == 500
    assert observation['coupon_description'] is None
    assert observation['conditional_coupon_price'] == (
        money('19.99', 'USD') - 500
    )


def test_normalize_explicit_coupon_description_preferred():
    raw = _raw(
        coupon='Apply 10% coupon',
        coupon_description='10% off with code',
    )

    _, observation = normalize(raw, 'imported', 'run1')

    assert observation['coupon_description'] == '10% off with code'


# ---------------------------------------------------------------------------
# 19. dry_run_import additional-seller processing
# ---------------------------------------------------------------------------

_backend_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..')
)

if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from scripts.dry_run_import import validate as _dry_validate


def _write_csv(rows, path):
    if not rows:
        return

    fields = list(rows[0].keys())

    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _base_row(**kwargs):
    row = {
        'asin': 'B000TEST99',
        'marketplace': 'amazon.com',
        'currency': 'USD',
        'final_price': '29.99',
        'seller_id': 'SELLER001',
        'seller_name': 'Main Seller',
        'timestamp': '2026-01-01T12:00:00+00:00',
        'other_sellers_prices': '',
    }

    row.update(kwargs)
    return row


def test_dry_run_valid_buybox_with_percentage_coupon_accepted():
    row = _base_row(coupon='Apply 10% coupon')

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline='',
    ) as f:
        path = f.name

    try:
        _write_csv([row], path)
        result = _dry_validate(path)

        assert result['accepted_product_observations'] == 1
        assert result['rejected_count'] == 0
    finally:
        os.unlink(path)


def test_dry_run_no_buybox_price_additional_sellers_retained():
    offers = json.dumps([
        {
            'seller_name': 'Extra Seller',
            'price': '24.99',
            'condition': 'new',
            'ships_from': 'Amazon',
        }
    ])

    row = _base_row(
        final_price='',
        other_sellers_prices=offers,
    )

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline='',
    ) as f:
        path = f.name

    try:
        _write_csv([row], path)
        result = _dry_validate(path)

        assert result['accepted_additional_seller_observations'] >= 1
    finally:
        os.unlink(path)


def test_dry_run_no_buybox_price_buybox_row_rejected():
    offers = json.dumps([
        {
            'seller_name': 'Extra Seller',
            'price': '24.99',
            'condition': 'new',
            'ships_from': 'Amazon',
        }
    ])

    row = _base_row(
        final_price='',
        other_sellers_prices=offers,
    )

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline='',
    ) as f:
        path = f.name

    try:
        _write_csv([row], path)
        result = _dry_validate(path)

        assert result['accepted_product_observations'] == 0
        assert result['rejected_count'] >= 1

        reasons = [
            record['reason']
            for record in result['rejected_records']
        ]

        assert any(
            'additional seller' not in reason
            for reason in reasons
        )
    finally:
        os.unlink(path)


def test_dry_run_invalid_additional_seller_does_not_block_valid():
    offers = json.dumps([
        {
            'seller_name': 'Bad Seller',
            'price': '',
            'condition': 'new',
        },
        {
            'seller_name': 'Good Seller',
            'price': '19.99',
            'condition': 'new',
            'ships_from': 'Amazon',
        },
    ])

    row = _base_row(other_sellers_prices=offers)

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline='',
    ) as f:
        path = f.name

    try:
        _write_csv([row], path)
        result = _dry_validate(path)

        assert result['accepted_additional_seller_observations'] >= 1
    finally:
        os.unlink(path)


def test_dry_run_seller_count_without_buybox():
    offers = json.dumps([
        {
            'seller_name': 'Seller A',
            'price': '10.00',
            'condition': 'new',
            'ships_from': 'Amazon',
        },
        {
            'seller_name': 'Seller B',
            'price': '11.00',
            'condition': 'new',
            'ships_from': 'Seller',
        },
    ])

    row = _base_row(
        final_price='',
        other_sellers_prices=offers,
    )

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline='',
    ) as f:
        path = f.name

    try:
        _write_csv([row], path)
        result = _dry_validate(path)

        assert result['seller_count_distribution']
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# 20. reviews_count alias for review_count
# ---------------------------------------------------------------------------

def test_reviews_count_alias_used_when_review_count_absent():
    raw = _raw()
    raw.pop('review_count', None)
    raw['reviews_count'] = '77'

    _, observation = normalize(raw, 'imported', 'run1')

    assert observation['review_count'] == '77'


def test_review_count_takes_precedence_over_reviews_count():
    raw = _raw(review_count='100')
    raw['reviews_count'] = '999'

    _, observation = normalize(raw, 'imported', 'run1')

    assert observation['review_count'] == '100'


# ---------------------------------------------------------------------------
# 21. Categories field — string or list, empty values safe
# ---------------------------------------------------------------------------

def test_categories_list_uses_first_element():
    raw = _raw()
    raw['categories'] = ['Electronics', 'Computers']

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Electronics'


def test_categories_string_used_directly():
    raw = _raw()
    raw['categories'] = 'Electronics'

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Electronics'


def test_categories_empty_list_falls_back_to_uncategorized():
    raw = _raw()
    raw['categories'] = []

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Uncategorized'


def test_categories_empty_string_falls_back_to_uncategorized():
    raw = _raw()
    raw['categories'] = ''

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Uncategorized'


# ---------------------------------------------------------------------------
# 22. Buy Box seller cleared on expanded additional-seller records
# ---------------------------------------------------------------------------

def test_expanded_seller_buybox_seller_cleared():
    raw = _raw(
        seller_name='Main Seller',
        other_sellers_prices=[
            _offer(seller_name='Extra Seller', price='14.00'),
        ],
    )

    raw['buybox_seller'] = 'Main Seller'

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1
    assert expanded[0].get('buybox_seller') is None
    assert expanded[0].get('buy_box') is False


def test_expanded_seller_not_promoted_to_buybox_via_inherited_field():
    raw = _raw(
        seller_name='Main Seller',
        other_sellers_prices=[
            _offer(seller_name='Extra Seller', price='14.00'),
        ],
    )

    raw['buybox_seller'] = 'Extra Seller'

    expanded = expand_additional_sellers(raw)

    assert len(expanded) == 1

    _, observation = normalize(
        expanded[0],
        'imported',
        'run1',
    )

    assert observation['buy_box'] is False


# ---------------------------------------------------------------------------
# 23. Seller-count consistency
# ---------------------------------------------------------------------------

def test_seller_count_name_variants_counted_once():
    raw = _raw(
        seller_id='MAIN001',
        seller_name='Main Seller',
        other_sellers_prices=[
            _offer(
                seller_name='Acme Corp',
                price='10.00',
                condition='new',
            ),
            _offer(
                seller_name='ACME CORP',
                price='11.00',
                condition='new',
            ),
            _offer(
                seller_name='acme corp',
                price='12.00',
                condition='new',
            ),
        ],
    )

    expanded = expand_additional_sellers(raw)

    for exp in expanded:
        assert exp['_derived_seller_count'] == 2


def test_seller_count_verified_ids_remain_distinct():
    raw = _raw(
        seller_id='MAIN001',
        seller_name='Main Seller',
        other_sellers_prices=[
            _offer(
                seller_url='https://www.amazon.com/sp?seller=SELLER_A',
                price='10.00',
            ),
            _offer(
                seller_url='https://www.amazon.com/sp?seller=SELLER_B',
                price='11.00',
            ),
        ],
    )

    expanded = expand_additional_sellers(raw)

    for exp in expanded:
        assert exp['_derived_seller_count'] == 3

    seller_ids = {exp['seller_id'] for exp in expanded}

    assert 'SELLER_A' in seller_ids
    assert 'SELLER_B' in seller_ids


# ---------------------------------------------------------------------------
# 24. Buy Box seller classification
# ---------------------------------------------------------------------------

def test_buybox_seller_with_seller_id_classified_verified():
    raw = _raw(
        seller_id='ATVPDKIKX0DER',
        seller_name='Amazon.com',
    )

    assert seller_id_type(raw) == 'verified'


def test_buybox_seller_with_seller_url_classified_verified():
    raw = _raw(
        seller_id=None,
        seller_url='https://www.amazon.com/sp?seller=A1B2C3D4E5F6G7',
    )

    raw.pop('seller_id', None)

    assert seller_id_type(raw) == 'verified'


def test_buybox_seller_name_only_classified_name_derived():
    raw = {
        'asin': 'B000TEST01',
        'marketplace': 'amazon.com',
        'currency': 'USD',
        'price': '19.99',
        'seller_name': 'Some Seller',
        'observed_at': '2026-01-01T12:00:00+00:00',
    }

    assert seller_id_type(raw) == 'name_derived'


def test_buybox_seller_id_type_not_overwritten_with_unknown():
    candidate = {
        'asin': 'B000TEST01',
        'marketplace': 'amazon.com',
        'currency': 'USD',
        'price': '19.99',
        'seller_name': 'Name Only Seller',
        'observed_at': '2026-01-01T12:00:00+00:00',
    }

    id_type = seller_id_type(candidate)

    assert id_type == 'name_derived'


# ---------------------------------------------------------------------------
# 25. Categories field — JSON-encoded list string
# ---------------------------------------------------------------------------

def test_categories_json_encoded_string_uses_first_element():
    raw = _raw()
    raw['categories'] = (
        '["Electronics","Computers & Accessories",'
        '"Laptop Accessories"]'
    )

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Electronics'


def test_categories_amazon_example_gives_electronics():
    raw = _raw()
    raw['categories'] = (
        '["Electronics","Computers & Accessories",'
        '"Laptop Accessories","Cooling Pads & External Fans",'
        '"Cooling Pads"]'
    )

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Electronics'


def test_categories_python_list_still_uses_first_element():
    raw = _raw()
    raw['categories'] = [
        'Office Products',
        'Office Supplies',
    ]

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Office Products'


def test_categories_plain_string_preserved():
    raw = _raw()
    raw['categories'] = 'Video Games'

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Video Games'


def test_categories_invalid_json_string_used_as_plain_string():
    raw = _raw()
    raw['categories'] = '[not valid json'

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == '[not valid json'


def test_categories_json_empty_array_falls_back_to_uncategorized():
    raw = _raw()
    raw['categories'] = '[]'

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Uncategorized'


def test_categories_missing_falls_back_to_uncategorized():
    raw = _raw()
    raw.pop('categories', None)
    raw.pop('category', None)

    product, _ = normalize(raw, 'imported', 'run1')

    assert product['category'] == 'Uncategorized'


# ---------------------------------------------------------------------------
# 26. Import preparation preserves original source records
# ---------------------------------------------------------------------------

def _prepare_import_row(original):
    """
    Prepare a CSV row using the same field aliases as the import workflow.

    Keep the original record in _original so raw provenance is unchanged.
    """
    prepared = dict(original)
    prepared['_original'] = original

    if not prepared.get('marketplace'):
        prepared['marketplace'] = 'amazon.com'

    if not prepared.get('price') and prepared.get('final_price'):
        prepared['price'] = prepared['final_price']

    if not prepared.get('observed_at') and prepared.get('timestamp'):
        prepared['observed_at'] = prepared['timestamp']

    if (
        not prepared.get('reference_price')
        and prepared.get('initial_price')
    ):
        prepared['reference_price'] = prepared['initial_price']

    return prepared


def test_import_preparation_preserves_original_record():
    original = {
        'asin': 'B000IMPORT01',
        'final_price': '29.99',
        'timestamp': '2026-01-01T12:00:00+00:00',
        'initial_price': '49.99',
    }

    prepared = _prepare_import_row(original)

    assert prepared['_original'] is original
    assert prepared['price'] == '29.99'
    assert prepared['observed_at'] == original['timestamp']
    assert prepared['reference_price'] == '49.99'

    # The source dictionary itself must not be modified.
    assert 'price' not in original
    assert 'observed_at' not in original
    assert 'reference_price' not in original


def test_import_preparation_does_not_overwrite_existing_normalized_fields():
    original = {
        'asin': 'B000IMPORT02',
        'marketplace': 'amazon.com',
        'price': '25.00',
        'final_price': '29.99',
        'observed_at': '2026-01-01T12:00:00+00:00',
        'timestamp': '2026-01-02T12:00:00+00:00',
        'reference_price': '40.00',
        'initial_price': '49.99',
    }

    prepared = _prepare_import_row(original)

    assert prepared['price'] == '25.00'
    assert prepared['observed_at'] == '2026-01-01T12:00:00+00:00'
    assert prepared['reference_price'] == '40.00'


# ---------------------------------------------------------------------------
# 27. Import preparation maps Buy Box fields before seller expansion
# ---------------------------------------------------------------------------

def test_import_preparation_allows_buybox_normalization():
    original = {
        'asin': 'B000IMPORT03',
        'marketplace': 'amazon.com',
        'currency': 'USD',
        'final_price': '29.99',
        'timestamp': '2026-01-01T12:00:00+00:00',
        'initial_price': '49.99',
        'seller_id': 'MAIN001',
        'seller_name': 'Main Seller',
    }

    prepared = _prepare_import_row(original)

    product, observation = normalize(
        prepared,
        'imported',
        'run-import-test',
    )

    assert product['external_id'] == 'B000IMPORT03'
    assert observation['price'] == 2999
    assert observation['reference_price'] == 4999
    assert observation['observed_at'] == (
        '2026-01-01T12:00:00+00:00'
    )


# ---------------------------------------------------------------------------
# 28. Additional seller prices must not be overwritten by Buy Box mapping
# ---------------------------------------------------------------------------

def test_import_preparation_preserves_individual_additional_seller_prices():
    original = {
        'asin': 'B000IMPORT04',
        'marketplace': 'amazon.com',
        'currency': 'USD',
        'final_price': '29.99',
        'timestamp': '2026-01-01T12:00:00+00:00',
        'initial_price': '49.99',
        'seller_id': 'MAIN001',
        'seller_name': 'Main Seller',
        'other_sellers_prices': json.dumps([
            {
                'seller_name': 'Extra Seller',
                'price': '24.50',
                'condition': 'new',
                'ships_from': 'Amazon',
            }
        ]),
    }

    prepared = _prepare_import_row(original)

    expanded = expand_additional_sellers(prepared)

    assert len(expanded) == 1

    additional = expanded[0]

    # The main Buy Box price and additional seller price must remain distinct.
    assert prepared['price'] == '29.99'
    assert additional['price'] == '24.50'

    _, main_observation = normalize(
        prepared,
        'imported',
        'run-import-test',
    )

    _, additional_observation = normalize(
        additional,
        'imported',
        'run-import-test',
    )

    assert main_observation['price'] == 2999
    assert additional_observation['price'] == 2450

    # Both records retain the original capture time.
    assert main_observation['observed_at'] == (
        '2026-01-01T12:00:00+00:00'
    )
    assert additional_observation['observed_at'] == (
        '2026-01-01T12:00:00+00:00'
    )

    # Expanded record must retain the original raw source for provenance.
    assert additional['_original'] is original

def test_same_product_different_conditions_share_product_id():
    new_raw = _raw(condition='New')
    used_raw = _raw(condition='Used')

    new_product, new_observation = normalize(
        new_raw, 'imported', 'run1'
    )
    used_product, used_observation = normalize(
        used_raw, 'imported', 'run1'
    )

    assert new_product['id'] == used_product['id']
    assert new_observation['product_id'] == used_observation['product_id']
    assert new_product['condition'] != used_product['condition']
