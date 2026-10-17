"""Measured synthetic evaluation; no claim of marketplace performance."""
import json,pathlib
from collections import defaultdict
from app.services.demo import generate,DEMO_END
from app.services.normalize import normalize
from app.analytics.engine import analyze

groups=defaultdict(list)
for raw in generate():
    _,o=normalize(raw,'demo','evaluation-731');o['ingested_at']=o['observed_at'];groups[raw['asin']].append(o)
result=[]
for asin,rows in groups.items():
    a=analyze(rows,DEMO_END)
    result.append(dict(asin=asin,title=rows[0]['metadata']['title'],observations=len(rows),days=a['quality']['distinct_days'],discount_score=a['discount'].get('score'),
        historical_savings=a['discount'].get('historical_savings_pct'),audit_rules=sorted({f['rule'] for f in a['audit']['findings']}),
        flagged_pairs=[{'a':p['a'],'b':p['b'],'score':p['score'],'adjusted_p':p['adjusted_p']} for p in a['sellers']['pairs'] if p['flagged']],
        analog_count=a['memory'].get('count',0),forecast={k:v for k,v in a['forecast'].items() if k!='points'},recommendation=a['advisor']['recommendation']))
output=dict(mode='demo',synthetic=True,seed=731,cutoff=DEMO_END,products=len(result),observations=sum(x['observations'] for x in result),results=result,
    limitation='Deterministic scenario evaluation is not real-world effectiveness. Provider live collection was not executed.')
path=pathlib.Path('submission/evaluation.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(output,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in output.items() if k!='results'},indent=2))
