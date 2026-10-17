import pytest
from datetime import datetime,timedelta,timezone
from app.services.normalize import money,normalize,ingest,parse_file
from app.analytics.engine import discount,eligible,memory,forecast,sellers,analyze,comparable
from app.services.alerts import evaluate
from app.workers.main import claim,execute
from app.providers.brightdata import product_url,AmbiguousSubmission

def test_money_exact_and_unknown():
    assert money('12.345')==1235
    assert money(None) is None
    assert money('100','JPY')==100
    for invalid in ['NaN','Infinity','-1','not a price']:
        with pytest.raises(ValueError): money(invalid)

def test_identity_variant_market_currency(raw):
    p,a=normalize(raw,'imported','run')
    q,b=normalize(dict(raw,variant={'size':'256GB'}),'imported','run')
    assert p['id']!=q['id']
    assert normalize(dict(raw,marketplace='amazon.com'),'imported','run')[0]['id']!=p['id']
    assert a['price']==10025 and a['total']==10025

def test_missing_fields_not_invented(raw):
    raw.pop('observed_at');raw.pop('shipping')
    _,o=normalize(raw,'imported','run')
    assert o['observed_at'] is None and o['shipping'] is None and o['total'] is None
    assert 'missing_timestamp' in o['quality_flags']

def test_timezone_required(raw):
    with pytest.raises(ValueError): normalize(dict(raw,observed_at='2026-01-01'),'imported','run')

@pytest.mark.parametrize('content,count',[('[{"price":1}]',1),('{"price":1}\n{"price":2}',2),('price,currency\n1,INR\n2,INR',2)])
def test_import_formats(content,count): assert len(parse_file(content))==count

def test_real_mongo_idempotency_and_immutability(db,raw):
    first=ingest(db,[raw],'imported','r1'); original=db.observations.find_one({})
    second=ingest(db,[raw],'imported','r2')
    assert first['accepted']==1 and second['duplicates']==1
    assert db.observations.find_one({})==original
    ingest(db,[dict(raw,observed_at='2026-01-02T12:00:00+00:00')],'imported','r3')
    assert db.observations.count_documents({})==2
    assert any(i.get('unique') for i in db.observations.index_information().values())

def test_quarantine(db,raw):
    result=ingest(db,[raw,dict(raw,price='bad')],'imported','r')
    assert result['rejected']==1 and db.quarantine.count_documents({})==1 and db.raw.count_documents({})==2

def test_discount_daily_baseline(history):
    d=discount(history,history[-1]['observed_at'],90)
    assert d['median']==10000 and d['historical_savings_pct']==20
    assert 0<=d['score']<=100 and d['days']==90
    assert history[-1]['key'] not in d['evidence']
    assert discount(history[:8],history[7]['observed_at'])['score'] is None

def test_no_future_leakage(history):
    cutoff=history[100]['observed_at']
    assert len(eligible(history,cutoff))==101
    original=discount(history,cutoff)
    history[-1]['price']=99999999
    assert discount(history,cutoff)==original

def test_no_currency_mixing(history):
    history[-1]['currency']='USD'
    assert len(comparable(history))==1
    assert discount(history,history[-1]['observed_at'])['score'] is None

def test_forecast_real_chronological_evaluation(history):
    f=forecast(history[:-1],history[-2]['observed_at'],14)
    assert f['status']=='ready' and f['model']=='last'
    assert f['mae']==0 and f['coverage']==1 and f['test_samples']>0
    assert len(f['points'])==14
    assert forecast(history[:8],history[7]['observed_at'])['status']=='insufficient history'

def test_constant_sellers_do_not_correlate(history):
    rows=[]
    for o in history[:-1]:
        rows.extend([dict(o,seller_id='a'),dict(o,seller_id='b'),dict(o,seller_id='c')])
    assert sellers(rows,history[-1]['observed_at'])['pairs']==[]

def test_memory_outcomes_completed(history):
    for i,o in enumerate(history): o['price']=o['total']=11000 if i%21==0 else 9000 if i%21 in [1,2,3] else 10000
    cutoff=history[150]['observed_at']; result=memory(history,cutoff)
    assert result['status']=='ready'
    for m in result['matches']: assert datetime.fromisoformat(m['event_date'])+timedelta(days=14)<datetime.fromisoformat(cutoff).replace(tzinfo=None)
    before=result; history[-1]['total']=999999
    assert memory(history,cutoff)==before

def test_alert_crossing_dedup_pause_rearm(db,history):
    db.observations.insert_many(history)
    db.watchlists.insert_one(dict(id='w',user_id='u',product_id=history[0]['product_id'],target=9000,paused=False,rules=['target']))
    assert evaluate(db,history[-2]['observed_at'])==0
    assert evaluate(db,history[-1]['observed_at'])==1
    assert evaluate(db,history[-1]['observed_at'])==0
    assert db.notifications.find_one({})['synthetic'] is True
    db.watchlists.update_one({'id':'w'},{'$set':{'paused':True}})
    assert evaluate(db,history[-1]['observed_at'])==0

def test_atomic_claim_and_recovery(db):
    clock='2026-01-01T12:00:00+00:00'
    db.jobs.insert_one(dict(id='j',state='queued',due=clock,attempts=0))
    assert claim(db,'worker-a',clock)['owner']=='worker-a'
    assert claim(db,'worker-b',clock) is None
    assert claim(db,'worker-b','2026-01-01T12:04:00+00:00')['owner']=='worker-b'
    db.jobs.update_one({'id':'j'},{'$set':{'state':'submitting','lease_until':clock}})
    assert claim(db,'worker-c','2026-01-01T12:05:00+00:00') is None
    assert db.jobs.find_one({'id':'j'})['state']=='needs_review'

def test_url_allowlist():
    assert product_url('https://www.amazon.in/name/dp/B012345678?x=1')=='https://www.amazon.in/dp/B012345678'
    for url in ['http://127.0.0.1/dp/B012345678','https://www.amazon.in.evil.com/dp/B012345678','https://www.amazon.in:444/dp/B012345678','https://u:p@www.amazon.in/dp/B012345678']:
        with pytest.raises(ValueError): product_url(url)

def test_provider_ambiguous_no_retry(db):
    class Provider:
        def trigger(self,urls): raise AmbiguousSubmission('unknown')
    db.jobs.insert_one(dict(id='j',state='queued',due='2000-01-01T00:00:00+00:00',attempts=0,urls=[]))
    j=claim(db,'worker');execute(db,j,Provider())
    assert db.jobs.find_one({'id':'j'})['state']=='needs_review'
