"""Explainable analytics. All prices are minor units; no synthetic interpolation."""
from datetime import datetime, timedelta
from collections import defaultdict
from itertools import combinations
import numpy as np

VERSION = 'heuristic-1.0'

def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00'))
def value(o): return o['total'] if o.get('total') is not None else o['price']

def eligible(rows, cutoff):
    return sorted([r for r in rows if r.get('observed_at') and r['observed_at'] <= cutoff and r.get('ingested_at',cutoff)<=cutoff],key=lambda r:r['observed_at'])

def comparable(rows):
    """Keep latest context. Buy Box scope permits observed seller switches."""
    if not rows: return []
    main=[r for r in rows if r.get('buy_box')] or rows
    latest=main[-1]
    def scope(r): return (r['currency'],r.get('region'),r.get('total') is not None,r.get('metadata',{}).get('condition'),str(r.get('metadata',{}).get('variant')))
    main=[r for r in main if scope(r)==scope(latest)]
    if not latest.get('buy_box'): main=[r for r in main if r.get('seller_id')==latest.get('seller_id')]
    return main

def daily(rows):
    groups=defaultdict(list)
    for r in rows: groups[r['observed_at'][:10]].append(value(r))
    return [(k,float(np.median(v))) for k,v in sorted(groups.items())]

def quality(rows, cutoff):
    timed=eligible(rows,cutoff); main=comparable(timed); days=daily(main)
    span=(dt(main[-1]['observed_at'])-dt(main[0]['observed_at'])).days+1 if main else 0
    coverage=min(1,len(days)/max(span,1))
    age=(dt(cutoff)-dt(main[-1]['observed_at'])).total_seconds()/3600 if main else None
    return dict(observations=len(timed),distinct_days=len(days),coverage=round(coverage,3),span_days=span,
        missing_days=max(0,span-len(days)),freshness_hours=age,sellers=len({r['seller_id'] for r in timed if r.get('seller_id')}),
        missing_timestamps=sum(not r.get('observed_at') for r in rows),
        eligible=dict(discount=len(days)>=15 and coverage>=.6,forecast=len(days)>=45 and coverage>=.8,seller=len({r['seller_id'] for r in timed if r.get('seller_id')})>=3))

def discount(rows, cutoff, window=90):
    main=comparable(eligible(rows,cutoff))
    if not main: return {'status':'insufficient history','score':None}
    current=main[-1]; start=dt(current['observed_at'])-timedelta(days=window)
    # Exclude the current day, so repeated intraday samples cannot bias its own baseline.
    history=[r for r in main if start<=dt(r['observed_at']) and r['observed_at'][:10]<current['observed_at'][:10]]
    days=daily(history); vals=np.array([v for _,v in days]); coverage=len(days)/window
    base=dict(window=window,days=len(days),coverage=round(coverage,3),current=value(current),basis='known landed price' if current.get('total') is not None else 'item price; incomplete total',version=VERSION,scope='observed Buy Box' if current.get('buy_box') else 'same seller',evidence=[r['key'] for r in history])
    if len(days)<14 or coverage<.6: return dict(status='insufficient history',score=None,**base)
    median=float(np.median(vals)); savings=100*(median-value(current))/median
    percentile=float(np.mean(vals<=value(current))*100)
    ref=current.get('reference_price'); advertised=100*(ref-current['price'])/ref if ref and ref>0 else None
    components=[('historical_savings',.6,max(0,min(100,savings/30*100))),('price_position',.25,100-percentile)]
    if advertised is not None: components.append(('claim_agreement',.15,max(0,100-abs(advertised-savings)*4)))
    weight=sum(w for _,w,_ in components)
    return dict(status='ready',score=round(sum(w*v for _,w,v in components)/weight),median=median,low=float(min(vals)),high=float(max(vals)),average=float(np.mean(vals)),
        percentile=percentile,volatility=float(np.std(vals)/median*100),historical_savings_pct=savings,advertised_discount=advertised,
        claim_gap=None if advertised is None else advertised-savings,components=[dict(name=n,weight=w/weight,value=v) for n,w,v in components],
        explanation='Versioned heuristic, not a probability of honesty. Daily medians prevent heavily sampled days dominating.',**base)

