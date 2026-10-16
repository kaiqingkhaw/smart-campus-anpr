import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Python_Code'))
import app as web
from export_utils import safe_csv_cell


@pytest.fixture
def client(monkeypatch):
    web.app.config.update(TESTING=True, SECRET_KEY='test-only-secret')
    def unexpected_database(*args, **kwargs):
        raise AssertionError('This request should not access the database')
    monkeypatch.setattr(web, 'get_conn', unexpected_database)
    return web.app.test_client()


@pytest.mark.parametrize('path', ['/dashboard', '/vehicles', '/logs', '/logs/export.csv', '/api/dashboard-snapshot'])
def test_private_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert response.headers['Location'] == '/login'


def test_csrf_rejects_external_referrer(client):
    response = client.post('/login', headers={'Referer': 'https://untrusted.example/'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/login'


def test_api_key_rejected_before_database(client, monkeypatch):
    monkeypatch.setattr(web, 'ANPR_API_KEY', 'device-test-key')
    assert client.post('/api/exit', json={'plate': 'WXY1234'}).status_code == 401
    assert client.get('/api/check?plate=WXY1234', headers={'X-API-Key': 'wrong'}).status_code == 401


def test_health_reports_database_failure(client):
    response = client.get('/health')
    assert response.status_code == 503
    assert response.json == {'status': 'degraded', 'database': 'unavailable'}


def test_login_template_and_headers(client):
    response = client.get('/login')
    assert response.status_code == 200
    assert b'csrf_token' in response.data
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('value', ['=1+1', '+SUM(A1)', '-1+2', '@SUM(A1)', '  =1', '\tdata', '\rdata', '\ndata'])
def test_csv_neutralises_formula_prefixes(value):
    assert safe_csv_cell(value) == "'" + value


def test_csv_preserves_plain_text():
    assert safe_csv_cell('WXY1234') == 'WXY1234'
    assert safe_csv_cell(None) == ''


def test_export_uses_safe_cells(client, monkeypatch):
    monkeypatch.setattr(web, 'fetch_all', lambda *args: [{'plate': 'WXY1234', 'details': '=1+1'}])
    with client.session_transaction() as session:
        session['admin_id'] = 1
    response = client.get('/logs/export.csv')
    assert response.status_code == 200
    assert "'=1+1" in response.get_data(as_text=True)


def test_forwarded_header_cannot_spoof_audit_ip():
    with web.app.test_request_context(headers={'X-Forwarded-For': 'forged'}, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert web.get_client_ip() == '127.0.0.1'


@pytest.mark.parametrize('payload', [None, [], ['WXY1234'], 'WXY1234', 1])
def test_exit_rejects_non_object_json(client, monkeypatch, payload):
    monkeypatch.setattr(web, 'ANPR_API_KEY', '')
    assert client.post('/api/exit', json=payload).status_code == 400


def test_api_exit_uses_shared_transaction(client, monkeypatch):
    monkeypatch.setattr(web, 'ANPR_API_KEY', '')
    monkeypatch.setattr(web, 'fetch_one', lambda *args: {'plate': 'WXY1234', 'category': 'Staff'})
    calls = []
    monkeypatch.setattr(web, 'release_parking_slot', lambda *args: calls.append(args) or True)
    response = client.post('/api/exit', json={'plate': 'WXY1234'})
    assert response.status_code == 200
    assert calls == [('Staff', 'WXY1234', 'Exit recorded (API)')]


def test_manual_exit_uses_shared_transaction(client, monkeypatch):
    monkeypatch.setattr(web, 'fetch_one', lambda *args: {'plate': 'WXY1234', 'category': 'Staff'})
    calls = []
    monkeypatch.setattr(web, 'release_parking_slot', lambda *args: calls.append(args) or True)
    with client.session_transaction() as session:
        session['admin_id'] = 1
        session['csrf_token'] = 'test-token'
    response = client.post('/parking/exit', data={'plate': 'WXY1234', 'csrf_token': 'test-token'})
    assert response.status_code == 302
    assert calls == [('Staff', 'WXY1234', 'Exit recorded')]


@pytest.mark.parametrize('path', ['/dashboard', '/vehicles', '/logs'])
def test_authenticated_pages_render_empty_database(client, monkeypatch, path):
    monkeypatch.setattr(web, 'fetch_all', lambda *args: [])
    monkeypatch.setattr(web, 'fetch_one', lambda *args: None)
    with client.session_transaction() as session:
        session['admin_id'] = 1
    response = client.get(path)
    assert response.status_code == 200
    assert b'Smart Campus ANPR' in response.data
