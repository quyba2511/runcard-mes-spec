"""Run only against an expendable database supplied through TEST_DATABASE_URL."""
import os
from pathlib import Path
from uuid import uuid4
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from app.db import connection,one,all_rows
from app.bootstrap import bootstrap
from app.main import app,attempts
from app.security import session

pytestmark=pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'),reason='Set TEST_DATABASE_URL to a dedicated expendable PostgreSQL database')
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def setup_db(monkeypatch):
    monkeypatch.setenv('DATABASE_URL',os.environ['TEST_DATABASE_URL'])
    monkeypatch.setenv('SEED_DEMO_ACCOUNTS','true')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD','test-only-password')
    monkeypatch.setenv('DEVICE_TOKEN','test-device-token')
    with connection() as db:
        db.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        for name in ['schema.sql','seed_demo.sql','002_runtime.sql']:
            db.execute((ROOT/'db'/name).read_text())
    bootstrap()
    attempts.clear()
    with connection() as db:
        identities={}
        for username,mode in [('admin','admin'),('operator','operator'),('qa','admin')]:
            a=one(db,'SELECT id FROM accounts WHERE username=%s',(username,))
            identities[username]=session(db,a['id'],mode)
        cards=all_rows(db,'SELECT * FROM run_cards ORDER BY card_no')
        equipment=one(db,"SELECT * FROM equipments WHERE mes_code='DEMO-PRT01'")
    return {'auth':identities,'cards':cards,'equipment':equipment}

@pytest.fixture
def client(setup_db):
    return TestClient(app)

def headers(s,user='operator',key=None):
    return {'Authorization':'Bearer '+s['auth'][user]['token'],'Idempotency-Key':key or str(uuid4())}

def post(client,s,path,data,user='operator',expected=200,key=None):
    r=client.post('/api'+path,json=data,headers=headers(s,user,key))
    assert r.status_code==expected,r.text
    return r.json()

def start(client,s,index=0):
    c=s['cards'][index]
    return post(client,s,f"/cards/{c['id']}/check-in",{'version':c['version'],'equipment_id':str(s['equipment']['id'])})['card']

def finish(client,s,c,qty=100):
    return post(client,s,f"/cards/{c['id']}/check-out",{'version':c['version'],'output_qty':qty,'defects':[] if qty==100 else [{'defect_code_id':1,'qty':100-qty}],'qc':[{'qc_item_id':1,'value':125}]})

def test_auth_and_seed(client,setup_db):
    s=setup_db
    assert client.get('/api/cards').status_code==401
    r=post(client,s,'/auth/login',{'username':'admin','password':'test-only-password'})
    assert r['actor']['mode']=='admin'
    assert client.post('/api/auth/operator',json={'employee_no':'OP001'},headers={'X-Device-Token':'bad'}).status_code==401
    r=client.post('/api/auth/operator',json={'employee_no':'OP001'},headers={'X-Device-Token':'test-device-token'})
    assert r.status_code==200
    assert r.json()['actor']['employee_no']=='OP001'
    r=client.get('/api/catalog',headers=headers(s))
    assert r.status_code==200 and len(r.json()['equipments'])==6
    assert 'accounts' not in r.json()
    assert len(client.get('/api/cards',headers=headers(s)).json()['items'])==3


def test_complete_seven_station_route(client,setup_db):
    s=setup_db;c=start(client,s)
    c=finish(client,s,c)['card']
    for seq in range(2,8):
        with connection() as db:
            n=one(db,'SELECT n.*,o.code,o.requires_equipment FROM route_nodes n JOIN operations o ON o.id=n.operation_id WHERE seq=%s',(seq,))
            e=one(db,'SELECT equipment_id FROM equipment_operations WHERE operation_id=%s',(n['operation_id'],))
        body={'version':c['version']}
        if e:body['equipment_id']=str(e['equipment_id'])
        else:body['operation_code']=n['code']
        c=post(client,s,f"/cards/{c['id']}/check-in",body)['card']
        c=post(client,s,f"/cards/{c['id']}/check-out",{'version':c['version'],'output_qty':100})['card']
    assert c['status']=='COMPLETED' and Decimal(c['current_qty'])==100
    with connection() as db:
        assert one(db,'SELECT count(*) AS n FROM work_records WHERE run_card_id=%s',(c['id'],))['n']==7
        assert not one(db,'SELECT 1 FROM work_records WHERE run_card_id=%s AND ended_at IS NULL',(c['id'],))
        assert one(db,'SELECT count(*) AS n FROM equipment_status_history WHERE ended_at IS NULL')['n']==6
        assert one(db,'SELECT count(*) AS n FROM audit_logs')['n']==one(db,'SELECT count(*) AS n FROM sync_outbox')['n']


