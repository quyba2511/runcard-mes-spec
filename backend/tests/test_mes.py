"""Extended MES functions (db/003_mes_extensions.sql + app/mes.py). Same expendable-database rule as test_integration."""
from uuid import uuid4
import os
import pytest
from fastapi.testclient import TestClient
from app.db import connection, one, all_rows
from app.main import app, attempts
from app.mes import format_outbox, pm_tick
from app.security import hash_password
from test_integration import ROOT, setup_db, client, headers, post, start, finish, pytestmark  # noqa: F401  (fixtures)


@pytest.mark.parametrize('already_extended', [False, True])
def test_startup_preserves_existing_database(monkeypatch, already_extended):
    monkeypatch.setenv('DATABASE_URL', os.environ['TEST_DATABASE_URL'])
    monkeypatch.setenv('SEED_DEMO_ACCOUNTS', 'true')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD', 'unused-seed-password')
    attempts.clear()
    with connection() as db:
        db.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        for name in ['schema.sql', 'seed_demo.sql', '002_runtime.sql']:
            db.execute((ROOT / 'db' / name).read_text())
        db.execute("INSERT INTO app_settings VALUES ('bootstrapped','true')")
        role = one(db, "SELECT id FROM roles WHERE code='admin'")
        db.execute("INSERT INTO permissions(code,description) VALUES ('backend:access','Backend access')")
        db.execute("INSERT INTO role_permissions VALUES (%s,'backend:access')", (role['id'],))
        db.execute("INSERT INTO accounts(username,display_name,role_id,password_hash) VALUES ('admin','Existing admin',%s,%s)",
                   (role['id'], hash_password('existing-admin-password')))
        original = one(db, """INSERT INTO run_cards(card_no,unit,route_free,original_qty,current_qty)
                             VALUES ('EXISTING','pcs',true,42,42) RETURNING id""")
        if already_extended:
            db.execute((ROOT / 'db' / '003_mes_extensions.sql').read_text())

    for _ in range(2):
        with TestClient(app) as running:
            login = running.post('/api/auth/login', json={'username': 'admin', 'password': 'existing-admin-password'})
            assert login.status_code == 200, login.text
            auth = {'Authorization': 'Bearer ' + login.json()['token']}
            cards = running.get('/api/cards', headers=auth).json()
            assert cards['total'] == 1
            assert cards['items'][0]['id'] == str(original['id'])
            assert float(cards['items'][0]['current_qty']) == 42
            assert running.get('/api/work-orders', headers=auth).json() == []
            assert len(running.get('/api/lots', headers=auth).json()) == 4


def get(client, s, path, user='admin', expected=200):
    r = client.get('/api' + path, headers=headers(s, user))
    assert r.status_code == expected, r.text
    return r.json() if 'json' in r.headers.get('content-type', '') else r


def put(client, s, path, data, user='admin'):
    r = client.put('/api' + path, json=data, headers=headers(s, user))
    assert r.status_code == 200, r.text
    return r.json()


def card(client, s, no):
    return next(c for c in get(client, s, '/cards?q=' + no)['items'] if c['card_no'] == no)


def sql(q, args=()):
    with connection() as db:
        db.execute(q, args)


def eq_id(s):
    return str(s['equipment']['id'])


def test_work_order_release_progress_and_close(client, setup_db):
    s = setup_db
    wo = post(client, s, '/work-orders', {'wo_no': 'WO-T1', 'item_no': 'PCBA-001', 'planned_qty': 250, 'lot_size': 100, 'priority': 2}, 'admin')
    r = post(client, s, f"/work-orders/{wo['id']}/release", {}, 'admin')
    assert [c['card_no'] for c in r['cards']] == ['WO-T1-001', 'WO-T1-002', 'WO-T1-003']
    assert [float(c['original_qty']) for c in r['cards']] == [100, 100, 50]
    post(client, s, f"/work-orders/{wo['id']}/release", {}, 'admin', expected=422)
    c = card(client, s, 'WO-T1-003')
    post(client, s, f"/cards/{c['id']}/check-in", {'version': c['version'], 'equipment_id': eq_id(s)})
    detail = get(client, s, f"/work-orders/{wo['id']}")
    assert detail['work_order']['status'] == 'IN_PROGRESS' and float(detail['work_order']['wip_qty']) == 250
    post(client, s, f"/work-orders/{wo['id']}/close", {'status': 'CLOSED', 'reason': 'x'}, 'admin', expected=422)
    post(client, s, '/work-orders', {'wo_no': 'WO-T2', 'planned_qty': 10}, 'operator', expected=403)


