"""Import a materialized history CSV into the imported-data database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Historical CSV path")
    parser.add_argument("--run-id", required=True, help="Distinct import run ID")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    print(
        "This will insert historical observations into marketlens_imported. "
        "Existing conflicting identities are rejected; matching rows are skipped."
    )
    if input('Type YES to continue: ') != "YES":
        print("Cancelled. No database changes made.")
        return 0

    from app.db import database
    from app.services.historical_csv_importer import import_historical_csv

    summary = import_historical_csv(
        database("imported"),
        args.csv,
        args.run_id,
        batch_size=args.batch_size,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
