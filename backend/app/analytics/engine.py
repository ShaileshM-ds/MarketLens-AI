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
            explanation=explanation,alternative=alternative,synthetic_evidence=any(r.get('synthetic') is True for r in evidence),evidence=[{'key':r['key'],'observed_at':r['observed_at'],'raw_ref':r.get('raw_ref')} for r in evidence]))
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

def _seller_daily_tracks(rows, cutoff):
    """
    Build per-seller daily price tracks from eligible observations.

    Internal grouping key is (seller_id, currency, region) so that
    observations from different currencies or regions are never combined
    into the same daily median.  The returned dicts use the same compound
    key; callers that need plain seller IDs must extract the first element.

    Returns:
      item_track    – {(seller_id, currency, region): {date: median_price}}
                      populated whenever a valid price exists.
      landed_track  – same shape, populated only when total is not None.
      rep           – {(seller_id, currency, region): {date: row}}
                      one representative row per slot, used by _seller_pairs
                      for metadata checks.  Because eligible() sorts rows by
                      observed_at ascending, the representative is the latest
                      intraday observation for that slot — a stable, documented
                      choice.  It is used only for metadata validation, never
                      for price values.

    Deduplication: if a row carries a non-empty 'key' field, it is counted
    at most once per (seller_id, currency, region, date) slot.  Rows without
    a key are always included (they are distinct by construction in tests and
    in the normalised schema where every stored observation has a unique key).
    """
    rs = eligible(rows, cutoff)

    # (seller_id, currency, region) -> date -> [price, ...]
    item_raw   = defaultdict(lambda: defaultdict(list))
    landed_raw = defaultdict(lambda: defaultdict(list))
    # (seller_id, currency, region) -> date -> representative row
    rep = defaultdict(dict)
    # deduplication: (slot_key, date) -> set of observation keys already seen
    seen_keys: dict = {}

    for r in rs:
        sid = r.get('seller_id')
        if not sid:
            continue
        p = r.get('price')
        if p is None or p <= 0:
            continue

        currency = r.get('currency') or ''
        region   = r.get('region')   or ''
        date     = r['observed_at'][:10]
        slot     = (sid, currency, region)

        # Skip exact duplicate observations (same observation key, same slot, same day).
        # Rows without a key are always included — they are distinct by construction.
        obs_key = r.get('key')
        if obs_key:
            dedup_slot = (slot, date)
            if dedup_slot not in seen_keys:
                seen_keys[dedup_slot] = set()
            if obs_key in seen_keys[dedup_slot]:
                continue
            seen_keys[dedup_slot].add(obs_key)

        item_raw[slot][date].append(p)
        # eligible() sorts ascending by observed_at, so this is the latest
        # intraday row for this slot — used only for metadata validation.
        rep[slot][date] = r

        t = r.get('total')
        if t is not None:
            landed_raw[slot][date].append(t)

    def _median_track(raw_dict):
        return {
            slot: {
                date: float(np.median(vals))
                for date, vals in daily_map.items()
            }
            for slot, daily_map in raw_dict.items()
        }

    return _median_track(item_raw), _median_track(landed_raw), rep


