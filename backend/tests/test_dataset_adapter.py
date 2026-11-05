import csv
import io
from copy import deepcopy

import pytest

from app.services.dataset_adapter import (
    AMAZON_SOURCE_FILES,
    CANONICAL_COLUMNS,
    CANONICAL_SOURCE_FILE,
    ENRICHED_SOURCE_FILES,
    ENRICHMENT_COLUMNS,
    AdapterContext,
    NestedOfferPolicy,
    adapt_csv_file,
    adapt_records,
    validate_source_headers,
)


AMAZON_ROW = {
    "asin": "B000TEST01",
    "title": "Test product",
    "seller_id": "SELLER001",
    "seller_name": "Test seller",
    "seller_url": "https://www.amazon.com/sp?seller=SELLER001",
    "domain": "https://www.amazon.com/",
    "url": "https://www.amazon.com/dp/B000TEST01",
    "currency": "USD",
    "final_price": "19.99",
    "initial_price": "25.00",
    "discount": "-20%",
    "availability": "In Stock",
    "delivery": '["Delivery tomorrow", "Prime delivery"]',
    "rating": "4.5",
    "categories": '["Electronics", "Cameras"]',
    "timestamp": "2026-10-15T08:57:56.450Z",
    "variant_condition": "New",
    "other_sellers_prices": "",
}


def _canonical_row(**changes):
    row = {
        "product_id": "MCL0001",
        "product_name": "Synthetic jacket",
        "seller_id": "SELLER001",
        "seller_name": "Seller",
        "timestamp": "2026-11-04T10:30:00",
        "price": "1380",
        "original_price": "1600",
        "currency": "INR",
        "marketplace": "Amazon",
        "product_url": "",
        "availability": "In Stock",
        "shipping_cost": "60",
        "delivery_information": "Delivery in 2-4 days",
        "discount": "13.75",
        "seller_rating": "4.3",
        "product_rating": "4.5",
        "category": "Jackets",
        "source": "Generated for project demonstration",
        "source_type": "Synthetic",
        "listing_id": "AMA-CLO-LIST-000001",
        "coupon_discount": "0",
        "payment_offer": "No payment offer",
        "urgency_message": "Synthetic urgency example",
        "stock_quantity": "8.0",
        "product_condition": "New",
        "seller_location": "Bengaluru, Karnataka",
        "last_updated": "2026-11-04T10:30:00",
    }
    row.update(changes)
    return row


def _enriched_row(**changes):
    row = _canonical_row(
        timestamp="2026-11-04T10:00:00",
        last_updated="2026-11-04T10:00:00",
        product_rating="",
        coupon_discount="150",
    )
    row.update({
        "image_url": "https://example.test/product.png",
        "review": "Synthetic individual review text.",
        "rating": "3",
        "review_source": "Synthetic (generated sample review and rating)",
    })
    row.update(changes)
    return row


@pytest.mark.parametrize("filename", sorted(AMAZON_SOURCE_FILES))
def test_each_amazon_dataset_uses_provider_adapter(filename):
    result = adapt_records([AMAZON_ROW], filename)

    assert len(result.records) == 1
    adapted = result.records[0]
    assert tuple(adapted.canonical) == CANONICAL_COLUMNS
    assert adapted.canonical["product_id"] == "B000TEST01"
    assert adapted.canonical["product_name"] == "Test product"
    assert adapted.canonical["marketplace"] == "amazon.com"
    assert adapted.canonical["category"] == "Cameras"
    assert adapted.canonical["delivery_information"] == "Delivery tomorrow; Prime delivery"
    assert adapted.provenance["source_filename"] == filename
    assert adapted.provenance["source_type"] == "scraped"
    assert adapted.provenance["synthetic"] is False
    assert result.quarantined == ()


