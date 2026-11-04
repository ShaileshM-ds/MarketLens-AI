
import os
import uuid
import secrets
import hashlib
import json
import re
import time
import logging

from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db import (
    database,
    indexes,
    clean,
    now,
    active_modes,
    review_mode_enabled,
)
from app.services.normalize import parse_file, profile, normalize, ingest
from app.services.demo import seed, DEMO_END
from app.services.auth import hash_password, verify, issue, current
from app.services.alerts import evaluate
from app.analytics.engine import analyze, eligible, comparable, value, dt
from app.providers.brightdata import product_url


STATIC_DIR = Path(__file__).resolve().parent.parent / 'static'


@asynccontextmanager
async def lifespan(app):
    if not re.fullmatch(
        r'[A-Za-z0-9_-]{1,60}',
        os.getenv('DB_PREFIX', 'marketlens')
    ):
        raise RuntimeError('Invalid database prefix')

    if (
        int(os.getenv('BATCH_LIMIT', '10')) not in range(1, 11)
        or int(os.getenv('DAILY_CAP', '50')) < 1
    ):
        raise RuntimeError('Invalid collection limits')

    if os.getenv('PRODUCTION') == '1' and any(
        not origin.startswith('https://')
        for origin in os.getenv('ALLOWED_ORIGINS', '').split(',')
    ):
        raise RuntimeError(
            'Production requires explicit HTTPS origins'
        )

    if review_mode_enabled():
        logging.getLogger('uvicorn.error').info(
            'Review mode enabled; skipping all database index initialization'
        )
    else:
        for mode in active_modes():
            indexes(database(mode))

    if (
        'demo' in active_modes()
        and os.getenv('AUTO_SEED', '1') == '1'
        and database('demo').products.count_documents({}) == 0
    ):
        seed(database('demo'))

    yield


app = FastAPI(
    title='MarketLens AI',
    version='1.0.0',
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        'ALLOWED_ORIGINS',
        'http://localhost:5173,http://127.0.0.1:5173'
    ).split(','),
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE'],
    allow_headers=['Content-Type', 'X-CSRF-Token']
)

Mode = Literal['demo', 'imported', 'live']

def default_mode() -> Mode:
    return active_modes()[0]


def envelope(data, mode='demo', synthetic=None):
    return dict(
        data=clean(data),
        meta=dict(
            mode=mode,
            synthetic=(mode == 'demo' if synthetic is None else synthetic),
            timestamp=now(),
            schema_version='1',
            analysis_version='heuristic-1.0'
        )
    )


def cutoff_for(mode, cutoff=None):
    if cutoff:
        try:
            t = dt(cutoff)

            if t.tzinfo is None:
                raise ValueError()

            return t.astimezone(timezone.utc).isoformat()

        except ValueError:
            raise HTTPException(
                422,
                'Cutoff must be an ISO timestamp with timezone'
            )

    return (
        database(mode).settings.find_one({'id': 'clock'}) or {}
    ).get('cutoff', DEMO_END) if mode == 'demo' else now()


def rows_for(mode, pid):
    rows = list(
        database(mode).observations.find({'product_id': pid})
    )

    if not rows:
        raise HTTPException(404, 'Product not found')

    return rows


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'error': {
                'code': str(exc.status_code),
                'message': exc.detail
            },
            'meta': {'timestamp': now()}
        }
    )


@app.exception_handler(PyMongoError)
async def db_error(request, exc):
    return JSONResponse(
        status_code=503,
        content={
            'error': {
                'code': 'database_unavailable',
                'message': 'Database unavailable; retry shortly'
            }
        }
    )