def audits(rows,cutoff):
    rs=comparable(eligible(rows,cutoff)); findings=[]
    def add(rule, evidence, explanation, alternative):
        findings.append(dict(id=rule+evidence[-1]['key'][:12],rule=rule,version=VERSION,severity='review',confidence='observed pattern',
            explanation=explanation,alternative=alternative,evidence=[{'key':r['key'],'observed_at':r['observed_at'],'raw_ref':r.get('raw_ref')} for r in evidence]))
    for a,b in zip(rs,rs[1:]):
        if a.get('reference_price') and b.get('reference_price') and b['reference_price']>a['reference_price']*1.1 and b['reference_price']>b['price']:
            add('reference-rise',[a,b],'Reference price increased by over 10% while a discount is implied.','Reference may reflect a revised manufacturer list price.')
        if a.get('shipping') is not None and b.get('shipping') is not None and b['price']<a['price'] and value(b)>=value(a):
            add('shipping-offset',[a,b],'Shipping increase offsets the item-price reduction.','Delivery service or region may have changed.')
        if a.get('seller_id') and b.get('seller_id') and a['seller_id']!=b['seller_id'] and value(a)!=value(b):
            add('seller-switch',[a,b],'Observed Buy Box seller and comparable cost changed.','Offer competition or availability may explain the switch.')
        if isinstance(a.get('countdown'),(int,float)) and isinstance(b.get('countdown'),(int,float)) and b['countdown']>a['countdown']:
            add('countdown-reset',[a,b],'Observed countdown increased between captures.','A new promotion could have started.')
    recent=rs[-30:]
    messages=defaultdict(list)
    for r in recent:
        if r.get('urgency'): messages[r['urgency']].append(r)
    for message, ev in messages.items():
        if len({r['observed_at'][:10] for r in ev})>=7:
            add('repeated-urgency',ev,'The same urgency message appeared on at least seven observed days: '+message,'Repeated text does not establish false scarcity or actual stock quantity.')
    d=discount(rows,cutoff)
    if d.get('historical_savings_pct',0)<0 and (d.get('advertised_discount') or 0)>0:
        add('sale-above-baseline',[rs[-1]],'Advertised discount accompanies a price above the historical median.','Reference and historical baseline measure different things.')
    return {'findings':findings[-100:],'unsupported':[f for f in ['countdown','urgency','shipping'] if not any(r.get(f) is not None for r in rs)],'disclaimer':'Evidence for investigation; no finding proves deception.'}