def test_canonical_clothing_source_preserves_cross_market_product_and_provenance():
    context = AdapterContext()
    rows = [
        _canonical_row(),
        _canonical_row(
            marketplace="Flipkart",
            seller_id="SELLER002",
            seller_name="Another seller",
            listing_id="FLI-CLO-LIST-000002",
            timestamp="2026-11-04T10:35:00",
            last_updated="2026-11-04T10:35:00",
        ),
    ]

    result = adapt_records(
        rows,
        CANONICAL_SOURCE_FILE,
        naive_timezone="Asia/Kolkata",
        context=context,
    )

    assert len(result.records) == 2
    assert result.records[0].canonical["product_id"] == "MCL0001"
    assert result.records[1].canonical["product_id"] == "MCL0001"
    assert result.records[0].product_identity == result.records[1].product_identity
    assert result.records[0].canonical["timestamp"] == "2026-11-04T05:00:00+00:00"
    assert result.records[0].canonical["price"] == "1380"
    assert result.records[0].canonical["shipping_cost"] == "60"
    assert result.records[0].canonical["discount"] == "13.75"
    assert result.records[0].provenance["synthetic"] is True
    assert result.records[0].canonical["source_type"] == "Synthetic"
    assert result.records[0].listing_identity != result.records[1].listing_identity


def test_provider_listing_identity_is_separate_and_stable_across_price_and_time():
    first = adapt_records([AMAZON_ROW], "charger_final_data.csv").records[0]
    later_row = dict(
        AMAZON_ROW,
        final_price="17.99",
        timestamp="2026-10-16T08:57:56.450Z",
    )
    later = adapt_records([later_row], "charger_final_data.csv").records[0]

    assert first.product_identity == ("amazon.com", "B000TEST01")
    assert first.listing_identity == later.listing_identity
    assert first.canonical["listing_id"] == later.canonical["listing_id"]
    assert first.observation_identity != later.observation_identity


@pytest.mark.parametrize(
    ("currency", "price"),
    [
        ("INR", "1299.25"),
        ("USD", "19.99"),
        ("GBP", "19.99"),
        ("EUR", "19.99"),
        ("JPY", "1500"),
        ("CAD", "19.99"),
        ("AUD", "19.99"),
    ],
)
def test_currency_and_major_unit_amounts_are_preserved(currency, price):
    row = dict(AMAZON_ROW, currency=currency, final_price=price)
    adapted = adapt_records([row], "amazon_products_32.csv").records[0]

    assert adapted.canonical["currency"] == currency
    assert adapted.canonical["price"] == price


def test_negative_provider_discount_is_calculated_from_validated_prices():
    adapted = adapt_records(
        [dict(AMAZON_ROW, initial_price="10.00", final_price="8.00", discount="-20%")],
        "charger_snapshot.csv",
    ).records[0]

    assert adapted.canonical["discount"] == "20.00"
    assert adapted.provenance["source_discount"] == "-20%"
    assert "provider_discount_disagrees_with_price_calculation" not in adapted.warnings


def test_negative_provider_discount_mismatch_warns_but_uses_price_formula():
    adapted = adapt_records(
        [dict(AMAZON_ROW, initial_price="10.00", final_price="8.00", discount="-25%")],
        "charger_snapshot.csv",
    ).records[0]

    assert adapted.canonical["discount"] == "20.00"
    assert "provider_discount_disagrees_with_price_calculation" in adapted.warnings


def test_original_price_below_sale_does_not_create_discount():
    adapted = adapt_records(
        [dict(AMAZON_ROW, initial_price="8.00", final_price="10.00", discount="-20%")],
        "charger_snapshot.csv",
    ).records[0]

    assert adapted.canonical["discount"] is None
    assert "original_price_below_selling_price" in adapted.warnings


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"asin": ""}, "missing required product identity"),
        ({"title": ""}, "missing required product name"),
        ({"seller_id": "", "seller_url": ""}, "missing required seller identity"),
        ({"domain": "", "url": ""}, "missing required marketplace"),
        ({"currency": ""}, "unsupported or missing currency"),
        ({"final_price": ""}, "missing or nonpositive selling price"),
        ({"timestamp": ""}, "missing observation timestamp"),
    ],
)
def test_missing_required_identity_or_value_is_quarantined(changes, reason):
    result = adapt_records(
        [dict(AMAZON_ROW, **changes)],
        "amazon_products_32.csv",
    )

    assert result.records == ()
    assert len(result.quarantined) == 1
    assert reason in result.quarantined[0].reason


