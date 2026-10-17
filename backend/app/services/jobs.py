import os,uuid
from datetime import datetime,timedelta,timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.db import now

def enqueue(db,urls):
    if len(urls)>int(os.getenv('BATCH_LIMIT','10')): raise ValueError('Configured batch limit exceeded')
    since=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
    if db.jobs.find_one({'urls':{'$in':urls},'created_at':{'$gt':since}}): raise ValueError('Minimum refresh interval is 24 hours; recent job exists')
    owner=uuid.uuid4().hex; held=[];clock=now()
    for url in sorted(set(urls)):
        try:
            lock=db.collection_locks.find_one_and_update({'_id':url,'until':{'$lte':clock}},
                {'$set':{'owner':owner,'until':(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()}},upsert=True,return_document=ReturnDocument.AFTER)
            held.append(url)
        except DuplicateKeyError:
            db.collection_locks.delete_many({'owner':owner})
            raise ValueError('Minimum refresh interval is 24 hours; URL reserved by another submission')
    day=now()[:10]; cap=int(os.getenv('DAILY_CAP','50'))
    db.usage.update_one({'_id':day},{'$setOnInsert':{'count':0}},upsert=True)
    counter=db.usage.find_one_and_update({'_id':day,'count':{'$lte':cap-len(urls)}},{'$inc':{'count':len(urls)}},return_document=ReturnDocument.AFTER)
    if not counter:
        db.collection_locks.delete_many({'owner':owner})
        raise ValueError('Daily collection cap reached')
    job=dict(id=uuid.uuid4().hex,kind='collection',urls=urls,state='queued',due=now(),created_at=now(),attempts=0)
    db.jobs.insert_one(job); return job

def schedule_due(db):
    if os.getenv('ENABLE_LIVE')!='1' or not os.getenv('BRIGHT_DATA_API_KEY') or not os.getenv('BRIGHT_DATA_DATASET_ID'): return
    for s in db.schedules.find({'enabled':True,'due':{'$lte':now()}}):
        claimed=db.schedules.find_one_and_update({'id':s['id'],'due':s['due']},{'$set':{'due':(datetime.now(timezone.utc)+timedelta(hours=s['interval_hours'])).isoformat()}})
        if claimed:
            try: enqueue(db,s['urls'])
            except ValueError as e: db.schedules.update_one({'id':s['id']},{'$set':{'last_error':str(e)}})
