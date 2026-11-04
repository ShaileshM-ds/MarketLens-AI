import asyncio

import httpx
import pytest

import app.main as api


def request(path):
    async def send():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url='https://testserver',
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


@pytest.fixture
def frontend_build(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text(
        '<!doctype html><html><body>frontend entry</body></html>',
        encoding='utf-8',
    )
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'app.js').write_text('console.log("built asset");', encoding='utf-8')
    monkeypatch.setattr(api, 'STATIC_DIR', tmp_path)


def test_root_serves_frontend_with_security_headers(frontend_build):
    response = request('/')

    assert response.status_code == 200
    assert 'frontend entry' in response.text
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['referrer-policy'] == 'same-origin'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


def test_existing_frontend_asset_is_served(frontend_build):
    response = request('/assets/app.js')

    assert response.status_code == 200
    assert 'built asset' in response.text
    assert response.headers['content-type'].startswith('text/javascript')


def test_missing_static_asset_returns_404(frontend_build):
    response = request('/assets/missing.js')

    assert response.status_code == 404
    assert 'frontend entry' not in response.text


def test_unknown_frontend_route_returns_index(frontend_build):
    response = request('/products/example-product')

    assert response.status_code == 200
    assert 'frontend entry' in response.text


def test_unknown_api_route_returns_api_404(frontend_build):
    response = request('/api/v1/unknown')

    assert response.status_code == 404
    assert 'frontend entry' not in response.text
    assert response.json()['error']['message'] == 'Not Found'


def test_docs_and_openapi_remain_accessible(frontend_build):
    docs = request('/docs')
    openapi = request('/openapi.json')

    assert docs.status_code == 200
    assert 'swagger-ui' in docs.text.lower()
    assert openapi.status_code == 200
    assert '/api/v1/health' in openapi.json()['paths']


def test_health_and_readiness_routes_remain_api_responses(frontend_build, monkeypatch):
    class Database:
        def command(self, name):
            assert name == 'ping'
            return {'ok': 1}

    monkeypatch.setattr(api, 'database', lambda mode: Database())
    health = request('/api/v1/health')
    ready = request('/api/v1/ready')

    assert health.status_code == 200
    assert health.json()['data'] == {'status': 'ok'}
    assert ready.status_code == 200
    assert ready.json()['data'] == {'status': 'ready'}
    assert 'frontend entry' not in health.text
    assert 'frontend entry' not in ready.text


def test_existing_api_response_is_not_replaced_by_frontend(frontend_build):
    response = request('/api/v1/config')

    assert response.status_code == 200
    assert response.json()['data']['active_modes']
    assert 'frontend entry' not in response.text
