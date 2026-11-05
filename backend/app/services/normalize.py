
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, urlsplit

from app.review_mode import review_mode_enabled


def _now():
    """Return the current UTC time as an ISO-8601 string.

    This is a local helper so that normalize.py has no import-time
    dependency on app.db (which instantiates a MongoDB client).
    """
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


_CURRENCY_PREFIX_RE = re.compile(
    r'^\s*(?:[A-Z]{2,4}\s+|[\$\Â£\â‚¬\Â¥\â‚¹])\s*',
    re.IGNORECASE
)

_COUPON_NUMERIC_RE = re.compile(
    r'^[\$\Â£\â‚¬\Â¥\â‚¹]?\s*[\d,]+(?:\.\d+)?$'
)


def parse_coupon(value, currency='INR'):
    """Return (numeric_minor_units_or_None, description_or_None)."""
    if value is None or value == '':
        return None, None

    text = str(value).strip()
    if not text:
        return None, None

    stripped = _CURRENCY_PREFIX_RE.sub(
        '', text
    ).replace(',', '').strip()

    if _COUPON_NUMERIC_RE.match(text) or (
        stripped and _COUPON_NUMERIC_RE.match(stripped)
    ):
        try:
            return money(text, currency), None
        except (ValueError, TypeError):
            return None, text

    return None, text


def money(value, currency='INR'):
    if value is None or value == '':
        return None

    if isinstance(value, dict):
        value = value.get('value', value.get('amount'))

    try:
        cleaned = _CURRENCY_PREFIX_RE.sub(
            '', str(value)
        ).replace(',', '').strip()

        d = Decimal(cleaned)

        if not d.is_finite() or d < 0:
            raise ValueError(
                'Price must be finite and nonnegative'
            )

        multiplier = 1 if currency == 'JPY' else 100

        return int(
            (d * multiplier).quantize(
                Decimal('1'),
                rounding=ROUND_HALF_UP
            )
        )

    except (InvalidOperation, TypeError):
        raise ValueError('Invalid numeric money field')


