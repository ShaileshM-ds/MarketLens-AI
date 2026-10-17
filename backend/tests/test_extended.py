from datetime import timedelta
from app.analytics.engine import dt,sellers
from app.services.normalize import ingest,parse_file,normalize
from app.services.alerts import evaluate
from app.workers.main import claim,execute
from app.services.jobs import enqueue
import pytest,httpx

def test_partial_jsonl_quarantine_and_mapping(db,raw):
    import json
    rows=parse_file(json.dumps(raw)+'\nnot-json\n'+json.dumps(dict(raw,price='x')))
    result=ingest(db,rows,'imported','partial',{'price':''})
    assert result=={'accepted':1,'duplicates':0,'rejected':2,'warnings':0}
    assert db.quarantine.count_documents({})==2

def test_analysis_job_runs_shared_service(db,history):
    db.observations.insert_many(history)
    cutoff=history[-1]['observed_at'];db.settings.insert_one({'id':'clock','cutoff':cutoff})
    db.jobs.insert_one(dict(id='analysis',kind='analysis',mode='demo',product_id=history[0]['product_id'],state='queued',due='2000-01-01T00:00:00+00:00',attempts=0))
    execute(db,claim(db,'test'))
    assert db.jobs.find_one({'id':'analysis'})['state']=='done'
    assert db.analyses.find_one({})['result']['discount']['historical_savings_pct']==20

def test_provider_partial_download_persists_valid_rows(db,raw):
    class Provider:
        def trigger(self,urls): return 'snapshot'
        def status(self,s): return 'ready'
        def download(self,s): return [raw,{'error':'Unavailable product'}]
    db.jobs.insert_one(dict(id='j',state='queued',due='2000-01-01T00:00:00+00:00',attempts=0,urls=[]))
    execute(db,claim(db,'test'),Provider())
    job=db.jobs.find_one({'id':'j'})
    assert job['state']=='done' and job['result']['accepted']==1 and job['result']['rejected']==1
    assert db.raw.count_documents({})==2

def test_poll_recovery_does_not_trigger_twice(db):
    class Provider:
        def trigger(self,urls): raise AssertionError('Must not resubmit')
        def status(self,s): return 'running'
    db.jobs.insert_one(dict(id='j',snapshot_id='existing',state='polling',due='2000-01-01T00:00:00+00:00',attempts=0,urls=[]))
    execute(db,claim(db,'test'),Provider())
    assert db.jobs.find_one({'id':'j'})['state']=='polling'

def test_rate_limit_is_bounded_retry(db):
    class Provider:
        def trigger(self,urls):
            r=httpx.Response(429,request=httpx.Request('POST','https://api.brightdata.com/datasets/v3/trigger'));r.raise_for_status()
    db.jobs.insert_one(dict(id='j',state='queued',due='2000-01-01T00:00:00+00:00',attempts=0,urls=[]))
    execute(db,claim(db,'test'),Provider())
    assert db.jobs.find_one({'id':'j'})['state']=='retry'

def test_collection_caps_and_refresh(db,monkeypatch):
    monkeypatch.setenv('DAILY_CAP','1')
    enqueue(db,['https://www.amazon.in/dp/B012345678'])
    with pytest.raises(ValueError,match='refresh'): enqueue(db,['https://www.amazon.in/dp/B012345678'])
    with pytest.raises(ValueError,match='cap'): enqueue(db,['https://www.amazon.in/dp/B012345679'])

def test_alert_rearms_after_false(db,history):
    db.observations.insert_many(history)
    db.watchlists.insert_one(dict(id='w',user_id='u',product_id=history[0]['product_id'],target=9000,paused=False,rules=['target']))
    assert evaluate(db,history[-1]['observed_at'])==1
    high=dict(history[-1],_id=None,key='high',observed_at=(dt(history[-1]['observed_at'])+timedelta(days=1)).isoformat(),price=10000,total=10000)
    high.pop('_id');high['ingested_at']=high['observed_at'];db.observations.insert_one(high)
    assert evaluate(db,high['observed_at'])==0
    low=dict(high,key='low',observed_at=(dt(high['observed_at'])+timedelta(days=1)).isoformat(),price=8000,total=8000)
    low.pop('_id',None);low['ingested_at']=low['observed_at'];db.observations.insert_one(low)
    assert evaluate(db,low['observed_at'])==1
    assert db.notifications.count_documents({})==2

def test_seeded_synchronization_and_common_movement():
    from app.services.demo import generate,DEMO_END
    groups={5:[],6:[]}
    for r in generate():
        number=int(r['asin'][4:])
        if number in groups:
            _,o=normalize(r,'demo','test');o['ingested_at']=o['observed_at'];groups[number].append(o)
    positive=sellers(groups[5],DEMO_END);negative=sellers(groups[6],DEMO_END)
    assert sum(p['flagged'] for p in positive['pairs'])==1
    assert not any(p['flagged'] for p in negative['pairs'])
    assert all('lead_lag_daily' in p for p in positive['pairs'])

def test_seeded_analysis_is_bson_serializable():
    from bson import BSON
    from app.services.demo import generate, DEMO_END
    from app.analytics.engine import analyze
    rows=[]
    for r in generate():
        if int(r['asin'][4:])==0:
            _,o=normalize(r,'demo','serialization');o['ingested_at']=o['observed_at'];rows.append(o)
    result=analyze(rows,DEMO_END)
    assert result['memory']['status']=='ready'
    assert BSON.encode(result)
