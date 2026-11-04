import asyncio
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

import app.db as db_module
import app.main as api
import app.services.auth as auth_module
import app.workers.main as worker
import app.services.alerts as alerts
import app.services.demo as demo
import app.services.jobs as jobs
import app.services.summaries as summaries
from app.services.auth import hash_password
from app.services.normalize import ingest, normalize, queue_analysis_jobs


class Collection:
    def __init__(self, document=None):
        self.document = document
        self.inserted = []
        self.updates = []
        self.deletes = []
        self.update_many_calls = 0

    def find_one(self, query):
        documents = ([self.document] if self.document else []) + self.inserted
        for document in documents:
            matches = True
            for key, value in query.items():
                actual = document.get(key)
                if isinstance(value, dict) and '$gt' in value:
                    matches = actual is not None and actual > value['$gt']
                else:
                    matches = actual == value
                if not matches:
                    break
            if matches:
                return document
        return None

    def insert_one(self, document):
        self.inserted.append(document)

    def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return SimpleNamespace(modified_count=1)

    def update_many(self, *args, **kwargs):
        self.update_many_calls += 1

    def find_one_and_update(self, *args, **kwargs):
        return {'id': 'job', 'owner': 'worker'}

    def delete_one(self, query):
        self.deletes.append(query)
        return SimpleNamespace(deleted_count=1)


class FakeDatabase:
    def __init__(self, user=None, cached_analysis=None):
        self.users = Collection(user)
        self.sessions = Collection()
        self.analyses = Collection(cached_analysis)
        self.jobs = Collection()


class ReadCursor(list):
    def sort(self, key, direction=1):
        if isinstance(key, list):
            for name, order in reversed(key):
                super().sort(
                    key=lambda row: row.get(name),
                    reverse=order < 0,
                )
        else:
            super().sort(
                key=lambda row: row.get(key),
                reverse=direction < 0,
            )
        return self

    def skip(self, count):
        return ReadCursor(self[count:])

    def limit(self, count):
        return ReadCursor(self[:count])


class ReadCollection:
    def __init__(self, documents):
        self.documents = documents
        self.find_calls = 0

    def find(self, query=None, projection=None):
        self.find_calls += 1
        del query
        rows = [dict(document) for document in self.documents]
        if projection and projection.get('_id') == 0:
            for row in rows:
                row.pop('_id', None)
        return ReadCursor(rows)

    def count_documents(self, query):
        del query
        return len(self.documents)

    def estimated_document_count(self):
        return len(self.documents)

    def distinct(self, field):
        return sorted({
            document[field]
            for document in self.documents
            if field in document
        })


class ReadDatabase:
    def __init__(self, products, observations, summaries):
        self.products = ReadCollection(products)
        self.observations = ReadCollection(observations)
        self.product_summaries = ReadCollection(summaries)
        self.runs = ReadCollection([])


