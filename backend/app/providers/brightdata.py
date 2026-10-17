import os,re
from urllib.parse import urlsplit
import httpx

class AmbiguousSubmission(Exception): pass

def product_url(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.username or u.password or u.port or u.hostname not in {'www.amazon.in','www.amazon.com','www.amazon.co.uk','www.amazon.de','www.amazon.ca','www.amazon.com.au','www.amazon.co.jp'}:
        raise ValueError('Use an HTTPS product URL on a supported www.amazon marketplace')
    match=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)',u.path)
    if not match: raise ValueError('URL needs a 10-character product ASIN')
    return f'https://{u.hostname}/dp/{match.group(1)}'

class BrightData:
    def __init__(self,client=None):
        self.client=client or httpx.Client(base_url='https://api.brightdata.com',headers={'Authorization':'Bearer '+os.getenv('BRIGHT_DATA_API_KEY','')},timeout=45,follow_redirects=False)
    def trigger(self,urls):
        try:
            r=self.client.post('/datasets/v3/trigger',params={'dataset_id':os.environ['BRIGHT_DATA_DATASET_ID'],'include_errors':'true'},json=[{'url':product_url(u)} for u in urls])
            if r.status_code>=500: raise AmbiguousSubmission('Provider response ambiguous; reconcile in provider console before retrying')
            r.raise_for_status()
            return r.json()['snapshot_id']
        except (httpx.TimeoutException,httpx.NetworkError,KeyError) as e:
            raise AmbiguousSubmission('Submission outcome unknown; manual reconciliation required') from e
    def status(self,snapshot):
        r=self.client.get('/datasets/v3/progress/'+snapshot); r.raise_for_status(); return r.json()['status']
    def download(self,snapshot):
        r=self.client.get('/datasets/v3/snapshot/'+snapshot,params={'format':'json'}); r.raise_for_status(); return r.json()

def adapter(rows):
    """Keep absent source fields unknown. Deployment-specific output mapping can be extended here."""
    for raw in rows:
        r=dict(raw);r['_original']=raw
        if r.get('url'):
            r['marketplace']=urlsplit(r['url']).hostname.removeprefix('www.')
        if isinstance(r.get('final_price'),dict):
            r['currency']=r.get('currency') or r['final_price'].get('currency')
        yield r
