"""Read-only export of old-catalogue dependent document IDs."""

from __future__ import annotations

import json
from pathlib import Path

from pymongo import MongoClient
from pymongo.read_preferences import ReadPreference

OUT = Path(__file__).resolve().parent
DB_NAME = "marketlens_imported"


def dump(path: Path, docs, fields: tuple[str, ...]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for doc in docs:
            payload = {field: doc.get(field) for field in fields}
            payload["mongo_id"] = str(doc["_id"])
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            count += 1
    return count


def main() -> None:
    client = MongoClient(
        "mongodb://127.0.0.1:27018",
        serverSelectionTimeoutMS=8000,
        read_preference=ReadPreference.PRIMARY,
    )
    db = client[DB_NAME]
    if db.name != DB_NAME:
        raise RuntimeError(db.name)

    marketplace = list(db.observations.aggregate([
        {"$group": {
            "_id": {
                "top_level": "$marketplace",
                "canonical": "$canonical.marketplace",
            },
            "n": {"$sum": 1},
        }},
    ], allowDiskUse=True))
    print("MARKETPLACE", json.dumps(marketplace, default=str))
    print("SUMMARIES", dump(
        OUT / "old_only_summaries.jsonl",
        db.product_summaries.find({}, {"id": 1}),
        ("id",),
    ))
    print("ANALYSES", dump(
        OUT / "old_only_analyses.jsonl",
        db.analyses.find({}, {"product_id": 1, "synthetic": 1, "version": 1}),
        ("product_id", "synthetic", "version"),
    ))
    print("JOBS", dump(
        OUT / "old_only_jobs.jsonl",
        db.jobs.find({}, {"id": 1, "product_id": 1, "state": 1, "kind": 1, "mode": 1, "run_id": 1}),
        ("id", "product_id", "state", "kind", "mode", "run_id"),
    ))
    print("SELLERS", dump(
        OUT / "old_only_sellers.jsonl",
        db.sellers.find({}, {"id": 1, "name": 1, "seller_id_type": 1}),
        ("id", "name", "seller_id_type"),
    ))
    print("QUARANTINE", dump(
        OUT / "old_only_quarantine.jsonl",
        db.quarantine.find({}, {"run_id": 1, "reason": 1, "raw_ref": 1}),
        ("run_id", "reason", "raw_ref"),
    ))
    print("JOB_KINDS", list(db.jobs.aggregate([
        {"$group": {"_id": "$kind", "n": {"$sum": 1}}},
    ])))


if __name__ == "__main__":
    main()