def test_idempotency_and_payload_conflict(client,setup_db):
    s=setup_db;c=s['cards'][0];body={'version':1,'equipment_id':str(s['equipment']['id'])};key=str(uuid4());path=f"/cards/{c['id']}/check-in"
    first=post(client,s,path,body,key=key)
    assert post(client,s,path,body,key=key)==first
    r=post(client,s,path,{**body,'version':2},expected=409,key=key)
    assert r['code']=='IDEMPOTENCY_CONFLICT'
    with connection() as db: assert one(db,'SELECT count(*) AS n FROM work_records')['n']==1


def test_rework_card_can_follow_its_own_route(client, setup_db):
    s = setup_db
    parent = s['cards'][0]
    route = {'route_name': 'Rework route', 'operation_ids': [1, 7]}
    draft = post(client, s, '/routes', {**route, 'version': 'v0', 'activate': False}, 'admin', 201)
    active = post(client, s, '/routes', {**route, 'route_id': draft['route_id'], 'version': 'v1'}, 'admin', 201)
    with connection() as db:
        nodes = all_rows(db, 'SELECT id FROM route_nodes WHERE route_version_id=%s ORDER BY seq', (active['id'],))
        draft_node = one(db, 'SELECT id FROM route_nodes WHERE route_version_id=%s ORDER BY seq LIMIT 1', (draft['id'],))
    path = f"/cards/{parent['id']}/rework"
    body = {'version': parent['version'], 'qty': 40}
    invalid = post(client, s, path, {**body, 'target_route_version_id': active['id'],
                                   'target_node_id': parent['current_node_id']}, 'admin', 422)
    assert invalid['code'] == 'ROUTE'
    invalid = post(client, s, path, {**body, 'target_route_version_id': draft['id'],
                                   'target_node_id': draft_node['id']}, 'admin', 422)
    assert invalid['code'] == 'ROUTE'
    request = {**body, 'target_route_version_id': active['id'], 'target_node_id': nodes[0]['id']}
    key = str(uuid4())
    result = post(client, s, path, request, 'admin', key=key)
    assert post(client, s, path, request, 'admin', key=key) == result
    original, rework = result['card'], result['children'][0]
    assert original['route_version_id'] == parent['route_version_id']
    assert original['current_node_id'] == parent['current_node_id']
    assert Decimal(original['current_qty']) == 60 and Decimal(original['rework_qty']) == 40
    assert rework['parent_card_id'] == str(parent['id']) and rework['card_no'].startswith('R')
    assert rework['route_version_id'] == active['id'] and rework['current_node_id'] == nodes[0]['id']
    card_path = f"/cards/{rework['id']}"
    started = post(client, s, card_path + '/check-in',
                   {'version': rework['version'], 'equipment_id': str(s['equipment']['id'])})['card']
    advanced = post(client, s, card_path + '/check-out',
                    {'version': started['version'], 'output_qty': 40, 'qc': [{'qc_item_id': 1, 'value': 125}]})['card']
    assert advanced['current_node_id'] == nodes[1]['id']


def test_stale_version_and_atomic_invalid_checkout(client,setup_db):
    s=setup_db;c=start(client,s)
    post(client,s,f"/cards/{c['id']}/check-out",{'version':1,'output_qty':100},expected=409)
    with connection() as db: count=one(db,'SELECT count(*) AS n FROM audit_logs')['n']
    post(client,s,f"/cards/{c['id']}/check-out",{'version':2,'output_qty':90,'defects':[{'defect_code_id':1,'qty':9}],'qc':[{'qc_item_id':1,'value':125}]},expected=422)
    with connection() as db:
        assert one(db,'SELECT count(*) AS n FROM audit_logs')['n']==count
        assert one(db,'SELECT count(*) AS n FROM run_cards')['n']==3
        assert one(db,'SELECT status FROM run_cards WHERE id=%s',(c['id'],))['status']=='IN_PROGRESS'