def sellers(rows,cutoff):
    rs=eligible(rows,cutoff); grouped=defaultdict(dict)
    for r in rs:
        if r.get('seller_id') and r.get('total') is not None:
            grouped[r['seller_id']][r['observed_at'][:10]]=r
    pairs=[]
    for a,b in combinations(sorted(grouped),2):
        dates=sorted(set(grouped[a])&set(grouped[b])); changes=[]
        for d0,d1 in zip(dates,dates[1:]):
            if (datetime.fromisoformat(d1)-datetime.fromisoformat(d0)).days!=1: continue
            aa,ab,ba,bb=grouped[a][d0],grouped[a][d1],grouped[b][d0],grouped[b][d1]
            if len({(r['currency'],r.get('region')) for r in (aa,ab,ba,bb)})!=1: continue
            changes.append((d1,(value(ab)-value(aa))/value(aa),(value(bb)-value(ba))/value(ba)))
        if len(changes)<20: continue
        x=np.array([c[1] for c in changes]); y=np.array([c[2] for c in changes]); active=(abs(x)>.002)|(abs(y)>.002)
        if sum(active)<5 or np.std(x)<1e-7 or np.std(y)<1e-7: continue
        corr=float(np.corrcoef(x,y)[0,1]); same=float(np.mean((np.sign(x[active])==np.sign(y[active]))))
        rng=np.random.default_rng(42); null=[float(np.corrcoef(x,rng.permutation(y))[0,1]) for _ in range(199)]
        p=(1+sum(c>=corr for c in null))/200
        # Product median changes supply a limited common-movement explanation.
        common=[]
        for day,dx,dy in changes:
            prior=(datetime.fromisoformat(day)-timedelta(days=1)).date().isoformat()
            other=[(value(g[day])-value(g[prior]))/value(g[prior]) for s,g in grouped.items() if s not in (a,b) and day in g and prior in g]
            common.append(float(np.median(other)) if other else 0)
        common=np.array(common); common_corr=float(np.corrcoef(x,common)[0,1]) if np.std(common)>1e-7 else 0
        score=max(0,corr)*same*100*(.3 if common_corr>.8 else 1)
        half=len(x)//2
        stability=[float(np.corrcoef(xx,yy)[0,1]) if np.std(xx)>1e-7 and np.std(yy)>1e-7 else None for xx,yy in [(x[:half],y[:half]),(x[half:],y[half:])]]
        magnitude=float(np.mean(1-np.minimum(1,abs(x[active]-y[active])/np.maximum(np.maximum(abs(x[active]),abs(y[active])),1e-9))))
        lead_lag={}
        if all((datetime.fromisoformat(b[0])-datetime.fromisoformat(a[0])).days==1 for a,b in zip(changes,changes[1:])):
            for lag in [-1,0,1]:
                xx,yy=(x[:-1],y[1:]) if lag==1 else (x[1:],y[:-1]) if lag==-1 else (x,y)
                lead_lag[str(lag)]=float(np.corrcoef(xx,yy)[0,1]) if np.std(xx)>1e-7 and np.std(yy)>1e-7 else None
        pairs.append(dict(a=a,b=b,correlation=corr,same_direction=same,overlap=len(changes),active_events=int(sum(active)),score=round(score),p_value=p,
            magnitude_similarity=magnitude,stability_halves=stability,lead_lag_daily=lead_lag,confidence='adequate daily overlap' if len(changes)>=60 and all(v is not None for v in stability) else 'limited',
            common_movement=common_corr>.8,events=[dict(date=c[0],a_change=c[1],b_change=c[2]) for c in changes if abs(c[1])>.002 and abs(c[2])>.002]))
    for p in pairs: p['adjusted_p']=min(1,p['p_value']*len(pairs)); p['flagged']=p['score']>=75 and p['adjusted_p']<=.05 and all(v is not None and v>.5 for v in p['stability_halves'])
    return dict(status='ready' if pairs else 'insufficient seller history',pairs=pairs,cadence='Daily, consecutive observed intervals only; no carry-forward or interpolation.',
        disclaimer='Changes within the same observed interval; not proof of coordination or collusion.',uncontrolled=['category demand','seasonality','promotion eligibility'],
        series={s:[{'date':d,'price':value(r)} for d,r in sorted(g.items())] for s,g in grouped.items()})

