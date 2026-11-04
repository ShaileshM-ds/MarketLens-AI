import sys
import time
from app.db import database, now
from app.services.summaries import (
    ensure_summary_indexes,
    build_summaries_batch,
    verify_summaries
)

def run_sync(mode='imported', batch_size=50, force=False):
    db = database(mode)
    print(f"[{now()}] Ensuring indexes on {mode}.product_summaries...")
    ensure_summary_indexes(db)

    total_products = db.products.count_documents({})
    print(f"[{now()}] Total products in catalog: {total_products}")

    if not force:
        existing_ids = set(db.product_summaries.distinct('id'))
        print(f"[{now()}] Found {len(existing_ids)} existing summaries. Skipping already processed products.")
        query = {'id': {'$nin': list(existing_ids)}}
    else:
        print(f"[{now()}] Force mode enabled: recalculating all summaries.")
        query = {}

    to_process = db.products.count_documents(query)
    print(f"[{now()}] Products to process: {to_process}")

    if to_process == 0:
        print(f"[{now()}] All product summaries are up to date.")
        v = verify_summaries(db, sample_size=20)
        print(f"[{now()}] Verification: verified={v['verified']}, sample_checked={v['checked']}")
        return

    cursor = db.products.find(query).batch_size(batch_size)
    batch = []
    processed = 0
    t0 = time.time()

    for p in cursor:
        batch.append(p)
        if len(batch) >= batch_size:
            build_summaries_batch(db, batch)
            processed += len(batch)
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            pct = (processed / to_process) * 100
            print(f"[{now()}] Progress: {processed}/{to_process} ({pct:.1f}%) - {rate:.1f} prods/sec", flush=True)
            batch = []

    if batch:
        build_summaries_batch(db, batch)
        processed += len(batch)

    elapsed = time.time() - t0
    print(f"[{now()}] Completed {processed} products in {elapsed:.1f}s ({processed/elapsed:.1f} prods/sec)")

    # Final verification
    print(f"[{now()}] Running verification on random sample of 25 products...")
    v = verify_summaries(db, sample_size=25)
    print(f"[{now()}] Verification result: verified={v['verified']}, checked={v['checked']}, mismatches={len(v['mismatches'])}")
    if v['mismatches']:
        print("Mismatches found:", v['mismatches'])

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'imported'
    force = '--force' in sys.argv
    run_sync(mode=mode, force=force)
