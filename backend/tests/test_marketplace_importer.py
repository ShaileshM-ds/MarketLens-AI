import csv
import io
from types import SimpleNamespace

import pytest

from app.services.dataset_adapter import (
    AdapterContext,
    CANONICAL_COLUMNS,
    ENRICHED_SOURCE_FILES,
    ENRICHMENT_COLUMNS,
    adapt_csv_content,
    adapt_records,
)
from app.services.marketplace_importer import (
    build_marketplace_documents,
    import_enriched_datasets,
    persist_adaptation,
)


def _enriched_csv_row(**changes):
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
        "product_rating": "",
        "category": "Jackets",
        "source": "Generated for project demonstration",
        "source_type": "Synthetic",
        "listing_id": "AMA-CLO-LIST-000001",
        "coupon_discount": "150",
        "payment_offer": "No payment offer",
        "urgency_message": "Synthetic urgency example",
        "stock_quantity": "8",
        "product_condition": "New",
        "seller_location": "Bengaluru, Karnataka",
        "last_updated": "2026-11-04T10:30:00",
        "image_url": "https://example.test/product.png",
        "review": "Synthetic individual review text.",
        "rating": "3",
        "review_source": "Generated sample review",
    }
    row.update(changes)
    return row


def _adapted_result(**changes):
    filename = "mens_clothing_dataset_enriched.csv"
    assert filename in ENRICHED_SOURCE_FILES
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=(*CANONICAL_COLUMNS, *ENRICHMENT_COLUMNS),
    )
    writer.writeheader()
    writer.writerow(_enriched_csv_row(**changes))
    return adapt_csv_content(output.getvalue(), filename)


def test_build_documents_preserve_canonical_data_and_inr():
    record = _adapted_result().records[0]
    product, seller, raw, observation = build_marketplace_documents(
        record.canonical,
        observation_identity=record.observation_identity,
        provenance=record.provenance,
        source_record=record.source_record,
        product_identity=record.product_identity,
        enrichment=record.enrichment,
        run_id="run-1",
    )

    assert product["id"] == "MCL0001"
    assert product["canonical_product_id"] == "MCL0001"
    assert observation["canonical_product_id"] == "MCL0001"
    assert observation["price"] == 138000
    assert observation["shipping"] == 6000
    assert observation["reference_price"] == 160000
    assert observation["currency"] == "INR"
    assert observation["listing_id"] == "AMA-CLO-LIST-000001"
    assert observation["observation_id"] == record.observation_identity
    assert observation["raw_ref"] == raw["id"]
    assert observation["canonical"]["coupon_discount"] is None
    assert observation["enrichment"]["rating"] == "3"
    assert observation["rating"] is None
    assert product["product_url"] is None
    assert seller["name"] == "Seller"
    assert raw["source_record"]["coupon_discount"] == "150"
    assert raw["provenance"]["source_timezone"] == "Asia/Kolkata"


def test_persist_adaptation_is_idempotent_and_preserves_extension_fields():
    result = _adapted_result()
    database = _FakeDatabase()

    first = persist_adaptation(database, result, "run-1")
    second = persist_adaptation(database, result, "run-1")

    assert first["observations_created"] == 1
    assert second["observations_created"] == 0
    assert second["observations_already_present"] == 1
    assert len(database.products.documents) == 1
    assert len(database.sellers.documents) == 1
    assert len(database.raw.documents) == 1
    stored = next(iter(database.observations.documents.values()))
    assert stored["canonical"]["product_id"] == "MCL0001"
    assert stored["enrichment"]["image_url"] == "https://example.test/product.png"
    assert stored["source_provenance"]["synthetic"] is True
    assert stored["raw_ref"] == stored["source_record_ref"]
    assert database.products.documents["MCL0001"]["marketplaces"] == ["Amazon"]