def test_opt01_multi_card_breakdown_atomic_rollback(client,setup_db):
    s=setup_db;c1=start(client,s,0);c2=start(client,s,1)
    path=f"/equipments/{s['equipment']['id']}/report-breakdown"
    bad={c1['id']:c1['version'],c2['id']:1}
    post(client,s,path,{'reason':'Motor failed','reason_code':'DU_1','card_versions':bad},expected=409)
    with connection() as db:
        assert one(db,"SELECT count(*) AS n FROM run_cards WHERE status='IN_PROGRESS'")['n']==2
        assert one(db,'SELECT reason_code FROM equipments WHERE id=%s',(s['equipment']['id'],))['reason_code']=='UP'
    result=post(client,s,path,{'reason':'Motor failed','reason_code':'DU_1','card_versions':{c1['id']:2,c2['id']:2}})
    assert result['equipment']['state']=='MAINTENANCE'
    with connection() as db:
        assert one(db,'SELECT count(*) AS n FROM work_records WHERE ended_at IS NULL')['n']==0
        assert one(db,"SELECT count(*) AS n FROM run_cards WHERE status='WAITING'")['n']==3
        assert one(db,'SELECT count(*) AS n FROM equipment_status_history WHERE equipment_id=%s AND ended_at IS NULL',(s['equipment']['id'],))['n']==1


@pytest.mark.parametrize('disposition,bucket',[('scrap','scrap_qty'),('return','returned_qty'),('transfer','transferred_qty')])
def test_opt04_cancellation_ledger(client,setup_db,disposition,bucket):
    s=setup_db;c=s['cards'][0]
    r=post(client,s,f"/cards/{c['id']}/cancel",{'version':1,'reason':'Order cancelled','disposition':disposition},user='admin')
    assert r['card']['status']=='CANCELLED' and Decimal(r['card'][bucket])==100
    assert len(r['children'])==(1 if disposition=='transfer' else 0)
    with connection() as db:
        m=one(db,'SELECT * FROM quantity_movements WHERE run_card_id=%s',(c['id'],))
        assert m['qty']==100 and m['disposition']==disposition


def test_br13_revoke_voids_unprocessed_defect(client,setup_db):
    s=setup_db;c=start(client,s);r=finish(client,s,c,95);child=r['children'][0]
    r=post(client,s,f"/cards/{c['id']}/revoke",{'version':3,'reason':'Incorrect output'},user='admin')
    assert Decimal(r['card']['current_qty'])==100 and r['card']['current_node_id']==1
    with connection() as db:
        d=one(db,'SELECT * FROM run_cards WHERE id=%s',(child['id'],))
        assert d['status']=='CANCELLED' and d['current_qty']==0 and d['transferred_qty']==5
        assert one(db,'SELECT revoked_at FROM work_records WHERE run_card_id=%s',(c['id'],))['revoked_at']


def test_br13_processed_defect_blocks_revoke(client,setup_db):
    s=setup_db;c=start(client,s);r=finish(client,s,c,95);child=r['children'][0]
    post(client,s,f"/cards/{child['id']}/scrap",{'version':1,'qty':1,'reason':'Confirmed bad'},user='qa')
    result=post(client,s,f"/cards/{c['id']}/revoke",{'version':3,'reason':'Wrong output'},user='admin',expected=422)
    assert result['code']=='BR-13'


def test_br13_split_after_work_blocks_revoke(client,setup_db):
    s=setup_db;c=finish(client,s,start(client,s))['card']
    c=post(client,s,f"/cards/{c['id']}/split",{'version':c['version'],'qty':10},user='admin')['card']
    r=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'undo'},user='admin',expected=422)
    assert r['code']=='BR-13'


