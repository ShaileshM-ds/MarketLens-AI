"""Persistence bridge for explicitly named marketplace adapter inputs."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.dataset_adapter import (
    AdaptationResult,
    AdaptedRecord,
    AdapterContext,
    ENRICHED_SOURCE_FILES,
    QuarantinedRecord,
    adapt_csv_content,
    adapt_csv_file,
)
from app.services.normalize import digest, money


def _stable_id(parts: tuple[str, ...], length: int | None = None) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:length] if length else digest


def application_product_id(
    canonical: Mapping[str, Any],
    product_identity: tuple[str, str] | None = None,
) -> str:
    """Keep adapter-canonical identities and scope provider IDs by marketplace."""
    if product_identity and product_identity[0] == "canonical":
        return str(canonical["product_id"])
    return digest([
        str(canonical["marketplace"]),
        str(canonical["product_id"]),
        {},
    ])[:24]


def build_marketplace_documents(
    canonical: Mapping[str, Any],
    *,
    observation_identity: str,
    provenance: Mapping[str, Any],
    source_record: Mapping[str, Any],
    product_identity: tuple[str, str] | None = None,
    enrichment: Mapping[str, Any] | None = None,
    run_id: str,
    synthetic: bool | None = None,
    source_type: str | None = None,
    source_record_ref: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build application-compatible documents without dropping canonical data."""
    canonical_data = dict(canonical)
    marketplace = str(canonical_data["marketplace"])
    currency = str(canonical_data["currency"])
    product_id = application_product_id(canonical_data, product_identity)
    seller_id = str(canonical_data["seller_id"])
    observed_at = str(canonical_data["timestamp"])
    listing_id = str(canonical_data["listing_id"])
    is_synthetic = (
        bool(provenance.get("synthetic"))
        if synthetic is None
        else synthetic
    )
    effective_source_type = (
        source_type
        or str(canonical_data.get("source_type") or "adapted_source")
    )
    source_name = str(
        provenance.get("source_filename")
        or canonical_data.get("source")
        or effective_source_type
    )
    row_number = provenance.get("source_row_number")
    raw_id = source_record_ref or _stable_id((
        str(provenance.get("source_filename") or source_name),
        str(row_number or ""),
        json.dumps(source_record, ensure_ascii=False, sort_keys=True),
    ))
    captured_at = datetime.now(timezone.utc).isoformat()

    product = {
        "id": product_id,
        "canonical_product_id": str(canonical_data["product_id"]),
        "external_id": str(canonical_data["product_id"]),
        "parent_id": None,
        "marketplace": marketplace,
        "variant": {},
        "condition": canonical_data.get("product_condition"),
        "product_url": canonical_data.get("product_url"),
        "image_url": (enrichment or {}).get("image_url"),
        "country": None,
        "title": canonical_data.get("product_name"),
        "brand": "Unknown",
        "brand_source": "not_present_in_canonical_schema",
        "category": canonical_data.get("category") or "Uncategorized",
        "currency": currency,
    }

    seller = {
        "id": seller_id,
        "name": canonical_data.get("seller_name"),
        "marketplace": marketplace,
        "location": canonical_data.get("seller_location"),
        "rating": (
            float(canonical_data["seller_rating"])
            if canonical_data.get("seller_rating") is not None
            else None
        ),
    }

    raw = {
        "id": raw_id,
        "run_id": run_id,
        "source_filename": provenance.get("source_filename"),
        "source_row_number": row_number,
        "source_record": deepcopy(dict(source_record)),
        "provenance": deepcopy(dict(provenance)),
        "created_at": captured_at,
    }

    source_provenance = deepcopy(dict(provenance))
    source_provenance.update({
        "run_id": run_id,
        "source_record_ref": raw_id,
        "source_type": effective_source_type,
        "synthetic": is_synthetic,
    })
    observation = {
        "key": observation_identity,
        "observation_id": observation_identity,
        "product_id": product_id,
        "canonical_product_id": str(canonical_data["product_id"]),
        "listing_id": listing_id,
        "seller_id": seller_id,
        "seller_name": canonical_data.get("seller_name"),
        "price": money(canonical_data["price"], currency),
        "shipping": (
            money(canonical_data["shipping_cost"], currency)
            if canonical_data.get("shipping_cost") is not None
            else None
        ),
        "reference_price": (
            money(canonical_data["original_price"], currency)
            if canonical_data.get("original_price") is not None
            else None
        ),
        "reference_type": "source_original_price",
        "total": None,
        "tax_treatment": "unknown",
        "coupon": None,
        "coupon_description": None,
        "coupon_conditions": None,
        "region": None,
        "conditional_coupon_price": None,
        "currency": currency,
        "availability": canonical_data.get("availability"),
        "urgency": canonical_data.get("urgency_message"),
        "countdown": None,
        "delivery": canonical_data.get("delivery_information"),
        "rating": (
            float(canonical_data["product_rating"])
            if canonical_data.get("product_rating") is not None
            else None
        ),
        "seller_rating": canonical_data.get("seller_rating"),
        "review_count": None,
        "seller_count": None,
        "buy_box": None,
        "promotion": canonical_data.get("payment_offer"),
        "seller_offer": False,
        "seller_offer_details": None,
        "ships_from": None,
        "seller_url": None,
        "metadata": {
            "canonical_product_id": str(canonical_data["product_id"]),
            "marketplace": marketplace,
            "title": canonical_data.get("product_name"),
            "category": canonical_data.get("category"),
            "condition": canonical_data.get("product_condition"),
            "product_url": canonical_data.get("product_url"),
            "image_url": (enrichment or {}).get("image_url"),
            "currency": currency,
        },
        "canonical": canonical_data,
        "enrichment": deepcopy(dict(enrichment or {})),
        "source_record_ref": raw_id,
        "raw_ref": raw_id,
        "source_provenance": source_provenance,
        "source_id": source_name,
        "source_type": effective_source_type,
        "synthetic": is_synthetic,
        "run_id": run_id,
        "observed_at": observed_at,
        "ingested_at": captured_at,
        "timestamp_quality": "explicit",
        "schema_version": "1",
        "quality_flags": [],
    }
    return product, seller, raw, observation


