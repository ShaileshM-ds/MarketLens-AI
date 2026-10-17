import os,uuid,secrets,hashlib,json,re,time,logging
from datetime import datetime,timedelta,timezone
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI,Request,Response,HTTPException,Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from pymongo.errors import DuplicateKeyError,PyMongoError
from app.db import database,indexes,clean,now,MODES
from app.services.normalize import parse_file,profile,normalize,ingest
from app.services.demo import seed,DEMO_END
from app.services.auth import hash_password,verify,issue,current
from app.services.alerts import evaluate
from app.analytics.engine import analyze,eligible,comparable,value,dt
from app.providers.brightdata import product_url

@asynccontextmanager
async def lifespan(app):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',os.getenv('DB_PREFIX','marketlens')): raise RuntimeError('Invalid database prefix')
    if int(os.getenv('BATCH_LIMIT','10')) not in range(1,11) or int(os.getenv('DAILY_CAP','50'))<1: raise RuntimeError('Invalid collection limits')
    if os.getenv('PRODUCTION')=='1' and any(not origin.startswith('https://') for origin in os.getenv('ALLOWED_ORIGINS','').split(',')): raise RuntimeError('Production requires explicit HTTPS origins')
    for mode in MODES: indexes(database(mode))
    if os.getenv('AUTO_SEED','1')=='1' and database('demo').products.count_documents({})==0: seed(database('demo'))
    yield

app=FastAPI(title='MarketLens AI',version='1.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('ALLOWED_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','),allow_credentials=True,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type','X-CSRF-Token'])
Mode=Literal['demo','imported','live']

def envelope(data,mode='demo'):
    return dict(data=clean(data),meta=dict(mode=mode,synthetic=mode=='demo',timestamp=now(),schema_version='1',analysis_version='heuristic-1.0'))

def cutoff_for(mode,cutoff=None):
    if cutoff:
        try:
            t=dt(cutoff)
            if t.tzinfo is None: raise ValueError()
            return t.astimezone(timezone.utc).isoformat()
        except ValueError: raise HTTPException(422,'Cutoff must be an ISO timestamp with timezone')
    return (database(mode).settings.find_one({'id':'clock'}) or {}).get('cutoff',DEMO_END) if mode=='demo' else now()

def rows_for(mode,pid):
    rows=list(database(mode).observations.find({'product_id':pid}))
    if not rows: raise HTTPException(404,'Product not found')
    return rows

@app.exception_handler(HTTPException)
async def http_error(request,exc): return JSONResponse(status_code=exc.status_code,content={'error':{'code':str(exc.status_code),'message':exc.detail},'meta':{'timestamp':now()}})

@app.exception_handler(PyMongoError)
async def db_error(request,exc): return JSONResponse(status_code=503,content={'error':{'code':'database_unavailable','message':'Database unavailable; retry shortly'}})