def test_br13_repeated_revoke_walks_back_records(client,setup_db):
    s=setup_db;c=finish(client,s,start(client,s))['card']
    with connection() as db: eq=one(db,"SELECT id FROM equipments WHERE mes_code='DEMO-SMT01'")['id']
    c=post(client,s,f"/cards/{c['id']}/check-in",{'version':c['version'],'equipment_id':str(eq)})['card']
    c=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'undo second'},user='admin')['card']
    c=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'undo first'},user='admin')['card']
    assert c['current_node_id']==1 and c['status']=='WAITING'


def test_br07_br08_release_policy_and_operator_permissions(client,setup_db):
    s=setup_db;c=start(client,s)
    c=post(client,s,f"/cards/{c['id']}/check-out",{'version':2,'output_qty':100,'qc':[{'qc_item_id':1,'value':160}]})['card']
    assert c['status']=='QUALITY_HOLD' and c['current_node_id']==1
    post(client,s,f"/cards/{c['id']}/release",{'version':3,'reason':'ok'},expected=403)
    post(client,s,f"/cards/{c['id']}/release",{'version':3},user='qa',expected=422)
    r=client.put('/api/settings',json={'release_reason_required':False},headers=headers(s,'admin'))
    assert r.status_code==200,r.text
    c=post(client,s,f"/cards/{c['id']}/release",{'version':3},user='qa')['card']
    assert c['status']=='WAITING' and c['current_node_id']==1


def test_br10_single_use_supervisor_authorization(client,setup_db):
    s=setup_db;c=s['cards'][0]
    a=post(client,s,'/authorizations/skip',{'username':'admin','password':'test-only-password','run_card_id':str(c['id']),'target_node_id':2})
    with connection() as db: eq=one(db,"SELECT id FROM equipments WHERE mes_code='DEMO-SMT01'")['id']
    body={'version':1,'target_node_id':2,'equipment_id':str(eq),'authorization_token':a['authorization_token']}
    c=post(client,s,f"/cards/{c['id']}/skip",body)['card']
    c=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'mistake'},user='admin')['card']
    result=post(client,s,f"/cards/{c['id']}/skip",{**body,'version':c['version']},expected=422)
    assert result['code']=='BR-10'


def test_opt03_single_use_employee_session(client,setup_db):
    s=setup_db
    r=client.post('/api/auth/operator',json={'employee_no':'OP001','reuse':False},headers={'X-Device-Token':'test-device-token'})
    s['auth']['operator']=r.json()
    c=start(client,s)
    post(client,s,f"/cards/{c['id']}/check-out",{'version':2,'output_qty':100},expected=401)


def test_equipment_occupied_and_history(client,setup_db):
    s=setup_db;start(client,s);p=f"/equipments/{s['equipment']['id']}/set-equipment-status"
    post(client,s,p,{'reason_code':'DU_1'},expected=422)
    post(client,s,p,{'reason_code':'US_5'})
    with connection() as db:
        assert one(db,'SELECT count(*) AS n FROM equipment_status_history WHERE equipment_id=%s AND ended_at IS NULL',(s['equipment']['id'],))['n']==1


def test_admin_backfill_reason_actual_worker_and_time(client,setup_db):
    s=setup_db;c=s['cards'][0]
    body={'version':1,'equipment_id':str(s['equipment']['id']),'actual_operator_id':str(s['auth']['operator']['actor']['id']),'reason':'Late record'}
    r=post(client,s,f"/cards/{c['id']}/backfill",body,user='admin')
    assert r['card']['status']=='IN_PROGRESS'
    post(client,s,f"/cards/{c['id']}/backfill",{**body,'version':2,'output_qty':100,'qc':[{'qc_item_id':1,'value':125}],'occurred_at':'2000-01-01T00:00:00Z'},user='admin',expected=422)
    with connection() as db: assert one(db,'SELECT is_backfilled FROM work_records')['is_backfilled']


