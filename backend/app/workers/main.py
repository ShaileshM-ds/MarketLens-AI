import os,time,random,logging,uuid
from datetime import datetime,timedelta,timezone
from pymongo import ReturnDocument
from app.db import database,indexes,now
from app.providers.brightdata import BrightData,AmbiguousSubmission,adapter
from app.services.normalize import ingest
from app.services.alerts import evaluate
from app.services.demo import DEMO_END

log=logging.getLogger('marketlens.worker'); logging.basicConfig(level=logging.INFO)

def claim(db,owner,clock=None,kind=None):
    clock=clock or now()
    # An expired submission lease is ambiguous; never resubmit a potentially paid request.
    db.jobs.update_many({'state':'submitting','lease_until':{'$lt':clock}},{'$set':{'state':'needs_review','error':'Worker stopped during submission; reconcile provider console'}})
    query={'$or':[{'state':{'$in':['queued','polling','retry']},'due':{'$lte':clock}}, {'state':'working','lease_until':{'$lt':clock}}]}
    if kind: query['kind']=kind
    return db.jobs.find_one_and_update(query,
        {'$set':{'state':'working','owner':owner,'lease_until':(datetime.fromisoformat(clock)+timedelta(minutes=3)).isoformat()},'$inc':{'attempts':1}},sort=[('due',1)],return_document=ReturnDocument.AFTER)

def execute(db,job,provider=None):
    query={'id':job['id'],'owner':job['owner']}
    try:
        if job.get('kind')=='analysis':
            from app.analytics.engine import analyze
            cutoff=(db.settings.find_one({'id':'clock'}) or {}).get('cutoff',DEMO_END) if job['mode']=='demo' else now()
            result=analyze(list(db.observations.find({'product_id':job['product_id']})),cutoff)
            identity=dict(product_id=job['product_id'],cutoff=cutoff,version='1:90:7:flexible:None')
            db.analyses.update_one(identity,{'$set':dict(**identity,result=result,synthetic=job['mode']=='demo',created_at=now())},upsert=True)
            db.jobs.update_one(query,{'$set':{'state':'done','finished_at':now()}})
            return
        provider=provider or BrightData()
        snap=job.get('snapshot_id')
        if not snap:
            db.jobs.update_one(query,{'$set':{'state':'submitting'}})
            snap=provider.trigger(job['urls'])
            db.jobs.update_one(query,{'$set':{'snapshot_id':snap,'state':'working'}})
        status=provider.status(snap)
        if status=='failed': raise ValueError('Provider collection failed')
        if status!='ready':
            db.jobs.update_one(query,{'$set':{'state':'polling','due':(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()}}); return
        result=ingest(db,list(adapter(provider.download(snap))),'live',job['id'])
        db.jobs.update_one(query,{'$set':{'state':'done','result':result,'finished_at':now()}})
        evaluate(db,now())
    except AmbiguousSubmission as e: db.jobs.update_one(query,{'$set':{'state':'needs_review','error':str(e)}})
    except Exception as e:
        status=getattr(getattr(e,'response',None),'status_code',None)
        retry=job['attempts']<5 and status not in [400,401,403] and not isinstance(e,ValueError)
        db.jobs.update_one(query,{'$set':{'state':'retry' if retry else 'failed','error':f'{type(e).__name__}; provider status {status or "unavailable"}',
            'due':(datetime.now(timezone.utc)+timedelta(seconds=min(900,2**job['attempts']*15)+random.random()*10)).isoformat()}})

def main():
    owner=uuid.uuid4().hex
    while True:
        for mode in ['demo','imported','live']:
            db=database(mode); indexes(db)
            try:
                if mode=='live':
                    from app.services.jobs import schedule_due
                    schedule_due(db)
                job=claim(db,owner,kind='analysis' if mode!='live' or os.getenv('ENABLE_LIVE')!='1' else None)
                if job: execute(db,job)
                clock=(db.settings.find_one({'id':'clock'}) or {}).get('cutoff',DEMO_END) if mode=='demo' else now()
                evaluate(db,clock)
            except Exception: log.exception('Worker cycle failed in %s',mode)
        time.sleep(15)

if __name__=='__main__': main()