@app.middleware('http')
async def guard(request, call_next):
    started = time.monotonic()
    request_id = uuid.uuid4().hex

    requested_mode = request.query_params.get('mode')
    if (
        request.url.path != '/api/v1/config'
        and requested_mode is not None
        and requested_mode not in active_modes()
    ):
        return JSONResponse(
            status_code=404,
            content={'error': {'message': 'Data mode is not active'}}
        )

    if (
        review_mode_enabled()
        and request.method not in ('GET', 'HEAD', 'OPTIONS')
        and request.url.path not in (
            '/api/v1/auth/login',
            '/api/v1/auth/logout'
        )
    ):
        return JSONResponse(
            status_code=403,
            content={'error': {'message': 'Writes are disabled in review mode'}}
        )

    if int(request.headers.get('content-length', '0')) > 8_000_000:
        return JSONResponse(
            status_code=413,
            content={'error': {'message': 'Upload exceeds 8 MB'}}
        )

    origin = request.headers.get('origin')

    if (
        request.method in ['POST', 'PUT', 'DELETE']
        and origin
        and origin not in os.getenv(
            'ALLOWED_ORIGINS',
            'http://localhost:5173,http://127.0.0.1:5173'
        ).split(',')
    ):
        return JSONResponse(
            status_code=403,
            content={'error': {'message': 'Origin not allowed'}}
        )

    result = await call_next(request)

    logging.getLogger('uvicorn.error').info(
        json.dumps({
            'event': 'request',
            'request_id': request_id,
            'method': request.method,
            'path': request.url.path,
            'status': result.status_code,
            'elapsed_ms': round(
                (time.monotonic() - started) * 1000
            )
        })
    )

    result.headers['X-Request-ID'] = request_id
    result.headers['X-Content-Type-Options'] = 'nosniff'
    result.headers['Referrer-Policy'] = 'same-origin'

    return result


def rate_limit(request, kind, limit):
    bucket = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')

    key = hashlib.sha256(
        f'{request.client.host}:{kind}:{bucket}'.encode()
    ).hexdigest()

    db = database('imported')

    db.limits.update_one(
        {'id': key},
        {
            '$inc': {'count': 1},
            '$setOnInsert': {
                'expires': datetime.now(timezone.utc)
                + timedelta(hours=1)
            }
        },
        upsert=True
    )

    if db.limits.find_one({'id': key})['count'] > limit:
        raise HTTPException(429, 'Rate limit reached; retry next minute')


def require_writes_enabled():
    if review_mode_enabled():
        raise HTTPException(
            403,
            'Writes are disabled in review mode'
        )


@app.get('/api/v1/config')
def config():
    return envelope({
        'review_mode': review_mode_enabled(),
        'active_modes': list(active_modes())
    }, default_mode())


@app.get('/api/v1/health')
def health():
    return envelope({'status': 'ok'})


@app.get('/api/v1/ready')
def ready():
    database(active_modes()[0]).command('ping')
    return envelope({'status': 'ready'})


class Credentials(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=80,
        pattern=r'^[a-zA-Z0-9_.@-]+$'
    )
    password: str = Field(min_length=12, max_length=200)


def session_response(db, user, response):
    token, csrf = issue(db, user)

    response.set_cookie(
        'ml_session',
        token,
        httponly=True,
        secure=os.getenv('PRODUCTION') == '1',
        samesite='strict',
        max_age=86400,
        path='/'
    )

    return {
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role']
        },
        'csrf': csrf
    }


@app.post('/api/v1/auth/register')
def register(
    body: Credentials,
    request: Request,
    response: Response,
    mode: Mode = default_mode()
):
    if review_mode_enabled():
        raise HTTPException(403, 'Registration is disabled in review mode')

    rate_limit(request, 'register', 5)
    db = database(mode)

    user = dict(
        id=uuid.uuid4().hex,
        username=body.username,
        password=hash_password(body.password),
        role='consumer'
    )

    try:
        db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(409, 'Username already exists')

    return envelope(session_response(db, user, response), mode)


@app.post('/api/v1/auth/login')
def login(
    body: Credentials,
    request: Request,
    response: Response,
    mode: Mode = default_mode()
):
    rate_limit(request, 'login', 10)
    db = database(mode)

    user = db.users.find_one({'username': body.username})

    if (
        not user
        or not user.get('password')
        or not verify(body.password, user['password'])
    ):
        raise HTTPException(401, 'Invalid credentials')

    return envelope(session_response(db, user, response), mode)