def memory(rows,cutoff):
    rs=comparable(eligible(rows,cutoff)); ds=daily(rs)
    if len(ds)<45: return dict(status='too few comparable events',matches=[])
    dates=[datetime.fromisoformat(d) for d,_ in ds]; y=np.array([v for _,v in ds]); features=[]; ids=[]
    by_day={r['observed_at'][:10]:r for r in rs}; captures=[by_day[d] for d,_ in ds]
    def events(i):
        a,b=captures[i-1],captures[i]; found=[]
        change=y[i]/y[i-1]-1
        if change>=.025: found.append('price increase')
        if change<=-.025: found.append('price drop')
        if a.get('reference_price') and b.get('reference_price') and b['reference_price']>a['reference_price']: found.append('reference increase')
        if y[i]>=max(y[max(0,i-14):i]) and y[i-1]<max(y[max(0,i-14):i]): found.append('return to recent high')
        if a.get('seller_id') and b.get('seller_id') and a['seller_id']!=b['seller_id']: found.append('seller switch')
        if a.get('availability') and b.get('availability') and a['availability']!=b['availability']: found.append('stockout/restock')
        if a.get('shipping') is not None and b.get('shipping') is not None and b['shipping']>a['shipping']: found.append('shipping increase')
        if b.get('promotion') and b['promotion']!=a.get('promotion') and any(c.get('promotion')==b['promotion'] for c in captures[:i-1]): found.append('repeated promotion')
        return found
    def feature(i):
        tail=y[i-7:i+1]; returns=np.diff(tail)/tail[:-1]
        regime=1
        for j in range(i-1,max(0,i-30),-1):
            if abs(y[j]/y[j-1]-1)>.002: break
            regime+=1
        a,b=captures[i-1],captures[i]
        ref_change=b['reference_price']/a['reference_price']-1 if a.get('reference_price') and b.get('reference_price') else 0
        return [returns[-1],y[i]/np.median(tail[:-1])-1,float(np.std(returns)),float(np.mean(returns)),regime/30,
            float('seller switch' in events(i)),float('stockout/restock' in events(i)),ref_change,
            float('shipping increase' in events(i)),float('repeated promotion' in events(i)),dates[i].weekday()/6]
    last=-99
    for i in range(7,len(y)-14):
        if (dates[i+14]-dates[i]).days!=14 or i-last<14: continue
        if not events(i): continue
        ids.append(i); features.append(feature(i)); last=i
    if len(ids)<3: return dict(status='too few comparable events',matches=[],eligible_events=len(ids))
    X=np.array(features); mean=X.mean(axis=0); std=X.std(axis=0); std[std<1e-6]=1
    distances=np.linalg.norm((X-mean)/std-(np.array(feature(len(y)-1))-mean)/std,axis=1)
    matches=[]
    for k in np.argsort(distances)[:8]:
        i=ids[k]
        outcomes={str(h):round(float((y[i+h]/y[i]-1)*100),3) for h in [1,3,7,14]}
        lower=[h for h in range(1,15) if y[i+h]<y[i]]
        matches.append(dict(event_date=ds[i][0],event_types=events(i),distance=float(distances[k]),outcomes=outcomes,time_to_lower=min(lower) if lower else None,
            explanation='Same-product returns, baseline deviation, volatility, regime duration, seller/stock/reference/shipping/promotion changes and weekday.',evidence=[r['key'] for r in rs if r['observed_at'][:10]==ds[i][0]]))
    vals=[m['outcomes']['3'] for m in matches]; lower=[m['time_to_lower'] for m in matches if m['time_to_lower']]
    return dict(status='ready',matches=matches,count=len(matches),lower_after_three_days=sum(v<0 for v in vals),median_change=float(np.median(vals)),
        spread=[float(min(vals)),float(max(vals))],typical_days_to_lower=float(np.median(lower)) if lower else None,
        warning='Small sample. Empirical historical frequency, not a calibrated probability. All analog outcomes completed before cutoff.')

