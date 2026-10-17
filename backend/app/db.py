import os
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING

client = MongoClient(os.getenv('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=4000)
PREFIX = os.getenv('DB_PREFIX', 'marketlens')
MODES = ('demo', 'imported', 'live')

def now():
    return datetime.now(timezone.utc).isoformat()

def database(mode):
    if mode not in MODES:
        raise ValueError('Unknown data mode')
    return client[f'{PREFIX}_{mode}']

def indexes(db):
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

def clean(value):
    if isinstance(value, list): return [clean(x) for x in value]
    if isinstance(value, dict): return {k: clean(v) for k,v in value.items() if k != '_id'}
    if isinstance(value, datetime): return value.isoformat()
    return value