def test_missing_optional_provider_fields_remain_null():
    row = dict(
        AMAZON_ROW,
        seller_name="",
        availability="",
        delivery="",
        rating="not-a-rating",
        categories="not-json",
        category_tree="not-json",
        other_sellers_prices="",
    )
    adapted = adapt_records([row], "sd_mu9exytq1cwty0ftme.csv").records[0]

    assert adapted.canonical["seller_name"] is None
    assert adapted.canonical["availability"] is None
    assert adapted.canonical["shipping_cost"] is None
    assert adapted.canonical["seller_rating"] is None
    assert adapted.canonical["product_rating"] is None
    assert adapted.canonical["category"] is None
    assert "invalid_optional_rating" in adapted.warnings
    assert "invalid_optional_category" in adapted.warnings


def test_naive_timestamp_is_quarantined_without_timezone():
    result = adapt_records([_canonical_row()], CANONICAL_SOURCE_FILE)

    assert result.records == ()
    assert "explicitly supplied timezone" in result.quarantined[0].reason


def test_ambiguous_dst_timestamp_is_quarantined():
    row = _canonical_row(timestamp="2026-11-01T01:30:00")
    result = adapt_records(
        [row],
        CANONICAL_SOURCE_FILE,
        naive_timezone="America/New_York",
    )

    assert result.records == ()
    assert "ambiguous local timestamp" in result.quarantined[0].reason


def test_nested_offers_are_preserved_without_expansion_by_default():
    source = deepcopy(AMAZON_ROW)
    source["other_sellers_prices"] = (
        '[{"seller_name":"Nested seller","seller_url":'
        '"https://amazon.com/sp?seller=NESTED1","price":12.5,"condition":"New"}]'
    )
    result = adapt_records([source], "charger_snapshot.csv")

    assert len(result.records) == 1
    assert result.records[0].canonical["seller_id"] == "SELLER001"
    assert result.records[0].provenance["nested_offer_count"] == 1
    assert result.records[0].provenance["nested_offer_policy"] == "preserve_only"
    assert result.records[0].source_record["other_sellers_prices"] == source["other_sellers_prices"]
    assert result.quarantined == ()


def test_nested_expansion_is_explicit_and_uses_offer_identity_and_price():
    source = dict(AMAZON_ROW)
    source["other_sellers_prices"] = (
        '[{"seller_name":"Nested seller","seller_url":'
        '"https://amazon.com/sp?seller=NESTED1","price":12.5,'
        '"condition":"Used - Good","seller_rating":4.1,"delivery":"Ships tomorrow"}]'
    )
    result = adapt_records(
        [source],
        "charger_snapshot.csv",
        nested_offer_policy=NestedOfferPolicy.EXPAND,
    )

    assert len(result.records) == 2
    nested = result.records[1]
    assert nested.canonical["seller_id"] == "NESTED1"
    assert nested.canonical["seller_name"] == "Nested seller"
    assert nested.canonical["price"] == "12.5"
    assert nested.canonical["product_condition"] == "Used - Good"
    assert nested.canonical["seller_rating"] == "4.1"
    assert nested.provenance["source_offer_index"] == 1
    assert nested.listing_identity != result.records[0].listing_identity
    assert nested.observation_identity != result.records[0].observation_identity
    assert result.quarantined == ()