def test_persist_adaptation_keeps_shared_canonical_product_and_seller_sources():
    filename = "mens_clothing_dataset_enriched.csv"
    first = _enriched_csv_row(
        seller_name="Seller A",
        listing_id="listing-amazon",
        marketplace="Amazon India",
    )
    second = _enriched_csv_row(
        seller_name="Seller B",
        listing_id="listing-flipkart",
        marketplace="Flipkart",
        timestamp="2026-11-04T11:30:00",
    )
    result = adapt_records(
        [first, second],
        filename,
        context=AdapterContext(),
    )
    database = _FakeDatabase()

    persist_adaptation(database, result, "run-1")

    assert len(database.products.documents) == 1
    product = database.products.documents["MCL0001"]
    assert product["marketplaces"] == ["Amazon India", "Flipkart"]
    assert {
        row["product_id"]
        for row in database.observations.documents.values()
    } == {"MCL0001"}
    seller = database.sellers.documents["SELLER001"]
    assert seller["name"] is None
    assert seller["source_names"] == ["Seller A", "Seller B"]


def test_persist_adaptation_stores_quarantined_rows():
    result = _adapted_result(product_name="")
    assert len(result.records) == 0
    assert len(result.quarantined) == 1
    database = _FakeDatabase()

    summary = persist_adaptation(database, result, "run-1")

    assert summary["quarantined"] == 1
    quarantine = next(iter(database.quarantine.documents.values()))
    assert quarantine["source_filename"] == "mens_clothing_dataset_enriched.csv"
    assert quarantine["source_row_number"] == 2
    assert "product name" in quarantine["reason"]


def test_enriched_import_is_restricted_to_imported_db_and_five_sources():
    with pytest.raises(ValueError, match="marketlens_imported"):
        import_enriched_datasets(
            SimpleNamespace(name="marketlens_demo"),
            [],
            "run-1",
        )

    paths = [
        "cameras_dataset_enriched.csv",
        "mens_clothing_dataset_enriched.csv",
        "smart_home_dataset_enriched.csv",
        "wearables_dataset_enriched.csv",
        "amazon_products_32.csv",
    ]
    with pytest.raises(ValueError, match="five approved"):
        import_enriched_datasets(
            SimpleNamespace(name="marketlens_imported"),
            paths,
            "run-1",
        )


class _FakeCollection:
    def __init__(self):
        self.documents = {}

    def update_one(self, query, update, upsert=False):
        field, value = next(iter(query.items()))
        existing = self.documents.get(value)
        if existing is not None:
            existing.update(update.get("$set", {}))
            for name, additions in update.get("$addToSet", {}).items():
                values = (
                    additions.get("$each", [])
                    if isinstance(additions, dict)
                    and "$each" in additions
                    else [additions]
                )
                existing.setdefault(name, [])
                for item in values:
                    if item not in existing[name]:
                        existing[name].append(item)
            return SimpleNamespace(upserted_id=None)
        if not upsert:
            return SimpleNamespace(upserted_id=None)
        document = dict(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))
        for name, additions in update.get("$addToSet", {}).items():
            values = (
                additions.get("$each", [])
                if isinstance(additions, dict) and "$each" in additions
                else [additions]
            )
            document[name] = list(dict.fromkeys(values))
        self.documents[value] = document
        return SimpleNamespace(upserted_id=value)

    def find_one(self, query, projection=None):
        field, value = next(iter(query.items()))
        document = self.documents.get(value)
        if document is None:
            return None
        if projection is None:
            return dict(document)
        return {
            key: document[key]
            for key in projection
            if key in document
        }

    def find(self, query, projection=None):
        del projection
        identities = set()
        for field in ("key", "observation_id"):
            identities.update(query["$or"][0].get(field, {}).get("$in", []))
            identities.update(query["$or"][1].get(field, {}).get("$in", []))
        return [
            dict(document)
            for document in self.documents.values()
            if document.get("key") in identities
            or document.get("observation_id") in identities
        ]


class _FakeDatabase:
    def __init__(self):
        for name in ("products", "sellers", "raw", "observations", "runs", "quarantine"):
            setattr(self, name, _FakeCollection())
