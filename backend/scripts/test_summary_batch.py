import time
import os
import resource
from app.db import database
from app.services.summaries import compute_product_summary

def get_mem_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

mem_before = get_mem_mb()

db = database('imported')
print(f"Testing on marketlens_imported...")
products = list(db.products.find({}).limit(10))
print(f"Fetched 10 product records.")

t0 = time.time()
for i, p in enumerate(products):
    t_start = time.time()
    summary = compute_product_summary(db, p)
    t_end = time.time()
    print(f"[{i+1}/10] {p['id']}: price={summary['current']}, days={summary['days']}, score={summary['discount'].get('score')}, obs={summary['observations_count']} in {(t_end - t_start)*1000:.1f}ms")

t_total = time.time() - t0
mem_after = get_mem_mb()

print(f"\n10 products processed in {t_total*1000:.1f}ms (Average: {t_total*100:.1f}ms/product)")
print(f"Memory: max_rss={mem_after:.1f}MB, diff={mem_after - mem_before:.2f}MB")