@app.post('/api/v1/auth/demo')
def guest(request: Request, response: Response):
    if review_mode_enabled():
        raise HTTPException(403, 'Guest login is disabled in review mode')

    rate_limit(request, 'guest', 10)

    db = database('demo')

    user = dict(
        id=uuid.uuid4().hex,
        username='Visitor-' + secrets.token_hex(3),
        role='consumer'
    )

    db.users.insert_one(user)

    return envelope(session_response(db, user, response))


@app.get('/api/v1/auth/me')
def me(request: Request, mode: Mode = default_mode()):
    user = current(request, mode)

    token = hashlib.sha256(
        request.cookies['ml_session'].encode()
    ).hexdigest()

    session = database(mode).sessions.find_one({'token': token})

    return envelope({
        'user': {
            k: user[k] for k in ['id', 'username', 'role']
        },
        'csrf': session['csrf']
    }, mode)


@app.post('/api/v1/auth/logout')
def logout(
    request: Request,
    response: Response,
    mode: Mode = default_mode()
):
    current(request, mode)

    token = hashlib.sha256(
        request.cookies['ml_session'].encode()
    ).hexdigest()

    database(mode).sessions.delete_one({'token': token})

    response.delete_cookie('ml_session')

    return envelope({'signed_out': True}, mode)


# ============================================================
# SHARED OBSERVATION LOADER
# ============================================================

def load_observations_by_product(db, product_ids=None):
    """
    Fetch observations grouped by product_id for specific products.
    Never runs an unbounded scan across the entire database.
    """
    observations_by_product = {}
    query = {'product_id': {'$in': list(product_ids)}} if product_ids is not None else {}
    for row in db.observations.find(query):
        pid = row.get('product_id')
        if pid is not None:
            observations_by_product.setdefault(pid, []).append(row)
    return observations_by_product


# ============================================================
# PRODUCTS ENDPOINT - HIGH PERFORMANCE
# ============================================================

@app.get('/api/v1/products')
def products(
    mode: Mode = default_mode(),
    q: str = '',
    category: str = '',
    brand: str = '',
    currency: str = '',
    marketplace: str = '',
    page: int = Query(1, ge=1),
    limit: int = Query(12, ge=1, le=50),
    sort: str = 'title',
    min_price: int = 0,
    max_price: int = 2**53 - 1,
    coverage: str = 'all',
    min_discount: float = 0
):
    db = database(mode)
    cutoff = cutoff_for(mode)

    if review_mode_enabled():
        from app.services.summaries import read_product_summaries
        summaries = read_product_summaries(db, cutoff)
        filtered = [
            summary for summary in summaries
            if (
                (not q or q.casefold() in summary['title'].casefold())
                and (not category or summary['category'] == category)
                and (not brand or summary['brand'] == brand)
                and (not currency or summary['currency'] == currency)
                and (not marketplace or summary['marketplace'] == marketplace)
                and (
                    not (
                        min_price > 0
                        or max_price < 2**53 - 1
                    )
                    or (
                        summary['current'] is not None
                        and min_price <= summary['current'] <= max_price
                    )
                )
                and (
                    coverage != 'history'
                    or summary['days'] >= 15
                )
                and (
                    coverage != 'sparse'
                    or summary['days'] < 15
                )
                and (
                    not min_discount
                    or (
                        summary['advertised'] is not None
                        and summary['advertised'] >= min_discount
                    )
                )
            )
        ]
        if sort == 'price':
            filtered.sort(key=lambda summary: (
                summary['current'] is not None,
                summary['current'] if summary['current'] is not None else 0,
                summary['id'],
            ))
        elif sort == 'coverage':
            filtered.sort(
                key=lambda summary: (-summary['days'], summary['id'])
            )
        else:
            filtered.sort(
                key=lambda summary: (summary['title'], summary['id'])
            )
        total = len(filtered)
        items = filtered[(page - 1) * limit:page * limit]
        return envelope({
            'items': items,
            'total': total,
            'page': page,
            'limit': limit,
            'categories': db.products.distinct('category'),
            'brands': db.products.distinct('brand')
        }, mode)

    from app.services.summaries import sync_product_summaries
    if (
        db.product_summaries.estimated_document_count() == 0
        and db.products.count_documents({}) > 0
    ):
        sync_product_summaries(db, cutoff=cutoff)

    query = {}
    if q:
        query['title'] = {'$regex': re.escape(q), '$options': 'i'}
    if category:
        query['category'] = category
    if brand:
        query['brand'] = brand
    if currency:
        query['currency'] = currency
    if marketplace:
        query['marketplace'] = marketplace
    if min_price > 0 or max_price < 2**53 - 1:
        query['current'] = {'$gte': min_price, '$lte': max_price}
    if coverage == 'history':
        query['days'] = {'$gte': 15}
    elif coverage == 'sparse':
        query['days'] = {'$lt': 15}
    if min_discount:
        query['advertised'] = {'$gte': min_discount}

    if sort == 'price':
        sort_spec = [('current', 1), ('id', 1)]
    elif sort == 'coverage':
        sort_spec = [('days', -1), ('id', 1)]
    else:
        sort_spec = [('title', 1), ('id', 1)]

    total = db.product_summaries.count_documents(query)
    items = list(
        db.product_summaries.find(query, {'_id': 0})
        .sort(sort_spec)
        .skip((page - 1) * limit)
        .limit(limit)
    )

    return envelope({
        'items': items,
        'total': total,
        'page': page,
        'limit': limit,
        'categories': db.products.distinct('category'),
        'brands': db.products.distinct('brand')
    }, mode)