def _seller_pairs(grouped, rep, price_basis):
    """
    Compute cross-seller price-change correlation pairs for one track.

    ``grouped`` maps (seller_id, currency, region) -> {date: median_price}.
    ``rep``     maps (seller_id, currency, region) -> {date: representative_row}.
    ``price_basis`` is the string label included in each pair dict.

    Because the grouping key already encodes currency and region, two slots
    with different currencies or regions are never combined.  Additionally,
    slots whose currency is the empty string (unknown) are never paired with
    any other slot — matching two unknowns is not evidence of a shared currency.
    The per-interval representative check below is kept as defence-in-depth.

    The 'a' and 'b' fields in each returned pair dict contain plain seller IDs
    (the first element of the compound key) for frontend compatibility.

    Lead-lag note: ``changes`` is built exclusively from consecutive-day
    intervals (the inner loop skips any gap > 1 day).  Therefore the guard
    ``all(...days == 1 ...)`` over ``changes`` is always True when
    len(changes) >= 2.  It is retained as an explicit, documented invariant
    check rather than removed, so that any future change to the construction
    of ``changes`` does not silently break lead-lag computation.
    """
    pairs = []
    for slot_a, slot_b in combinations(sorted(grouped), 2):
        # Only compare slots that share the same currency and region.
        # Empty-string currency means the source did not record a currency;
        # two unknown currencies must not be treated as the same currency.
        sid_a, cur_a, reg_a = slot_a
        sid_b, cur_b, reg_b = slot_b
        if not cur_a or not cur_b or cur_a != cur_b or reg_a != reg_b:
            continue

        dates = sorted(set(grouped[slot_a]) & set(grouped[slot_b]))
        changes = []
        for d0, d1 in zip(dates, dates[1:]):
            if (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days != 1:
                continue
            # Defence-in-depth: verify representative metadata is consistent
            # within each slot across both days.
            reps = [
                rep[slot_a].get(d0), rep[slot_a].get(d1),
                rep[slot_b].get(d0), rep[slot_b].get(d1),
            ]
            if any(r is None for r in reps):
                continue
            if len({(r.get('currency') or '', r.get('region') or '') for r in reps}) != 1:
                continue
            pa0, pa1 = grouped[slot_a][d0], grouped[slot_a][d1]
            pb0, pb1 = grouped[slot_b][d0], grouped[slot_b][d1]
            changes.append((d1, (pa1 - pa0) / pa0, (pb1 - pb0) / pb0))
        if len(changes) < 20:
            continue
        x = np.array([c[1] for c in changes])
        y = np.array([c[2] for c in changes])
        active = (abs(x) > .002) | (abs(y) > .002)
        if sum(active) < 5 or np.std(x) < 1e-7 or np.std(y) < 1e-7:
            continue
        corr = float(np.corrcoef(x, y)[0, 1])
        same = float(np.mean(np.sign(x[active]) == np.sign(y[active])))
        rng = np.random.default_rng(42)
        null = [float(np.corrcoef(x, rng.permutation(y))[0, 1]) for _ in range(999)]
        p = (1 + sum(c >= corr for c in null)) / 1000
        # Product median changes supply a limited common-movement explanation.
        common = []
        for day, dx, dy in changes:
            prior = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
            other = [
                (grouped[s][day] - grouped[s][prior]) / grouped[s][prior]
                for s in grouped
                if s not in (slot_a, slot_b) and day in grouped[s] and prior in grouped[s]
            ]
            common.append(float(np.median(other)) if other else 0)
        common = np.array(common)
        common_corr = float(np.corrcoef(x, common)[0, 1]) if np.std(common) > 1e-7 else 0
        score = max(0, corr) * same * 100 * (.3 if common_corr > .8 else 1)
        half = len(x) // 2
        stability = [
            float(np.corrcoef(xx, yy)[0, 1])
            if np.std(xx) > 1e-7 and np.std(yy) > 1e-7 else None
            for xx, yy in [(x[:half], y[:half]), (x[half:], y[half:])]
        ]
        magnitude = float(np.mean(
            1 - np.minimum(1, abs(x[active] - y[active]) /
                           np.maximum(np.maximum(abs(x[active]), abs(y[active])), 1e-9))
        ))
        # changes contains only consecutive-day intervals (gaps are skipped above),
        # so this guard is always True for len(changes) >= 2.  It is kept as an
        # explicit invariant check; see docstring.
        lead_lag = {}
        if all(
            (datetime.fromisoformat(b2[0]) - datetime.fromisoformat(a2[0])).days == 1
            for a2, b2 in zip(changes, changes[1:])
        ):
            for lag in [-1, 0, 1]:
                xx, yy = (
                    (x[:-1], y[1:]) if lag == 1 else
                    (x[1:], y[:-1]) if lag == -1 else
                    (x, y)
                )
                lead_lag[str(lag)] = (
                    float(np.corrcoef(xx, yy)[0, 1])
                    if np.std(xx) > 1e-7 and np.std(yy) > 1e-7 else None
                )
        pairs.append(dict(
            a=sid_a, b=sid_b,
            correlation=corr, same_direction=same,
            overlap=len(changes), active_events=int(sum(active)),
            score=round(score), p_value=p,
            magnitude_similarity=magnitude, stability_halves=stability,
            lead_lag_daily=lead_lag,
            price_basis=price_basis,
            confidence=(
                'adequate daily overlap'
                if len(changes) >= 60 and all(v is not None for v in stability)
                else 'limited'
            ),
            common_movement=common_corr > .8,
            events=[
                dict(date=c[0], a_change=c[1], b_change=c[2])
                for c in changes
                if abs(c[1]) > .002 and abs(c[2]) > .002
            ],
        ))
    return pairs


def sellers(rows, cutoff):
    """
    Cross-seller price-change correlation analysis.

    Two price tracks are maintained per (seller, currency, region) slot:
      item     – item price only (includes additional-seller observations)
      landed   – item + shipping when both are known and tax is included

    Observations from different currencies or regions are never combined
    into the same daily median.  Slots with unknown currency (empty string)
    are never paired.  Correlations are computed within each track;
    item-price changes are never compared against landed-price changes.
    Multiple offers from the same seller on the same day are aggregated with
    median.  Duplicate observations (same key) are counted once per slot per
    day.  Bonferroni correction is applied across the combined family of all
    pairs (999 permutations, minimum p = 0.001) because the frontend reports
    a single aggregated flagged count.  Series keys are plain seller IDs when
    a seller has exactly one (currency, region) slot; composite keys of the
    form ``"<seller_id>|<currency>|<region>"`` are used otherwise so that no
    history is silently overwritten.
    """
    item_track, landed_track, rep = _seller_daily_tracks(rows, cutoff)

    item_pairs   = _seller_pairs(item_track,   rep, 'item price')
    landed_pairs = _seller_pairs(landed_track, rep, 'known landed price')
    all_pairs    = item_pairs + landed_pairs

    # Bonferroni correction is applied across the combined family of all pairs
    # (item-price and landed-price together).  The frontend aggregates flagged
    # counts from both price bases into a single total, so the reported family
    # is the union.  Applying separate per-track corrections would be less
    # conservative than the combined correction when the same seller pair
    # appears in both tracks, inflating the apparent evidence.
    n_all = len(all_pairs)
    for pair in all_pairs:
        pair['adjusted_p'] = min(1, pair['p_value'] * n_all) if n_all > 0 else pair['p_value']
        pair['flagged'] = (
            pair['score'] >= 75
            and pair['adjusted_p'] <= .05
            and all(v is not None and v > .5 for v in pair['stability_halves'])
        )

    def _series_key(slot, multi_slot_sids):
        """Return the dict key for a slot in a series response.

        When a seller appears in only one (currency, region) slot the key is
        the plain seller ID, preserving backward compatibility with the
        frontend and single-currency datasets.

        When the same seller ID appears in more than one slot a composite key
        ``"<seller_id>|<currency>|<region>"`` is used so that each slot gets
        its own entry and no history is silently overwritten.
        """
        sid, cur, reg = slot
        if sid in multi_slot_sids:
            return f"{sid}|{cur}|{reg}" if (cur or reg) else sid
        return sid

    def _series(track, basis):
        # Count how many slots each seller_id has in this track.
        from collections import Counter
        slot_counts = Counter(slot[0] for slot in track)
        multi = {sid for sid, n in slot_counts.items() if n > 1}
        result = {}
        for slot, daily_map in track.items():
            key = _series_key(slot, multi)
            result[key] = [
                {'date': d, 'price': price, 'price_basis': basis}
                for d, price in sorted(daily_map.items())
            ]
        return result

    # Backward-compatible ``series`` key: prefer the landed track per seller
    # if available, otherwise fall back to item.  Use composite keys when a
    # seller appears in multiple currency/region slots so no history is lost.
    from collections import Counter as _Counter
    _all_slots = list(item_track) + list(landed_track)
    _slot_counts = _Counter(slot[0] for slot in _all_slots)
    _multi_combined = {sid for sid, n in _slot_counts.items() if n > 1}
    landed_sids = {slot[0] for slot in landed_track}
    combined_series = {}
    for slot, daily_map in landed_track.items():
        key = _series_key(slot, _multi_combined)
        combined_series[key] = [
            {'date': d, 'price': price, 'price_basis': 'known landed price'}
            for d, price in sorted(daily_map.items())
        ]
    for slot, daily_map in item_track.items():
        sid = slot[0]
        if sid not in landed_sids:
            key = _series_key(slot, _multi_combined)
            combined_series[key] = [
                {'date': d, 'price': price, 'price_basis': 'item price'}
                for d, price in sorted(daily_map.items())
            ]

    return dict(
        status='ready' if all_pairs else 'insufficient seller history',
        pairs=all_pairs,
        cadence='Daily, consecutive observed intervals only; no carry-forward or interpolation.',
        disclaimer='Changes within the same observed interval; not proof of coordination or collusion.',
        uncontrolled=['category demand', 'seasonality', 'promotion eligibility'],
        series=combined_series,
        series_item=_series(item_track, 'item price'),
        series_landed=_series(landed_track, 'known landed price'),
        note=(
            'item-price and landed-price pairs are computed separately. '
            'A landed price (item + known shipping) is never compared against '
            'an item-only price. Additional seller observations are included '
            'in the item-price track even when shipping cost is unknown.'
        ),
    )

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
    eligible_rows = eligible(rows, cutoff)
    synthetic_count = sum(1 for row in eligible_rows if row.get('synthetic') is True)
    real_count = len(eligible_rows) - synthetic_count
    provenance = dict(
        total_observations=len(eligible_rows),
        real_observations=real_count,
        synthetic_observations=synthetic_count,
        contains_synthetic=synthetic_count > 0,
        evidence_type=(
            'mixed' if synthetic_count and real_count
            else 'synthetic' if synthetic_count
            else 'observed_only' if real_count
            else 'none'
        )
    )
    return dict(version=VERSION,cutoff=cutoff,provenance=provenance,quality=q,discount=d,forecast=f,memory=m,sellers=sellers(rows,cutoff),audit=audits(rows,cutoff),
        advisor=dict(recommendation=advice,reasons=reasons,urgency=urgency,target=budget,version='policy-1',disclaimer='Shopping decision aid, not guaranteed savings.'))