def request(method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url='http://test'
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_review_mode_defaults_to_imported_and_restricts_modes(monkeypatch):
    monkeypatch.setenv('REVIEW_MODE', 'true')
    monkeypatch.delenv('ACTIVE_MODES', raising=False)

    assert db_module.active_modes() == ('imported',)
    with pytest.raises(ValueError, match='not active'):
        db_module.database('demo')


def test_review_startup_initializes_only_imported_database(monkeypatch):
    monkeypatch.setenv('REVIEW_MODE', '1')
    monkeypatch.delenv('ACTIVE_MODES', raising=False)
    monkeypatch.setenv('AUTO_SEED', '0')
    monkeypatch.setattr(
        api,
        'database',
        lambda mode: pytest.fail(f'Review startup accessed {mode} database'),
    )
    monkeypatch.setattr(
        api,
        'indexes',
        lambda db: pytest.fail('Review startup attempted index creation'),
    )

    async def start():
        async with api.lifespan(api.app):
            pass

    asyncio.run(start())

def test_normal_startup_still_initializes_all_indexes(monkeypatch):
    monkeypatch.delenv('REVIEW_MODE', raising=False)
    monkeypatch.delenv('ACTIVE_MODES', raising=False)
    monkeypatch.setenv('AUTO_SEED', '0')
    indexed = []
    monkeypatch.setattr(api, 'database', lambda mode: mode)
    monkeypatch.setattr(api, 'indexes', indexed.append)

    async def start():
        async with api.lifespan(api.app):
            pass

    asyncio.run(start())

    assert indexed == ['demo', 'imported', 'live']


def test_normal_mode_defaults_to_all_existing_modes(monkeypatch):
    monkeypatch.delenv('REVIEW_MODE', raising=False)
    monkeypatch.delenv('ACTIVE_MODES', raising=False)

    assert db_module.active_modes() == ('demo', 'imported', 'live')


def test_review_config_and_disabled_write_routes(monkeypatch):
    monkeypatch.setenv('REVIEW_MODE', '1')
    monkeypatch.delenv('ACTIVE_MODES', raising=False)

    config = request('GET', '/api/v1/config?mode=demo')
    assert config.status_code == 200
    assert config.json()['data'] == {
        'review_mode': True,
        'active_modes': ['imported'],
    }

    paths = [
        '/api/v1/auth/register?mode=imported',
        '/api/v1/auth/demo',
        '/api/v1/admin/import/preview?mode=imported',
        '/api/v1/admin/import?mode=imported',
    ]
    for path in paths:
        response = request('POST', path, json={})
        assert response.status_code == 403


def test_existing_reviewer_login_still_creates_session(monkeypatch):
    monkeypatch.setenv('REVIEW_MODE', '1')
    monkeypatch.delenv('ACTIVE_MODES', raising=False)
    user = {
        'id': 'reviewer-id',
        'username': 'reviewer',
        'role': 'admin',
        'password': hash_password('reviewer-password-123'),
    }
    db = FakeDatabase(user=user)
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(auth_module, 'database', lambda mode: db)
    monkeypatch.setattr(api, 'rate_limit', lambda *args: None)

    response = request(
        'POST',
        '/api/v1/auth/login?mode=imported',
        json={
            'username': 'reviewer',
            'password': 'reviewer-password-123',
        },
    )

    assert response.status_code == 200
    assert response.json()['data']['user']['id'] == 'reviewer-id'
    assert len(db.sessions.inserted) == 1
    assert response.cookies.get('ml_session')


def test_reviewer_login_does_not_grant_admin_access(monkeypatch):
    monkeypatch.setenv('REVIEW_MODE', '1')
    monkeypatch.delenv('ACTIVE_MODES', raising=False)
    user = {
        'id': 'reviewer-id',
        'username': 'reviewer',
        'role': 'reviewer',
        'password': hash_password('reviewer-password-123'),
    }
    db = FakeDatabase(user=user)
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(auth_module, 'database', lambda mode: db)
    monkeypatch.setattr(api, 'rate_limit', lambda *args: None)

    login_response = request(
        'POST',
        '/api/v1/auth/login?mode=imported',
        json={
            'username': 'reviewer',
            'password': 'reviewer-password-123',
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()['data']['user']['role'] == 'reviewer'
    session_cookie = login_response.cookies.get('ml_session')
    assert session_cookie
    assert len(db.sessions.inserted) == 1
    admin_request = Request({
        'type': 'http',
        'method': 'GET',
        'path': '/api/v1/admin',
        'headers': [
            (b'cookie', f'ml_session={session_cookie}'.encode()),
        ],
    })
    assert admin_request.cookies.get('ml_session') == session_cookie
    session_token = hashlib.sha256(
        session_cookie.encode()
    ).hexdigest()
    session = db.sessions.find_one({
        'token': session_token,
        'expires': {'$gt': datetime.now(timezone.utc)},
    })
    assert session is not None
    assert db.users.find_one({'id': session['user_id']}) is user
    with pytest.raises(api.HTTPException) as error:
        api.admin(admin_request, mode='imported')
    assert error.value.status_code == 403


def test_review_mode_reads_cached_analysis_without_writing(monkeypatch):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: True)
    data = {'value': 'cached', 'provenance': {'contains_synthetic': False}}
    cached = {
        'product_id': 'product-1',
        'cutoff': '2026-01-01T00:00:00+00:00',
        'version': '1:90:7:flexible:None',
        'result': data,
        'synthetic': False,
    }
    db = FakeDatabase(cached_analysis=cached)
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(
        api,
        'cutoff_for',
        lambda mode, cutoff: '2026-01-01T00:00:00+00:00',
    )
    monkeypatch.setattr(
        api,
        'analyze',
        lambda *args: pytest.fail('Cached analysis should be returned'),
    )

    result = api.analysis('product-1', mode='imported')

    assert result['data'] == data
    assert db.analyses.updates == []


@pytest.mark.parametrize('endpoint', ['products', 'overview'])
def test_review_get_falls_back_without_summary_writes(
    endpoint,
    monkeypatch,
    raw,
):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(
        api,
        'cutoff_for',
        lambda mode, cutoff=None: '2026-01-01T12:00:00+00:00',
    )
    product, observation = normalize(raw, 'imported', 'run')
    db = ReadDatabase([product], [observation], [])
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(
        summaries,
        'sync_product_summaries',
        lambda *args, **kwargs: pytest.fail('GET attempted summary sync'),
    )
    monkeypatch.setattr(
        summaries,
        'ensure_summary_indexes',
        lambda *args, **kwargs: pytest.fail('GET attempted summary indexes'),
    )

    if endpoint == 'products':
        response = api.products(mode='imported', page=1, limit=12)
        assert response['data']['total'] == 1
        assert response['data']['items'][0]['id'] == product['id']
    else:
        response = api.overview(mode='imported')
        assert response['data']['products'] == 1
        assert response['data']['observations'] == 1
        assert response['data']['candidate_count'] == 0
        assert len(response['data']['coverage']) == 1


def test_review_get_recomputes_incomplete_summaries(monkeypatch, raw):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: True)
    product, observation = normalize(raw, 'imported', 'run')
    incomplete = {
        'id': product['id'],
        'title': product['title'],
        'category': product['category'],
        'brand': product['brand'],
        'currency': product['currency'],
        'marketplace': product['marketplace'],
        'condition': product['condition'],
        'current': None,
    }
    db = ReadDatabase([product], [observation], [incomplete])

    result = summaries.read_product_summaries(
        db,
        '2026-01-01T12:00:00+00:00',
    )

    assert len(result) == 1
    assert result[0]['current'] == observation['total']
    assert result[0]['observations_count'] == 1


@pytest.mark.parametrize(
    'path',
    [
        ('discount', 'scope'),
        ('discount', 'current'),
        ('quality', 'eligible', 'discount'),
        ('latest', 'total'),
    ],
)
def test_malformed_nested_summaries_trigger_read_only_fallback(
    monkeypatch,
    raw,
    path,
):
    monkeypatch.setattr(summaries, 'review_mode_enabled', lambda: True)
    summaries.clear_product_summary_cache()
    product, observation = normalize(raw, 'imported', 'run')
    observation['ingested_at'] = '2026-01-01T12:00:00+00:00'
    complete = summaries.compute_product_summary_from_rows(
        product,
        [observation],
        '2026-01-02T12:00:00+00:00',
    )
    malformed = deepcopy(complete)
    target = malformed
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    db = ReadDatabase([product], [observation], [malformed])

    result = summaries.read_product_summaries(
        db,
        '2026-01-02T12:00:00+00:00',
    )

    assert result[0]['id'] == product['id']
    assert db.observations.find_calls == 1
    summaries.clear_product_summary_cache()


def test_repeated_fallback_uses_bounded_process_cache_without_stale_freshness(
    monkeypatch,
    raw,
):
    monkeypatch.setattr(summaries, 'review_mode_enabled', lambda: True)
    summaries.clear_product_summary_cache()
    product, observation = normalize(raw, 'imported', 'run')
    observation['ingested_at'] = '2026-01-01T12:00:00+00:00'
    db = ReadDatabase([product], [observation], [])

    first = summaries.read_product_summaries(
        db,
        '2099-01-01T12:00:00+00:00',
    )
    second = summaries.read_product_summaries(
        db,
        '2099-01-01T13:00:00+00:00',
    )

    assert db.observations.find_calls == 1
    assert (
        second[0]['quality']['freshness_hours']
        - first[0]['quality']['freshness_hours']
    ) == 1
    summaries.clear_product_summary_cache()


def test_fallback_cache_rescans_when_cutoff_reaches_future_observation(
    monkeypatch,
    raw,
):
    monkeypatch.setattr(summaries, 'review_mode_enabled', lambda: True)
    summaries.clear_product_summary_cache()
    product, observation = normalize(raw, 'imported', 'run')
    observation['ingested_at'] = '2026-01-01T12:00:00+00:00'
    future_observation = deepcopy(observation)
    future_observation['observed_at'] = '2100-01-01T12:00:00+00:00'
    future_observation['price'] = 5000
    future_observation['total'] = 5000
    db = ReadDatabase(
        [product],
        [observation, future_observation],
        [],
    )

    before = summaries.read_product_summaries(
        db,
        '2099-01-01T12:00:00+00:00',
    )
    after = summaries.read_product_summaries(
        db,
        '2101-01-01T12:00:00+00:00',
    )

    assert before[0]['current'] == observation['total']
    assert after[0]['current'] == 5000
    assert db.observations.find_calls == 2
    summaries.clear_product_summary_cache()


def test_direct_write_helpers_reject_review_mode(monkeypatch):
    monkeypatch.setattr(db_module, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(summaries, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(demo, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(alerts, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(jobs, 'review_mode_enabled', lambda: True)

    with pytest.raises(RuntimeError, match='index initialization'):
        db_module.indexes(None)
    with pytest.raises(RuntimeError, match='Summary index'):
        summaries.ensure_summary_indexes(None)
    with pytest.raises(RuntimeError, match='Summary writes'):
        summaries.build_summaries_batch(None, [])
    with pytest.raises(RuntimeError, match='Summary writes'):
        summaries.sync_product_summaries(None)
    with pytest.raises(RuntimeError, match='Demo data writes'):
        demo.seed(None)
    with pytest.raises(RuntimeError, match='Alert processing'):
        alerts.evaluate(None, '2026-01-01T00:00:00+00:00')
    with pytest.raises(RuntimeError, match='Collection job writes'):
        jobs.enqueue(None, [])
    with pytest.raises(RuntimeError, match='Schedule processing'):
        jobs.schedule_due(None)
    with pytest.raises(RuntimeError, match='Analysis job writes'):
        queue_analysis_jobs(None, [], 'imported', 'run')


def test_review_mode_computes_analysis_without_cache_write(monkeypatch):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: True)
    db = FakeDatabase()
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(
        api,
        'cutoff_for',
        lambda mode, cutoff: '2026-01-01T00:00:00+00:00',
    )
    monkeypatch.setattr(api, 'rows_for', lambda mode, pid: [{'id': 'row'}])
    monkeypatch.setattr(
        api,
        'analyze',
        lambda *args: {
            'value': 'computed',
            'provenance': {'contains_synthetic': False},
        },
    )

    result = api.analysis('product-1', mode='imported')

    assert result['data']['value'] == 'computed'
    assert db.analyses.updates == []


def test_normal_mode_still_writes_analysis_cache(monkeypatch):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: False)
    db = FakeDatabase()
    monkeypatch.setattr(api, 'database', lambda mode: db)
    monkeypatch.setattr(
        api,
        'cutoff_for',
        lambda mode, cutoff: '2026-01-01T00:00:00+00:00',
    )
    monkeypatch.setattr(api, 'rows_for', lambda mode, pid: [{'id': 'row'}])
    monkeypatch.setattr(
        api,
        'analyze',
        lambda *args: {'value': 'computed', 'provenance': {}},
    )

    result = api.analysis('product-1', mode='demo')

    assert result['data']['value'] == 'computed'
    assert len(db.analyses.updates) == 1


def test_review_mode_disables_worker_helpers_and_source_ingest(monkeypatch):
    monkeypatch.setattr(worker, 'review_mode_enabled', lambda: True)
    assert worker.claim(None, 'worker') is None
    assert worker.execute(None, {'id': 'job'}) is None
    assert worker.main() is None

    monkeypatch.setattr(db_module, 'review_mode_enabled', lambda: True)
    with pytest.raises(RuntimeError, match='Source-data writes'):
        ingest(None, [], 'imported', 'run')

    from app.services.history import seed_history
    with pytest.raises(RuntimeError, match='History generation'):
        seed_history(None)


def test_worker_claim_is_unchanged_when_review_mode_is_disabled(monkeypatch):
    monkeypatch.setattr(worker, 'review_mode_enabled', lambda: False)
    jobs = Collection()
    db = SimpleNamespace(jobs=jobs)

    result = worker.claim(
        db,
        'worker',
        clock='2026-01-01T12:00:00+00:00',
    )

    assert result['id'] == 'job'
    assert jobs.update_many_calls == 1


def test_review_direct_mutating_handlers_reject_before_database_access(
    monkeypatch,
):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: True)
    monkeypatch.setattr(
        api,
        'current',
        lambda *args, **kwargs: pytest.fail('Authentication ran before guard'),
    )
    monkeypatch.setattr(
        api,
        'database',
        lambda *args: pytest.fail('Database accessed before guard'),
    )
    monkeypatch.setattr(
        api,
        'rows_for',
        lambda *args: pytest.fail('Product rows accessed before guard'),
    )
    request_object = SimpleNamespace(method='PUT')

    handlers = [
        lambda: api.watch(
            api.Watch(product_id='product-1'),
            request_object,
            mode='imported',
        ),
        lambda: api.remove_watch(
            'product-1',
            request_object,
            mode='imported',
        ),
        lambda: api.read_notification(
            'notification-1',
            request_object,
            mode='imported',
        ),
        lambda: api.schedule(
            api.Schedule(urls=['https://example.com/product']),
            request_object,
            mode='live',
        ),
        lambda: api.delete_schedule(
            'schedule-1',
            request_object,
            mode='live',
        ),
        lambda: api.collect(
            api.Collect(urls=['https://example.com/product']),
            request_object,
            mode='live',
        ),
        lambda: api.reset(request_object, mode='demo'),
        lambda: api.clock(
            api.Clock(cutoff='2026-01-01T00:00:00+00:00'),
            request_object,
            mode='demo',
        ),
    ]

    for handler in handlers:
        with pytest.raises(api.HTTPException) as error:
            handler()
        assert error.value.status_code == 403


def test_normal_mode_direct_mutating_handlers_keep_existing_behavior(
    monkeypatch,
):
    monkeypatch.setattr(api, 'review_mode_enabled', lambda: False)
    monkeypatch.setattr(
        api,
        'current',
        lambda *args, **kwargs: {
            'id': 'admin-id',
            'role': 'admin',
        },
    )
    monkeypatch.setattr(api, 'rows_for', lambda *args: [])
    monkeypatch.setattr(api, 'product_url', lambda url: url)
    watchlists = Collection()
    notifications = Collection()
    schedules = Collection()
    db = SimpleNamespace(
        watchlists=watchlists,
        notifications=notifications,
        schedules=schedules,
    )
    monkeypatch.setattr(api, 'database', lambda mode: db)
    request_object = SimpleNamespace(method='PUT')

    api.watch(
        api.Watch(product_id='product-1'),
        request_object,
        mode='imported',
    )
    api.remove_watch('product-1', request_object, mode='imported')
    api.read_notification('notification-1', request_object, mode='imported')
    api.schedule(
        api.Schedule(urls=['https://example.com/product']),
        request_object,
        mode='live',
    )
    api.delete_schedule('schedule-1', request_object, mode='live')

    assert len(watchlists.updates) == 1
    assert len(watchlists.deletes) == 1
    assert len(notifications.updates) == 1
    assert len(schedules.inserted) == 1
    assert len(schedules.deletes) == 1