def test_nested_duplicate_listing_identity_is_quarantined_not_merged():
    source = dict(AMAZON_ROW)
    source["other_sellers_prices"] = (
        '[{"seller_name":"Nested seller","seller_url":"https://amazon.com/sp?seller=NESTED1",'
        '"price":12.5,"condition":"New"},'
        '{"seller_name":"Nested seller","seller_url":"https://amazon.com/sp?seller=NESTED1",'
        '"price":13.0,"condition":"New"}]'
    )
    result = adapt_records(
        [source],
        "charger_snapshot.csv",
        nested_offer_policy=NestedOfferPolicy.EXPAND,
    )

    assert len(result.records) == 1
    assert [item.source_offer_index for item in result.quarantined] == [1, 2]
    assert all(item.reason == "ambiguous_nested_listing_identity" for item in result.quarantined)


def test_nested_offer_missing_seller_identity_is_quarantined():
    source = dict(AMAZON_ROW)
    source["other_sellers_prices"] = (
        '[{"seller_name":"","seller_url":"","price":12.5,"condition":"New"}]'
    )
    result = adapt_records(
        [source],
        "charger_snapshot.csv",
        nested_offer_policy=NestedOfferPolicy.EXPAND,
    )

    assert len(result.records) == 1
    assert len(result.quarantined) == 1
    assert "missing required seller identity" in result.quarantined[0].reason


def test_reject_nested_policy_quarantines_row_with_offers():
    row = dict(
        AMAZON_ROW,
        other_sellers_prices='[{"seller_name":"Nested","price":2}]',
    )
    result = adapt_records(
        [row],
        "amazon_products_32.csv",
        nested_offer_policy=NestedOfferPolicy.REJECT_IF_PRESENT,
    )

    assert result.records == ()
    assert result.quarantined[0].reason == "nested_seller_offers_present_by_policy"


def test_source_mapping_is_immutable_and_raw_fields_are_preserved():
    source = deepcopy(AMAZON_ROW)
    original = deepcopy(source)
    adapted = adapt_records([source], "amazon_products_32.csv").records[0]

    assert source == original
    assert adapted.source_record == original
    assert adapted.provenance["source_filename"] == "amazon_products_32.csv"


def test_identity_conflict_across_files_is_quarantined():
    context = AdapterContext()
    first = adapt_records([AMAZON_ROW], "charger_final_data.csv", context=context)
    conflicting = adapt_records(
        [dict(AMAZON_ROW, title="Different product title")],
        "multiple_products_final.csv",
        context=context,
    )

    assert len(first.records) == 1
    assert conflicting.records == ()
    assert conflicting.quarantined[0].reason == "unresolved_product_identity_conflict:product_name"


def test_canonical_listing_id_collision_with_different_identity_is_quarantined():
    rows = [
        _canonical_row(),
        _canonical_row(product_id="MCL0002", listing_id="AMA-CLO-LIST-000001"),
    ]
    result = adapt_records(
        rows,
        CANONICAL_SOURCE_FILE,
        naive_timezone="Asia/Kolkata",
    )

    assert len(result.records) == 1
    assert result.quarantined[0].reason == "source_listing_id_collision"


def test_headers_are_checked_for_each_of_the_eleven_datasets():
    for filename in AMAZON_SOURCE_FILES:
        assert validate_source_headers(
            filename,
            ("asin", "title", "seller_id", "timestamp", "currency", "final_price"),
        ) == ()
    assert validate_source_headers(
        CANONICAL_SOURCE_FILE,
        CANONICAL_COLUMNS,
    ) == ()
    assert "canonical_header_must_match_exact_order" in validate_source_headers(
        CANONICAL_SOURCE_FILE,
        reversed(CANONICAL_COLUMNS),
    )


def test_unknown_file_or_incomplete_header_is_rejected():
    with pytest.raises(ValueError, match="unsupported marketplace dataset"):
        adapt_records([AMAZON_ROW], "unrelated.csv")
    issues = validate_source_headers("amazon_products_32.csv", ("asin", "title"))
    assert issues == ("missing_provider_headers:currency,final_price,seller_id,timestamp",)