def test_auto_schedule_and_dispatch(client, setup_db):
    s = setup_db
    r = post(client, s, '/schedule/auto', {}, 'admin')
    assert r['planned'] == 3 and r['unplanned'] == []
    slots = get(client, s, f"/dispatch/{eq_id(s)}", 'operator')
    assert len(slots) == 3 and slots[0]['planned_end'] <= slots[1]['planned_start']
    c = start(client, s, 0)
    finish(client, s, c)
    assert all_rows_status(c['id']) == ['DONE']


def all_rows_status(card_id):
    with connection() as db:
        return [r['status'] for r in all_rows(db, "SELECT status FROM schedule_slots WHERE run_card_id=%s AND status<>'CANCELLED'", (card_id,))]


def test_material_issue_gate_and_traceability(client, setup_db):
    s = setup_db
    put(client, s, '/mes/settings', {'material_enforced': True})
    c = start(client, s, 0)
    r = client.post(f"/api/cards/{c['id']}/check-out", json={'version': c['version'], 'output_qty': 100, 'qc': [{'qc_item_id': 1, 'value': 125}]}, headers=headers(s))
    assert r.json()['code'] == 'MATERIAL'
    post(client, s, f"/cards/{c['id']}/issue-material", {'lot_no': 'LOT-IC-001', 'qty': 1}, expected=422)          # not in BOM at printing
    post(client, s, f"/cards/{c['id']}/issue-material", {'lot_no': 'LOT-PCB-001', 'qty': 600}, expected=422)       # more than the lot
    post(client, s, f"/cards/{c['id']}/issue-material", {'lot_no': 'LOT-PCB-001', 'qty': 100})
    post(client, s, f"/cards/{c['id']}/issue-material", {'lot_no': 'LOT-SOL-001', 'qty': 50})
    extras = get(client, s, f"/cards/{c['id']}/extras", 'operator')
    assert {b['code']: float(b['issued']) for b in extras['bom']} == {'DEMO-PCB': 100, 'DEMO-SOLDER': 50}
    finish(client, s, card(client, s, c['card_no']))
    lot = next(l for l in get(client, s, '/lots') if l['lot_no'] == 'LOT-PCB-001')
    assert float(lot['remaining_qty']) == 400
    t = get(client, s, '/trace/card/' + c['card_no'])
    assert {m['lot_no'] for m in t['tree']['materials']} == {'LOT-PCB-001', 'LOT-SOL-001'} and t['tree']['records'][0]['employee_no'] == 'OP001'
    f = get(client, s, '/trace/lot/LOT-PCB-001')
    assert f['used_by'][0]['card_no'] == c['card_no']


def test_first_article_and_skill_gates(client, setup_db):
    s = setup_db
    put(client, s, '/mes/settings', {'fai_enforced': True, 'skill_enforced': True})
    catalog = get(client, s, '/catalog')
    operation = next(op for op in catalog['operations'] if op['code'] == 'DEMO_PRT')
    qc_items = [{key: row[key] for key in ('id', 'name', 'unit', 'lower_limit', 'upper_limit')}
                for row in catalog['operation_qc_items'] if row['operation_id'] == operation['id']]
    config = {'code': operation['code'], 'name': operation['name'], 'qc_items': qc_items,
              'defect_code_ids': [row['defect_code_id'] for row in catalog['operation_defect_codes']
                                  if row['operation_id'] == operation['id']]}
    path = '/operations/DEMO_PRT'
    put(client, s, path, {**config, 'requires_first_article': True, 'requires_skill': True, 'std_cycle_sec': 24})
    edited = put(client, s, path, config)
    assert edited['requires_first_article'] and edited['requires_skill'] and float(edited['std_cycle_sec']) == 24
    c = s['cards'][0]
    body = {'version': c['version'], 'equipment_id': eq_id(s)}
    assert client.post(f"/api/cards/{c['id']}/check-in", json=body, headers=headers(s)).json()['code'] == 'SKILL'
    post(client, s, '/skills', {'employee_no': 'OP001', 'operation_code': 'DEMO_PRT', 'level': 2}, 'admin')
    assert get(client, s, '/skills')[0]['employee_no'] == 'OP001' and get(client, s, '/shifts')['shifts'][0]['start_time'] == '08:00:00'
    assert client.post(f"/api/cards/{c['id']}/check-in", json=body, headers=headers(s)).json()['code'] == 'FIRST_ARTICLE'
    op = one_val("SELECT id FROM operations WHERE code='DEMO_PRT'")
    fai = post(client, s, '/first-articles', {'equipment_id': eq_id(s), 'operation_id': op, 'result': 'PASS', 'note': 'ok'})
    assert get(client, s, '/first-articles?pending=true')[0]['id'] == fai['id']
    post(client, s, f"/first-articles/{fai['id']}/approve", {}, 'qa')
    post(client, s, f"/cards/{c['id']}/check-in", body)


