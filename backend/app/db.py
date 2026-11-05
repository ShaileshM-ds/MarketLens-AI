import os
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING
from app.review_mode import review_mode_enabled

client = MongoClient(os.getenv('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=4000)
PREFIX = os.getenv('DB_PREFIX', 'marketlens')
MODES = ('demo', 'imported', 'live')

def active_modes():
    configured = os.getenv('ACTIVE_MODES')
    if configured is None:
        return ('imported',) if review_mode_enabled() else MODES

    modes = tuple(
        mode.strip()
        for mode in configured.split(',')
        if mode.strip()
    )
    if not modes or any(mode not in MODES for mode in modes):
        raise RuntimeError('ACTIVE_MODES must contain valid data modes')
    if len(set(modes)) != len(modes):
        raise RuntimeError('ACTIVE_MODES must not contain duplicates')
    if review_mode_enabled() and modes != ('imported',):
        raise RuntimeError(
            'REVIEW_MODE requires ACTIVE_MODES=imported'
        )
    return modes

def now():
    return datetime.now(timezone.utc).isoformat()

def database(mode):
    if mode not in MODES:
        raise ValueError('Unknown data mode')
    if mode not in active_modes():
        raise ValueError('Data mode is not active')
    return client[f'{PREFIX}_{mode}']

def indexes(db):
    if review_mode_enabled():
        raise RuntimeError('Database index initialization is disabled in review mode')

    db.products.create_index('id', unique=True)
    db.sellers.create_index('id', unique=True)
    db.raw.create_index('id', unique=True)
    db.runs.create_index('id', unique=True)
    db.jobs.create_index('id', unique=True)
    db.limits.create_index('id', unique=True)
    db.limits.create_index('expires', expireAfterSeconds=0)
    db.observations.create_index([('product_id', 1), ('observed_at', 1)])
    db.observations.create_index([('product_id', 1), ('seller_id', 1), ('observed_at', 1)])
    db.observations.create_index('key', unique=True)
    db.products.create_index([('marketplace', 1), ('external_id', 1)])
    db.jobs.create_index([('state', 1), ('due', 1), ('lease_until', 1)])
    db.watchlists.create_index([('user_id', 1), ('product_id', 1)], unique=True)
    db.notifications.create_index([('user_id', 1), ('event_key', 1)], unique=True)
    db.analyses.create_index([('product_id', 1), ('cutoff', 1), ('version', 1)], unique=True)
    db.users.create_index('username', unique=True)
    db.sessions.create_index('token', unique=True)
    db.sessions.create_index('expires', expireAfterSeconds=0)
    db.product_summaries.create_index('id', unique=True)
    db.product_summaries.create_index([('current', 1)])
    db.product_summaries.create_index([('days', -1)])
    db.product_summaries.create_index([('category', 1)])
    db.product_summaries.create_index([('brand', 1)])
    db.product_summaries.create_index([('marketplace', 1)])
    db.product_summaries.create_index([('currency', 1)])
    db.product_summaries.create_index([('discount.score', -1)])
    db.product_summaries.create_index([('quality.distinct_days', -1)])
    db.product_summaries.create_index([('title', 1)])

def clean(value):
    if isinstance(value, list): return [clean(x) for x in value]
    if isinstance(value, dict): return {k: clean(v) for k,v in value.items() if k != '_id'}
    if isinstance(value, datetime): return value.isoformat()
    return value
