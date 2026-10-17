"""Ephemeral browser-test admin; credentials arrive on stdin, never committed."""
import json,sys,uuid
from app.db import database,indexes
from app.services.auth import hash_password
body=json.load(sys.stdin);db=database('imported');indexes(db)
if body['action']=='create':
    db.users.insert_one(dict(id=body['id'],username=body['username'],password=hash_password(body['password']),role='admin'))
else:
    db.users.delete_one({'id':body['id']});db.sessions.delete_many({'user_id':body['id']})
    pids=[p['id'] for p in db.products.find({'external_id':body['external_id']})]
    for pid in pids:
        db.observations.delete_many({'product_id':pid});db.products.delete_many({'id':pid});db.analyses.delete_many({'product_id':pid});db.jobs.delete_many({'product_id':pid})
