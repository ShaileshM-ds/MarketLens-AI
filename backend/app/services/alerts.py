from datetime import timedelta
from pymongo.errors import DuplicateKeyError
from app.analytics.engine import analyze,dt,value,eligible,comparable

def evaluate(db,cutoff):
    delivered=0
    for w in db.watchlists.find({'paused':False}):
        rows=list(db.observations.find({'product_id':w['product_id']})); rs=comparable(eligible(rows,cutoff))
        if not rs: continue
        current=rs[-1]; previous=rs[-2] if len(rs)>1 else None; result=analyze(rows,cutoff,window=w.get('window',90))
        prior_prices=[value(r) for r in rs[:-1] if dt(r['observed_at'])>=dt(cutoff)-timedelta(days=w.get('window',90))]
        conditions=dict(target=w.get('target') is not None and value(current)<=w['target'],
            new_low=bool(prior_prices) and value(current)<min(prior_prices),discount=result['discount'].get('historical_savings_pct',0)>=15,
            recommendation=w.get('last_recommendation') is not None and w['last_recommendation']!=result['advisor']['recommendation'],
            seller_switch=bool(previous and previous.get('seller_id') and previous.get('seller_id')!=current.get('seller_id')),
            restock=bool(previous and previous.get('availability')=='out of stock' and current.get('availability')=='in stock'),
            audit=any(e['observed_at']==current['observed_at'] for f in result['audit']['findings'] for e in f['evidence']),
            synchronization=any(p['flagged'] for p in result['sellers']['pairs']))
        old=w.get('conditions',{}); last=w.get('last_sent',{}); changed={}
        for rule in w.get('rules',['target']):
            if conditions.get(rule) and not old.get(rule) and (not last.get(rule) or dt(cutoff)-dt(last[rule])>=timedelta(hours=24)):
                event_key=f"{w['id']}:{rule}:{current['key']}"
                try:
                    db.notifications.insert_one(dict(id=event_key,event_key=event_key,user_id=w['user_id'],product_id=w['product_id'],rule=rule,
                        message=f"{current['metadata']['title']}: {rule.replace('_',' ')} condition met",observed_at=cutoff,read=False,
                        synthetic=current['synthetic'],delivery='in-app delivered',email_status='local outbox; no email provider configured',evidence=current['key']))
                    delivered+=1; changed[rule]=cutoff
                except DuplicateKeyError: pass
        db.watchlists.update_one({'id':w['id']},{'$set':{'conditions':conditions,'last_evaluated':cutoff,'last_recommendation':result['advisor']['recommendation'],'last_sent':{**last,**changed}}})
    return delivered