# ============================================================
# OVERVIEW ENDPOINT - HIGH PERFORMANCE
# ============================================================

@app.get('/api/v1/overview')
def overview(mode: Mode = default_mode()):
    db = database(mode)
    cutoff = cutoff_for(mode)
    candidate_query = {'discount.score': {'$gte': 65}}

    if review_mode_enabled():
        from app.services.summaries import read_product_summaries
        summaries = read_product_summaries(db, cutoff)
        candidates = [
            summary for summary in summaries
            if (summary.get('discount', {}).get('score') or 0) >= 65
        ]
        candidates.sort(key=lambda summary: (
            -summary['discount']['score'],
            summary['id'],
        ))
        candidate_count = len(candidates)
        candidates = candidates[:4]

        coverage = [
            {
                'id': summary['id'],
                'title': summary['title'],
                'quality': summary['quality'],
                'discount': {
                    key: summary.get('discount', {}).get(key)
                    for key in ('score', 'days')
                },
            }
            for summary in summaries
        ]
        coverage.sort(key=lambda summary: (
            -summary['quality'].get('distinct_days', 0),
            summary['id'],
        ))
        coverage = coverage[:6]
    else:
        if (
            db.product_summaries.estimated_document_count() == 0
            and db.products.count_documents({}) > 0
        ):
            from app.services.summaries import sync_product_summaries
            sync_product_summaries(db, cutoff=cutoff)
        candidate_count = db.product_summaries.count_documents(
            candidate_query
        )
        candidates = list(
            db.product_summaries.find(
                candidate_query,
                {
                    '_id': 0,
                    'id': 1,
                    'title': 1,
                    'discount.days': 1,
                    'discount.scope': 1,
                    'discount.current': 1,
                    'discount.historical_savings_pct': 1,
                    'discount.score': 1,
                }
            ).sort(
                [('discount.score', -1), ('id', 1)]
            ).limit(4)
        )
        coverage = list(
            db.product_summaries.find(
                {},
                {
                    '_id': 0,
                    'id': 1,
                    'title': 1,
                    'quality': 1,
                    'discount.score': 1,
                    'discount.days': 1,
                }
            ).sort(
                [('quality.distinct_days', -1), ('id', 1)]
            ).limit(6)
        )

    total_products = db.products.count_documents({})
    total_observations = db.observations.estimated_document_count()

    runs = list(
        db.runs.find({}).sort('finished_at', -1).limit(5)
    )

    return envelope({
        'products': total_products,
        'observations': total_observations,
        'cutoff': cutoff,
        'candidate_count': candidate_count,
        'candidates': candidates,
        'coverage': coverage,
        'runs': runs
    }, mode)