def test_csv_file_adapter_reads_headers_and_reports_source_row_numbers(tmp_path):
    source = tmp_path / "amazon_products_32.csv"
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=AMAZON_ROW)
    writer.writeheader()
    writer.writerow(AMAZON_ROW)
    source.write_text(output.getvalue(), encoding="utf-8")

    result = adapt_csv_file(source)

    assert len(result.records) == 1
    assert result.records[0].provenance["source_row_number"] == 2
    assert result.records[0].source_record["asin"] == "B000TEST01"


@pytest.mark.parametrize("filename", sorted(ENRICHED_SOURCE_FILES))
def test_each_enriched_dataset_is_supported(filename):
    result = adapt_records([_enriched_row()], filename)

    assert len(result.records) == 1
    adapted = result.records[0]
    assert tuple(adapted.canonical) == CANONICAL_COLUMNS
    assert adapted.canonical["timestamp"] == "2026-11-04T04:30:00+00:00"
    assert adapted.canonical["last_updated"] == "2026-11-04T04:30:00+00:00"
    assert adapted.canonical["product_rating"] is None
    assert adapted.provenance["source_timezone"] == "Asia/Kolkata"
    assert adapted.provenance["source_capture_timestamp"] == "2026-11-04T10:00:00"
    assert adapted.provenance["source_coupon_discount"] == "150"
    assert adapted.enrichment == {
        "image_url": "https://example.test/product.png",
        "review": "Synthetic individual review text.",
        "rating": "3",
        "review_source": "Synthetic (generated sample review and rating)",
    }


def test_enriched_header_requires_exact_canonical_and_extension_order():
    headers = (*CANONICAL_COLUMNS, *ENRICHMENT_COLUMNS)

    assert validate_source_headers(
        "cameras_dataset_enriched.csv",
        headers,
    ) == ()
    assert "enriched_header_must_match_canonical_and_extension_order" in (
        validate_source_headers(
            "cameras_dataset_enriched.csv",
            (*headers, "unexpected"),
        )
    )
    assert "enriched_header_must_match_canonical_and_extension_order" in (
        validate_source_headers(
            "cameras_dataset_enriched.csv",
            reversed(headers),
        )
    )
    with pytest.raises(ValueError, match="unsupported marketplace dataset"):
        validate_source_headers("unrelated_dataset_enriched.csv", headers)


def test_enriched_naive_timestamps_are_interpreted_as_ist_and_saved_as_utc():
    row = _enriched_row(
        timestamp="2026-11-04T19:35:00",
        last_updated="2026-11-04T19:42:55",
    )
    adapted = adapt_records(
        [row],
        "wearables_dataset_enriched.csv",
    ).records[0]

    assert adapted.canonical["timestamp"] == "2026-11-04T14:05:00+00:00"
    assert adapted.canonical["last_updated"] == "2026-11-04T14:12:55+00:00"
    assert adapted.source_record["timestamp"] == "2026-11-04T19:35:00"
    assert adapted.provenance["source_timezone"] == "Asia/Kolkata"


def test_enriched_timestamp_timezone_cannot_be_overridden_as_utc():
    with pytest.raises(ValueError, match="timestamps are fixed to Asia/Kolkata"):
        adapt_records(
            [_enriched_row()],
            "cameras_dataset_enriched.csv",
            naive_timezone="UTC",
        )


def test_unconfirmed_coupon_value_is_preserved_raw_but_not_normalized():
    adapted = adapt_records(
        [_enriched_row(coupon_discount="500")],
        "smart_home_dataset_enriched.csv",
    ).records[0]

    assert adapted.canonical["coupon_discount"] is None
    assert adapted.provenance["source_coupon_discount"] == "500"
    assert adapted.source_record["coupon_discount"] == "500"
    assert "coupon_discount_unit_unconfirmed" in adapted.warnings


