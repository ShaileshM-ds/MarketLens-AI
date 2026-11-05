"""Read-only ownership audit for marketlens_imported.

This script only calls count, find, distinct, and aggregate. It does not
insert, update, delete, or create indexes.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient
from pymongo.read_preferences import ReadPreference

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.services.dataset_adapter import AdapterContext, adapt_csv_content  # noqa: E402
from app.services.normalize import digest  # noqa: E402

SOURCE_DIR = Path(r"C:\Users\Impana Dechamma\final_products_files")
SOURCE_FILES = (
    "cameras_dataset_enriched.csv",
    "mens_clothing_dataset_enriched.csv",
    "smart_home_dataset_enriched.csv",
    "wearables_dataset_enriched.csv",
    "womens_clothing_dataset_enriched.csv",
)
OUT_DIR = Path(__file__).resolve().parent
INR_RUN = "history-inr-120d-v1"
OLD_RUNS = (
    "ee40b2c5fc9f4cc4a3d8c7a0edae72b4",
    "amazon-charger-v1",
    "amazon-multiple-products-v1",
    "amazon-usb-keyboard-mouse-v1",
    "history-gen-v1",
    "history-gen-new-imports-v1",
)
URI = "mongodb://127.0.0.1:27018"
DB_NAME = "marketlens_imported"


def adapt_sources() -> dict:
    context = AdapterContext()
    per_file = []
    observation_ids: list[str] = []
    product_ids: list[str] = []
    seller_ids: list[str] = []
    listing_ids: list[str] = []
    marketplaces: set[str] = set()
    for name in SOURCE_FILES:
        path = SOURCE_DIR / name
        content = path.read_text(encoding="utf-8-sig")
        result = adapt_csv_content(content, name, context=context)
        ids = [record.observation_identity for record in result.records]
        products = [str(record.canonical["product_id"]) for record in result.records]
        sellers = [str(record.canonical["seller_id"]) for record in result.records]
        listings = [str(record.canonical["listing_id"]) for record in result.records]
        marketplaces.update(str(record.canonical["marketplace"]) for record in result.records)
        observation_ids.extend(ids)
        product_ids.extend(products)
        seller_ids.extend(sellers)
        listing_ids.extend(listings)
        per_file.append({
            "file": name,
            "rows_adapted": len(result.records),
            "quarantined": len(result.quarantined),
            "distinct_observation_ids": len(set(ids)),
            "distinct_product_ids": len(set(products)),
            "distinct_seller_ids": len(set(sellers)),
            "distinct_listing_ids": len(set(listings)),
        })
    return {
        "per_file": per_file,
        "observation_ids": observation_ids,
        "product_ids": sorted(set(product_ids)),
        "seller_ids": sorted(set(seller_ids)),
        "listing_ids": sorted(set(listing_ids)),
        "marketplaces": sorted(marketplaces),
        "duplicate_observation_ids_in_sources": sum(
            count - 1 for count in Counter(observation_ids).values() if count > 1
        ),
        "duplicate_product_ids_across_rows": len(product_ids) - len(set(product_ids)),
    }


def hashed_application_ids(product_ids: list[str], marketplace: str) -> set[str]:
    return {
        digest([marketplace, product_id, {}])[:24]
        for product_id in product_ids
    }


def collection_counts(db) -> dict[str, int]:
    return {
        name: db[name].count_documents({})
        for name in sorted(db.list_collection_names())
    }


def group_counts(db, collection: str, field: str) -> dict:
    rows = db[collection].aggregate([
        {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ], allowDiskUse=True)
    return {("null" if row["_id"] is None else str(row["_id"])): row["n"] for row in rows}


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    sources = adapt_sources()
    source_observation_ids = set(sources["observation_ids"])
    source_product_ids = set(sources["product_ids"])
    source_seller_ids = set(sources["seller_ids"])

    client = MongoClient(
        URI,
        serverSelectionTimeoutMS=8000,
        socketTimeoutMS=600000,
        read_preference=ReadPreference.PRIMARY,
    )
    client.admin.command("ping")
    database_names = client.list_database_names()
    db = client[DB_NAME]
    if db.name != DB_NAME:
        raise RuntimeError(f"Refusing to audit unexpected database {db.name}")

    counts = collection_counts(db)
    run = db.runs.find_one({"id": INR_RUN}, {"_id": 0})
    run_ids = list(db.runs.find({}, {"_id": 0, "id": 1, "status": 1, "source_type": 1, "mode": 1}))

    observation_groups = list(db.observations.aggregate([
        {"$group": {
            "_id": {
                "run_id": "$run_id",
                "source_type": "$source_type",
                "synthetic": "$synthetic",
                "currency": "$currency",
                "marketplace": "$marketplace",
            },
            "n": {"$sum": 1},
        }},
    ], allowDiskUse=True))
    source_id_frequency = list(db.observations.aggregate([
        {"$group": {"_id": "$source_observation_id", "n": {"$sum": 1}}},
        {"$group": {
            "_id": "$n",
            "source_ids": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ], allowDiskUse=True))
    duplicate_observation_ids = next(db.observations.aggregate([
        {"$group": {"_id": "$observation_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$count": "duplicate_observation_ids"},
    ], allowDiskUse=True), {"duplicate_observation_ids": 0})
    blank_source_ids = db.observations.count_documents({
        "$or": [
            {"source_observation_id": None},
            {"source_observation_id": ""},
            {"source_observation_id": {"$exists": False}},
        ]
    })
    non_historical = {
        "synthetic_not_true": db.observations.count_documents({"synthetic": {"$ne": True}}),
        "source_type_not_generated_history": db.observations.count_documents(
            {"source_type": {"$ne": "generated_history"}}
        ),
        "run_id_not_inr": db.observations.count_documents({"run_id": {"$ne": INR_RUN}}),
        "currency_not_inr": db.observations.count_documents({"currency": {"$ne": "INR"}}),
        "self_source_rows": db.observations.count_documents({
            "$expr": {"$eq": ["$observation_id", "$source_observation_id"]}
        }),
    }
    old_observation_counts = {
        run_id: db.observations.count_documents({"run_id": run_id})
        for run_id in OLD_RUNS
    }
    old_raw_counts = {
        run_id: db.raw.count_documents({"run_id": run_id})
        for run_id in OLD_RUNS
    }

    observation_product_ids = set(db.observations.distinct("product_id"))
    observation_seller_ids = set(db.observations.distinct("seller_id"))
    canonical_product_ids = set(db.observations.distinct("canonical.product_id"))
    mongo_source_observation_ids = set(db.observations.distinct("source_observation_id"))

    products = list(db.products.find({}, {
        "id": 1,
        "external_id": 1,
        "parent_id": 1,
        "marketplace": 1,
        "currency": 1,
        "title": 1,
        "category": 1,
        "condition": 1,
        "brand": 1,
        "canonical_product_id": 1,
    }))
    product_key_sample = sorted(db.products.find_one({}).keys())
    summary_ids = set(db.product_summaries.distinct("id"))
    analyses = list(db.analyses.find({}, {"product_id": 1, "synthetic": 1, "version": 1}))
    jobs = list(db.jobs.find({}, {
        "id": 1,
        "product_id": 1,
        "state": 1,
        "kind": 1,
        "mode": 1,
        "run_id": 1,
    }))
    sellers = list(db.sellers.find({}, {"id": 1, "marketplace": 1, "name": 1}))
    seller_key_sample = sorted(db.sellers.find_one({}).keys()) if sellers else []
    backup_products = list(db["_repair_backup_20261101_173817_products"].find({}, {
        "id": 1, "external_id": 1, "marketplace": 1, "currency": 1,
    }))
    backup_observation_product_ids = set(
        db["_repair_backup_20261101_173817_observations"].distinct("product_id")
    )
    backup_job_product_ids = set(
        db["_repair_backup_20261101_173817_jobs"].distinct("product_id")
    )
    quarantine_docs = list(db.quarantine.find({}, {
        "run_id": 1, "reason": 1, "source": 1, "product_id": 1, "marketplace": 1, "currency": 1,
    }))
    quarantine_keys = sorted(db.quarantine.find_one({}).keys()) if quarantine_docs else []

    analysis_counts = Counter(doc.get("product_id") for doc in analyses)
    analysis_synthetic = Counter(bool(doc.get("synthetic")) for doc in analyses)
    job_counts = Counter(doc.get("product_id") for doc in jobs)
    job_states = Counter(doc.get("state") for doc in jobs)
    job_run_ids = Counter(str(doc.get("run_id")) for doc in jobs)
    seller_ids = {doc.get("id") for doc in sellers}
    catalogue_ids = {doc.get("id") for doc in products}
    catalogue_external_ids = {doc.get("external_id") for doc in products}
    external_to_product = {}
    for doc in products:
        external_to_product.setdefault(doc.get("external_id"), set()).add(doc.get("id"))
    backup_ids = {doc.get("id") for doc in backup_products}
    backup_external_pairs = {
        (doc.get("marketplace"), doc.get("external_id")) for doc in backup_products
    }

    hash_ids = set()
    for marketplace in sources["marketplaces"]:
        hash_ids.update(hashed_application_ids(sources["product_ids"], marketplace))
    hash_collision_with_catalogue = sorted(hash_ids & catalogue_ids)
    hash_collision_with_observations = sorted(hash_ids & observation_product_ids)

    classes = Counter()
    rows = []
    for doc in products:
        product_id = doc.get("id")
        external_id = doc.get("external_id")
        evidence = []
        inr_id_match = product_id in observation_product_ids or product_id in source_product_ids
        inr_external_match = (
            external_id in observation_product_ids or external_id in source_product_ids
        )
        inr_canonical_field_match = product_id in canonical_product_ids
        if inr_id_match:
            evidence.append("product.id matches an INR observation or source product_id")
        if inr_external_match:
            evidence.append("external_id matches an INR observation or source product_id")
        if inr_canonical_field_match:
            evidence.append("product.id matches canonical.product_id on an observation")
        old_shape = doc.get("marketplace") == "amazon.com" and doc.get("currency") == "USD"
        if old_shape:
            evidence.append("catalogue document is amazon.com / USD")
        elif doc.get("currency") == "INR":
            evidence.append("catalogue document currency is INR")
        else:
            evidence.append(
                f"catalogue marketplace={doc.get('marketplace')} currency={doc.get('currency')}"
            )

        summary_hit = product_id in summary_ids
        analysis_count = analysis_counts.get(product_id, 0)
        job_count = job_counts.get(product_id, 0)
        backup_product_hit = product_id in backup_ids
        backup_observation_hit = product_id in backup_observation_product_ids
        backup_job_hit = product_id in backup_job_product_ids
        backup_external_hit = (doc.get("marketplace"), external_id) in backup_external_pairs
        parent_id = doc.get("parent_id")
        parent_targets = sorted(external_to_product.get(parent_id, set())) if parent_id else []

        linked_to_inr = inr_id_match or inr_external_match or inr_canonical_field_match
        identifiers_present = bool(product_id) and bool(doc.get("marketplace")) and bool(doc.get("currency"))
        only_old_dependents = (
            not linked_to_inr
            and old_shape
            and identifiers_present
            and analysis_counts.get(product_id, 0) == analysis_count
        )
        if linked_to_inr and old_shape:
            classification = "C_shared"
            evidence.append("same document matches both the old USD catalogue shape and an INR identifier")
        elif linked_to_inr and not old_shape:
            classification = "A_new_inr"
            evidence.append("identifier matches the new INR dataset")
        elif only_old_dependents:
            classification = "B_old_only"
            evidence.append("no exact INR identifier match; dependents use this old catalogue id only")
        else:
            classification = "D_unresolved"
            evidence.append("ownership evidence is incomplete or conflicting")
        classes[classification] += 1
        rows.append({
            "mongo_id": str(doc["_id"]),
            "product_id": product_id,
            "marketplace": doc.get("marketplace"),
            "external_id": external_id,
            "parent_id": parent_id,
            "parent_resolves_to_product_ids": parent_targets,
            "currency": doc.get("currency"),
            "category": doc.get("category"),
            "condition": doc.get("condition"),
            "title": doc.get("title"),
            "canonical_product_id": doc.get("canonical_product_id"),
            "brand": doc.get("brand"),
            "observation_count": 0 if product_id not in observation_product_ids else None,
            "observations_by_product_id": int(product_id in observation_product_ids),
            "observations_by_external_id": int(external_id in observation_product_ids),
            "associated_seller_ids": [],
            "summary_match": summary_hit,
            "analysis_count": analysis_count,
            "job_count": job_count,
            "backup_product_id_match": backup_product_hit,
            "backup_observation_product_id_match": backup_observation_hit,
            "backup_job_product_id_match": backup_job_hit,
            "backup_marketplace_external_id_match": backup_external_hit,
            "classification": classification,
            "evidence": evidence,
        })

    # Exact observation counts are zero for non-matching IDs. Record the
    # positive case explicitly instead of leaving it unknown.
    observation_counts = {}
    if observation_product_ids & catalogue_ids:
        for row in db.observations.aggregate([
            {"$match": {"product_id": {"$in": sorted(observation_product_ids & catalogue_ids)}}},
            {"$group": {"_id": "$product_id", "n": {"$sum": 1}}},
        ], allowDiskUse=True):
            observation_counts[row["_id"]] = row["n"]
    seller_links = {}
    if observation_counts:
        for row in db.observations.aggregate([
            {"$match": {"product_id": {"$in": list(observation_counts)}}},
            {"$group": {"_id": "$product_id", "sellers": {"$addToSet": "$seller_id"}}},
        ], allowDiskUse=True):
            seller_links[row["_id"]] = sorted(str(item) for item in row["sellers"] if item)
    for row in rows:
        product_id = row["product_id"]
        row["observation_count"] = observation_counts.get(product_id, 0)
        row["associated_seller_ids"] = seller_links.get(product_id, [])

    analysis_ids_outside_catalogue = sorted(
        str(doc.get("product_id")) for doc in analyses
        if doc.get("product_id") not in catalogue_ids
    )
    analysis_ids_in_inr = sorted(
        str(doc.get("product_id")) for doc in analyses
        if doc.get("product_id") in source_product_ids
        or doc.get("product_id") in observation_product_ids
    )
    job_ids_outside_catalogue = sorted(
        str(doc.get("product_id")) for doc in jobs
        if doc.get("product_id") not in catalogue_ids
    )
    job_ids_in_inr = sorted(
        str(doc.get("product_id")) for doc in jobs
        if doc.get("product_id") in source_product_ids
        or doc.get("product_id") in observation_product_ids
    )
    summary_outside = sorted(summary_ids - catalogue_ids)
    summary_in_inr = sorted(summary_ids & (source_product_ids | observation_product_ids))

    source_match = {
        "source_file_rows": len(sources["observation_ids"]),
        "source_unique_observation_ids": len(source_observation_ids),
        "source_duplicate_observation_ids": sources["duplicate_observation_ids_in_sources"],
        "source_unique_products": len(source_product_ids),
        "source_unique_sellers": len(source_seller_ids),
        "source_unique_listings": len(sources["listing_ids"]),
        "mongo_distinct_source_observation_ids": len(mongo_source_observation_ids),
        "matching_source_observation_ids": len(
            mongo_source_observation_ids & source_observation_ids
        ),
        "source_ids_missing_from_mongo_references": sorted(
            source_observation_ids - mongo_source_observation_ids
        ),
        "mongo_source_ids_not_in_files": sorted(
            mongo_source_observation_ids - source_observation_ids
        ),
        "source_frequency": [
            {"rows_per_source_id": row["_id"], "source_ids": row["source_ids"]}
            for row in source_id_frequency
        ],
        "blank_source_observation_ids": blank_source_ids,
        "duplicate_stored_observation_ids": duplicate_observation_ids[
            "duplicate_observation_ids"
        ],
        "non_historical_observation_counts": non_historical,
        "source_product_ids_match_observation_product_ids": (
            source_product_ids == observation_product_ids
        ),
        "source_products_missing_from_observations": sorted(
            source_product_ids - observation_product_ids
        ),
        "observation_products_missing_from_sources": sorted(
            observation_product_ids - source_product_ids
        ),
        "canonical_product_ids_match_product_id_field": (
            canonical_product_ids == observation_product_ids
        ),
    }

    seller_summary = {
        "catalogue_sellers": len(seller_ids),
        "observation_sellers": len(observation_seller_ids),
        "source_sellers": len(source_seller_ids),
        "sellers_in_both_catalogue_and_inr_observations": len(
            seller_ids & observation_seller_ids
        ),
        "old_catalogue_sellers_absent_from_inr_observations": len(
            seller_ids - observation_seller_ids
        ),
        "inr_observation_sellers_absent_from_catalogue": len(
            observation_seller_ids - seller_ids
        ),
        "source_sellers_match_observation_sellers": (
            source_seller_ids == observation_seller_ids
        ),
        "seller_document_keys": seller_key_sample,
        "seller_marketplaces": dict(Counter(doc.get("marketplace") for doc in sellers)),
    }

    summary = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "database_names_seen": database_names,
        "database_audited": DB_NAME,
        "writes_performed": False,
        "collection_counts": counts,
        "inr_run": run,
        "run_documents": run_ids,
        "observation_groups": observation_groups,
        "old_run_observation_counts": old_observation_counts,
        "old_run_raw_counts": old_raw_counts,
        "raw_count": counts.get("raw", 0),
        "product_document_keys": product_key_sample,
        "classification_counts": dict(classes),
        "catalogue_products": len(products),
        "catalogue_marketplaces": dict(Counter(doc.get("marketplace") for doc in products)),
        "catalogue_currencies": dict(Counter(doc.get("currency") for doc in products)),
        "observation_product_ids": len(observation_product_ids),
        "catalogue_ids_overlapping_inr_observations": sorted(
            catalogue_ids & observation_product_ids
        ),
        "catalogue_external_ids_overlapping_inr_products": sorted(
            str(item) for item in catalogue_external_ids
            if item in source_product_ids or item in observation_product_ids
        ),
        "application_hash_collision_with_catalogue": hash_collision_with_catalogue,
        "application_hash_collision_with_observations": hash_collision_with_observations,
        "source_match": {
            key: value
            for key, value in source_match.items()
            if not key.endswith("_from_mongo_references")
            and not key.endswith("_not_in_files")
            and not key.endswith("_from_observations")
            and not key.endswith("_from_sources")
        },
        "source_match_exceptions": {
            "source_ids_missing_from_mongo_references": source_match[
                "source_ids_missing_from_mongo_references"
            ],
            "mongo_source_ids_not_in_files": source_match["mongo_source_ids_not_in_files"],
            "source_products_missing_from_observations": source_match[
                "source_products_missing_from_observations"
            ],
            "observation_products_missing_from_sources": source_match[
                "observation_products_missing_from_sources"
            ],
        },
        "source_files": sources["per_file"],
        "analyses": {
            "documents": len(analyses),
            "distinct_product_ids": len(analysis_counts),
            "synthetic_counts": {str(key): value for key, value in analysis_synthetic.items()},
            "product_ids_outside_catalogue": analysis_ids_outside_catalogue,
            "product_ids_matching_inr": analysis_ids_in_inr,
        },
        "jobs": {
            "documents": len(jobs),
            "states": {str(key): value for key, value in job_states.items()},
            "run_id_values": {str(key): value for key, value in job_run_ids.items()},
            "product_ids_outside_catalogue": job_ids_outside_catalogue,
            "product_ids_matching_inr": job_ids_in_inr,
        },
        "summaries": {
            "documents": len(summary_ids),
            "ids_outside_catalogue": summary_outside,
            "ids_matching_inr": summary_in_inr,
        },
        "sellers": seller_summary,
        "quarantine": {
            "documents": len(quarantine_docs),
            "keys": quarantine_keys,
            "run_ids": dict(Counter(str(doc.get("run_id")) for doc in quarantine_docs)),
            "sample": [
                {key: doc.get(key) for key in ("run_id", "reason", "source", "product_id", "marketplace", "currency")}
                for doc in quarantine_docs[:3]
            ],
        },
        "backups": {
            "products": len(backup_products),
            "product_id_overlap_with_current_catalogue": len(backup_ids & catalogue_ids),
            "observation_product_id_overlap_with_current_catalogue": len(
                backup_observation_product_ids & catalogue_ids
            ),
            "job_product_id_overlap_with_current_catalogue": len(
                backup_job_product_ids & catalogue_ids
            ),
            "marketplace_external_overlap_with_current_catalogue": sum(
                1 for doc in products
                if (doc.get("marketplace"), doc.get("external_id")) in backup_external_pairs
            ),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk_path = OUT_DIR / "product_ownership_crosswalk.jsonl"
    with crosswalk_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    (OUT_DIR / "product_ownership_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    old_rows = [row for row in rows if row["classification"] == "B_old_only"]
    (OUT_DIR / "old_only_product_ids.txt").write_text(
        "\n".join(row["product_id"] for row in old_rows) + ("\n" if old_rows else ""),
        encoding="utf-8",
    )
    (OUT_DIR / "old_only_mongo_ids.txt").write_text(
        "\n".join(row["mongo_id"] for row in old_rows) + ("\n" if old_rows else ""),
        encoding="utf-8",
    )
    print(json.dumps({
        "classification_counts": dict(classes),
        "collections": counts,
        "source_observation_match": source_match["matching_source_observation_ids"],
        "source_unique": source_match["source_unique_observation_ids"],
        "mongo_source_ids": source_match["mongo_distinct_source_observation_ids"],
        "non_historical": non_historical,
        "inr_run_status": None if not run else run.get("status"),
        "hash_collisions": len(hash_collision_with_catalogue),
        "seller_overlap": seller_summary["sellers_in_both_catalogue_and_inr_observations"],
        "analysis_inr": len(analysis_ids_in_inr),
        "job_inr": len(job_ids_in_inr),
    }, indent=2))


if __name__ == "__main__":
    main()
