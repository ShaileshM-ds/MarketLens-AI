import time
from app.db import database
from app.services.summaries import build_summaries_batch, verify_summaries, ensure_summary_indexes

db = database('imported')
ensure_summary_indexes(db)

products = list(db.products.find({}).limit(10))
count = build_summaries_batch(db, products)
print(f"Upserted {count} summaries.")

v = verify_summaries(db, sample_size=10)
print(f"Verification result: verified={v['verified']}, checked={v['checked']}, mismatches={len(v['mismatches'])}")
if v['mismatches']:
    print("Mismatches:", v['mismatches'])
