import asyncio
import hmac
import json
import os
import secrets
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, Depends, Header, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from openpyxl import load_workbook, Workbook
from .db import connection, one, all_rows, clean, jb, lock
from .domain import RuleError, require
from .security import authenticate, session, verify, allowed, digest, hash_password, PERMISSIONS
from .bootstrap import bootstrap
from .service import run_command, card_action, create_card, set_equipment, report_breakdown, audit
from . import mes

@asynccontextmanager
async def lifespan(app):
    bootstrap()
    yield

app=FastAPI(title='RunCard MES',version='1.0.0',lifespan=lifespan,description='Bilingual RunCard MES. All writes require authentication and an Idempotency-Key. Card actions require the observed version.')

@app.exception_handler(RuleError)
async def rule_error(request, exc):
    code=401 if exc.code=='UNAUTHORIZED' else 403 if exc.code=='FORBIDDEN' else 404 if exc.code=='NOT_FOUND' else 409 if exc.code in {'VERSION_CONFLICT','IDEMPOTENCY_CONFLICT','BR-05'} else 422
    return JSONResponse(status_code=code,content={'code':exc.code,'detail':str(exc)})

@app.exception_handler(psycopg.IntegrityError)
async def integrity_error(request,exc):
    return JSONResponse(status_code=409,content={'code':'CONSTRAINT','detail':'Data conflicts with an existing record or database constraint; refresh and review.'})

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Login(Strict):
    username:str
    password:str

class EmployeeLogin(Strict):
    employee_no:str
    reuse:bool=True

class Defect(Strict):
    defect_code_id:int
    qty:Decimal=Field(gt=0)
    remark:str|None=None

class QC(Strict):
    qc_item_id:int
    value:Decimal

class Action(Strict):
    version:int=Field(ge=1)
    reason:str|None=None
    equipment_id:UUID|None=None
    operation_id:int|None=None
    operation_code:str|None=None
    output_qty:Decimal|None=Field(default=None,ge=0)
    qty:Decimal|None=Field(default=None,gt=0)
    defects:list[Defect]=Field(default_factory=list)
    qc:list[QC]=Field(default_factory=list)
    production_note:str|None=None
    disposition:Literal['keep','mark','scrap','return','transfer']|None=None
    target_node_id:int|None=None
    authorization_token:str|None=None
    actual_operator_id:UUID|None=None
    occurred_at:datetime|None=None
    finish_route_free:bool=False
    fields:dict[str,str]|None=None

class NewCard(Strict):
    card_no:str=Field(min_length=1,max_length=160)
    work_order_no:str|None=None
    item_no:str|None=None
    item_name:str|None=None
    unit:str=Field(default='pcs',min_length=1)
    original_qty:Decimal=Field(gt=0)
    route_free:bool=False
    route_version_id:int|None=None

class EquipmentStatus(Strict):
    reason_code:str
    reason:str=''

class Breakdown(Strict):
    reason_code:str
    reason:str=Field(min_length=1)
    card_versions:dict[str,int]

class Authorization(Login):
    run_card_id:UUID
    target_node_id:int

Key=Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=160)]

def actor(authorization:Annotated[str|None,Header()]=None):
    require(authorization and authorization.startswith('Bearer '),'UNAUTHORIZED','Bearer token required')
    with connection() as db:
        return authenticate(db,authorization[7:])

Actor=Annotated[dict,Depends(actor)]

def admin(a, permission='backend:access'):
    require(a['mode']=='admin','FORBIDDEN','Admin session required')
    allowed(a,'backend:access')
    allowed(a,permission)

attempts=defaultdict(deque)
def throttle(request):
    import time
    key=request.client.host
    now=time.monotonic()
    q=attempts[key]
    while q and q[0]<now-60: q.popleft()
    if len(q)>=20: raise HTTPException(429,'Too many authentication attempts; wait one minute')
    q.append(now)

@app.get('/api/health',tags=['system'])
def health():
    with connection() as db: one(db,'SELECT 1')
    return {'status':'ok'}

@app.post('/api/auth/login',tags=['auth'])
def login(body:Login,request:Request):
    throttle(request)
    with connection() as db:
        a=one(db,'SELECT * FROM accounts WHERE username=%s AND is_active',(body.username,))
        require(a and verify(body.password,a['password_hash']),'UNAUTHORIZED','Invalid credentials')
        return clean(session(db,a['id'],'admin'))