def one_val(q, args=()):
    with connection() as db:
        return list(one(db, q, args).values())[0]


def test_breakdown_opens_maintenance_order_and_mttr(client, setup_db):
    s = setup_db
    c = start(client, s, 0)
    post(client, s, f"/equipments/{eq_id(s)}/report-breakdown", {'reason': 'Motor', 'reason_code': 'DU_1', 'card_versions': {c['id']: c['version']}})
    mo = get(client, s, '/maintenance-orders?status=OPEN')
    assert len(mo) == 1 and mo[0]['kind'] == 'BREAKDOWN' and mo[0]['reason_code'] == 'DU_1'
    post(client, s, f"/maintenance-orders/{mo[0]['id']}", {'action': 'finish'}, 'admin', expected=422)
    post(client, s, f"/maintenance-orders/{mo[0]['id']}", {'action': 'start'}, 'admin')
    post(client, s, f"/maintenance-orders/{mo[0]['id']}", {'action': 'finish', 'findings': 'Thay motor', 'parts_used': 'Motor x1'}, 'admin')
    assert one_val('SELECT reason_code FROM equipments WHERE id=%s', (s['equipment']['id'],)) == 'US_1'
    rep = get(client, s, '/reports/maintenance')
    assert rep[0]['breakdowns'] == 1 and rep[0]['mttr_hours'] is not None


def test_pm_overdue_and_tool_life_gates(client, setup_db):
    s = setup_db
    c = s['cards'][0]
    body = {'version': c['version'], 'equipment_id': eq_id(s)}
    put(client, s, '/mes/settings', {'pm_block_overdue': True})
    sql("UPDATE pm_plans SET next_due_at=now()-interval '1 day' WHERE equipment_id=%s", (s['equipment']['id'],))
    assert client.post(f"/api/cards/{c['id']}/check-in", json=body, headers=headers(s)).json()['code'] == 'PM_OVERDUE'
    with connection() as db:
        assert pm_tick(db) >= 1
    pm = [m for m in get(client, s, '/maintenance-orders') if m['kind'] == 'PM' and m['mes_code'] == 'DEMO-PRT01'][0]
    post(client, s, f"/maintenance-orders/{pm['id']}", {'action': 'finish', 'findings': 'ok', 'restore_reason_code': None}, 'admin')
    assert not [p for p in get(client, s, '/pm-plans') if p['mes_code'] == 'DEMO-PRT01'][0]['overdue']
    sql("UPDATE tools SET used=life_limit WHERE code='DEMO-STENCIL-01'")
    assert client.post(f"/api/cards/{c['id']}/check-in", json=body, headers=headers(s)).json()['code'] == 'TOOL_LIFE'
    post(client, s, '/tools/DEMO-STENCIL-01/step', {'action': 'reset'}, 'admin')
    c2 = post(client, s, f"/cards/{c['id']}/check-in", body)['card']
    finish(client, s, c2)
    assert float(one_val("SELECT used FROM tools WHERE code='DEMO-STENCIL-01'")) == 100


def test_reports_spc_pareto_export(client, setup_db):
    s = setup_db
    finish(client, s, start(client, s, 0), 95)
    c3 = start(client, s, 2)
    post(client, s, f"/cards/{c3['id']}/check-out", {'version': c3['version'], 'output_qty': 200, 'qc': [{'qc_item_id': 1, 'value': 125}]})
    oee = [r for r in get(client, s, '/reports/oee') if r['mes_code'] == 'DEMO-PRT01'][0]
    assert float(oee['processed']) == 300 and abs(oee['quality'] - 295 / 300) < 1e-9 and oee['availability'] is not None
    prod = get(client, s, '/reports/production?group=operator')
    assert prod[0]['key'] == 'OP001' and float(prod[0]['defect']) == 5
    assert get(client, s, '/reports/production?group=shift')[0]['key'] in ('A', 'B')
    assert get(client, s, '/reports/downtime')
    x = get(client, s, '/reports/oee.xlsx')
    assert x.content[:2] == b'PK'
    spc = get(client, s, '/quality/spc?qc_item_id=1')
    assert spc['stats']['n'] == 2 and spc['stats']['cp'] is None and spc['stats']['mean'] == 125
    assert get(client, s, '/quality/pareto')[0]['cumulative_pct'] == 100.0
    get(client, s, '/reports/oee', 'operator', expected=403)


def test_ncr_lifecycle(client, setup_db):
    s = setup_db
    n = post(client, s, '/ncrs', {'card_no': 'DEMO-RC-0001', 'defect_code': 'DEMO-D01', 'qty': 5, 'description': 'Cầu thiếc'}, 'qa')
    assert n['ncr_no'].startswith('NCR')
    post(client, s, f"/ncrs/{n['id']}", {'status': 'CLOSED'}, 'qa', expected=422)
    post(client, s, f"/ncrs/{n['id']}", {'status': 'CLOSED', 'root_cause': 'Stencil mòn', 'corrective_action': 'Thay stencil'}, 'qa')
    assert get(client, s, '/ncrs?status=CLOSED')[0]['id'] == n['id']