def forecast(rows,cutoff,horizon=7):
    ds=daily(comparable(eligible(rows,cutoff))); y=np.array([v for _,v in ds]); dates=[datetime.fromisoformat(d) for d,_ in ds]
    if len(ds)<max(45,horizon*4) or (dates[-1]-dates[0]).days+1>len(ds)/.8:
        return dict(status='insufficient history',points=[])
    lookup=dict(ds)
    def predict(train,name,h):
        if name=='last': return float(train[-1])
        if name=='moving_average': return float(np.mean(train[-7:]))
        if name=='seasonal_naive': return float(train[-7+(h-1)%7])
        state=float(train[0])
        for v in train[1:]: state=.3*v+.7*state
        return state
    models=['last','moving_average','exponential_smoothing']
    if all((b-a).days==1 for a,b in zip(dates,dates[1:])) and len(ds)>=90: models.append('seasonal_naive')
    errors={m:[] for m in models}; origins=[]
    for i in range(28,len(y)-horizon,7):
        target=(dates[i]+timedelta(days=horizon)).date().isoformat()
        if target not in lookup: continue
        origins.append(ds[i][0])
        for m in models: errors[m].append(lookup[target]-predict(y[:i+1],m,horizon))
    if len(origins)<4: return dict(status='insufficient backtest targets',points=[])
    # Model selection on early origins; evaluate chosen model on later untouched origins.
    split=max(2,len(origins)//2)
    best=min(models,key=lambda m:np.mean(np.abs(errors[m][:split])))
    baseline=float(np.mean(np.abs(errors['last'][:split])))
    if np.mean(np.abs(errors[best][:split]))>=baseline*.98: best='last'
    calibration=np.abs(errors[best][:split]); width=float(np.quantile(calibration,.9,method='higher'))
    held=np.array(errors[best][split:]); mae=float(np.mean(abs(held))); scale=float(np.mean(abs(np.diff(y))))
    points=[dict(date=(dates[-1]+timedelta(days=h)).date().isoformat(),price=predict(y,best,h),low=max(0,predict(y,best,h)-width),high=predict(y,best,h)+width) for h in range(1,horizon+1)]
    return dict(status='ready',model=best,version='forecast-1',horizon=horizon,points=points,training_cutoff=cutoff,created_at=cutoff,
        mae=mae,scaled_mae=mae/scale if scale else None,coverage=float(np.mean(abs(held)<=width)),test_samples=len(held),calibration_samples=split,
        backtest_range=[origins[0],origins[-1]],baselines={m:float(np.mean(np.abs(errors[m][split:]))) for m in models},
        interval_method='90th percentile absolute residual on early rolling origins; measured coverage on later origins. Horizon-end interval reused across path; not individually calibrated daily.',
        warning='Prediction, not a guarantee. Missing target dates are skipped, never fabricated.')

def analyze(rows,cutoff,window=90,horizon=7,urgency='flexible',budget=None):
    q=quality(rows,cutoff); d=discount(rows,cutoff,window); f=forecast(rows,cutoff,horizon); m=memory(rows,cutoff)
    advice='Insufficient evidence'; reasons=[]
    if d.get('score') is not None and (q['freshness_hours'] or 0)<=72:
        if budget and d['current']>budget: advice='Consider waiting'; reasons.append('Current observed price exceeds your target.')
        elif d['percentile']<=25 and (urgency=='soon' or f.get('status')=='ready' and f['points'][-1]['high']>=d['current']):
            advice='Buy now'; reasons.append('Price is in the lower quarter of the observed window; no clear forecast reduction beyond the interval.')
        elif d['historical_savings_pct']<0 or f.get('status')=='ready' and f['points'][-1]['high']<d['current']:
            advice='Consider waiting'; reasons.append('Current historical price position or forecast favors waiting.')
        else: reasons.append('Signals are mixed; the policy abstains.')
    else: reasons.append('History is sparse or the latest observation is stale.')
    current=comparable(eligible(rows,cutoff))
    if current and current[-1].get('availability')=='out of stock':
        advice='Insufficient evidence';reasons.append('The latest observed offer is out of stock; a purchasable offer must be confirmed.')
    if d.get('volatility',0)>15: reasons.append('Observed volatility exceeds 15%; price uncertainty is elevated.')
    if m.get('count'): reasons.append(f"{m['lower_after_three_days']} of {m['count']} completed analogs had a lower three-day observed price.")
    return dict(version=VERSION,cutoff=cutoff,quality=q,discount=d,forecast=f,memory=m,sellers=sellers(rows,cutoff),audit=audits(rows,cutoff),
        advisor=dict(recommendation=advice,reasons=reasons,urgency=urgency,target=budget,version='policy-1',disclaimer='Shopping decision aid, not guaranteed savings.'))