@app.post('/api/auth/operator',tags=['auth'])
def employee_login(body:EmployeeLogin,request:Request,x_device_token:Annotated[str|None,Header()]=None):
    throttle(request)
    expected=os.environ.get('DEVICE_TOKEN','')
    with connection() as db:
        # Shared bootstrap token (env) or a per-tablet token issued under 'Tích hợp → Thiết bị' that can be revoked alone.
        tablet=one(db,"SELECT id FROM devices WHERE token_hash=%s AND kind='TABLET' AND is_active",(digest(x_device_token or ''),))
        require(tablet or (expected and x_device_token and hmac.compare_digest(expected,x_device_token)),'UNAUTHORIZED','Provisioned device token required')
        if tablet: db.execute('UPDATE devices SET last_seen_at=now() WHERE id=%s',(tablet['id'],))
        a=one(db,'SELECT id FROM accounts WHERE employee_no=%s AND is_active',(body.employee_no,))
        require(a,'UNAUTHORIZED','Unknown employee number')
        return clean(session(db,a['id'],'operator',body.reuse))

@app.get('/api/auth/me',tags=['auth'])
def me(a:Actor):
    return clean({k:v for k,v in a.items() if k not in {'token_hash','single_use'}})

@app.post('/api/auth/logout',tags=['auth'])
def logout(a:Actor):
    with connection() as db: db.execute('DELETE FROM auth_sessions WHERE token_hash=%s',(a['token_hash'],))
    return {'ok':True}

@app.post('/api/authorizations/skip',tags=['operator'])
def authorize_skip(body:Authorization,a:Actor,request:Request):
    throttle(request)
    with connection() as db:
        supervisor=one(db,'SELECT * FROM accounts WHERE username=%s AND is_active',(body.username,))
        require(supervisor and verify(body.password,supervisor['password_hash']),'UNAUTHORIZED','Invalid supervisor credentials')
        permitted=one(db,"SELECT 1 FROM role_permissions WHERE role_id=%s AND permission_code='work_records:skip_node'",(supervisor['role_id'],))
        require(permitted,'FORBIDDEN','Supervisor cannot authorize skip')
        valid=one(db,'SELECT 1 FROM run_cards c JOIN route_nodes t ON t.route_version_id=c.route_version_id JOIN route_nodes n ON n.id=c.current_node_id WHERE c.id=%s AND t.id=%s AND t.seq>n.seq',(body.run_card_id,body.target_node_id))
        require(valid,'BR-10','Target must be a later node of this route')
        token=secrets.token_urlsafe(32)
        db.execute("INSERT INTO skip_authorizations(token_hash,account_id,run_card_id,target_node_id,expires_at) VALUES(%s,%s,%s,%s,now()+interval '2 minutes')",(digest(token),supervisor['id'],body.run_card_id,body.target_node_id))
        audit(db,a,'authorize-skip',payload={'supervisor_id':supervisor['id'],'card_id':body.run_card_id,'target_node_id':body.target_node_id})
        return {'authorization_token':token,'expires_in':120}

@app.get('/api/scan',tags=['operator'])
def scan(code:str,a:Actor):
    code=code.strip()
    prefix,_,value=code.partition(':')
    if prefix.upper() in {'RC','EQ','EMP','OP'}: code=value
    else: prefix=''
    with connection() as db:
        matches=[]
        for kind,table,col,fields in [('runcard','run_cards','card_no','id,card_no'),('equipment','equipments','mes_code','id,mes_code'),('employee','accounts','employee_no','id,employee_no,display_name'),('operation','operations','code','id,code,name')]:
            if prefix and {'RC':'runcard','EQ':'equipment','EMP':'employee','OP':'operation'}[prefix.upper()]!=kind: continue
            active=' AND is_active' if table in {'accounts','equipments','operations'} else ''
            row=one(db,f'SELECT {fields} FROM {table} WHERE {col}=%s{active}',(code,))
            if row: matches.append({'kind':kind,'value':row})
        require(matches,'NOT_FOUND','Barcode not found')
        require(len(matches)==1,'AMBIGUOUS_SCAN','Use RC:, EQ:, EMP: or OP: prefix')
        return clean(matches[0])