@app.get('/api/v1/products/{pid}')
def product(pid: str, mode: Mode = default_mode()):
    db = database(mode)

    p = db.products.find_one({'id': pid})

    if not p:
        raise HTTPException(404, 'Product not found')

    p['latest_observation'] = db.observations.find_one(
        {'product_id': pid},
        sort=[
            ('observed_at', -1),
            ('ingested_at', -1)
        ]
    )

    return envelope(p, mode)


@app.get('/api/v1/products/{pid}/history')
def history(
    pid: str,
    mode: Mode = default_mode(),
    cutoff: str | None = None
):
    return envelope(
        eligible(rows_for(mode, pid), cutoff_for(mode, cutoff)),
        mode
    )


@app.get('/api/v1/products/{pid}/as-of')
def asof(
    pid: str,
    at: str,
    mode: Mode = default_mode(),
    compare: str | None = None
):
    rows = rows_for(mode, pid)

    def state(t):
        t = cutoff_for(mode, t)
        rs = comparable(eligible(rows, t))
        o = rs[-1] if rs else None

        return dict(
            requested_at=t,
            observation=o,
            age_hours=(
                (dt(t) - dt(o['observed_at'])).total_seconds() / 3600
                if o else None
            ),
            label='Observed-state reconstruction'
        )

    a = state(at)
    b = state(compare) if compare else None

    changed = [
        k for k in [
            'price',
            'reference_price',
            'shipping',
            'seller_id',
            'availability',
            'urgency',
            'delivery',
            'rating',
            'review_count',
            'promotion'
        ]
        if (
            b
            and a['observation']
            and b['observation']
            and a['observation'].get(k) != b['observation'].get(k)
        )
    ]

    return envelope(
        dict(
            selected=a,
            comparison=b,
            changed_fields=changed
        ),
        mode
    )


@app.get('/api/v1/products/{pid}/analysis')
def analysis(
    pid: str,
    mode: Mode = default_mode(),
    cutoff: str | None = None,
    window: int = 90,
    horizon: int = 7,
    urgency: Literal['soon', 'flexible'] = 'flexible',
    budget: int | None = None
):
    if window not in [7, 30, 90, 180] or horizon not in [7, 14, 30]:
        raise HTTPException(422, 'Unsupported window or horizon')

    c = cutoff_for(mode, cutoff)

    identity = dict(
        product_id=pid,
        cutoff=c,
        version=f'1:{window}:{horizon}:{urgency}:{budget}'
    )

    db = database(mode)
    if review_mode_enabled():
        cached = db.analyses.find_one(identity)
        if cached and cached.get('result') is not None:
            data = cached['result']
            return envelope(
                data,
                mode,
                synthetic=data.get('provenance', {}).get(
                    'contains_synthetic',
                    cached.get('synthetic', mode == 'demo')
                )
            )

    data = analyze(
        rows_for(mode, pid),
        c,
        window,
        horizon,
        urgency,
        budget
    )

    if not review_mode_enabled():
        db.analyses.update_one(
            identity,
            {
                '$set': dict(
                    **identity,
                    result=data,
                    synthetic=data.get('provenance', {}).get('contains_synthetic', mode == 'demo'),
                    created_at=now()
                )
            },
            upsert=True
        )

    return envelope(
        data,
        mode,
        synthetic=data.get('provenance', {}).get('contains_synthetic', mode == 'demo')
    )


