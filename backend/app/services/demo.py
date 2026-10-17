from datetime import datetime,timedelta,timezone
import math, random
from app.services.normalize import ingest
from app.db import indexes

DEMO_END='2026-10-14T12:00:00+00:00'
NAMES=['Auralis Studio Headphones','Luma Mechanical Keyboard','Terra Trail Backpack','Nimbus Air Purifier','Orbit Fitness Watch','Forma Desk Lamp','Echo Portable Speaker','Atlas Coffee Maker','Prism 27-inch Monitor','Solace Reading Light','Cobalt USB-C Hub','Moss Travel Bottle']
CATEGORIES=['Audio','Computing','Outdoors','Home','Wearables','Home']

def generate():
    rng=random.Random(731); end=datetime.fromisoformat(DEMO_END); rows=[]
    for p in range(36):
        base=2500+p*730; n=8 if p==10 else 190
        for day in range(n):
            if p==11 and day%3==0: continue
            t=end-timedelta(days=n-1-day)
            cycle=(day%21); price=base*(1+.045*math.sin(day/8))
            if p==0: price=base*(.72 if day>=n-7 else 1+.03*math.sin(day/9))
            elif p==1: price=base*1.08
            elif p==7: price=base*(1.15 if cycle==0 else .88 if cycle in [1,2,3] else 1)
            elif p==8: price=base*(1.15 if cycle==0 else (.9 if (day//21)%2 else 1.1) if cycle in [1,2,3] else 1)
            elif p==9: price=base
            if p==3 and day>=n-5: price-=200
            shipping=350 if p==3 and day>=n-5 else 0
            ref=base*(1.8 if p==1 and day>=n-10 else 1.2)
            for s in range(4 if p in [5,6] else 1):
                seller_price=price
                if p==5: seller_price=base*(1+(day//5%2)*.12) if s<2 else base*(1+.03*math.sin(day/(2+s)+s))
                if p==6: seller_price=base*(1+(day//5%2)*.08)+s*20
                row=dict(asin=f'DEMO{p:06d}',marketplace='amazon.in',variant={'edition':'Standard'},condition='new',
                    title=NAMES[p%12]+(' '+str(p//12+1) if p>=12 else ''),brand=['Auralis','Luma','Terra','Nimbus'][p%4],category=CATEGORIES[p%6],
                    price=round(seller_price,2),reference_price=round(ref,2),reference_type='observed list price',shipping=shipping,
                    tax_treatment='included',currency='INR',seller_id=f'Fictional seller {s+1 if not (p==4 and day>=n-8) else 5}',
                    availability='out of stock' if p==4 and day in [n-12,n-11] else 'in stock',buy_box=s==0,
                    observed_at=t.isoformat(),source_id='seed-731-v1',urgency='Only a few left — offer ends soon' if p==2 else None,
                    rating=4.2,review_count=120+day,delivery='2–4 days',promotion='Seasonal offer' if day%21<4 else None)
                rows.append(row)
                if p==5 and day>=n-7:
                    for hour in [3,6,9]:
                        rows.append(dict(row,observed_at=(t+timedelta(hours=hour)-timedelta(days=1)).isoformat()))
    return rows

def seed(db,reset=False):
    if not db.name.endswith('_demo'): raise ValueError('Demo reset refuses other databases')
    if reset:
        for name in ['products','observations','sellers','raw','runs','analyses','notifications','watchlists','jobs','quarantine']:
            db[name].delete_many({})
    indexes(db)
    result=ingest(db,generate(),'demo','seed-731-v1')
    # Controlled synthetic ingestion clock: data was "available" at its simulated observation time.
    db.observations.update_many({'source_id':'seed-731-v1'},[{'$set':{'ingested_at':'$observed_at'}}])
    db.settings.update_one({'id':'clock'},{'$set':{'id':'clock','cutoff':DEMO_END}},upsert=True)
    return result