@app.get('/api/catalog',tags=['shared'])
def catalog(a:Actor):
    with connection() as db:
        tables=['operations','operation_qc_items','defect_codes','operation_defect_codes','equipments','equipment_operations','equipment_reasons','equipment_reason_whitelist','process_routes','process_route_versions','route_nodes']
        result={t:all_rows(db,f'SELECT * FROM {t}') for t in tables}
        result['settings']={r['key']:r['value'] for r in all_rows(db,"SELECT * FROM app_settings WHERE key='release_reason_required'")}
        if a['mode']=='admin':
            result['accounts']=all_rows(db,'SELECT id,username,display_name,employee_no,role_id,is_active,email FROM accounts ORDER BY username')
            result['roles']=all_rows(db,'SELECT * FROM roles ORDER BY id')
            result['role_permissions']=all_rows(db,'SELECT * FROM role_permissions')
            result['permissions']=PERMISSIONS
        return clean(result)

@app.get('/api/cards',tags=['shared'])
def cards(a:Actor,q:str='',status:str|None=None,source_type:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
    with connection() as db:
        args=('%'+q+'%',status or None,status or None,source_type or None,source_type or None)
        where="concat_ws(' ',c.card_no,c.work_order_no,c.item_no,c.item_name) ILIKE %s AND (%s::text IS NULL OR c.status::text=%s) AND (%s::text IS NULL OR c.source_type::text=%s)"
        rows=all_rows(db,f'SELECT c.*,o.name AS operation_name FROM run_cards c LEFT JOIN route_nodes n ON n.id=c.current_node_id LEFT JOIN operations o ON o.id=n.operation_id WHERE {where} ORDER BY c.created_at DESC,c.id LIMIT %s OFFSET %s',(*args,limit,offset))
        total=one(db,f'SELECT count(*) AS n FROM run_cards c WHERE {where}',args)['n']
        return clean({'items':rows,'total':total})

@app.get('/api/cards/{card_id}',tags=['shared'])
def card_detail(card_id:UUID,a:Actor):
    with connection() as db:
        c=one(db,'SELECT * FROM run_cards WHERE id=%s',(card_id,))
        require(c,'NOT_FOUND','Card not found')
        records=all_rows(db,'SELECT w.*,o.name AS operation_name,e.mes_code,COALESCE(a.employee_no,a.display_name) AS operator_name,a.display_name AS operator_display FROM work_records w JOIN operations o ON o.id=w.operation_id LEFT JOIN equipments e ON e.id=w.equipment_id JOIN accounts a ON a.id=w.operator_id WHERE w.run_card_id=%s ORDER BY started_at DESC,w.id DESC',(card_id,))
        route=one(db,'SELECT r.name,v.version FROM process_route_versions v JOIN process_routes r ON r.id=v.route_id WHERE v.id=%s',(c['route_version_id'],)) if c['route_version_id'] else None
        return clean({'card':c,'route':route,'records':records,'children':all_rows(db,'SELECT * FROM run_cards WHERE parent_card_id=%s',(card_id,)),
          'nodes':all_rows(db,'SELECT n.*,o.name,o.code FROM route_nodes n JOIN operations o ON o.id=n.operation_id WHERE n.route_version_id=%s ORDER BY seq',(c['route_version_id'],)),
          'defects':all_rows(db,'SELECT d.* FROM work_record_defects d JOIN work_records w ON w.id=d.work_record_id WHERE w.run_card_id=%s',(card_id,)),
          'qc':all_rows(db,'SELECT q.* FROM work_record_qc_values q JOIN work_records w ON w.id=q.work_record_id WHERE w.run_card_id=%s',(card_id,))})

@app.post('/api/cards',tags=['admin'],status_code=201)
def new_card(body:NewCard,a:Actor,idempotency_key:Key):
    admin(a,'run_cards:create')
    data=body.model_dump()
    with connection() as db:
        return run_command(db,a,idempotency_key,{'create':data},lambda:create_card(db,a,data))

# Explicit routes produce separate operation IDs and typed OpenAPI entries.
def action_endpoint(kind):
    def endpoint(card_id:UUID,body:Action,a:Actor,idempotency_key:Key):
        data=body.model_dump(exclude_none=True)
        data['type']=kind
        with connection() as db:
            return run_command(db,a,idempotency_key,{'card_id':card_id,**data},lambda:card_action(db,a,card_id,data))
    endpoint.__name__=kind.replace('-','_')
    return endpoint

for action in ['check-in','check-out','abnormal-end','split','rework','backfill','revoke','release','cancel','scrap','edit','temporary','skip']:
    app.post('/api/cards/{card_id}/'+action,tags=['actions'],summary=action)(action_endpoint(action))

@app.get('/api/equipments/{equipment_id}',tags=['shared'])
def equipment_detail(equipment_id:UUID,a:Actor):
    with connection() as db:
        e=one(db,'SELECT * FROM equipments WHERE id=%s',(equipment_id,))
        require(e,'NOT_FOUND','Equipment not found')
        return clean({'equipment':e,'cards':all_rows(db,'SELECT c.* FROM run_cards c JOIN work_records w ON w.run_card_id=c.id WHERE w.equipment_id=%s AND w.ended_at IS NULL AND w.revoked_at IS NULL',(equipment_id,)),
         'history':all_rows(db,'SELECT * FROM equipment_status_history WHERE equipment_id=%s ORDER BY started_at DESC,id DESC LIMIT 100',(equipment_id,))})

@app.post('/api/equipments/{equipment_id}/set-equipment-status',tags=['actions'])
def equipment_status(equipment_id:UUID,body:EquipmentStatus,a:Actor,idempotency_key:Key):
    data=body.model_dump()
    with connection() as db:
        return run_command(db,a,idempotency_key,{'equipment_id':equipment_id,'action':'set-equipment-status',**data},lambda:set_equipment(db,a,equipment_id,body.reason_code,body.reason))

@app.post('/api/equipments/{equipment_id}/report-breakdown',tags=['actions'])
def breakdown(equipment_id:UUID,body:Breakdown,a:Actor,idempotency_key:Key):
    data=body.model_dump()
    with connection() as db:
        return run_command(db,a,idempotency_key,{'equipment_id':equipment_id,'action':'report-breakdown',**data},lambda:report_breakdown(db,a,equipment_id,data))

@app.get('/api/dashboard',tags=['shared'])
def dashboard(a:Actor):
    with connection() as db:
        return clean({'wip':all_rows(db,"SELECT o.id,o.code,o.name,c.status,count(c.id) AS cards,coalesce(sum(c.current_qty),0) AS qty,min(c.updated_at) AS oldest_at FROM operations o LEFT JOIN route_nodes n ON n.operation_id=o.id LEFT JOIN run_cards c ON c.current_node_id=n.id AND c.status NOT IN ('COMPLETED','CANCELLED','TRANSFERRED','SCRAPPED') GROUP BY o.id,c.status ORDER BY o.id,c.status"),
         'equipments':all_rows(db,'SELECT e.*,count(w.id) AS occupied FROM equipments e LEFT JOIN work_records w ON w.equipment_id=e.id AND w.ended_at IS NULL AND w.revoked_at IS NULL GROUP BY e.id ORDER BY e.mes_code'),
         'counts':all_rows(db,'SELECT status,count(*) AS cards,sum(current_qty) AS qty FROM run_cards GROUP BY status'),
         'revision':one(db,'SELECT coalesce(max(id),0) AS id FROM audit_logs')['id']})

@app.get('/api/events',tags=['shared'])
def events(a:Actor,request:Request):
    async def stream():
        last=None
        while not await request.is_disconnected():
            with connection() as db:
                s=one(db,'SELECT 1 FROM auth_sessions WHERE token_hash=%s AND expires_at>now()',(a['token_hash'],))
                if not s: break
                revision=one(db,'SELECT coalesce(max(id),0) AS id FROM audit_logs')['id']
            if revision!=last:
                yield f'event: update\ndata: {json.dumps({"revision":revision})}\n\n'
                last=revision
            else: yield ': heartbeat\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

READS={
 'work-records':('SELECT w.*,c.card_no,o.name AS operation_name FROM work_records w JOIN run_cards c ON c.id=w.run_card_id JOIN operations o ON o.id=w.operation_id ORDER BY w.started_at DESC','backend:access'),
 'audit':('SELECT l.*,a.display_name,c.card_no FROM audit_logs l LEFT JOIN accounts a ON a.id=l.actor_id LEFT JOIN run_cards c ON c.id=l.run_card_id ORDER BY l.id DESC','audit:view_system'),
 'outbox':('SELECT * FROM sync_outbox ORDER BY id DESC','mes_sync:manage'),
 'equipment-history':('SELECT h.*,e.mes_code,extract(epoch FROM coalesce(h.ended_at,now())-h.started_at) AS duration_seconds FROM equipment_status_history h JOIN equipments e ON e.id=h.equipment_id ORDER BY h.id DESC','backend:access'),
 'notification-rules':('SELECT * FROM notification_rules ORDER BY event_code','notifications:manage'),
 'movements':('SELECT m.*,c.card_no FROM quantity_movements m JOIN run_cards c ON c.id=m.run_card_id ORDER BY m.id DESC','backend:access')}

@app.get('/api/admin/{resource}',tags=['admin'])
def admin_read(resource:str,a:Actor,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
    require(resource in READS,'NOT_FOUND','Unknown resource')
    sql,permission=READS[resource]
    admin(a,permission)
    with connection() as db:
        return clean(all_rows(db,sql+' LIMIT %s OFFSET %s',(limit,offset)))

@app.get('/api/notifications',tags=['shared'])
def notifications(a:Actor,offset:int=Query(0,ge=0)):
    with connection() as db:
        return clean({'items':all_rows(db,'SELECT * FROM notifications WHERE account_id=%s ORDER BY id DESC LIMIT 50 OFFSET %s',(a['id'],offset)),
         'unread':one(db,'SELECT count(*) AS n FROM notifications WHERE account_id=%s AND read_at IS NULL',(a['id'],))['n']})

@app.post('/api/notifications/read-all',tags=['shared'])
def read_notifications(a:Actor,idempotency_key:Key):
    def execute(db):
        db.execute('UPDATE notifications SET read_at=now() WHERE account_id=%s AND read_at IS NULL',(a['id'],))
        audit(db,a,'read-notifications')
        return {'ok':True}
    with connection() as db: return run_command(db,a,idempotency_key,{'action':'read-notifications'},lambda:execute(db))

class Policy(Strict):
    release_reason_required:bool

@app.put('/api/settings',tags=['admin'])
def settings(body:Policy,a:Actor,idempotency_key:Key):
    admin(a,'accounts:manage')
    def execute(db):
        db.execute("UPDATE app_settings SET value=%s WHERE key='release_reason_required'",(jb(body.release_reason_required),))
        audit(db,a,'update-policy',payload=body.model_dump())
        return body.model_dump()
    with connection() as db: return run_command(db,a,idempotency_key,body.model_dump(),lambda:execute(db))

class AccountInput(Strict):
    username:str=Field(min_length=1)
    display_name:str=Field(min_length=1)
    employee_no:str=Field(min_length=1)
    email:str|None=None
    role_id:int
    password:str|None=Field(default=None,min_length=8)
    is_active:bool=True

@app.put('/api/accounts/{username}',tags=['admin'])
def save_account(username:str,body:AccountInput,a:Actor,idempotency_key:Key):
    admin(a,'accounts:manage')
    require(username==body.username,'ACCOUNT','Username cannot change')
    def execute(db):
        old=one(db,'SELECT * FROM accounts WHERE username=%s',(username,))
        require(old or body.password,'PASSWORD','New account requires a password')
        require(not old or old['id']!=a['id'] or (body.is_active and body.role_id==old['role_id']),'ACCOUNT','Do not remove your own access')
        hashed=hash_password(body.password) if body.password else old['password_hash']
        result=one(db,'INSERT INTO accounts(username,display_name,employee_no,email,role_id,password_hash,is_active) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(username) DO UPDATE SET display_name=EXCLUDED.display_name,employee_no=EXCLUDED.employee_no,email=EXCLUDED.email,role_id=EXCLUDED.role_id,password_hash=EXCLUDED.password_hash,is_active=EXCLUDED.is_active RETURNING id,username,display_name,employee_no,email,role_id,is_active',(username,body.display_name,body.employee_no,body.email,body.role_id,hashed,body.is_active))
        if old: db.execute('DELETE FROM auth_sessions WHERE account_id=%s',(old['id'],))
        audit(db,a,'save-account',payload=result)
        return result
    # Store a hash, never a password, in idempotency fingerprints.
    data=body.model_dump(); data['password']=digest(body.password) if body.password else None
    with connection() as db: return run_command(db,a,idempotency_key,{'account':data},lambda:execute(db))

class RoleInput(Strict):
    code:str=Field(min_length=1)
    name:str=Field(min_length=1)
    permissions:list[str]

@app.put('/api/roles/{code}',tags=['admin'])
def save_role(code:str,body:RoleInput,a:Actor,idempotency_key:Key):
    admin(a,'accounts:manage')
    require(code==body.code and set(body.permissions)<=set(PERMISSIONS),'ROLE','Invalid role or permission')
    require(code!='admin','ROLE','Built-in administrator role cannot be modified')
    def execute(db):
        r=one(db,'INSERT INTO roles(code,name) VALUES(%s,%s) ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name RETURNING *',(code,body.name))
        db.execute('DELETE FROM role_permissions WHERE role_id=%s',(r['id'],))
        for p in set(body.permissions): db.execute('INSERT INTO role_permissions VALUES(%s,%s)',(r['id'],p))
        audit(db,a,'save-role',payload=body.model_dump())
        return r
    with connection() as db: return run_command(db,a,idempotency_key,{'role':body.model_dump()},lambda:execute(db))

class OperationInput(Strict):
    code:str=Field(min_length=1)
    name:str=Field(min_length=1)
    requires_equipment:bool=True
    is_active:bool=True
    defect_code_ids:list[int]=Field(default_factory=list)
    qc_items:list[dict]=Field(default_factory=list)

@app.put('/api/operations/{code}',tags=['admin'])
def save_operation(code:str,body:OperationInput,a:Actor,idempotency_key:Key):
    admin(a,'operations:manage')
    require(code==body.code,'OPERATION','Code mismatch')
    def execute(db):
        old=one(db,'SELECT * FROM operations WHERE code=%s',(code,))
        if old:
            require(not one(db,'SELECT 1 FROM work_records WHERE operation_id=%s AND ended_at IS NULL AND revoked_at IS NULL',(old['id'],)),'OPERATION','Cannot change operation configuration during active work')
        o=one(db,'INSERT INTO operations(code,name,requires_equipment,is_active) VALUES(%s,%s,%s,%s) ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,requires_equipment=EXCLUDED.requires_equipment,is_active=EXCLUDED.is_active RETURNING *',(code,body.name,body.requires_equipment,body.is_active))
        db.execute('DELETE FROM operation_defect_codes WHERE operation_id=%s',(o['id'],))
        for d in set(body.defect_code_ids): db.execute('INSERT INTO operation_defect_codes VALUES(%s,%s)',(o['id'],d))
        existing={q['id'] for q in all_rows(db,'SELECT id FROM operation_qc_items WHERE operation_id=%s',(o['id'],))}
        given={q['id'] for q in body.qc_items if q.get('id')}
        require(existing<=given,'QC_HISTORY','Existing QC items must be retained to preserve history')
        for q in body.qc_items:
            require(set(q)<={'id','name','unit','lower_limit','upper_limit'} and q.get('name'),'QC','Invalid QC item')
            require(q.get('lower_limit') is None or q.get('upper_limit') is None or Decimal(str(q['lower_limit']))<=Decimal(str(q['upper_limit'])),'QC','Lower limit must not exceed upper limit')
            if q.get('id'):
                require(q['id'] in existing,'QC','QC item belongs to another operation')
                db.execute('UPDATE operation_qc_items SET name=%s,unit=%s,lower_limit=%s,upper_limit=%s WHERE id=%s',(q['name'],q.get('unit'),q.get('lower_limit'),q.get('upper_limit'),q['id']))
            else: db.execute('INSERT INTO operation_qc_items(operation_id,name,unit,lower_limit,upper_limit) VALUES(%s,%s,%s,%s,%s)',(o['id'],q['name'],q.get('unit'),q.get('lower_limit'),q.get('upper_limit')))
        audit(db,a,'save-operation',payload=body.model_dump())
        return o
    with connection() as db: return run_command(db,a,idempotency_key,{'operation':body.model_dump()},lambda:execute(db))

class DefectCodeInput(Strict):
    name:str=Field(min_length=1)
    is_active:bool=True

@app.put('/api/defect-codes/{code}',tags=['admin'])
def save_defect(code:str,body:DefectCodeInput,a:Actor,idempotency_key:Key):
    admin(a,'defect_codes:manage')
    def execute(db):
        d=one(db,'INSERT INTO defect_codes(code,name,is_active) VALUES(%s,%s,%s) ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,is_active=EXCLUDED.is_active RETURNING *',(code,body.name,body.is_active))
        audit(db,a,'save-defect-code',payload=d)
        return d
    with connection() as db: return run_command(db,a,idempotency_key,{'code':code,**body.model_dump()},lambda:execute(db))

class EquipmentInput(Strict):
    name:str=Field(min_length=1)
    location:str|None=None
    is_active:bool=True
    operation_ids:list[int]
    reason_codes:list[str]=Field(default_factory=list)

@app.put('/api/equipment-config/{code}',tags=['admin'])
def save_equipment(code:str,body:EquipmentInput,a:Actor,idempotency_key:Key):
    admin(a,'equipments:manage')
    def execute(db):
        old=one(db,'SELECT * FROM equipments WHERE mes_code=%s',(code,))
        require(not old or not one(db,'SELECT 1 FROM work_records WHERE equipment_id=%s AND ended_at IS NULL AND revoked_at IS NULL',(old['id'],)),'EQUIPMENT','Cannot reconfigure occupied equipment')
        e=one(db,"INSERT INTO equipments(mes_code,name,location,is_active,state,reason_code) VALUES(%s,%s,%s,%s,'OFF','OFF') ON CONFLICT(mes_code) DO UPDATE SET name=EXCLUDED.name,location=EXCLUDED.location,is_active=EXCLUDED.is_active RETURNING *",(code,body.name,body.location,body.is_active))
        db.execute('DELETE FROM equipment_operations WHERE equipment_id=%s',(e['id'],))
        db.execute('DELETE FROM equipment_reason_whitelist WHERE equipment_id=%s',(e['id'],))
        for op in set(body.operation_ids): db.execute('INSERT INTO equipment_operations VALUES(%s,%s)',(e['id'],op))
        for reason in set(body.reason_codes): db.execute('INSERT INTO equipment_reason_whitelist VALUES(%s,%s)',(e['id'],reason))
        if not old: set_equipment(db,a,e['id'],'OFF',automatic=True)
        audit(db,a,'save-equipment',payload=body.model_dump())
        return e
    with connection() as db: return run_command(db,a,idempotency_key,{'code':code,**body.model_dump()},lambda:execute(db))

class RouteInput(Strict):
    route_name:str=Field(min_length=1)
    route_id:int|None=None
    version:str=Field(min_length=1)
    operation_ids:list[int]=Field(min_length=1)
    activate:bool=True

@app.post('/api/routes',tags=['admin'],status_code=201)
def save_route(body:RouteInput,a:Actor,idempotency_key:Key):
    admin(a,'process_routes:manage')
    def execute(db):
        route=one(db,'SELECT * FROM process_routes WHERE id=%s',(body.route_id,)) if body.route_id else one(db,'INSERT INTO process_routes(name) VALUES(%s) RETURNING *',(body.route_name,))
        require(route,'ROUTE','Route not found')
        if body.activate: db.execute("UPDATE process_route_versions SET status='RETIRED' WHERE route_id=%s AND status='ACTIVE'",(route['id'],))
        v=one(db,'INSERT INTO process_route_versions(route_id,version,status) VALUES(%s,%s,%s) RETURNING *',(route['id'],body.version,'ACTIVE' if body.activate else 'DRAFT'))
        for i,op in enumerate(body.operation_ids,1): db.execute('INSERT INTO route_nodes(route_version_id,seq,operation_id) VALUES(%s,%s,%s)',(v['id'],i,op))
        audit(db,a,'create-route-version',payload=body.model_dump())
        return v
    with connection() as db: return run_command(db,a,idempotency_key,body.model_dump(),lambda:execute(db))

class NotificationRule(Strict):
    channel_email:bool=False
    channel_inapp:bool=True
    repeat_enabled:bool=False
    repeat_every_min:int=Field(default=30,ge=1)
    repeat_max:int=Field(default=5,ge=1,le=100)
    escalate_enabled:bool=False
    escalate_after_min:int=Field(default=30,ge=1)
    role_ids:list[int]=Field(default_factory=list)
    account_ids:list[UUID]=Field(default_factory=list)
    escalation_role_ids:list[int]=Field(default_factory=list)
    escalation_account_ids:list[UUID]=Field(default_factory=list)

@app.put('/api/notification-rules/{code}',tags=['admin'])
def save_rule(code:str,body:NotificationRule,a:Actor,idempotency_key:Key):
    admin(a,'notifications:manage')
    def execute(db):
        fields=['channel_email','channel_inapp','repeat_enabled','repeat_every_min','repeat_max','escalate_enabled','escalate_after_min']
        r=one(db,'UPDATE notification_rules SET '+','.join(f+'=%s' for f in fields)+' WHERE event_code=%s RETURNING *',(*[getattr(body,f) for f in fields],code))
        require(r,'NOT_FOUND','Rule not found')
        db.execute('DELETE FROM notification_rule_targets WHERE event_code=%s',(code,))
        for escalation,roles,accounts in [(False,body.role_ids,body.account_ids),(True,body.escalation_role_ids,body.escalation_account_ids)]:
            for role in set(roles): db.execute('INSERT INTO notification_rule_targets(event_code,role_id,is_escalation) VALUES(%s,%s,%s)',(code,role,escalation))
            for account in set(accounts): db.execute('INSERT INTO notification_rule_targets(event_code,account_id,is_escalation) VALUES(%s,%s,%s)',(code,account,escalation))
        audit(db,a,'save-notification-rule',payload={'code':code,**body.model_dump()})
        return r
    with connection() as db: return run_command(db,a,idempotency_key,{'code':code,**body.model_dump()},lambda:execute(db))

@app.get('/api/notification-targets',tags=['admin'])
def rule_targets(a:Actor):
    admin(a,'notifications:manage')
    with connection() as db: return clean(all_rows(db,'SELECT * FROM notification_rule_targets'))

@app.post('/api/outbox/{event_id}/retry',tags=['admin'])
def retry_outbox(event_id:int,a:Actor,idempotency_key:Key):
    admin(a,'mes_sync:manage')
    def execute(db):
        r=one(db,"UPDATE sync_outbox SET status='PENDING',next_attempt_at=now() WHERE id=%s AND status<>'SENT' RETURNING id,status",(event_id,))
        require(r,'STATE','Only unsent events can be retried')
        audit(db,a,'retry-outbox',payload=r)
        return r
    with connection() as db: return run_command(db,a,idempotency_key,{'retry':event_id},lambda:execute(db))

@app.get('/api/import/template',tags=['admin'])
def import_template(a:Actor):
    admin(a,'run_cards:create')
    wb=Workbook(); ws=wb.active
    ws.append(['card_no','work_order_no','item_no','item_name','unit','original_qty','route_free','route_version_id'])
    ws.append(['DEMO-IMPORT-001','DEMO-WO-002','PCBA-001','PCBA','pcs',100,False,1])
    b=BytesIO(); wb.save(b); b.seek(0)
    return StreamingResponse(b,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="runcard-template.xlsx"'})

@app.post('/api/import/cards',tags=['admin'])
async def import_cards(a:Actor,idempotency_key:Key,file:UploadFile=File(),validate_only:bool=True):
    admin(a,'run_cards:create')
    content=await file.read(2_000_001)
    require(len(content)<=2_000_000,'IMPORT','Maximum upload is 2 MB')
    import zipfile
    try:
        with zipfile.ZipFile(BytesIO(content)) as z:
            require(sum(i.file_size for i in z.infolist())<=20_000_000,'IMPORT','Expanded spreadsheet too large')
        wb=load_workbook(BytesIO(content),read_only=True,data_only=False)
        rows=iter(wb.active.iter_rows(values_only=True)); headers=next(rows)
        require(set(headers)<=set(NewCard.model_fields),'IMPORT','Unknown spreadsheet columns')
        cards=[]
        for row in rows:
            if not any(x is not None for x in row): continue
            require(len(cards)<1000,'IMPORT','Maximum 1000 cards per import')
            values={k:v for k,v in zip(headers,row) if k and v is not None}
            require(not any(isinstance(v,str) and v.startswith('=') for v in values.values()),'IMPORT','Formulas are not accepted')
            cards.append(NewCard(**values).model_dump())
    except RuleError: raise
    except Exception: raise RuleError('IMPORT','Invalid XLSX or row values')
    # Validation uses the same constraints and rolls back all inserts, audit and events.
    with connection() as db:
        if validate_only:
            lock(db)
            result=[create_card(db,a,c) for c in cards]
            db.rollback()
            return {'valid':True,'count':len(result)}
        return run_command(db,a,idempotency_key,{'import':cards},lambda:{'items':[create_card(db,a,c) for c in cards]})

app.include_router(mes.router)