@app.middleware('http')
async def guard(request,call_next):
    started=time.monotonic();request_id=uuid.uuid4().hex
    if int(request.headers.get('content-length','0'))>8_000_000: return JSONResponse(status_code=413,content={'error':{'message':'Upload exceeds 8 MB'}})
    origin=request.headers.get('origin')
    if request.method in ['POST','PUT','DELETE'] and origin and origin not in os.getenv('ALLOWED_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','):
        return JSONResponse(status_code=403,content={'error':{'message':'Origin not allowed'}})
    result=await call_next(request)
    logging.getLogger('uvicorn.error').info(json.dumps({'event':'request','request_id':request_id,'method':request.method,'path':request.url.path,'status':result.status_code,'elapsed_ms':round((time.monotonic()-started)*1000)}))
    result.headers['X-Request-ID']=request_id
    result.headers['X-Content-Type-Options']='nosniff'; result.headers['Referrer-Policy']='same-origin'
    return result

def rate_limit(request,kind,limit):
    bucket=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')
    key=hashlib.sha256(f'{request.client.host}:{kind}:{bucket}'.encode()).hexdigest()
    db=database('imported')
    db.limits.update_one({'id':key},{'$inc':{'count':1},'$setOnInsert':{'expires':datetime.now(timezone.utc)+timedelta(hours=1)}},upsert=True)
    if db.limits.find_one({'id':key})['count']>limit: raise HTTPException(429,'Rate limit reached; retry next minute')

@app.get('/api/v1/health')
def health(): return envelope({'status':'ok'})

@app.get('/api/v1/ready')
def ready(): database('demo').command('ping'); return envelope({'status':'ready'})

class Credentials(BaseModel):
    username:str=Field(min_length=3,max_length=80,pattern=r'^[a-zA-Z0-9_.@-]+$')
    password:str=Field(min_length=12,max_length=200)

def session_response(db,user,response):
    token,csrf=issue(db,user)
    response.set_cookie('ml_session',token,httponly=True,secure=os.getenv('PRODUCTION')=='1',samesite='strict',max_age=86400,path='/')
    return {'user':{'id':user['id'],'username':user['username'],'role':user['role']},'csrf':csrf}

@app.post('/api/v1/auth/register')
def register(body:Credentials,request:Request,response:Response,mode:Mode='demo'):
    rate_limit(request,'register',5); db=database(mode)
    user=dict(id=uuid.uuid4().hex,username=body.username,password=hash_password(body.password),role='consumer')
    try: db.users.insert_one(user)
    except DuplicateKeyError: raise HTTPException(409,'Username already exists')
    return envelope(session_response(db,user,response),mode)

@app.post('/api/v1/auth/login')
def login(body:Credentials,request:Request,response:Response,mode:Mode='demo'):
    rate_limit(request,'login',10); db=database(mode); user=db.users.find_one({'username':body.username})
    if not user or not user.get('password') or not verify(body.password,user['password']): raise HTTPException(401,'Invalid credentials')
    return envelope(session_response(db,user,response),mode)

@app.post('/api/v1/auth/demo')
def guest(request:Request,response:Response):
    rate_limit(request,'guest',10); db=database('demo'); user=dict(id=uuid.uuid4().hex,username='Visitor-'+secrets.token_hex(3),role='consumer')
    db.users.insert_one(user); return envelope(session_response(db,user,response))

@app.get('/api/v1/auth/me')
def me(request:Request,mode:Mode='demo'):
    user=current(request,mode)
    token=hashlib.sha256(request.cookies['ml_session'].encode()).hexdigest(); session=database(mode).sessions.find_one({'token':token})
    return envelope({'user':{k:user[k] for k in ['id','username','role']},'csrf':session['csrf']},mode)

@app.post('/api/v1/auth/logout')
def logout(request:Request,response:Response,mode:Mode='demo'):
    current(request,mode); database(mode).sessions.delete_one({'token':hashlib.sha256(request.cookies['ml_session'].encode()).hexdigest()})
    response.delete_cookie('ml_session'); return envelope({'signed_out':True},mode)

@app.get('/api/v1/products')
def products(mode:Mode='demo',q:str='',category:str='',brand:str='',currency:str='',marketplace:str='',page:int=Query(1,ge=1),limit:int=Query(12,ge=1,le=50),sort:str='title',min_price:int=0,max_price:int=2**53-1,coverage:str='all',min_discount:float=0):
    db=database(mode); cutoff=cutoff_for(mode); items=[]
    for p in db.products.find({}):
        if q.lower() not in p['title'].lower() or category and p['category']!=category or brand and p['brand']!=brand or currency and p['currency']!=currency or marketplace and p['marketplace']!=marketplace: continue
        rows=list(db.observations.find({'product_id':p['id']})); rs=comparable(eligible(rows,cutoff))
        current_row=rs[-1] if rs else next(iter(rows),None)
        if not current_row: continue
        price=value(current_row)
        ref=current_row.get('reference_price')
        advertised=100*(ref-current_row['price'])/ref if ref else None
        if min_discount and (advertised is None or advertised<min_discount): continue
        if not min_price<=price<=max_price: continue
        days=len({r['observed_at'][:10] for r in rs})
        if coverage=='history' and days<15 or coverage=='sparse' and days>=15: continue
        items.append(dict(**clean(p),current=price,days=days,latest=clean(current_row)))
    items.sort(key=lambda p:p['current'] if sort=='price' else -p['days'] if sort=='coverage' else p['title'])
    return envelope({'items':items[(page-1)*limit:page*limit],'total':len(items),'page':page,'limit':limit,'categories':db.products.distinct('category'),'brands':db.products.distinct('brand')},mode)

@app.get('/api/v1/overview')
def overview(mode:Mode='demo'):
    db=database(mode); cutoff=cutoff_for(mode); results=[]
    from app.analytics.engine import discount,quality
    for p in db.products.find({}):
        rows=list(db.observations.find({'product_id':p['id']})); d=discount(rows,cutoff); q=quality(rows,cutoff)
        results.append({'id':p['id'],'title':p['title'],'discount':d,'quality':q})
    return envelope(dict(products=len(results),observations=db.observations.count_documents({}),cutoff=cutoff,
        candidates=[r for r in results if (r['discount'].get('score') or 0)>=65],coverage=results,
        runs=list(db.runs.find({}).sort('finished_at',-1).limit(5))),mode)

@app.get('/api/v1/products/{pid}')
def product(pid:str,mode:Mode='demo'):
    p=database(mode).products.find_one({'id':pid})
    if not p: raise HTTPException(404,'Product not found')
    p['latest_observation']=database(mode).observations.find_one({'product_id':pid},sort=[('observed_at',-1),('ingested_at',-1)])
    return envelope(p,mode)

@app.get('/api/v1/products/{pid}/history')
def history(pid:str,mode:Mode='demo',cutoff:str|None=None):
    return envelope(eligible(rows_for(mode,pid),cutoff_for(mode,cutoff)),mode)

@app.get('/api/v1/products/{pid}/as-of')
def asof(pid:str,at:str,mode:Mode='demo',compare:str|None=None):
    rows=rows_for(mode,pid)
    def state(t):
        t=cutoff_for(mode,t); rs=comparable(eligible(rows,t)); o=rs[-1] if rs else None
        return dict(requested_at=t,observation=o,age_hours=(dt(t)-dt(o['observed_at'])).total_seconds()/3600 if o else None,label='Observed-state reconstruction')
    a=state(at); b=state(compare) if compare else None
    changed=[k for k in ['price','reference_price','shipping','seller_id','availability','urgency','delivery','rating','review_count','promotion'] if b and a['observation'] and b['observation'] and a['observation'].get(k)!=b['observation'].get(k)]
    return envelope(dict(selected=a,comparison=b,changed_fields=changed),mode)

@app.get('/api/v1/products/{pid}/analysis')
def analysis(pid:str,mode:Mode='demo',cutoff:str|None=None,window:int=90,horizon:int=7,urgency:Literal['soon','flexible']='flexible',budget:int|None=None):
    if window not in [7,30,90,180] or horizon not in [7,14,30]: raise HTTPException(422,'Unsupported window or horizon')
    c=cutoff_for(mode,cutoff); data=analyze(rows_for(mode,pid),c,window,horizon,urgency,budget)
    identity=dict(product_id=pid,cutoff=c,version=f'1:{window}:{horizon}:{urgency}:{budget}')
    database(mode).analyses.update_one(identity,{'$set':dict(**identity,result=data,synthetic=mode=='demo',created_at=now())},upsert=True)
    return envelope(data,mode)

@app.get('/api/v1/evidence/{key}')
def evidence(key:str,mode:Mode='demo'):
    o=database(mode).observations.find_one({'key':key})
    if not o: raise HTTPException(404,'Evidence not found')
    return envelope({'observation':o,'raw':(database(mode).raw.find_one({'id':o['raw_ref']}) or {}).get('record')},mode)

class Watch(BaseModel):
    product_id:str
    target:int|None=Field(None,ge=1)
    notes:str=Field('',max_length=1000)
    horizon:Literal[7,14,30]=7
    window:Literal[7,30,90,180]=90
    paused:bool=False
    rules:list[Literal['target','new_low','discount','recommendation','seller_switch','restock','audit','synchronization']]=['target']

@app.get('/api/v1/watchlists')
def watches(request:Request,mode:Mode='demo'):
    u=current(request,mode); return envelope(list(database(mode).watchlists.find({'user_id':u['id']})),mode)

@app.put('/api/v1/watchlists')
def watch(body:Watch,request:Request,mode:Mode='demo'):
    u=current(request,mode); rows_for(mode,body.product_id); db=database(mode)
    query={'user_id':u['id'],'product_id':body.product_id}
    db.watchlists.update_one(query,{'$set':body.model_dump(),'$setOnInsert':dict(id=uuid.uuid4().hex,user_id=u['id'],created_at=now())},upsert=True)
    return envelope(db.watchlists.find_one(query),mode)

@app.delete('/api/v1/watchlists/{pid}')
def remove_watch(pid:str,request:Request,mode:Mode='demo'):
    u=current(request,mode); result=database(mode).watchlists.delete_one({'product_id':pid,'user_id':u['id']}); return envelope({'deleted':result.deleted_count},mode)

@app.get('/api/v1/notifications')
def notifications(request:Request,mode:Mode='demo'):
    u=current(request,mode); return envelope(list(database(mode).notifications.find({'user_id':u['id']}).sort('observed_at',-1).limit(100)),mode)

@app.post('/api/v1/notifications/{nid}/read')
def read_notification(nid:str,request:Request,mode:Mode='demo'):
    u=current(request,mode); result=database(mode).notifications.update_one({'id':nid,'user_id':u['id']},{'$set':{'read':True}}); return envelope({'updated':result.modified_count},mode)

class Import(BaseModel):
    content:str=Field(max_length=7_500_000)
    mapping:dict[str,str]={}

@app.post('/api/v1/admin/import/preview')
def preview(body:Import,request:Request,mode:Mode='imported'):
    current(request,mode,['admin']); rate_limit(request,'import',10)
    try: rows=parse_file(body.content)
    except (ValueError,json.JSONDecodeError): raise HTTPException(422,'Cannot parse JSON array, JSONL or CSV')
    sample=[]
    for r in rows[:20]:
        try: sample.append({'normalized':normalize(r,mode,'preview',body.mapping)[1]})
        except (ValueError,TypeError) as e: sample.append({'rejection':str(e)})
    return envelope({'profile':profile(rows),'sample':sample},mode)

@app.post('/api/v1/admin/import')
def upload(body:Import,request:Request,mode:Mode='imported'):
    current(request,mode,['admin']); rate_limit(request,'import',10)
    if mode!='imported': raise HTTPException(422,'File imports must use imported-data mode')
    try: rows=parse_file(body.content)
    except ValueError: raise HTTPException(422,'Invalid import')
    return envelope(ingest(database(mode),rows,mode,uuid.uuid4().hex,body.mapping),mode)

@app.get('/api/v1/admin')
def admin(request:Request,mode:Mode='demo'):
    current(request,mode,['admin']); db=database(mode)
    return envelope(dict(runs=list(db.runs.find({}).sort('finished_at',-1).limit(30)),jobs=list(db.jobs.find({}).sort('created_at',-1).limit(30)),
        rejections=list(db.quarantine.find({}).limit(100)),provider=dict(enabled=os.getenv('ENABLE_LIVE')=='1',configured=bool(os.getenv('BRIGHT_DATA_API_KEY') and os.getenv('BRIGHT_DATA_DATASET_ID')),
        batch_limit=int(os.getenv('BATCH_LIMIT','10')),daily_cap=int(os.getenv('DAILY_CAP','50')),minimum_refresh_hours=24,price_per_record=os.getenv('PRICE_PER_RECORD'),billing_note='Configured estimate only, not provider billing'),schedules=list(db.schedules.find({}))),mode)

class Collect(BaseModel): urls:list[str]=Field(min_length=1,max_length=10)

@app.post('/api/v1/admin/collect')
def collect(body:Collect,request:Request,mode:Mode='live'):
    current(request,mode,['admin']); rate_limit(request,'collect',3)
    if mode!='live' or os.getenv('ENABLE_LIVE')!='1' or not os.getenv('BRIGHT_DATA_API_KEY') or not os.getenv('BRIGHT_DATA_DATASET_ID'): raise HTTPException(409,'Live collection is disabled or not configured')
    try: urls=list(dict.fromkeys(product_url(u) for u in body.urls))
    except ValueError as e: raise HTTPException(422,str(e))
    from app.services.jobs import enqueue
    try: job=enqueue(database(mode),urls)
    except ValueError as e: raise HTTPException(409,str(e))
    return envelope(job,mode)

@app.post('/api/v1/admin/demo/reset')
def reset(request:Request,mode:Mode='demo'):
    current(request,mode,['admin'])
    if mode!='demo': raise HTTPException(422,'Reset is only available for the demo database')
    return envelope(seed(database('demo'),True))

class Clock(BaseModel): cutoff:str
@app.post('/api/v1/admin/demo/clock')
def clock(body:Clock,request:Request,mode:Mode='demo'):
    current(request,mode,['admin'])
    if mode!='demo': raise HTTPException(422,'Simulated clock is demo-only')
    c=cutoff_for(mode,body.cutoff); database(mode).settings.update_one({'id':'clock'},{'$set':{'cutoff':c}},upsert=True)
    return envelope({'cutoff':c,'notifications':evaluate(database(mode),c)},mode)

@app.get('/api/v1/analyst')
def analyst(mode:Mode='demo'):
    from app.analytics.engine import audits,sellers,quality,forecast
    db=database(mode); c=cutoff_for(mode); result=[]
    for p in db.products.find({}):
        rows=list(db.observations.find({'product_id':p['id']}))
        captures=comparable(eligible(rows,c));previous=[r for r in captures if dt(r['observed_at'])<=dt(captures[-1]['observed_at'])-timedelta(days=7)] if captures else []
        movement=100*(value(captures[-1])/value(previous[-1])-1) if previous and value(previous[-1])>0 else None
        result.append(dict(product=p,quality=quality(rows,c),audit=audits(rows,c),sellers=sellers(rows,c),forecast=forecast(rows,c),movement_pct=movement,
            movement_dates=[previous[-1]['observed_at'],captures[-1]['observed_at']] if previous else None))
    return envelope(result,mode)

class Schedule(BaseModel):
    urls:list[str]=Field(min_length=1,max_length=10)
    interval_hours:int=Field(24,ge=24,le=720)
    enabled:bool=True

@app.post('/api/v1/admin/schedules')
def schedule(body:Schedule,request:Request,mode:Mode='live'):
    current(request,mode,['admin'])
    if mode!='live': raise HTTPException(422,'Schedules require live mode')
    try: urls=[product_url(u) for u in body.urls]
    except ValueError as e: raise HTTPException(422,str(e))
    record=dict(id=uuid.uuid4().hex,urls=urls,interval_hours=body.interval_hours,enabled=body.enabled,due=now())
    database(mode).schedules.insert_one(record); return envelope(record,mode)

@app.delete('/api/v1/admin/schedules/{sid}')
def delete_schedule(sid:str,request:Request,mode:Mode='live'):
    current(request,mode,['admin']); return envelope({'deleted':database(mode).schedules.delete_one({'id':sid}).deleted_count},mode)
