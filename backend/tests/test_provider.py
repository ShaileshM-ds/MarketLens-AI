import httpx,pytest
from app.providers.brightdata import BrightData,AmbiguousSubmission,adapter

def test_trigger_status_download_contract(monkeypatch):
    monkeypatch.setenv('BRIGHT_DATA_DATASET_ID','configured-test-scraper')
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('trigger'): return httpx.Response(200,json={'snapshot_id':'s_fixture'})
        if '/progress/' in request.url.path: return httpx.Response(200,json={'status':'ready'})
        return httpx.Response(200,json=[{'asin':'B012345678','final_price':{'value':10,'currency':'USD'},'url':'https://www.amazon.com/dp/B012345678'}])
    provider=BrightData(httpx.Client(base_url='https://api.brightdata.com',transport=httpx.MockTransport(handler)))
    assert provider.trigger(['https://www.amazon.com/dp/B012345678'])=='s_fixture'
    assert provider.status('s_fixture')=='ready'
    rows=list(adapter(provider.download('s_fixture')))
    assert rows[0]['currency']=='USD' and rows[0]['marketplace']=='amazon.com'
    assert 'shipping' not in rows[0]
    assert b'"url"' in calls[0].content

@pytest.mark.parametrize('status',[401,429,503])
def test_provider_errors(monkeypatch,status):
    monkeypatch.setenv('BRIGHT_DATA_DATASET_ID','fixture')
    provider=BrightData(httpx.Client(base_url='https://api.brightdata.com',transport=httpx.MockTransport(lambda r:httpx.Response(status))))
    with pytest.raises(AmbiguousSubmission if status==503 else httpx.HTTPStatusError): provider.trigger(['https://www.amazon.com/dp/B012345678'])
