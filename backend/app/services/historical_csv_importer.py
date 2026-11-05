"""Streaming import of materialized historical observation CSV files."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any, Iterator

from app.services.normalize import money, normalize


_REQUIRED_COLUMNS = frozenset({
    "product_id",
    "listing_id",
    "observation_id",
    "asin",
    "marketplace",
    "seller_id",
    "currency",
    "price",
    "observed_at",
    "source_type",
    "synthetic",
    "_generated",
    "source_observation_id",
})
_SOURCE_TYPE = "generated_history"


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_batches(
    path: Path,
    batch_size: int,
) -> Iterator[list[dict[str, str | None]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError("Historical CSV has no header")
        missing = sorted(_REQUIRED_COLUMNS - set(reader.fieldnames))
        if missing:
            raise ValueError(
                "Historical CSV is missing required columns: "
                + ", ".join(missing)
            )
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Historical CSV has duplicate headers")

        batch: list[dict[str, str | None]] = []
        for row in reader:
            batch.append(row)
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def _observation_document(
    row: dict[str, str | None],
    run_id: str,
) -> dict[str, Any]:
    observation_id = (row.get("observation_id") or "").strip()
    source_observation_id = (
        row.get("source_observation_id") or ""
    ).strip()
    if not observation_id:
        raise ValueError("Historical row has a blank observation_id")
    if not source_observation_id:
        raise ValueError(
            f"Historical observation {observation_id} has no "
            "source_observation_id"
        )
    if (row.get("source_type") or "").strip() != _SOURCE_TYPE:
        raise ValueError(
            f"Historical observation {observation_id} has an invalid "
            "source_type"
        )
    if (row.get("synthetic") or "").strip().casefold() != "true":
        raise ValueError(
            f"Historical observation {observation_id} is not synthetic"
        )
    if (row.get("_generated") or "").strip().casefold() != "true":
        raise ValueError(
            f"Historical observation {observation_id} is not marked generated"
        )

    _, observation = normalize(row, "imported", run_id)
    original_price = row.get("original_price")
    if original_price not in (None, ""):
        observation["reference_price"] = money(
            original_price,
            row["currency"],
        )
    observation.update({
        "key": observation_id,
        "observation_id": observation_id,
        "product_id": row["product_id"],
        "listing_id": row["listing_id"],
        "seller_id": row["seller_id"],
        "source_observation_id": source_observation_id,
        "source_id": row.get("source_id"),
        "source_type": row["source_type"],
        "synthetic": True,
        "_generated": True,
        "run_id": run_id,
        "canonical": dict(row),
        "source_record_ref": row.get("source_record_ref"),
        "source_record": row.get("source_record"),
        "source_provenance": row.get("source_provenance"),
        "enrichment": row.get("enrichment"),
    })
    return observation


def _same_imported_observation(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    return (
        existing.get("key") == candidate["key"]
        and existing.get("observation_id") == candidate["observation_id"]
        and existing.get("run_id") == candidate["run_id"]
        and existing.get("canonical") == candidate["canonical"]
        and existing.get("source_observation_id")
        == candidate["source_observation_id"]
        and existing.get("synthetic") is True
        and existing.get("source_type") == _SOURCE_TYPE
    )


def import_historical_csv(
    db: Any,
    csv_path: str | Path,
    run_id: str,
    batch_size: int = 1000,
) -> dict[str, int | str]:
    """Insert a historical CSV in bounded batches without replacing records.

    Re-running with the same run ID and identical file skips already inserted
    observations. A conflicting observation identity raises an error.
    """
    from app.db import review_mode_enabled

    if review_mode_enabled():
        raise RuntimeError("Historical CSV imports are disabled in review mode")
    if getattr(db, "name", None) != "marketlens_imported":
        raise ValueError(
            "Historical CSV imports must target marketlens_imported"
        )
    if not run_id or not run_id.strip():
        raise ValueError("A distinct nonblank run_id is required")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Historical CSV not found: {path}")
    source_hash = _file_sha256(path)
    existing_run = db.runs.find_one({"id": run_id})
    if existing_run and (
        existing_run.get("source_type") != "historical_csv_import"
        or existing_run.get("source_sha256") != source_hash
        or existing_run.get("source_filename") != path.name
    ):
        raise ValueError(
            f"run_id {run_id!r} already belongs to a different run"
        )

    inserted = 0
    skipped = 0
    total = 0
    for rows in _read_batches(path, batch_size):
        candidates = [
            _observation_document(row, run_id)
            for row in rows
        ]
        total += len(candidates)
        identities = [item["observation_id"] for item in candidates]
        existing_docs = list(db.observations.find({
            "$or": [
                {"key": {"$in": identities}},
                {"observation_id": {"$in": identities}},
            ]
        }))
        by_identity: dict[str, dict[str, Any]] = {}
        for document in existing_docs:
            for field in ("key", "observation_id"):
                identity = document.get(field)
                if identity in identities:
                    previous = by_identity.get(identity)
                    if previous is not None and previous.get("_id") != document.get("_id"):
                        raise ValueError(
                            f"Multiple existing observations use identity {identity}"
                        )
                    by_identity[identity] = document

        batch_seen: dict[str, dict[str, Any]] = {}
        to_insert = []
        for candidate in candidates:
            identity = candidate["observation_id"]
            previous_in_batch = batch_seen.get(identity)
            if previous_in_batch is not None:
                if previous_in_batch["canonical"] != candidate["canonical"]:
                    raise ValueError(
                        f"Conflicting duplicate input identity {identity}"
                    )
                skipped += 1
                continue
            batch_seen[identity] = candidate

            existing = by_identity.get(identity)
            if existing is not None:
                if not _same_imported_observation(existing, candidate):
                    raise ValueError(
                        f"Conflicting existing observation identity {identity}"
                    )
                skipped += 1
                continue
            to_insert.append(candidate)

        if to_insert:
            db.observations.insert_many(to_insert, ordered=True)
            inserted += len(to_insert)

    if existing_run is None:
        db.runs.insert_one({
            "id": run_id,
            "mode": "imported",
            "source_type": "historical_csv_import",
            "synthetic": True,
            "status": "completed",
            "source_filename": path.name,
            "source_sha256": source_hash,
            "generated_count": total,
            "observations_inserted": inserted,
            "observations_already_present": skipped,
        })

    return {
        "run_id": run_id,
        "source_filename": path.name,
        "observations_total": total,
        "observations_inserted": inserted,
        "observations_already_present": skipped,
    }
