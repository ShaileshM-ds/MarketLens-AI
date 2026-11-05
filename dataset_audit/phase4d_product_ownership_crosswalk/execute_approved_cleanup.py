"""Approved Phase 4D cleanup for marketlens_imported.

Deletes only the exact MongoDB _id values in the Phase 4D ID lists.
Refuses to delete unless the fresh backup counts and live preflight match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId, decode_file_iter
from pymongo import MongoClient
from pymongo.read_preferences import ReadPreference

BASE = Path(__file__).resolve().parent
URI = "mongodb://127.0.0.1:27018"
DB_NAME = "marketlens_imported"
INR_RUN = "history-inr-120d-v1"
PROTECTED = (
    "observations",
    "raw",
    "runs",
    "_repair_backup_20261101_173817_jobs",
    "_repair_backup_20261101_173817_observations",
    "_repair_backup_20261101_173817_products",
)
TARGETS = (
    ("jobs", "old_only_jobs.jsonl", 466),
    ("analyses", "old_only_analyses.jsonl", 3004),
    ("product_summaries", "old_only_summaries.jsonl", 6342),
    ("products", "old_only_mongo_ids.txt", 6342),
    ("sellers", "old_only_sellers.jsonl", 4013),
    ("quarantine", "old_only_quarantine.jsonl", 123),
)
UNTOUCHED_DBS = ("marketlens_demo", "marketlens_live")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_ids() -> dict[str, list[ObjectId]]:
    loaded: dict[str, list[ObjectId]] = {}
    errors: list[str] = []
    for collection, filename, expected in TARGETS:
        path = BASE / filename
        if not path.is_file():
            errors.append(f"missing ID list {path}")
            continue
        rows = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        values: list[str] = []
        for row in rows:
            if filename.endswith(".txt"):
                values.append(row)
            else:
                values.append(str(json.loads(row)["mongo_id"]))
        if len(values) != expected:
            errors.append(f"{filename} has {len(values)} ids; expected {expected}")
        if len(values) != len(set(values)):
            errors.append(f"{filename} contains duplicate ids")
        try:
            loaded[collection] = [ObjectId(value) for value in values]
        except Exception as exc:
            errors.append(f"{filename} has an invalid ObjectId: {exc}")
    if errors:
        raise SystemExit("ID list check failed:\n" + "\n".join(errors))
    return loaded


def count_bson(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for _ in decode_file_iter(handle):
            count += 1
    return count


def verify_backup(backup_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    errors: list[str] = []
    dump_root = backup_dir / "marketlens_imported"
    if not dump_root.is_dir():
        raise SystemExit(f"Backup directory is missing {dump_root}")
    for collection, _filename, expected in TARGETS:
        bson_path = dump_root / f"{collection}.bson"
        metadata_path = dump_root / f"{collection}.metadata.json"
        if not bson_path.is_file() or bson_path.stat().st_size <= 0:
            errors.append(f"missing or empty backup file {bson_path}")
            continue
        if not metadata_path.is_file():
            errors.append(f"missing backup metadata {metadata_path}")
        count = count_bson(bson_path)
        counts[collection] = count
        if count != expected:
            errors.append(f"backup {collection} has {count} documents; expected {expected}")
    dry_run = backup_dir / "mongorestore_dryrun.txt"
    if not dry_run.is_file():
        errors.append("mongorestore dry-run output is missing")
    else:
        text = dry_run.read_text(encoding="utf-8", errors="replace")
        if "dry run completed" not in text or "0 document(s) failed to restore" not in text:
            errors.append("mongorestore dry-run output does not show a completed dry run")
        for collection, _filename, _expected in TARGETS:
            needle = f"found collection `{DB_NAME}.{collection}` bson to restore to `{DB_NAME}.{collection}`"
            if needle not in text:
                errors.append(f"dry-run output does not recognize {DB_NAME}.{collection}")
    if errors:
        raise SystemExit("Backup verification failed:\n" + "\n".join(errors))
    return counts


def worker_status() -> str:
    completed = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=marketlens-ai-worker-1", "--format", "{{.Status}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def require_worker_stopped() -> str:
    status = worker_status()
    if not status.startswith("Exited"):
        raise SystemExit(f"Worker is not stopped: {status!r}")
    return status


def connect():
    client = MongoClient(
        URI,
        serverSelectionTimeoutMS=8000,
        socketTimeoutMS=600000,
        read_preference=ReadPreference.PRIMARY,
    )
    client.admin.command("ping")
    database = client[DB_NAME]
    if database.name != DB_NAME:
        raise SystemExit(f"Refusing unexpected database {database.name}")
    return client, database


def collection_counts(database) -> dict[str, int]:
    return {
        name: database[name].count_documents({})
        for name in sorted(database.list_collection_names())
    }


def other_database_counts(client) -> dict[str, dict[str, int]]:
    snapshot: dict[str, dict[str, int]] = {}
    for name in UNTOUCHED_DBS:
        database = client[name]
        snapshot[name] = {
            collection: database[collection].count_documents({})
            for collection in sorted(database.list_collection_names())
        }
    return snapshot


def observation_integrity(database) -> dict:
    observations = database.observations
    run = database.runs.find_one({"id": INR_RUN}, {"_id": 0})
    return {
        "observations": observations.count_documents({}),
        "inr_run_id": observations.count_documents({"run_id": INR_RUN}),
        "currency_inr": observations.count_documents({"currency": "INR"}),
        "synthetic_true": observations.count_documents({"synthetic": True}),
        "generated_history": observations.count_documents({"source_type": "generated_history"}),
        "non_inr_currency": observations.count_documents({"currency": {"$ne": "INR"}}),
        "other_run_id": observations.count_documents({"run_id": {"$ne": INR_RUN}}),
        "run_document": run,
    }


def live_id_check(database, ids: dict[str, list[ObjectId]]) -> list[str]:
    errors: list[str] = []
    for collection, _filename, expected in TARGETS:
        values = ids[collection]
        found = database[collection].count_documents({"_id": {"$in": values}})
        total = database[collection].count_documents({})
        if found != expected or total != expected or len(values) != expected:
            errors.append(
                f"{collection}: listed={len(values)} matched={found} live_total={total} expected={expected}"
            )
    return errors


def preservation_errors(before: dict, after: dict) -> list[str]:
    errors: list[str] = []
    for name in PROTECTED:
        if before["imported"].get(name) != after["imported"].get(name):
            errors.append(
                f"{name} changed from {before['imported'].get(name)} to {after['imported'].get(name)}"
            )
    before_names = set(before["imported"])
    after_names = set(after["imported"])
    if before_names != after_names:
        errors.append(f"collection set changed: added={sorted(after_names - before_names)} removed={sorted(before_names - after_names)}")
    for name in sorted(before_names & after_names):
        if name in {item[0] for item in TARGETS}:
            continue
        if before["imported"][name] != after["imported"][name]:
            errors.append(
                f"untargeted {name} changed from {before['imported'][name]} to {after['imported'][name]}"
            )
    if before["other_databases"] != after["other_databases"]:
        errors.append("marketlens_demo or marketlens_live counts changed")
    if before["observations"] != after["observations"]:
        errors.append("INR observation integrity changed")
    return errors


def snapshot(client, database) -> dict:
    return {
        "taken_at": utc_now(),
        "imported": collection_counts(database),
        "other_databases": other_database_counts(client),
        "observations": observation_integrity(database),
    }


def delete_exact(database, ids: dict[str, list[ObjectId]], results: list[dict]) -> None:
    allowed = {collection for collection, _filename, _expected in TARGETS}
    for collection, _filename, expected in TARGETS:
        if collection not in allowed:
            raise SystemExit(f"Refusing unapproved collection {collection}")
        values = ids[collection]
        deleted = database[collection].delete_many({"_id": {"$in": values}}).deleted_count
        remaining = database[collection].count_documents({})
        results.append({
            "collection": collection,
            "listed": len(values),
            "deleted": deleted,
            "remaining": remaining,
        })
        if deleted != expected or remaining != 0:
            raise SystemExit(
                "Deletion check failed after "
                f"{collection}: deleted={deleted} remaining={remaining} expected_deleted={expected}. "
                "Stopped. No further collections were deleted by this step."
            )
    return results


def expected_post_counts(before: dict[str, int]) -> dict[str, int]:
    expected = dict(before)
    for collection, _filename, _count in TARGETS:
        expected[collection] = 0
    return expected


def write_log(payload: dict) -> None:
    path = BASE / "phase4d_cleanup_execution_log.txt"
    lines = [
        "MarketLens-AI Phase 4D cleanup execution log",
        "============================================",
        f"Started: {payload['started_at']}",
        f"Finished: {payload['finished_at']}",
        f"Outcome: {payload['outcome']}",
        f"Database: {DB_NAME}",
        f"Worker status before: {payload.get('worker_before')}",
        f"Worker status after: {payload.get('worker_after')}",
        "",
        "Backup",
        "------",
        f"Location: {payload.get('backup_dir')}",
        f"mongodump document counts: {json.dumps(payload.get('backup_counts', {}))}",
        f"Dry-run file: {payload.get('dry_run_file')}",
        "",
        "ID lists",
        "--------",
    ]
    for item in payload.get("id_files", []):
        lines.append(
            f"{item['file']}: ids={item['ids']} sha256={item['sha256']}"
        )
    lines.extend([
        "",
        "Deleted counts",
        "--------------",
    ])
    for item in payload.get("deleted", []):
        lines.append(
            f"{item['collection']}: listed={item['listed']} deleted={item['deleted']} remaining={item['remaining']}"
        )
    lines.extend([
        "",
        "Post-deletion counts",
        "--------------------",
        json.dumps(payload.get("post_counts", {}), indent=2),
        "",
        "Preservation",
        "------------",
    ])
    for item in payload.get("preservation", []):
        lines.append(item)
    if payload.get("errors"):
        lines.extend(["", "Errors", "------", *payload["errors"]])
    if payload.get("warnings"):
        lines.extend(["", "Warnings", "--------", *payload["warnings"]])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    started = utc_now()
    backup_dir = Path(args.backup_dir)
    payload: dict = {
        "started_at": started,
        "backup_dir": str(backup_dir),
        "dry_run_file": str(backup_dir / "mongorestore_dryrun.txt"),
        "warnings": [
            "marketlens-ai-backend-1 and marketlens-ai-frontend-1 were left running.",
            "The worker was not restarted.",
            "No product or seller documents were created.",
        ],
        "id_files": [
            {
                "file": filename,
                "ids": expected,
                "sha256": sha256(BASE / filename),
            }
            for _collection, filename, expected in TARGETS
        ],
    }
    try:
        payload["worker_before"] = require_worker_stopped()
        ids = load_ids()
        payload["backup_counts"] = verify_backup(backup_dir)
        client, database = connect()
        before = snapshot(client, database)
        payload["pre_counts"] = before["imported"]
        payload["pre_observations"] = before["observations"]
        errors = live_id_check(database, ids)
        integrity = before["observations"]
        run = integrity["run_document"] or {}
        if integrity["observations"] != 900000 or integrity["inr_run_id"] != 900000:
            errors.append(f"INR observations are not intact: {integrity}")
        if integrity["non_inr_currency"] != 0 or integrity["other_run_id"] != 0:
            errors.append(f"Active observations contain non-INR rows: {integrity}")
        if run.get("status") != "completed" or run.get("id") != INR_RUN:
            errors.append(f"Run document is not the completed INR run: {run}")
        if errors:
            payload["errors"] = errors
            raise SystemExit("Pre-deletion verification failed:\n" + "\n".join(errors))
        if not args.execute:
            payload["outcome"] = "PREFLIGHT PASSED; NO DELETION"
            payload["finished_at"] = utc_now()
            payload["worker_after"] = worker_status()
            write_log(payload)
            print(json.dumps({"outcome": payload["outcome"], "pre_counts": before["imported"]}, indent=2))
            return

        payload["worker_before_delete"] = require_worker_stopped()
        payload["deleted"] = []
        delete_exact(database, ids, payload["deleted"])
        after = snapshot(client, database)
        payload["post_counts"] = after["imported"]
        payload["post_observations"] = after["observations"]
        payload["worker_after"] = worker_status()
        problems = preservation_errors(before, after)
        expected = expected_post_counts(before["imported"])
        for collection, count in expected.items():
            if after["imported"].get(collection) != count:
                problems.append(
                    f"post count {collection}={after['imported'].get(collection)} expected {count}"
                )
        if not payload["worker_after"].startswith("Exited"):
            problems.append(f"worker status changed: {payload['worker_after']}")
        payload["preservation"] = [
            "observations, runs, raw, and _repair_backup_* counts match the pre-deletion snapshot",
            "marketlens_demo and marketlens_live collection counts match the pre-deletion snapshot",
            "all 900,000 observations remain run_id=history-inr-120d-v1, currency=INR, synthetic=true",
            "history-inr-120d-v1 remains completed",
        ] if not problems else []
        payload["errors"] = problems
        if problems:
            payload["outcome"] = "DELETED WITH VERIFICATION FAILURE"
            raise SystemExit("Post-deletion verification failed:\n" + "\n".join(problems))
        payload["outcome"] = "SUCCESS"
        payload["finished_at"] = utc_now()
        write_log(payload)
        print(json.dumps({
            "outcome": payload["outcome"],
            "deleted": payload["deleted"],
            "post_counts": payload["post_counts"],
        }, indent=2))
    except SystemExit as exc:
        payload["outcome"] = payload.get("outcome") or "STOPPED WITHOUT SUCCESS"
        payload["finished_at"] = utc_now()
        payload["errors"] = payload.get("errors") or [str(exc)]
        try:
            payload["worker_after"] = worker_status()
        except Exception as worker_error:
            payload["worker_after"] = f"unavailable: {worker_error}"
        write_log(payload)
        print(str(exc), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