@app.get('/api/v1/evidence/{key}')
def evidence(key: str, mode: Mode = default_mode()):
    db = database(mode)

    o = db.observations.find_one({'key': key})

    if not o:
        raise HTTPException(404, 'Evidence not found')

    raw = db.raw.find_one({'id': o['raw_ref']}) or {}

    return envelope({
        'observation': o,
        'raw': raw.get('record')
    }, mode)


class Watch(BaseModel):
    product_id: str
    target: int | None = Field(None, ge=1)
    notes: str = Field('', max_length=1000)
    horizon: Literal[7, 14, 30] = 7
    window: Literal[7, 30, 90, 180] = 90
    paused: bool = False
    rules: list[
        Literal[
            'target',
            'new_low',
            'discount',
            'recommendation',
            'seller_switch',
            'restock',
            'audit',
            'synchronization'
        ]
    ] = [
        'target'
    ]


@app.get('/api/v1/watchlists')
def watches(request: Request, mode: Mode = default_mode()):
    u = current(request, mode)

    return envelope(
        list(database(mode).watchlists.find({'user_id': u['id']})),
        mode
    )


@app.put('/api/v1/watchlists')
def watch(
    body: Watch,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    u = current(request, mode)

    rows_for(mode, body.product_id)

    db = database(mode)

    query = {
        'user_id': u['id'],
        'product_id': body.product_id
    }

    db.watchlists.update_one(
        query,
        {
            '$set': body.model_dump(),
            '$setOnInsert': dict(
                id=uuid.uuid4().hex,
                user_id=u['id'],
                created_at=now()
            )
        },
        upsert=True
    )

    return envelope(db.watchlists.find_one(query), mode)


@app.delete('/api/v1/watchlists/{pid}')
def remove_watch(
    pid: str,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    u = current(request, mode)

    result = database(mode).watchlists.delete_one({
        'product_id': pid,
        'user_id': u['id']
    })

    return envelope({'deleted': result.deleted_count}, mode)


@app.get('/api/v1/notifications')
def notifications(request: Request, mode: Mode = default_mode()):
    u = current(request, mode)

    return envelope(
        list(
            database(mode).notifications.find(
                {'user_id': u['id']}
            ).sort('observed_at', -1).limit(100)
        ),
        mode
    )


@app.post('/api/v1/notifications/{nid}/read')
def read_notification(
    nid: str,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    u = current(request, mode)

    result = database(mode).notifications.update_one(
        {
            'id': nid,
            'user_id': u['id']
        },
        {'$set': {'read': True}}
    )

    return envelope({'updated': result.modified_count}, mode)


class Import(BaseModel):
    content: str = Field(max_length=7_500_000)
    mapping: dict[str, str] = {}


@app.post('/api/v1/admin/import/preview')
def preview(
    body: Import,
    request: Request,
    mode: Mode = default_mode()
):
    if review_mode_enabled():
        raise HTTPException(403, 'Imports are disabled in review mode')

    current(request, mode, ['admin'])
    rate_limit(request, 'import', 10)

    try:
        rows = parse_file(body.content)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            422,
            'Cannot parse JSON array, JSONL or CSV'
        )

    sample = []

    for r in rows[:20]:
        try:
            sample.append({
                'normalized': normalize(
                    r,
                    mode,
                    'preview',
                    body.mapping
                )[1]
            })
        except (ValueError, TypeError) as e:
            sample.append({'rejection': str(e)})

    return envelope({
        'profile': profile(rows),
        'sample': sample
    }, mode)


@app.post('/api/v1/admin/import')
def upload(
    body: Import,
    request: Request,
    mode: Mode = default_mode()
):
    if review_mode_enabled():
        raise HTTPException(403, 'Imports are disabled in review mode')

    current(request, mode, ['admin'])
    rate_limit(request, 'import', 10)

    if mode != 'imported':
        raise HTTPException(
            422,
            'File imports must use imported-data mode'
        )

    try:
        rows = parse_file(body.content)
    except ValueError:
        raise HTTPException(422, 'Invalid import')

    return envelope(
        ingest(
            database(mode),
            rows,
            mode,
            uuid.uuid4().hex,
            body.mapping
        ),
        mode
    )


@app.get('/api/v1/admin')
def admin(request: Request, mode: Mode = default_mode()):
    current(request, mode, ['admin'])

    db = database(mode)

    return envelope(
        dict(
            runs=list(
                db.runs.find({}).sort('finished_at', -1).limit(30)
            ),
            jobs=list(
                db.jobs.find({}).sort('created_at', -1).limit(30)
            ),
            rejections=list(
                db.quarantine.find({}).limit(100)
            ),
            provider=dict(
                enabled=os.getenv('ENABLE_LIVE') == '1',
                configured=bool(
                    os.getenv('BRIGHT_DATA_API_KEY')
                    and os.getenv('BRIGHT_DATA_DATASET_ID')
                ),
                batch_limit=int(os.getenv('BATCH_LIMIT', '10')),
                daily_cap=int(os.getenv('DAILY_CAP', '50')),
                minimum_refresh_hours=24,
                price_per_record=os.getenv('PRICE_PER_RECORD'),
                billing_note='Configured estimate only, not provider billing'
            ),
            schedules=list(db.schedules.find({}))
        ),
        mode
    )


class Collect(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)


@app.post('/api/v1/admin/collect')
def collect(
    body: Collect,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    current(request, mode, ['admin'])
    rate_limit(request, 'collect', 3)

    if (
        mode != 'live'
        or os.getenv('ENABLE_LIVE') != '1'
        or not os.getenv('BRIGHT_DATA_API_KEY')
        or not os.getenv('BRIGHT_DATA_DATASET_ID')
    ):
        raise HTTPException(
            409,
            'Live collection is disabled or not configured'
        )

    try:
        urls = list(dict.fromkeys(
            product_url(u) for u in body.urls
        ))
    except ValueError as e:
        raise HTTPException(422, str(e))

    from app.services.jobs import enqueue

    try:
        job = enqueue(database(mode), urls)
    except ValueError as e:
        raise HTTPException(409, str(e))

    return envelope(job, mode)


@app.post('/api/v1/admin/demo/reset')
def reset(request: Request, mode: Mode = default_mode()):
    require_writes_enabled()
    current(request, mode, ['admin'])

    if mode != 'demo':
        raise HTTPException(
            422,
            'Reset is only available for the demo database'
        )

    return envelope(seed(database('demo'), True))


class Clock(BaseModel):
    cutoff: str


@app.post('/api/v1/admin/demo/clock')
def clock(
    body: Clock,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    current(request, mode, ['admin'])

    if mode != 'demo':
        raise HTTPException(422, 'Simulated clock is demo-only')

    c = cutoff_for(mode, body.cutoff)

    database(mode).settings.update_one(
        {'id': 'clock'},
        {'$set': {'cutoff': c}},
        upsert=True
    )

    return envelope({
        'cutoff': c,
        'notifications': evaluate(database(mode), c)
    }, mode)


# ============================================================
# ANALYST ENDPOINT
# ============================================================

@app.get('/api/v1/analyst')
def analyst(mode: Mode = default_mode()):
    from app.analytics.engine import (
        audits,
        sellers,
        quality,
        forecast
    )

    db = database(mode)
    c = cutoff_for(mode)
    result = []

    if mode == 'imported':
        cached_analyses = {
            a['product_id']: a['result']
            for a in db.analyses.find({'result': {'$exists': True}})
        }
        pids = list(cached_analyses.keys())
        products_by_id = {p['id']: p for p in db.products.find({'id': {'$in': pids}})}
        for pid, a_res in cached_analyses.items():
            p = products_by_id.get(pid)
            if not p:
                continue
            movement = None
            movement_dates = None
            series = a_res.get('sellers', {}).get('series', {})
            all_points = []
            for s_pts in series.values():
                all_points.extend(s_pts)
            all_points.sort(key=lambda x: x.get('date', ''))
            if len(all_points) >= 2:
                p_prev = all_points[0].get('price')
                p_curr = all_points[-1].get('price')
                if p_prev and p_curr and p_prev > 0:
                    movement = 100 * (p_curr / p_prev - 1)
                    movement_dates = [all_points[0].get('date'), all_points[-1].get('date')]

            result.append(dict(
                product=clean(p),
                quality=a_res.get('quality', {}),
                audit=a_res.get('audit', {}),
                sellers=a_res.get('sellers', {}),
                forecast=a_res.get('forecast', {}),
                movement_pct=movement,
                movement_dates=movement_dates
            ))
        return envelope(result, mode)

    # Demo mode: small catalog (36 products)
    products = list(db.products.find({}))
    pids = [p['id'] for p in products]
    observations_by_product = load_observations_by_product(db, pids)

    for p in products:
        rows = observations_by_product.get(p['id'], [])
        captures = comparable(eligible(rows, c))
        previous = (
            [
                r for r in captures
                if dt(r['observed_at'])
                <= dt(captures[-1]['observed_at'])
                - timedelta(days=7)
            ]
            if captures else []
        )
        movement = (
            100 * (
                value(captures[-1]) / value(previous[-1]) - 1
            )
            if previous and value(previous[-1]) > 0
            else None
        )
        result.append(dict(
            product=clean(p),
            quality=quality(rows, c),
            audit=audits(rows, c),
            sellers=sellers(rows, c),
            forecast=forecast(rows, c),
            movement_pct=movement,
            movement_dates=[
                previous[-1]['observed_at'],
                captures[-1]['observed_at']
            ] if previous else None
        ))

    return envelope(result, mode)


class Schedule(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    interval_hours: int = Field(24, ge=24, le=720)
    enabled: bool = True


@app.post('/api/v1/admin/schedules')
def schedule(
    body: Schedule,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    current(request, mode, ['admin'])

    if mode != 'live':
        raise HTTPException(422, 'Schedules require live mode')

    try:
        urls = [
            product_url(u) for u in body.urls
        ]
    except ValueError as e:
        raise HTTPException(422, str(e))

    record = dict(
        id=uuid.uuid4().hex,
        urls=urls,
        interval_hours=body.interval_hours,
        enabled=body.enabled,
        due=now()
    )

    database(mode).schedules.insert_one(record)

    return envelope(record, mode)


@app.delete('/api/v1/admin/schedules/{sid}')
def delete_schedule(
    sid: str,
    request: Request,
    mode: Mode = default_mode()
):
    require_writes_enabled()
    current(request, mode, ['admin'])

    return envelope({
        'deleted': database(mode).schedules.delete_one(
            {'id': sid}
        ).deleted_count
    }, mode)


@app.api_route('/{full_path:path}', methods=['GET', 'HEAD'], include_in_schema=False)
def frontend(full_path: str, request: Request):
    request_path = request.url.path
    if request_path == '/api' or request_path.startswith('/api/'):
        raise HTTPException(404, 'Not Found')

    static_root = STATIC_DIR.resolve()
    if full_path:
        asset_path = (static_root / full_path).resolve()
        if not asset_path.is_relative_to(static_root):
            raise HTTPException(404, 'Not Found')
    else:
        asset_path = static_root / 'index.html'

    if asset_path.is_file():
        return FileResponse(
            asset_path,
            headers={
                'X-Content-Type-Options': 'nosniff',
                'Referrer-Policy': 'same-origin',
                'Content-Security-Policy': (
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'"
                ),
            },
        )

    if Path(full_path).suffix:
        raise HTTPException(404, 'Not Found')

    index_path = static_root / 'index.html'
    if not index_path.is_file():
        raise HTTPException(404, 'Frontend build is unavailable')

    return FileResponse(
        index_path,
        headers={
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': (
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; font-src 'self'; frame-ancestors 'none'"
            ),
        },
    )