def test_documents_sop_release(client, setup_db):
    s = setup_db
    h = headers(s, 'admin')
    r = client.post('/api/documents', headers=h, data={'doc_no': 'SOP-PRT', 'version': 'A', 'title': 'In kem hàn', 'operation_code': 'DEMO_PRT'},
                    files={'file': ('sop.pdf', b'%PDF-1.4 demo', 'application/pdf')})
    assert r.status_code == 200, r.text
    d = r.json()
    get(client, s, f"/documents/{d['id']}/file", 'operator', expected=404)
    post(client, s, f"/documents/{d['id']}/release", {}, 'admin')
    extras = get(client, s, f"/cards/{s['cards'][0]['id']}/extras", 'operator')
    assert extras['docs'][0]['doc_no'] == 'SOP-PRT'
    assert get(client, s, f"/documents/{d['id']}/file", 'operator').content.startswith(b'%PDF')


def test_devices_machine_and_erp(client, setup_db):
    s = setup_db
    tab = post(client, s, '/devices', {'name': 'Tablet PRT', 'kind': 'TABLET'}, 'admin')
    login = lambda tok: client.post('/api/auth/operator', json={'employee_no': 'OP001'}, headers={'X-Device-Token': tok})
    assert login(tab['token']).status_code == 200
    post(client, s, f"/devices/{tab['id']}/revoke", {}, 'admin')
    assert login(tab['token']).status_code == 401
    m = post(client, s, '/devices', {'name': 'PLC PRT', 'kind': 'MACHINE', 'equipment_mes_code': 'DEMO-PRT01'}, 'admin')
    for state, cnt in [('RUN', 10), ('DOWN', 20), ('DOWN', 20)]:
        assert client.post('/api/machine/telemetry', json={'state': state, 'counter': cnt}, headers={'X-Device-Token': m['token']}).status_code == 200
    assert one_val("SELECT count(*) FROM notification_jobs WHERE event_code='EQUIPMENT_ABNORMAL'") == 1
    assert client.post('/api/machine/telemetry', json={'state': 'RUN'}, headers={'X-Device-Token': 'nope'}).status_code == 401
    erp = post(client, s, '/devices', {'name': 'SAP', 'kind': 'ERP'}, 'admin')
    eh = {'X-Device-Token': erp['token']}
    wo = {'external_id': 'SAP-1', 'wo_no': 'WO-ERP-1', 'item_no': 'PCBA-001', 'planned_qty': 40}
    assert client.post('/api/erp/work-orders', json=wo, headers=eh).json()['status'] == 'APPLIED'
    assert client.post('/api/erp/work-orders', json=wo, headers=eh).json()['duplicate'] is True
    bad = client.post('/api/erp/boms', json={'external_id': 'B1', 'item_no': 'X', 'version': '1', 'lines': [{'material_code': 'NOPE', 'qty_per': 1}]}, headers=eh).json()
    assert bad['status'] == 'REJECTED'
    rec = get(client, s, '/erp/reconcile')
    assert {(r['kind'], r['status']) for r in rec['inbound']} == {('work-order', 'APPLIED'), ('bom', 'REJECTED')}
    put(client, s, '/mes/settings', {'erp_format': 'wip'})
    finish(client, s, start(client, s, 0))
    with connection() as db:
        ev = one(db, "SELECT * FROM sync_outbox WHERE event_type='check-out' ORDER BY id DESC LIMIT 1")
        out = format_outbox(db, ev)
    assert out['TransactionType'] == 'CHECKOUT' and out['LotSerial'] == 'DEMO-RC-0001' and out['ExternalTransactionID'] == ev['idempotency_key']
    assert out['StepCode'] == 'DEMO_PRT' and out['Operator'] == 'OP001' and float(out['GoodQty']) == 100


def test_labels_and_serials(client, setup_db):
    s = setup_db
    c = s['cards'][1]
    assert 'RC:DEMO-RC-0002' in get(client, s, f"/labels/card/{c['id']}").text
    assert '^XA' in get(client, s, '/labels/lot/LOT-PCB-001').text
    r = post(client, s, f"/cards/{c['id']}/serials", {}, 'admin')
    assert r['created'] == 50 and r['last'] == 'DEMO-RC-0002-00050'
    assert get(client, s, '/trace/card/DEMO-RC-0002-00007')['tree']['card']['card_no'] == 'DEMO-RC-0002'
