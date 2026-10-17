import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import database
from app.services.auth import hash_password
from app.services.normalize import ingest

@pytest.fixture
def clients(monkeypatch):
    import app.db as db_module
    prefix='marketlens_api_test_'+uuid.uuid4().hex
    monkeypatch.setattr(db_module,'PREFIX',prefix);monkeypatch.setenv('AUTO_SEED','0')
    with TestClient(app) as a,TestClient(app) as b: yield a,b
    for mode in ['demo','imported','live']: db_module.client.drop_database(prefix+'_'+mode)

def test_sessions_ownership_roles_csrf(clients,raw):
    a,b=clients
    r=a.post('/api/v1/auth/demo');assert r.status_code==200
    csrf=r.json()['data']['csrf']
    assert a.get('/api/v1/admin').status_code==403
    ingest(database('demo'),[raw],'demo','r');pid=database('demo').products.find_one({})['id']
    assert a.put('/api/v1/watchlists',json={'product_id':pid}).status_code==403
    assert a.put('/api/v1/watchlists',json={'product_id':pid},headers={'x-csrf-token':csrf}).status_code==200
    assert len(a.get('/api/v1/watchlists').json()['data'])==1
    rb=b.post('/api/v1/auth/demo');token=rb.json()['data']['csrf']
    assert b.get('/api/v1/watchlists').json()['data']==[]
    assert b.delete('/api/v1/watchlists/'+pid,headers={'x-csrf-token':token}).json()['data']['deleted']==0
    assert a.get('/api/v1/watchlists?mode=live').status_code==401

def test_admin_import_and_unknown_time(clients,raw):
    a,_=clients; db=database('imported');db.users.insert_one(dict(id='admin',username='admin',role='admin',password=hash_password('test-password-12345')))
    token=a.post('/api/v1/auth/login?mode=imported',json={'username':'admin','password':'test-password-12345'}).json()['data']['csrf'];headers={'x-csrf-token':token}
    import json
    raw.pop('observed_at'); body={'content':json.dumps([raw])}
    assert a.post('/api/v1/admin/import/preview?mode=imported',json=body,headers=headers).json()['data']['sample'][0]['normalized']['observed_at'] is None
    assert a.post('/api/v1/admin/import?mode=imported',json=body,headers=headers).json()['data']['accepted']==1
    assert a.post('/api/v1/admin/import?mode=imported',json=body,headers=headers).json()['data']['duplicates']==1
    pid=db.products.find_one({})['id']
    assert a.get(f'/api/v1/products/{pid}/analysis?mode=imported').json()['data']['discount']['score'] is None
    assert a.post('/api/v1/admin/demo/reset?mode=imported',headers=headers).status_code==422

def test_asof_never_future(clients,raw):
    a,_=clients;db=database('demo');ingest(db,[raw,dict(raw,observed_at='2026-01-03T12:00:00+00:00',price=80)],'demo','r')
    db.observations.update_many({},[{'$set':{'ingested_at':'$observed_at'}}]);pid=db.products.find_one({})['id']
    r=a.get(f'/api/v1/products/{pid}/as-of',params={'at':'2026-01-02T23:00:00+00:00'}).json()['data']
    assert r['selected']['observation']['price']==10025
    assert a.get(f'/api/v1/products/{pid}/analysis?window=90&horizon=7').status_code==200
    assert a.get(f'/api/v1/products/{pid}/analysis?window=13').status_code==422