def test_scan_prefixes_and_permissions(client,setup_db):
    s=setup_db
    for code,kind in [('RC:DEMO-RC-0001','runcard'),('EQ:DEMO-PRT01','equipment'),('EMP:OP001','employee'),('OP:DEMO_PKG','operation')]:
        r=client.get('/api/scan',params={'code':code},headers=headers(s))
        assert r.status_code==200,r.text
        assert r.json()['kind']==kind
    assert client.get('/api/admin/audit',headers=headers(s)).status_code==403
    assert client.get('/api/admin/audit',headers=headers(s,'qa')).status_code==403
    assert client.get('/api/admin/audit',headers=headers(s,'admin')).status_code==200


def test_notifications_targets_and_outbox_not_falsely_sent(client,setup_db):
    s=setup_db;c=start(client,s)
    post(client,s,f"/cards/{c['id']}/abnormal-end",{'version':2,'reason':'Unusual material','disposition':'mark'})
    r=client.get('/api/notifications',headers=headers(s,'qa')).json()
    assert r['unread']>0
    from app.worker import tick
    tick()
    with connection() as db:
        assert not one(db,"SELECT 1 FROM sync_outbox WHERE status='SENT'")
        db.execute("UPDATE notification_jobs SET next_at=now()-interval '1 minute',created_at=now()-interval '1 hour'")
    tick()
    with connection() as db:
        assert one(db,'SELECT escalated FROM notification_jobs')['escalated']


def test_xlsx_validation_rolls_back_and_commit_is_atomic(client,setup_db):
    from io import BytesIO
    from openpyxl import Workbook
    s=setup_db;wb=Workbook();ws=wb.active
    ws.append(['card_no','unit','original_qty','route_version_id']);ws.append(['NEW-001','pcs',50,1]);buf=BytesIO();wb.save(buf)
    for validate in [True,False]:
        r=client.post('/api/import/cards?validate_only='+str(validate).lower(),headers=headers(s,'admin'),files={'file':('cards.xlsx',buf.getvalue())})
        assert r.status_code==200,r.text
        with connection() as db: assert one(db,"SELECT count(*) AS n FROM run_cards WHERE card_no='NEW-001'")['n']==(0 if validate else 1)


def test_master_configuration_and_route_version_fixed(client,setup_db):
    s=setup_db
    r=post(client,s,'/routes',{'route_id':1,'route_name':'DEMO','version':'v2.0','operation_ids':[1,3,7],'activate':True},user='admin',expected=201)
    assert r['version']=='v2.0'
    with connection() as db:
        assert one(db,'SELECT route_version_id FROM run_cards LIMIT 1')['route_version_id']==1
    r=client.put('/api/equipment-config/NEW-EQ',json={'name':'New machine','operation_ids':[1]},headers=headers(s,'admin'))
    assert r.status_code==200,r.text
    assert r.json()['state']=='OFF'
    post(client,s,'/cards',{'card_no':'NO-ROUTE','unit':'pcs','original_qty':5,'route_free':True},user='admin',expected=201)


def test_revoke_cannot_erase_split_before_later_work(client,setup_db):
    s=setup_db;c=finish(client,s,start(client,s))['card']
    c=post(client,s,f"/cards/{c['id']}/split",{'version':c['version'],'qty':10},user='admin')['card']
    with connection() as db: eq=one(db,"SELECT id FROM equipments WHERE mes_code='DEMO-SMT01'")['id']
    c=post(client,s,f"/cards/{c['id']}/check-in",{'version':c['version'],'equipment_id':str(eq)})['card']
    c=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'Undo second'},user='admin')['card']
    r=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'Must not erase split'},user='admin',expected=422)
    assert r['code']=='BR-13'
    assert Decimal(c['split_qty'])==10


def test_release_then_revoke_preserves_quantity(client,setup_db):
    s=setup_db;c=start(client,s)
    c=post(client,s,f"/cards/{c['id']}/check-out",{'version':2,'output_qty':100,'qc':[{'qc_item_id':1,'value':160}]})['card']
    c=post(client,s,f"/cards/{c['id']}/release",{'version':c['version'],'reason':'QA approves'},user='qa')['card']
    c=post(client,s,f"/cards/{c['id']}/revoke",{'version':c['version'],'reason':'Incorrect work record'},user='admin')['card']
    assert c['status']=='WAITING' and Decimal(c['current_qty'])==100
