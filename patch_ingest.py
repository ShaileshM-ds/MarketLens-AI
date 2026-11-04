from pathlib import Path

path = Path("backend/app/services/normalize.py")
source = path.read_text(encoding="utf-8")

marker = "def ingest(db, rows, mode, run, mapping=None, queue_analysis=True):"

if marker not in source:
    raise SystemExit("ERROR: ingest function marker not found.")

if "def _ingest_bulk(" in source:
    raise SystemExit("ERROR: bulk helper already exists; no changes made.")

helper = r'''

def _ingest_bulk(db, rows, mode, run, mapping=None, queue_analysis=False):
    """Batch ingestion used by historical generation."""
    from pymongo import UpdateOne
    from pymongo.errors import BulkWriteError

    counts = dict(accepted=0, duplicates=0, rejected=0, warnings=0)
    affected = set()

    batch_size = 250
    raw_ops = {}
    observations = []
    quarantine = []

    def flush():
        nonlocal raw_ops, observations, quarantine

        if not observations and not raw_ops and not quarantine:
            return

        if raw_ops:
            db.raw.bulk_write(list(raw_ops.values()), ordered=False)

        successful = []

        if observations:
            docs = [entry[1] for entry in observations]

            try:
                db.observations.insert_many(docs, ordered=False)
                successful = list(range(len(observations)))

            except BulkWriteError as exc:
                errors = exc.details.get('writeErrors', [])
                non_duplicates = [
                    error for error in errors
                    if error.get('code') != 11000
                ]

                if non_duplicates:
                    raise

                failed_indices = {
                    error['index'] for error in errors
                }

                counts['duplicates'] += len(errors)

                successful = [
                    index for index in range(len(observations))
                    if index not in failed_indices
                ]

        product_ops = {}
        seller_ops = {}

        for index in successful:
            p, o, candidate = observations[index]

            product_ops[p['id']] = UpdateOne(
                {'id': p['id']},
                {'$set': p},
                upsert=True
            )

            if o['seller_id']:
                offer_details = o.get('seller_offer_details') or {}
                id_type = (
                    offer_details.get('seller_id_type')
                    or seller_id_type(candidate)
                )

                seller_doc = {
                    'id': o['seller_id'],
                    'name': o['seller_name'],
                    'seller_id_type': id_type,
                }

                seller_ops[o['seller_id']] = UpdateOne(
                    {'id': o['seller_id']},
                    {'$set': seller_doc},
                    upsert=True
                )

            counts['accepted'] += 1
            counts['warnings'] += bool(o['quality_flags'])
            affected.add(p['id'])

        if product_ops:
            db.products.bulk_write(
                list(product_ops.values()),
                ordered=False
            )

        if seller_ops:
            db.sellers.bulk_write(
                list(seller_ops.values()),
                ordered=False
            )

        if quarantine:
            db.quarantine.insert_many(quarantine, ordered=False)

        raw_ops = {}
        observations = []
        quarantine = []

    for raw in rows:
        source_raw = (
            raw.get('_original', raw)
            if isinstance(raw, dict)
            else raw
        )

        raw_id = digest(source_raw)

        raw_ops[raw_id] = UpdateOne(
            {'id': raw_id},
            {'$setOnInsert': {
                'id': raw_id,
                'record': source_raw,
                'run_id': run,
            }},
            upsert=True
        )

        candidates = [raw]

        if isinstance(raw, dict):
            candidates.extend(expand_additional_sellers(raw))

            offers = parse_additional_sellers(
                raw.get('other_sellers_prices')
            )

            if offers:
                identifiers = set()
                main_id = seller_identifier(raw)

                if main_id:
                    identifiers.add(main_id)

                for offer in offers:
                    offer_id = (
                        seller_id_from_url(offer.get('seller_url'))
                        or offer.get('seller_name')
                    )

                    if offer_id and offer.get('price') not in ('', ''):
                        raw_id_val = seller_id_from_url(
                            offer.get('seller_url')
                        )

                        if raw_id_val:
                            identifiers.add(raw_id_val.strip())
                        else:
                            identifiers.add(
                                _normalise_name(str(offer_id))
                            )

                for candidate in candidates:
                    candidate['_derived_seller_count'] = len(identifiers)

        for candidate in candidates:
            try:
                p, o = normalize(candidate, mode, run, mapping)
                o['raw_ref'] = raw_id
                observations.append((p, o, candidate))

            except (ValueError, TypeError, KeyError) as exc:
                counts['rejected'] += 1
                quarantine.append({
                    'run_id': run,
                    'raw_ref': raw_id,
                    'reason': str(exc),
                })

        if len(raw_ops) >= batch_size:
            flush()

    flush()

    db.runs.update_one(
        {'id': run},
        {'$set': dict(
            id=run,
            mode=mode,
            synthetic=mode == 'demo',
            finished_at=_now(),
            **counts
        )},
        upsert=True
    )

    if queue_analysis:
        queue_analysis_jobs(db, affected, mode, run)

    return counts

'''

source = source.replace(marker, helper + "\n" + marker, 1)

branch = (
    marker + "\n"
    "    if not queue_analysis:\n"
    "        return _ingest_bulk(db, rows, mode, run, mapping, queue_analysis)\n"
)

source = source.replace(marker, branch.rstrip(), 1)

path.write_text(source, encoding="utf-8")
print("SUCCESS: Added batched historical ingestion.")
