import csv
from pathlib import Path

import pytest

from app.services.historical_csv_importer import import_historical_csv


FIELDNAMES = [
    "product_id",
    "listing_id",
    "observation_id",
    "asin",
    "marketplace",
    "seller_id",
    "seller_name",
    "currency",
    "price",
    "original_price",
    "shipping",
    "tax_treatment",
    "observed_at",
    "source_id",
    "source_type",
    "synthetic",
    "_generated",
    "source_observation_id",
    "source_provenance",
]


def _row(index=1, **changes):
    row = {
        "product_id": "product-1",
        "listing_id": "listing-1",
        "observation_id": f"history-observation-{index}",
        "asin": "external-1",
        "marketplace": "amazon.in",
        "seller_id": "seller-1",
        "seller_name": "Seller",
        "currency": "INR",
        "price": "125.50",
        "original_price": "150.00",
        "shipping": "0.00",
        "tax_treatment": "included",
        "observed_at": f"2026-10-{index:02d}T12:00:00+00:00",
        "source_id": "history-source",
        "source_type": "generated_history",
        "synthetic": "True",
        "_generated": "True",
        "source_observation_id": f"source-observation-{index}",
        "source_provenance": '{"source":"demo"}',
    }
    row.update(changes)
    return row


def _write_csv(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


class _Collection:
    def __init__(self):
        self.documents = []
        self.insert_batches = []

    def find_one(self, query):
        return next(
            (doc for doc in self.documents if doc.get("id") == query.get("id")),
            None,
        )

    def find(self, query):
        identities = set()
        for clause in query["$or"]:
            field, condition = next(iter(clause.items()))
            if field in {"key", "observation_id"}:
                identities.update(condition["$in"])
        return [
            doc for doc in self.documents
            if doc.get("key") in identities
            or doc.get("observation_id") in identities
        ]

    def insert_many(self, documents, ordered=True):
        assert ordered is True
        self.insert_batches.append(len(documents))
        self.documents.extend(documents)

    def insert_one(self, document):
        self.documents.append(document)


class _Database:
    name = "marketlens_imported"

    def __init__(self):
        self.observations = _Collection()
        self.runs = _Collection()


def test_historical_import_preserves_provenance_and_canonical_fields(tmp_path):
    path = tmp_path / "history.csv"
    _write_csv(path, [_row()])
    db = _Database()

    result = import_historical_csv(db, path, "history-inr-120d-v1")

    stored = db.observations.documents[0]
    assert result["observations_inserted"] == 1
    assert stored["price"] == 12550
    assert stored["reference_price"] == 15000
    assert stored["key"] == "history-observation-1"
    assert stored["listing_id"] == "listing-1"
    assert stored["source_observation_id"] == "source-observation-1"
    assert stored["source_type"] == "generated_history"
    assert stored["synthetic"] is True
    assert stored["_generated"] is True
    assert stored["run_id"] == "history-inr-120d-v1"
    assert stored["canonical"]["source_provenance"] == '{"source":"demo"}'
    assert stored["canonical"]["original_price"] == "150.00"


def test_historical_import_is_idempotent_and_batches_inserts(tmp_path):
    path = tmp_path / "history.csv"
    _write_csv(path, [_row(index) for index in range(1, 6)])
    db = _Database()

    first = import_historical_csv(
        db, path, "history-inr-120d-v1", batch_size=2
    )
    second = import_historical_csv(
        db, path, "history-inr-120d-v1", batch_size=2
    )

    assert first["observations_inserted"] == 5
    assert second["observations_inserted"] == 0
    assert second["observations_already_present"] == 5
    assert len(db.observations.documents) == 5
    assert db.observations.insert_batches == [2, 2, 1]
    assert len(db.runs.documents) == 1


def test_historical_import_rejects_conflicting_identity_without_overwrite(
    tmp_path,
):
    path = tmp_path / "history.csv"
    _write_csv(path, [_row()])
    db = _Database()
    db.observations.documents.append({
        "key": "history-observation-1",
        "observation_id": "history-observation-1",
        "run_id": "another-run",
        "canonical": {"price": "different"},
        "source_observation_id": "source-observation-1",
        "synthetic": True,
        "source_type": "generated_history",
    })

    with pytest.raises(ValueError, match="Conflicting existing observation"):
        import_historical_csv(db, path, "history-inr-120d-v1")

    assert db.observations.documents[0]["run_id"] == "another-run"
    assert len(db.observations.documents) == 1
    assert db.runs.documents == []