def _offer_details(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def timestamp(value):
    if not value:
        return None

    t = datetime.fromisoformat(
        str(value).replace('Z', '+00:00')
    )

    if t.tzinfo is None:
        raise ValueError(
            'Observation timestamp requires timezone'
        )

    return t.astimezone(timezone.utc).isoformat()


def parse_file(content):
    s = content.lstrip('\ufeff \r\n\t')

    if s.startswith('['):
        rows = json.loads(s)

    elif s.startswith('{'):
        try:
            parsed = json.loads(s)
            rows = parsed if isinstance(parsed, list) else [parsed]

        except json.JSONDecodeError:
            rows = []

            for n, line in enumerate(s.splitlines(), 1):
                if not line.strip():
                    continue

                try:
                    rows.append(json.loads(line))

                except json.JSONDecodeError:
                    rows.append({
                        '_parse_error': (
                            f'Malformed JSONL at line {n}'
                        ),
                        'original_line': line
                    })

    else:
        rows = list(csv.DictReader(io.StringIO(s)))

    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError(
            'Expected at most 20,000 records'
        )

    return rows


def parse_additional_sellers(value):
    """Parse Bright Data's other_sellers_prices field."""

    if isinstance(value, list):
        offers = value

    elif isinstance(value, str) and value.strip():
        try:
            offers = json.loads(value)

        except (json.JSONDecodeError, TypeError):
            return []

    else:
        return []

    if isinstance(offers, dict):
        offers = offers.get('offers', [])

    if not isinstance(offers, list):
        return []

    return [
        offer for offer in offers
        if isinstance(offer, dict)
    ]


def seller_id_from_url(url):
    """Extract an Amazon seller ID when the URL provides one."""

    if not url:
        return None

    try:
        values = parse_qs(
            urlsplit(str(url)).query
        )

        seller_ids = values.get('seller', [])

        return (
            seller_ids[0].strip()
            if seller_ids and seller_ids[0].strip()
            else None
        )

    except (TypeError, ValueError):
        return None


def _normalise_name(name: str) -> str:
    """Normalize seller names used as fallback identities."""
    return name.strip().casefold()


def seller_identifier(record):
    """Return a stable seller identifier."""

    seller_id = record.get('seller_id')

    if seller_id:
        return str(seller_id).strip()

    url_id = seller_id_from_url(
        record.get('seller_url')
    )

    if url_id:
        return url_id

    seller_name = record.get('seller_name')

    return (
        _normalise_name(str(seller_name))
        if seller_name else None
    )


def seller_id_type(record):
    """Classify seller identity."""

    if record.get('seller_id'):
        return 'verified'

    if seller_id_from_url(record.get('seller_url')):
        return 'verified'

    if record.get('seller_name'):
        return 'name_derived'

    return 'unknown'


def _boolean(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    if isinstance(value, str):
        return value.strip().lower() in (
            'true', '1', 'yes'
        )

    return False


def expand_additional_sellers(raw):
    """
    Produce separate records for valid additional seller offers.

    Each expanded record retains a link to the original source record.
    """

    if not isinstance(raw, dict):
        return []

    offers = parse_additional_sellers(
        raw.get('other_sellers_prices')
    )

    if not offers:
        return []

    original = raw.get('_original', raw)
    main_seller = seller_identifier(raw)

    identifiers = (
        {main_seller} if main_seller else set()
    )

    valid_offers = []

    for offer in offers:
        offer_name = offer.get('seller_name')
        offer_id = seller_id_from_url(
            offer.get('seller_url')
        )

        if not offer_id and offer_name:
            offer_id = _normalise_name(
                str(offer_name)
            )

        if not offer_id or offer.get('price') in (None, ''):
            continue

        identifiers.add(offer_id)
        valid_offers.append((offer, offer_id))

    seller_count = len(identifiers)

    expanded = []
    seen = set()

    for offer, offer_id in valid_offers:
        condition = offer.get('condition') or raw.get(
            'condition',
            raw.get('variant_condition', 'unknown')
        )

        dedupe_key = (
            offer_id,
            str(offer.get('price')),
            str(condition).strip().lower(),
            str(offer.get('ships_from') or '')
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        row = dict(raw)
        row['_original'] = original
        row['_is_additional_seller'] = True

        _offer_id_type = (
            'verified'
            if seller_id_from_url(offer.get('seller_url'))
            else 'name_derived'
        )

        row['_offer_details'] = {
            'seller_url': offer.get('seller_url'),
            'seller_rating': offer.get('seller_rating'),
            'seller_review_count': offer.get('num_of_ratings'),
            'ships_from': offer.get('ships_from'),
            'price_per_unit': offer.get('price_per_unit'),
            'unit': offer.get('unit'),
            'condition': condition,
            'seller_id_type': _offer_id_type,
        }

        row['seller_id'] = offer_id
        row['seller_name'] = (
            offer.get('seller_name') or offer_id
        )
        row['seller_url'] = offer.get('seller_url')
        row['price'] = offer.get('price')
        row['condition'] = condition

        row['shipping'] = None
        row['tax_treatment'] = 'unknown'
        row['reference_price'] = None
        row['initial_price'] = None
        row['coupon'] = None
        row['coupon_conditions'] = None
        row['urgency'] = None
        row['countdown'] = None
        row['promotion'] = None
        row['buy_box'] = False
        row['buybox_seller'] = None
        row['seller_count'] = seller_count
        row['delivery'] = offer.get('delivery')
        row['_derived_seller_count'] = seller_count

        expanded.append(row)

    return expanded


def normalize(raw, mode, run, mapping=None):
    if not isinstance(raw, dict):
        raise ValueError('Record is not an object')

    if raw.get('_parse_error'):
        raise ValueError(raw['_parse_error'])

    r = dict(raw)
    offer_details = _offer_details(r.get('_offer_details'))

    for target, source in (mapping or {}).items():
        if source:
            r[target] = raw.get(source)

    external = (
        r.get('external_id')
        or r.get('asin')
    )

    url = (
        r.get('url')
        or r.get('input_url')
        or r.get('origin_url')
    )

    marketplace = r.get('marketplace')

    if not marketplace and url:
        marketplace = urlsplit(
            str(url)
        ).hostname

        if marketplace:
            marketplace = marketplace.removeprefix('www.')

    if not marketplace and r.get('domain'):
        marketplace = str(
            r['domain']
        ).removeprefix('www.')

    if not external or not marketplace:
        raise ValueError(
            'external_id/asin and marketplace are required'
        )

    currency = str(
        r.get('currency', '')
    ).upper()

    if currency not in (
        'INR', 'USD', 'GBP', 'EUR',
        'JPY', 'CAD', 'AUD'
    ):
        raise ValueError(
            'Supported currency is required'
        )

    price = money(
        r.get('price', r.get('final_price')),
        currency
    )

    if price is None or price <= 0:
        raise ValueError('Positive price is required')

    variant = r.get('variant', {})

    condition = (
        r.get('condition')
        or r.get('variant_condition')
        or 'unknown'
    )

    # IMPORTANT:
    # Product identity must not depend on seller or offer condition.
    # Different conditions remain separate observations/offers.
    # Marketplace + external product ID + variant identify the product.
    pid = digest([
        marketplace,
        str(external),
        variant
    ])[:24]

    observed = timestamp(
        r.get('observed_at', r.get('timestamp'))
    )

    shipping = money(
        r.get('shipping'),
        currency
    )

    tax = r.get('tax_treatment', 'unknown')

    seller = seller_identifier(r)
    seller_name = r.get('seller_name') or seller

    buy_box = _boolean(
        r.get('buy_box')
    )

    buybox_seller = r.get('buybox_seller')

    if buybox_seller and seller_name:
        if (
            str(buybox_seller).strip().casefold()
            == str(seller_name).strip().casefold()
        ):
            buy_box = True

    _is_generated = bool(
        r.get('_generated')
    )

    provenance = dict(
        source_type=(
            'generated_history'
            if _is_generated else mode
        ),
        source_id=r.get(
            'source_id',
            'file' if mode == 'imported' else mode
        ),
        run_id=run,
        synthetic=(
            _is_generated or mode == 'demo'
        ),
        observed_at=observed,
        ingested_at=_now(),
        timestamp_quality=(
            'generated' if _is_generated
            else 'explicit' if observed
            else 'missing'
        ),
        schema_version='1'
    )

    _cats = (
        r.get('categories')
        or r.get('category')
    )

    if (
        isinstance(_cats, str)
        and _cats.strip().startswith('[')
    ):
        try:
            _cats = json.loads(_cats)

        except (json.JSONDecodeError, ValueError):
            pass

    if isinstance(_cats, list):
        _cats = next(
            (c for c in _cats if c),
            None
        )

    category = (
        str(_cats).strip()
        if _cats else 'Uncategorized'
    )

    p = dict(
        id=pid,
        external_id=str(external),
        parent_id=r.get('parent_asin'),
        marketplace=marketplace,
        variant=variant,
        condition=condition,
        product_url=url,
        image_url=r.get('image_url'),
        country=r.get('country'),
        title=r.get('title') or str(external),
        brand=r.get('brand', 'Unknown'),
        category=category,
        currency=currency
    )

    reference_value = (
        r.get('reference_price')
        or r.get('initial_price')
    )

    coupon_value, coupon_desc = parse_coupon(
        r.get('coupon'),
        currency
    )

    coupon_description = (
        r.get('coupon_description')
        or coupon_desc
    )

    o = dict(
        product_id=pid,
        seller_id=seller,
        seller_name=seller_name,
        price=price,
        shipping=shipping,
        reference_price=money(
            reference_value,
            currency
        ),
        reference_type=r.get(
            'reference_type',
            'unknown'
        ),
        total=(
            price + shipping
            if shipping is not None and tax == 'included'
            else None
        ),
        tax_treatment=tax,
        coupon=coupon_value,
        coupon_description=coupon_description,
        coupon_conditions=r.get('coupon_conditions'),
        region=r.get('region'),
        conditional_coupon_price=(
            max(0, price - coupon_value)
            if (
                coupon_value is not None
                and r.get('coupon_conditions')
            )
            else None
        ),
        currency=currency,
        availability=r.get('availability'),
        urgency=r.get('urgency'),
        countdown=r.get('countdown'),
        delivery=r.get('delivery'),
        rating=r.get('rating'),
        review_count=(
            r.get('review_count')
            or r.get('reviews_count')
        ),
        seller_count=r.get(
            '_derived_seller_count',
            r.get(
                'seller_count',
                r.get('number_of_sellers')
            )
        ),
        buy_box=buy_box,
        promotion=r.get('promotion'),
        seller_offer=bool(
            r.get('_is_additional_seller', False)
        ),
        seller_offer_details=offer_details or None,
        ships_from=(
            offer_details.get('ships_from')
            or r.get('ships_from')
        ),
        seller_url=r.get('seller_url'),
        metadata=p,
        **provenance,
        quality_flags=(
            ['missing_timestamp'] if not observed else []
        ) + (
            ['incomplete_total']
            if shipping is None or tax != 'included'
            else []
        )
    )

    o['key'] = digest([
        mode,
        r.get('source_id', 'file'),
        pid,
        seller,
        observed,
        r
    ])

    o['raw_ref'] = digest(
        r.get('_original', r)
    )

    return p, o


def profile(rows):
    fields = sorted({
        k for r in rows if isinstance(r, dict) for k in r
    })

    objects = [
        r for r in rows
        if isinstance(r, dict)
    ]

    timestamps = sorted({
        str(r.get(
            'observed_at',
            r.get('timestamp')
        ))
        for r in objects
        if r.get(
            'observed_at',
            r.get('timestamp')
        )
    })

    repetitions = Counter(
        str((
            r.get('marketplace'),
            r.get('asin', r.get('external_id')),
            seller_identifier(r)
        ))
        for r in objects
    )

    def nested(value, prefix=''):
        result = []

        if isinstance(value, dict):
            for k, v in value.items():
                path = (
                    prefix + '.' + k
                    if prefix else k
                )

                result.append(path)
                result.extend(
                    nested(v, path)
                )

        elif isinstance(value, list) and value:
            result.extend(
                nested(value[0], prefix + '[]')
            )

        return result

    offer_counts = [
        len(parse_additional_sellers(
            r.get('other_sellers_prices')
        ))
        for r in objects
    ]

    seller_ids = {
        seller_identifier(r)
        for r in objects
        if seller_identifier(r)
    }

    for r in objects:
        for offer in parse_additional_sellers(
            r.get('other_sellers_prices')
        ):
            identifier = (
                seller_id_from_url(
                    offer.get('seller_url')
                )
                or offer.get('seller_name')
            )

            if identifier:
                seller_ids.add(
                    str(identifier).strip()
                )

    return dict(
        records=len(rows),
        fields=fields,
        missingness={
            k: sum(
                not isinstance(r, dict)
                or r.get(k) in (None, '')
                for r in rows
            )
            for k in fields
        },
        unique_products=len({
            str(r.get(
                'asin',
                r.get('external_id')
            ))
            for r in objects
            if r.get(
                'asin',
                r.get('external_id')
            )
        }),
        unique_sellers=len(seller_ids),
        marketplaces=dict(Counter(
            str(r.get(
                'marketplace',
                r.get('domain', 'unknown')
            ))
            for r in objects
        )),
        currencies=dict(Counter(
            str(r.get('currency', 'unknown'))
            for r in objects
        )),
        nested_fields=sorted({
            p
            for r in objects[:100]
            for p in nested(r)
        }),
        nested_field_sample_limit=100,
        observation_time_range=(
            [timestamps[0], timestamps[-1]]
            if timestamps else None
        ),
        distinct_timestamps=len(timestamps),
        repeated_product_seller_groups=sum(
            n > 1 for n in repetitions.values()
        ),
        maximum_group_records=max(
            repetitions.values(),
            default=0
        ),
        malformed_records=sum(
            not isinstance(r, dict)
            or '_parse_error' in r
            for r in rows
        ),
        additional_seller_records=sum(
            n > 0 for n in offer_counts
        ),
        additional_seller_offers=sum(
            offer_counts
        ),
        features_supported=[
            'catalog',
            'current observations'
        ] + (
            [
                'historical eligibility assessed after normalization'
            ]
            if len(timestamps) > 1 else []
        ),
        requires_more_evidence=[
            'Longitudinal analyses require repeated comparable observations, '
            'reliable timestamps and adequate coverage'
        ],
        duplicates=(
            len(rows) - len({digest(r) for r in rows})
        ),
        timestamps=timestamps,
        note=(
            'Additional seller offers are expanded during ingestion. '
            'Repeated observations with reliable timestamps are required '
            'for historical analysis.'
        )
    )


def queue_analysis_jobs(db, product_ids, mode, run):
    if review_mode_enabled():
        raise RuntimeError('Analysis job writes are disabled in review mode')

    for pid in product_ids:
        job_id = digest([
            'analysis',
            run,
            pid
        ])

        db.jobs.update_one(
            {'id': job_id},
            {'$setOnInsert': dict(
                id=job_id,
                kind='analysis',
                product_id=pid,
                mode=mode,
                state='queued',
                due=_now(),
                created_at=_now(),
                attempts=0
            )},
            upsert=True
        )



def _ingest_bulk(db, rows, mode, run, mapping=None, queue_analysis=False):
    """Batch ingestion used by historical generation."""
    if review_mode_enabled():
        raise RuntimeError('Source-data writes are disabled in review mode')

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
                offer_details = _offer_details(
                    o.get('seller_offer_details')
                )
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

    if affected:
        try:
            from app.services.summaries import sync_product_summaries
            sync_product_summaries(db, product_ids=affected)
        except Exception:
            pass

    return counts


def ingest(db, rows, mode, run, mapping=None, queue_analysis=True):
    if review_mode_enabled():
        raise RuntimeError('Source-data writes are disabled in review mode')

    if not queue_analysis:
        return _ingest_bulk(db, rows, mode, run, mapping, queue_analysis)
    from pymongo.errors import DuplicateKeyError

    counts = dict(
        accepted=0,
        duplicates=0,
        rejected=0,
        warnings=0
    )

    affected = set()

    for raw in rows:
        source_raw = (
            raw.get('_original', raw)
            if isinstance(raw, dict)
            else raw
        )

        raw_id = digest(source_raw)

        db.raw.update_one(
            {'id': raw_id},
            {'$setOnInsert': {
                'id': raw_id,
                'record': source_raw,
                'run_id': run
            }},
            upsert=True
        )

        candidates = [raw]

        if isinstance(raw, dict):
            candidates.extend(
                expand_additional_sellers(raw)
            )

        if isinstance(raw, dict):
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
                        seller_id_from_url(
                            offer.get('seller_url')
                        )
                        or offer.get('seller_name')
                    )

                    if (
                        offer_id
                        and offer.get('price') not in (None, '')
                    ):
                        raw_id_val = seller_id_from_url(
                            offer.get('seller_url')
                        )

                        if raw_id_val:
                            identifiers.add(
                                raw_id_val.strip()
                            )
                        else:
                            identifiers.add(
                                _normalise_name(
                                    str(offer_id)
                                )
                            )

                for candidate in candidates:
                    candidate['_derived_seller_count'] = len(
                        identifiers
                    )

        for candidate in candidates:
            try:
                p, o = normalize(
                    candidate,
                    mode,
                    run,
                    mapping
                )

                o['raw_ref'] = raw_id

                try:
                    db.observations.insert_one(o)

                except DuplicateKeyError:
                    counts['duplicates'] += 1
                    continue

                db.products.update_one(
                    {'id': p['id']},
                    {'$set': p},
                    upsert=True
                )

                if o['seller_id']:
                    seller_doc = {
                        'id': o['seller_id'],
                        'name': o['seller_name'],
                    }

                    offer_details = _offer_details(
                        o.get('seller_offer_details')
                    )

                    id_type = offer_details.get(
                        'seller_id_type'
                    )

                    if not id_type:
                        id_type = seller_id_type(candidate)

                    seller_doc['seller_id_type'] = id_type

                    db.sellers.update_one(
                        {'id': o['seller_id']},
                        {'$set': seller_doc},
                        upsert=True
                    )

                counts['accepted'] += 1
                affected.add(p['id'])

                counts['warnings'] += bool(
                    o['quality_flags']
                )

            except (ValueError, TypeError, KeyError) as e:
                counts['rejected'] += 1

                db.quarantine.insert_one({
                    'run_id': run,
                    'raw_ref': raw_id,
                    'reason': str(e)
                })

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

    if affected:
        try:
            from app.services.summaries import sync_product_summaries
            sync_product_summaries(db, product_ids=affected)
        except Exception:
            pass

    return counts
