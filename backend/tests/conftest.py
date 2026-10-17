import os,uuid
import pytest
from pymongo import MongoClient
from app.db import indexes

@pytest.fixture
def db():
    client=MongoClient(os.getenv('MONGO_URI','mongodb://127.0.0.1:27028'),serverSelectionTimeoutMS=3000)
    client.admin.command('ping')
    name='marketlens_test_'+uuid.uuid4().hex
    database=client[name]; indexes(database)
    yield database
    client.drop_database(name); client.close()

@pytest.fixture
def raw():
    return dict(asin='B012345678',marketplace='amazon.in',currency='INR',price='100.25',shipping='0',tax_treatment='included',condition='new',variant={'size':'128GB'},seller_id='test-seller',observed_at='2026-01-01T12:00:00+00:00',buy_box=True)

@pytest.fixture
def history(raw):
    from app.services.normalize import normalize
    from datetime import datetime,timedelta
    records=[]
    for i in range(190):
        t=(datetime.fromisoformat(raw['observed_at'])+timedelta(days=i)).isoformat()
        _,o=normalize(dict(raw,observed_at=t,price=100 if i<189 else 80),'demo','unit')
        o['ingested_at']=t;records.append(o)
    return records