def _documents_for_record(
    record: AdaptedRecord,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return build_marketplace_documents(
        record.canonical,
        observation_identity=record.observation_identity,
        provenance=record.provenance,
        source_record=record.source_record,
        product_identity=record.product_identity,
        enrichment=record.enrichment,
        run_id=run_id,
    )


def _quarantine_document(
    record: QuarantinedRecord,
    run_id: str,
) -> dict[str, Any]:
    quarantine_id = _stable_id((
        record.source_filename,
        str(record.source_row_number),
        str(record.source_offer_index or ""),
        record.reason,
    ))
    return {
        "id": quarantine_id,
        "run_id": run_id,
        "source_filename": record.source_filename,
        "source_row_number": record.source_row_number,
        "source_offer_index": record.source_offer_index,
        "reason": record.reason,
        "raw": deepcopy(dict(record.source_record)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_adaptation(
    db: Any,
    result: AdaptationResult,
    run_id: str,
) -> dict[str, Any]:
    """Idempotently persist adapted records through existing collections."""
    started_at = datetime.now(timezone.utc).isoformat()
    source_files = sorted({
        str(record.provenance.get("source_filename") or "")
        for record in result.records
    } | {
        record.source_filename for record in result.quarantined
    })
    source_fingerprint = hashlib.sha256(json.dumps(
        [
            {
                "observation_identity": record.observation_identity,
                "canonical": record.canonical,
                "provenance": record.provenance,
                "enrichment": record.enrichment,
                "source_record": record.source_record,
            }
            for record in result.records
        ] + [
            {
                "source_filename": record.source_filename,
                "source_row_number": record.source_row_number,
                "reason": record.reason,
                "source_record": record.source_record,
            }
            for record in result.quarantined
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    existing_run = db.runs.find_one({"id": run_id})
    if existing_run and (
        existing_run.get("source_type") != "adapted_marketplace_csv"
        or existing_run.get("source_sha256") != source_fingerprint
    ):
        raise ValueError(f"run_id {run_id!r} belongs to a different import")

    prepared = [
        _documents_for_record(record, run_id)
        for record in result.records
    ]
    observation_ids = [observation["key"] for _, _, _, observation in prepared]
    observation_id_set = set(observation_ids)
    if len(observation_id_set) != len(observation_ids):
        raise ValueError("Adapted source observation identities are not unique")

    existing_docs = list(db.observations.find({
        "$or": [
            {"key": {"$in": observation_ids}},
            {"observation_id": {"$in": observation_ids}},
        ]
    }, {
        "key": 1,
        "observation_id": 1,
        "canonical": 1,
        "source_record_ref": 1,
        "source_type": 1,
        "synthetic": 1,
    })) if observation_ids else []
    existing_by_identity: dict[str, dict[str, Any]] = {}
    for existing in existing_docs:
        for field in ("key", "observation_id"):
            identity = existing.get(field)
            if identity not in observation_id_set:
                continue
            previous = existing_by_identity.get(identity)
            if previous is not None and previous.get("_id") != existing.get("_id"):
                raise ValueError(
                    f"Multiple observations use source identity {identity}"
                )
            existing_by_identity[identity] = existing

    for _, _, _, observation in prepared:
        existing = existing_by_identity.get(observation["key"])
        if existing and (
            existing.get("key") != observation["key"]
            or existing.get("observation_id") != observation["observation_id"]
            or existing.get("canonical") != observation["canonical"]
            or existing.get("source_record_ref") != observation["source_record_ref"]
            or existing.get("source_type") != observation["source_type"]
            or existing.get("synthetic") is not observation["synthetic"]
        ):
            raise ValueError(
                "existing observation identity conflicts with adapted record"
            )

    db.runs.update_one(
        {"id": run_id},
        {"$setOnInsert": {
            "id": run_id,
            "mode": "imported",
            "status": "in_progress",
            "source_type": "adapted_marketplace_csv",
            "source_filenames": source_files,
            "source_sha256": source_fingerprint,
            "synthetic": bool(result.records) and all(
                record.provenance.get("synthetic") is True
                for record in result.records
            ),
            "created_at": started_at,
        }},
        upsert=True,
    )

    counts = {
        "adapted": len(result.records),
        "quarantined": len(result.quarantined),
        "products_created": 0,
        "sellers_created": 0,
        "raw_created": 0,
        "observations_created": 0,
        "observations_already_present": 0,
        "warnings": sum(len(record.warnings) for record in result.records),
    }
    affected_products: set[str] = set()
    seller_profiles: dict[str, dict[str, set[str]]] = {}
    for record in result.records:
        seller_id = str(record.canonical["seller_id"])
        profile = seller_profiles.setdefault(seller_id, {
            "names": set(),
            "marketplaces": set(),
            "locations": set(),
            "ratings": set(),
        })
        for field, key in (
            ("seller_name", "names"),
            ("marketplace", "marketplaces"),
            ("seller_location", "locations"),
            ("seller_rating", "ratings"),
        ):
            value = record.canonical.get(field)
            if value is not None and str(value).strip():
                profile[key].add(str(value).strip())

    for record, (product, seller, raw, observation) in zip(
        result.records,
        prepared,
    ):
        seller_profile = seller_profiles[seller["id"]]
        seller["name"] = (
            next(iter(seller_profile["names"]))
            if len(seller_profile["names"]) == 1
            else None
        )
        seller["source_names"] = sorted(seller_profile["names"])
        seller["marketplaces"] = sorted(seller_profile["marketplaces"])
        seller["location"] = (
            next(iter(seller_profile["locations"]))
            if len(seller_profile["locations"]) == 1
            else None
        )
        seller["source_locations"] = sorted(seller_profile["locations"])
        seller["rating"] = (
            float(next(iter(seller_profile["ratings"])))
            if len(seller_profile["ratings"]) == 1
            else None
        )
        seller["source_ratings"] = sorted(seller_profile["ratings"])
        affected_products.add(product["id"])
        product_result = db.products.update_one(
            {"id": product["id"]},
            {
                "$setOnInsert": product,
                "$addToSet": {"marketplaces": product["marketplace"]},
            },
            upsert=True,
        )
        counts["products_created"] += int(product_result.upserted_id is not None)
        seller_result = db.sellers.update_one(
            {"id": seller["id"]},
            {
                "$setOnInsert": {
                    key: value
                    for key, value in seller.items()
                    if key not in {
                        "marketplaces",
                        "source_names",
                        "source_locations",
                        "source_ratings",
                    }
                },
                "$addToSet": {
                    "marketplaces": {"$each": seller["marketplaces"]},
                    "source_names": {"$each": seller["source_names"]},
                    "source_locations": {"$each": seller["source_locations"]},
                    "source_ratings": {"$each": seller["source_ratings"]},
                },
            },
            upsert=True,
        )
        counts["sellers_created"] += int(seller_result.upserted_id is not None)
        raw_result = db.raw.update_one(
            {"id": raw["id"]},
            {"$setOnInsert": raw},
            upsert=True,
        )
        counts["raw_created"] += int(raw_result.upserted_id is not None)
        if observation["key"] in existing_by_identity:
            counts["observations_already_present"] += 1
        else:
            observation_result = db.observations.update_one(
                {"key": observation["key"]},
                {"$setOnInsert": observation},
                upsert=True,
            )
            if observation_result.upserted_id is not None:
                counts["observations_created"] += 1
            else:
                raise ValueError(
                    "source observation appeared after identity preflight"
                )

    for record in result.quarantined:
        document = _quarantine_document(record, run_id)
        db.quarantine.update_one(
            {"id": document["id"]},
            {"$setOnInsert": document},
            upsert=True,
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        **counts,
        "run_id": run_id,
        "source_product_count": len({
            record.product_identity for record in result.records
        }),
        "affected_product_count": len(affected_products),
        "source_filenames": source_files,
        "source_sha256": source_fingerprint,
        "finished_at": finished_at,
    }
    db.runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "completed",
            "finished_at": finished_at,
            "summary": summary,
        }},
        upsert=True,
    )
    return summary


def import_csv_content(
    db: Any,
    content: str,
    filename: str,
    run_id: str,
) -> dict[str, Any]:
    result = adapt_csv_content(content, filename)
    return persist_adaptation(db, result, run_id)


def import_enriched_datasets(
    db: Any,
    csv_paths: list[str | Path],
    run_id: str,
) -> dict[str, Any]:
    """Import the five approved enriched datasets through the canonical adapter."""
    if getattr(db, "name", None) != "marketlens_imported":
        raise ValueError(
            "Enriched dataset imports must target marketlens_imported"
        )

    paths = [Path(path) for path in csv_paths]
    filenames = {path.name for path in paths}
    if (
        len(paths) != len(ENRICHED_SOURCE_FILES)
        or filenames != ENRICHED_SOURCE_FILES
    ):
        raise ValueError(
            "Import exactly the five approved enriched dataset files"
        )

    context = AdapterContext()
    records: list[AdaptedRecord] = []
    quarantined: list[QuarantinedRecord] = []
    for path in sorted(paths, key=lambda item: item.name):
        result = adapt_csv_file(path, context=context)
        records.extend(result.records)
        quarantined.extend(result.quarantined)

    return persist_adaptation(
        db,
        AdaptationResult(tuple(records), tuple(quarantined)),
        run_id,
    )
