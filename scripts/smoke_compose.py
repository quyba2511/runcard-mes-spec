"""Smoke-test the Compose deployment without exposing its generated credentials."""
import json
from pathlib import Path
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
settings = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if line and not line.startswith('#'))
base = 'http://localhost:' + settings.get('HTTP_PORT', '8080')


def request(path, payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with urlopen(req, timeout=10) as response:
        return response.read()


assert json.loads(request('/api/health'))['status'] == 'ok'
for path in ('/admin', '/operator'):
    assert b'id="root"' in request(path), path
session = json.loads(request('/api/auth/login', {'username': 'admin', 'password': settings['DEMO_ADMIN_PASSWORD']}))
cards = json.loads(request('/api/cards', token=session['token']))
assert cards['total'] == 3
catalog = json.loads(request('/api/catalog', token=session['token']))
assert len(catalog['equipments']) == 6 and len(catalog['route_nodes']) == 7
assert json.loads(request('/api/work-orders', token=session['token'])) == []
assert len(json.loads(request('/api/lots', token=session['token']))) == 4
schema = json.loads(request('/openapi.json'))
assert schema['info']['title'] == 'RunCard MES' and '/api/work-orders' in schema['paths']
print('Compose smoke test passed: both apps, admin login, RunCard data, work orders, material lots and OpenAPI.')