def test_review_rating_stays_in_enrichment_and_does_not_replace_product_rating():
    adapted = adapt_records(
        [_enriched_row(product_rating="4.7", rating="3")],
        "mens_clothing_dataset_enriched.csv",
    ).records[0]

    assert adapted.canonical["product_rating"] == "4.7"
    assert adapted.enrichment["rating"] == "3"
    assert adapted.provenance["rating_interpretation"] == "individual_review_rating"


def test_invalid_review_rating_warns_and_preserves_original_enrichment():
    adapted = adapt_records(
        [_enriched_row(rating="6")],
        "cameras_dataset_enriched.csv",
    ).records[0]

    assert adapted.enrichment["rating"] == "6"
    assert adapted.canonical["product_rating"] is None
    assert "invalid_enrichment_rating" in adapted.warnings


def test_missing_enrichment_and_optional_fields_remain_null():
    row = _enriched_row(
        seller_name="",
        product_url="",
        shipping_cost="",
        stock_quantity="",
        product_rating="",
        coupon_discount="",
        image_url="",
        review="",
        rating="",
        review_source="",
    )
    adapted = adapt_records(
        [row],
        "womens_clothing_dataset_enriched.csv",
    ).records[0]

    assert adapted.canonical["seller_name"] is None
    assert adapted.canonical["product_url"] is None
    assert adapted.canonical["shipping_cost"] is None
    assert adapted.canonical["stock_quantity"] is None
    assert adapted.canonical["product_rating"] is None
    assert adapted.canonical["coupon_discount"] is None
    assert adapted.enrichment == dict.fromkeys(ENRICHMENT_COLUMNS)
    assert "coupon_discount_unit_unconfirmed" not in adapted.warnings


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"product_id": ""}, "missing required product identity"),
        ({"product_name": ""}, "missing required product name"),
        ({"seller_id": ""}, "missing required seller identity"),
        ({"price": "0"}, "nonpositive selling price"),
        ({"price": "not-money"}, "invalid numeric value"),
        ({"currency": "XYZ"}, "unsupported or missing currency"),
        ({"timestamp": "not-a-timestamp"}, "invalid ISO-8601"),
    ],
)
def test_invalid_enriched_required_fields_are_quarantined(changes, reason):
    result = adapt_records(
        [_enriched_row(**changes)],
        "cameras_dataset_enriched.csv",
    )

    assert result.records == ()
    assert len(result.quarantined) == 1
    assert reason in result.quarantined[0].reason


def test_duplicate_observation_is_quarantined_without_changing_listing_identity():
    row = _enriched_row()
    result = adapt_records(
        [row, dict(row)],
        "cameras_dataset_enriched.csv",
    )

    assert len(result.records) == 1
    assert len(result.quarantined) == 1
    assert result.quarantined[0].source_row_number == 3
    assert result.quarantined[0].reason == "duplicate_observation"
    assert result.records[0].listing_identity == (
        result.records[0].canonical["marketplace"],
        result.records[0].canonical["product_id"],
        result.records[0].canonical["seller_id"],
        "new",
    )


def test_conflicting_same_time_observation_is_quarantined():
    row = _enriched_row()
    changed = dict(row, price="1390")
    result = adapt_records(
        [row, changed],
        "cameras_dataset_enriched.csv",
    )

    assert len(result.records) == 1
    assert result.quarantined[0].reason == "conflicting_observation_identity"


def test_enriched_csv_file_adapter_validates_header_and_keeps_enrichment(tmp_path):
    filename = "cameras_dataset_enriched.csv"
    source = tmp_path / filename
    headers = (*CANONICAL_COLUMNS, *ENRICHMENT_COLUMNS)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerow(_enriched_row())
    source.write_text(output.getvalue(), encoding="utf-8")

    result = adapt_csv_file(source)

    assert len(result.records) == 1
    assert result.records[0].provenance["source_row_number"] == 2
    assert result.records[0].enrichment["review"] == "Synthetic individual review text."
