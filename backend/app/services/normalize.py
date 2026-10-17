import csv, hashlib, io, json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from app.db import now

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

def money(value, currency='INR'):
    if value is None or value == '': return None
    if isinstance(value, dict): value = value.get('value', value.get('amount'))
    try:
        d = Decimal(str(value).replace(',', ''))
        if not d.is_finite() or d < 0: raise ValueError('Price must be finite and nonnegative')
        return int((d * (1 if currency == 'JPY' else 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError): raise ValueError('Invalid numeric money field')

def timestamp(value):
    if not value: return None
    t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if t.tzinfo is None: raise ValueError('Observation timestamp requires timezone')
    return t.astimezone(timezone.utc).isoformat()

def parse_file(content):
    s = content.lstrip('\ufeff \r\n\t')
    if s.startswith('['): rows = json.loads(s)
    elif s.startswith('{'):
        try:
            parsed=json.loads(s)
            rows=parsed if isinstance(parsed,list) else [parsed]
        except json.JSONDecodeError:
            rows=[]
            for n,line in enumerate(s.splitlines(),1):
                if not line.strip(): continue
                try: rows.append(json.loads(line))
                except json.JSONDecodeError: rows.append({'_parse_error':f'Malformed JSONL at line {n}','original_line':line})
    else: rows = list(csv.DictReader(io.StringIO(s)))
    if not isinstance(rows, list) or len(rows) > 20000: raise ValueError('Expected at most 20,000 records')
    return rows

def normalize(raw, mode, run, mapping=None):
    if not isinstance(raw, dict): raise ValueError('Record is not an object')
    if raw.get('_parse_error'): raise ValueError(raw['_parse_error'])
    r = dict(raw)
    for target, source in (mapping or {}).items():
        if source: r[target] = raw.get(source)
    external = r.get('external_id') or r.get('asin')
    marketplace = r.get('marketplace')
    if not external or not marketplace: raise ValueError('external_id/asin and marketplace are required')
    currency = str(r.get('currency', '')).upper()
    if currency not in ('INR','USD','GBP','EUR','JPY','CAD','AUD'): raise ValueError('Supported currency is required')
    price = money(r.get('price', r.get('final_price')), currency)
    if price is None or price <= 0: raise ValueError('Positive price is required')
    variant = r.get('variant', {})
    condition = r.get('condition', 'unknown')
    pid = digest([marketplace, str(external), variant, condition])[:24]
    observed = timestamp(r.get('observed_at', r.get('timestamp')))
    shipping = money(r.get('shipping'), currency)
    tax = r.get('tax_treatment', 'unknown')
    seller = r.get('seller_id') or r.get('seller_name')
    provenance = dict(source_type=mode, source_id=r.get('source_id', 'file' if mode=='imported' else mode), run_id=run,
        synthetic=mode=='demo', observed_at=observed, ingested_at=now(), timestamp_quality='explicit' if observed else 'missing', schema_version='1')
    p = dict(id=pid, external_id=str(external), parent_id=r.get('parent_asin'),marketplace=marketplace, variant=variant, condition=condition,
        product_url=r.get('url'),image_url=r.get('image_url'),country=r.get('country'),
        title=r.get('title') or str(external), brand=r.get('brand', 'Unknown'), category=r.get('category','Uncategorized'), currency=currency)
    o = dict(product_id=pid, seller_id=seller, seller_name=r.get('seller_name', seller), price=price, shipping=shipping,
        reference_price=money(r.get('reference_price', r.get('initial_price')),currency), reference_type=r.get('reference_type','unknown'),
        total=price+shipping if shipping is not None and tax=='included' else None, tax_treatment=tax,
        coupon=money(r.get('coupon'),currency), coupon_conditions=r.get('coupon_conditions'), region=r.get('region'),
        conditional_coupon_price=max(0,price-money(r['coupon'],currency)) if r.get('coupon') is not None and r.get('coupon_conditions') else None,
        currency=currency, availability=r.get('availability'), urgency=r.get('urgency'), countdown=r.get('countdown'),
        delivery=r.get('delivery'), rating=r.get('rating'), review_count=r.get('review_count'), seller_count=r.get('seller_count'),
        buy_box=r.get('buy_box', False), promotion=r.get('promotion'), metadata=p, **provenance,
        quality_flags=(['missing_timestamp'] if not observed else [])+(['incomplete_total'] if shipping is None or tax!='included' else []))
    o['key'] = digest([mode, r.get('source_id','file'), pid, seller, observed, raw])
    o['raw_ref'] = digest(raw)
    return p, o

def profile(rows):
    from collections import Counter
    fields = sorted({k for r in rows if isinstance(r,dict) for k in r})
    objects=[r for r in rows if isinstance(r,dict)]
    timestamps=sorted({str(r.get('observed_at',r.get('timestamp'))) for r in objects if r.get('observed_at',r.get('timestamp'))})
    repetitions=Counter(str((r.get('marketplace'),r.get('asin',r.get('external_id')),r.get('seller_id'))) for r in objects)
    def nested(value,prefix=''):
        result=[]
        if isinstance(value,dict):
            for k,v in value.items():
                path=prefix+'.'+k if prefix else k;result.append(path);result.extend(nested(v,path))
        elif isinstance(value,list) and value: result.extend(nested(value[0],prefix+'[]'))
        return result
    return dict(records=len(rows), fields=fields, missingness={k:sum(not isinstance(r,dict) or r.get(k) in (None,'') for r in rows) for k in fields},
        unique_products=len({str(r.get('asin',r.get('external_id'))) for r in rows if isinstance(r,dict)}),
        unique_sellers=len({str(r.get('seller_id',r.get('seller_name'))) for r in objects if r.get('seller_id',r.get('seller_name'))}),
        marketplaces=dict(Counter(str(r.get('marketplace','unknown')) for r in objects)),currencies=dict(Counter(str(r.get('currency','unknown')) for r in objects)),
        nested_fields=sorted({p for r in objects[:100] for p in nested(r)}),nested_field_sample_limit=100,
        observation_time_range=[timestamps[0],timestamps[-1]] if timestamps else None,distinct_timestamps=len(timestamps),
        repeated_product_seller_groups=sum(n>1 for n in repetitions.values()),maximum_group_records=max(repetitions.values(),default=0),
        malformed_records=sum(not isinstance(r,dict) or '_parse_error' in r for r in rows),
        additional_seller_records=sum(bool(r.get('additional_sellers') or r.get('other_sellers')) for r in objects),
        features_supported=['catalog','current observations']+(['historical eligibility assessed after normalization'] if len(timestamps)>1 else []),
        requires_more_evidence=['Longitudinal analyses require repeated comparable observations, reliable timestamps and adequate coverage'],
        duplicates=len(rows)-len({digest(r) for r in rows}), timestamps=timestamps,
        note='Repeated products with reliable observation times are required for historical analysis. Additional seller arrays are retained raw; map individual offers into rows.')

def ingest(db, rows, mode, run, mapping=None):
    from pymongo.errors import DuplicateKeyError
    counts = dict(accepted=0, duplicates=0, rejected=0, warnings=0)
    affected=set()
    for raw in rows:
        source_raw=raw.get('_original',raw) if isinstance(raw,dict) else raw
        raw_id = digest(source_raw)
        db.raw.update_one({'id':raw_id}, {'$setOnInsert':{'id':raw_id,'record':source_raw,'run_id':run}}, upsert=True)
        try:
            p,o = normalize(raw,mode,run,mapping)
            o['raw_ref']=raw_id
            try: db.observations.insert_one(o)
            except DuplicateKeyError:
                counts['duplicates'] += 1
                continue
            db.products.update_one({'id':p['id']},{'$set':p},upsert=True)
            if o['seller_id']: db.sellers.update_one({'id':o['seller_id']},{'$set':{'id':o['seller_id'],'name':o['seller_name']}},upsert=True)
            counts['accepted'] += 1
            affected.add(p['id'])
            counts['warnings'] += bool(o['quality_flags'])
        except (ValueError,TypeError,KeyError) as e:
            counts['rejected'] += 1
            db.quarantine.insert_one({'run_id':run,'raw_ref':raw_id,'reason':str(e)})
    db.runs.update_one({'id':run},{'$set':dict(id=run,mode=mode,synthetic=mode=='demo',finished_at=now(),**counts)},upsert=True)
    for pid in affected:
        job_id=digest(['analysis',run,pid])
        db.jobs.update_one({'id':job_id},{'$setOnInsert':dict(id=job_id,kind='analysis',product_id=pid,mode=mode,state='queued',due=now(),created_at=now(),attempts=0)},upsert=True)
    return counts
