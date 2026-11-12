import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from .db import all_rows, one, jb, clean, lock
from .domain import transition, equipment_transition, require, invariant, number, QTY
from .security import ACTION_PERMISSION, allowed, consume_session, digest
from .bootstrap import EVENTS
from . import gates

CARD_COLUMNS = ('card_no','work_order_no','item_no','item_name','unit','route_free','route_version_id','current_node_id',
 'status','source_type','data_source','parent_card_id','original_qty',*QTY,'cancel_reason','version','abnormal_counts','origin_record_id','work_order_id')


def audit(db, actor, action, before=None, after=None, reason=None, payload=None, equipment_id=None):
    card_id = after.get('id') if after else before.get('id') if before else None
    db.execute('INSERT INTO audit_logs(action,actor_id,run_card_id,from_status,to_status,reason,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)',
               (action,actor['id'],card_id,before['status'] if before else None,after['status'] if after else None,reason,jb(payload or {})))
    db.execute('INSERT INTO sync_outbox(idempotency_key,event_type,payload) VALUES(%s,%s,%s)',
               (str(uuid4()),action,jb({'actor_id':actor['id'],'run_card_id':card_id,'equipment_id':equipment_id,'before':before,'after':after,'reason':reason,'detail':payload or {}})))


def notify(db, code, card_id=None, equipment_id=None, body=''):
    title = EVENTS[code]
    rule = one(db,'SELECT * FROM notification_rules WHERE event_code=%s',(code,))
    if not rule:
        return
    job = one(db, 'INSERT INTO notification_jobs(event_code,run_card_id,equipment_id,title,body,next_at) VALUES(%s,%s,%s,%s,%s,now()+(%s * interval \'1 minute\')) RETURNING *',
              (code,card_id,equipment_id,title,body,rule['repeat_every_min'] or 30))
    deliver_notification(db, job, False)


def deliver_notification(db, job, escalation):
    rule = one(db,'SELECT * FROM notification_rules WHERE event_code=%s',(job['event_code'],))
    targets = all_rows(db, 'SELECT DISTINCT a.id FROM accounts a JOIN notification_rule_targets t ON (a.id=t.account_id OR a.role_id=t.role_id) WHERE a.is_active AND t.event_code=%s AND t.is_escalation=%s', (job['event_code'],escalation))
    title = ('[ESCALATION] ' if escalation else '') + job['title']
    for a in targets:
        if rule['channel_inapp']:
            db.execute('INSERT INTO notifications(event_code,account_id,title,body,run_card_id,equipment_id) VALUES(%s,%s,%s,%s,%s,%s)', (job['event_code'],a['id'],title,job['body'],job['run_card_id'],job['equipment_id']))
        if rule['channel_email']:
            db.execute('INSERT INTO email_deliveries(account_id,title,body) VALUES(%s,%s,%s)',(a['id'],title,job['body']))


def run_command(db, actor, key, payload, function):
    lock(db)
    fingerprint = hashlib.sha256(json.dumps(clean(payload), sort_keys=True).encode()).hexdigest()
    receipt = one(db, 'SELECT * FROM command_receipts WHERE actor_id=%s AND key=%s', (actor['id'],key))
    if receipt:
        require(receipt['fingerprint'] == fingerprint, 'IDEMPOTENCY_CONFLICT', 'Key already used for different input')
        return receipt['result']
    consume_session(db, actor)
    result = clean(function())
    db.execute('INSERT INTO command_receipts(actor_id,key,fingerprint,result) VALUES(%s,%s,%s,%s)',(actor['id'],key,fingerprint,jb(result)))
    return result


def insert_card(db, values):
    c = dict(values)
    c.setdefault('source_type','NORMAL')
    c.setdefault('data_source','SYSTEM')
    c.setdefault('status','WAITING')
    c.setdefault('version',1)
    c.setdefault('route_free',False)
    c.setdefault('abnormal_counts',{})
    for key in QTY:
        c.setdefault(key, c['original_qty'] if key == 'current_qty' else 0)
    invariant(c)
    cols = [k for k in CARD_COLUMNS if k in c]
    args = [jb(c[k]) if k == 'abnormal_counts' else c[k] for k in cols]
    return one(db, f"INSERT INTO run_cards({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *", args)


def create_card(db, actor, values):
    require(actor['mode']=='admin','FORBIDDEN','Admin session required')
    allowed(actor,'run_cards:create')
    c = dict(values)
    require(bool(str(c.get('card_no','')).strip()) and bool(str(c.get('unit','')).strip()),'CARD','Card number and unit required')
    if not c.get('route_free'):
        n = one(db,"SELECT n.* FROM route_nodes n JOIN process_route_versions v ON v.id=n.route_version_id WHERE v.id=%s AND v.status='ACTIVE' ORDER BY n.seq LIMIT 1",(c.get('route_version_id'),))
        require(n,'ROUTE','Active route version required')
        c['current_node_id']=n['id']
    else:
        require(c.get('route_version_id') is None,'BR-11','Route-free card cannot have a route')
        c['current_node_id']=None
    card = insert_card(db,c)
    audit(db,actor,'create',after=card)
    return card


def set_equipment(db, actor, equipment_id, reason_code, note='', card_id=None, automatic=False):
    e = one(db,'SELECT * FROM equipments WHERE id=%s FOR UPDATE',(equipment_id,))
    require(e,'NOT_FOUND','Equipment not found')
    reason = one(db,'SELECT * FROM equipment_reasons WHERE code=%s',(reason_code,))
    require(reason,'EQUIPMENT_REASON','Unknown equipment reason')
    occupied = bool(one(db,'SELECT 1 FROM work_records WHERE equipment_id=%s AND ended_at IS NULL AND revoked_at IS NULL',(equipment_id,)))
    whitelist = [r['reason_code'] for r in all_rows(db,'SELECT reason_code FROM equipment_reason_whitelist WHERE equipment_id=%s',(equipment_id,))]
    if not automatic:
        allowed(actor,'equipment_status:set')
        equipment_transition(e,reason,{**actor,'occupied':occupied,'whitelist':whitelist})
    db.execute('UPDATE equipment_status_history SET ended_at=now() WHERE equipment_id=%s AND ended_at IS NULL',(equipment_id,))
    db.execute('INSERT INTO equipment_status_history(equipment_id,state,reason_code,run_card_id,recorded_by,note) VALUES(%s,%s,%s,%s,%s,%s)',(equipment_id,reason['state'],reason_code,card_id,actor['id'],note))
    updated=one(db,'UPDATE equipments SET state=%s,reason_code=%s WHERE id=%s RETURNING *',(reason['state'],reason_code,equipment_id))
    audit(db,actor,'set-equipment-status',reason=note,payload={'before':e,'after':updated},equipment_id=equipment_id)
    gates.on_equipment_change(db,actor,e,updated,reason,note)
    if reason['state']=='MAINTENANCE' and e['state']!='MAINTENANCE':
        notify(db,'EQUIPMENT_MAINTENANCE',equipment_id=equipment_id,body=f"{e['mes_code']} · {reason['name']} · {note}")
    if reason['state']!='MAINTENANCE':
        db.execute("UPDATE notification_jobs SET resolved_at=now() WHERE equipment_id=%s AND event_code IN ('EQUIPMENT_MAINTENANCE','EQUIPMENT_ABNORMAL') AND resolved_at IS NULL",(equipment_id,))
    return updated


def card_action(db, actor, card_id, action):
    kind=action['type']
    if kind in ACTION_PERMISSION:
        allowed(actor,ACTION_PERMISSION[kind])
    card=one(db,'SELECT * FROM run_cards WHERE id=%s FOR UPDATE',(card_id,))
    require(card,'NOT_FOUND','RunCard not found')
    ctx=dict(actor)
    ctx['release_reason_required']=one(db,"SELECT value FROM app_settings WHERE key='release_reason_required'")['value']
    opened=one(db,'SELECT * FROM work_records WHERE run_card_id=%s AND ended_at IS NULL AND revoked_at IS NULL',(card_id,))
    ctx['open_record']=opened
    start=kind in {'check-in','temporary','skip'} or (kind=='backfill' and card['status']=='WAITING')
    if kind=='backfill':
        actual=one(db,'SELECT id FROM accounts WHERE id=%s AND is_active AND employee_no IS NOT NULL',(action.get('actual_operator_id'),))
        require(actual,'BR-22','Active actual operator required')
        at=action.get('occurred_at')
        if at:
            at=datetime.fromisoformat(str(at).replace('Z','+00:00'))
            require(at.tzinfo is not None and at<=datetime.now(timezone.utc),'WORK_TIME','Backfill time must be timezone-aware and not in the future')
            if opened:
                require(at>=opened['started_at'],'WORK_TIME','End cannot precede start')
            else:
                prev=one(db,'SELECT max(ended_at) AS at FROM work_records WHERE run_card_id=%s AND revoked_at IS NULL',(card_id,))
                require(not prev['at'] or at>=prev['at'],'WORK_TIME','Backfill cannot overlap previous work')
    target_route_version = action.get('target_route_version_id')
    if target_route_version is not None:
        require(kind == 'rework', 'ROUTE', 'A new route can only be selected for rework')
        if target_route_version != card['route_version_id']:
            active = one(db, "SELECT id FROM process_route_versions WHERE id=%s AND status='ACTIVE'", (target_route_version,))
            require(active, 'ROUTE', 'Choose an active route version for rework')
    if action.get('target_node_id') is not None:
        route_version = target_route_version if target_route_version is not None else card['route_version_id']
        target=one(db,'SELECT * FROM route_nodes WHERE id=%s AND route_version_id=%s',(action['target_node_id'],route_version))
        ctx['target_node_valid']=bool(target)
    if kind=='skip':
        current=one(db,'SELECT seq FROM route_nodes WHERE id=%s',(card['current_node_id'],))
        ctx['target_node_valid']=bool(ctx.get('target_node_valid') and current and target['seq']>current['seq'])
        auth=one(db,'SELECT s.* FROM skip_authorizations s JOIN accounts a ON a.id=s.account_id JOIN role_permissions rp ON rp.role_id=a.role_id AND rp.permission_code=\'work_records:skip_node\' WHERE s.token_hash=%s AND s.run_card_id=%s AND s.target_node_id=%s AND s.used_at IS NULL AND s.expires_at>now() AND a.is_active',(digest(action.get('authorization_token') or ''),card_id,action.get('target_node_id')))
        ctx.update(skip_authorized=bool(auth),authorized_by=auth['account_id'] if auth else None)
    if start:
        if kind=='temporary' or card['route_free']:
            op=one(db,'SELECT * FROM operations WHERE id=%s',(action.get('operation_id'),))
        else:
            node_id=action.get('target_node_id') if kind=='skip' else card['current_node_id']
            op=one(db,'SELECT o.* FROM operations o JOIN route_nodes n ON n.operation_id=o.id WHERE n.id=%s',(node_id,))
        ctx['operation']=op
        e=one(db,'SELECT * FROM equipments WHERE id=%s',(action.get('equipment_id'),)) if action.get('equipment_id') else None
        ctx['equipment']=e
        ctx['equipment_operations']=[r['operation_id'] for r in all_rows(db,'SELECT operation_id FROM equipment_operations WHERE equipment_id=%s',(e['id'],))] if e else []
    if opened:
        ctx['defect_code_ids']=[r['defect_code_id'] for r in all_rows(db,'SELECT d.defect_code_id FROM operation_defect_codes d JOIN defect_codes c ON c.id=d.defect_code_id WHERE d.operation_id=%s AND c.is_active',(opened['operation_id'],))]
        ctx['qc_items']=all_rows(db,'SELECT * FROM operation_qc_items WHERE operation_id=%s',(opened['operation_id'],))
        nxt=one(db,'SELECT id FROM route_nodes WHERE route_version_id=%s AND seq>(SELECT seq FROM route_nodes WHERE id=%s) ORDER BY seq LIMIT 1',(card['route_version_id'],card['current_node_id']))
        ctx['next_node_id']=nxt['id'] if nxt else None
    if kind=='revoke':
        last=one(db,'SELECT * FROM work_records WHERE run_card_id=%s AND revoked_at IS NULL ORDER BY started_at DESC,id DESC LIMIT 1',(card_id,))
        ctx['last_record']=last
        ctx['subsequent_mutation']=bool(last and (not last.get('after_card') or any(number(last['after_card'].get(k,0))!=number(card[k]) for k in QTY) or last['after_card'].get('current_node_id')!=card['current_node_id']))
        ctx['processed_defects']=bool(last and one(db,"SELECT 1 FROM run_cards WHERE origin_record_id=%s AND (status<>'DEFECT_HOLD' OR version<>1)",(last['id'],)))
    gates.precheck(db,actor,card,action,ctx,kind)
    result=transition(card,action,ctx)
    updated=result['card']
    # Persist compare-and-swap even though the service lock already serializes commands.
    fields=[k for k in CARD_COLUMNS if k in updated and k not in {'card_no','origin_record_id'}]
    args=[jb(updated[k]) if k=='abnormal_counts' else updated[k] for k in fields]
    stored=one(db,f"UPDATE run_cards SET {','.join(k+'=%s' for k in fields)},updated_at=now() WHERE id=%s AND version=%s RETURNING *",(*args,card_id,card['version']))
    require(stored,'VERSION_CONFLICT','Card changed; refresh and review')
    record_id=opened['id'] if opened else None
    children=[]
    for fx in result['effects']:
        typ=fx['type']
        if typ=='open_record':
            record=one(db,'INSERT INTO work_records(run_card_id,node_id,operation_id,is_temporary,is_skip,authorized_by,equipment_id,operator_id,input_qty,started_at,is_backfilled,end_reason,before_card,after_card,after_card_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()),%s,%s,%s,%s,%s) RETURNING *',
              (card_id,fx['node_id'],fx['operation_id'],fx['is_temporary'],fx['is_skip'],ctx.get('authorized_by'),ctx['equipment']['id'] if ctx['equipment'] and ctx['operation']['requires_equipment'] else None,action.get('actual_operator_id') if kind=='backfill' else actor['id'],fx['input_qty'],action.get('occurred_at') if kind=='backfill' else None,kind=='backfill',action.get('reason'),jb(card),jb(stored),stored['version']))
            record_id=record['id']
            if kind=='skip':
                db.execute('UPDATE skip_authorizations SET used_at=now() WHERE token_hash=%s',(digest(action['authorization_token']),))
        elif typ=='close_record':
            db.execute('UPDATE work_records SET output_qty=%s,quality_result=%s,production_note=%s,ended_at=COALESCE(%s,now()),end_type=%s,end_reason=%s,end_operator_id=%s,is_backfilled=is_backfilled OR %s,after_card=%s,after_card_version=%s WHERE id=%s',
                       (fx['output_qty'],fx['quality_result'],action.get('production_note'),action.get('occurred_at') if kind=='backfill' else None,fx['end_type'],action.get('reason'),action.get('actual_operator_id') if kind=='backfill' else actor['id'],kind=='backfill',jb(stored),stored['version'],record_id))
            for d in fx['defects']:
                db.execute('INSERT INTO work_record_defects(work_record_id,defect_code_id,qty,remark) VALUES(%s,%s,%s,%s)',(record_id,d['defect_code_id'],d['qty'],d.get('remark')))
            for q in fx['qc']:
                db.execute('INSERT INTO work_record_qc_values VALUES(%s,%s,%s,%s)',(record_id,q['qc_item_id'],q['value'],q['in_spec']))
        elif typ=='child':
            values=fx['card']
            source=values['source_type']
            if source in {'DEFECT','SPLIT'}:
                count=one(db,'SELECT count(*) AS n FROM run_cards WHERE parent_card_id=%s AND source_type=%s',(card_id,source))['n']+1
                suffix=f'-D{count}' if source=='DEFECT' else f'-{count:02d}'
                values['card_no']=card['card_no']+suffix
            else:
                seq=one(db,"SELECT nextval('child_card_seq') AS n")['n']
                values['card_no']=('R'+datetime.now(timezone.utc).strftime('%y%m') if source=='REWORK' else card['card_no']+'-T')+f'{seq:06d}'
            values['origin_record_id']=record_id if source=='DEFECT' else None
            values['cancel_reason']=None
            child=insert_card(db,values)
            children.append(child)
            audit(db,actor,'create-child',after=child,reason=action.get('reason'))
            if source=='DEFECT':
                notify(db,'DEFECT_CREATED',child['id'],body=child['card_no'])
        elif typ=='equipment':
            set_equipment(db,actor,fx['id'],fx['reason_code'],action.get('reason') or '',card_id,automatic=True)
        elif typ=='movement':
            db.execute('INSERT INTO quantity_movements(run_card_id,disposition,qty,target_card_id,actor_id,reason) VALUES(%s,%s,%s,%s,%s,%s)',(card_id,fx['disposition'],fx['qty'],children[-1]['id'] if children else None,actor['id'],action['reason']))
        elif typ=='revoke_record':
            db.execute('UPDATE work_records SET revoked_at=now(),revoked_by=%s,revoke_reason=%s WHERE id=%s',(actor['id'],action['reason'],fx['record_id']))
            for child in all_rows(db,'SELECT * FROM run_cards WHERE origin_record_id=%s',(fx['record_id'],)):
                # Returning defect quantity to its parent is explicitly classified in the extended balance.
                revoked=one(db,"UPDATE run_cards SET status='CANCELLED',transferred_qty=transferred_qty+current_qty,current_qty=0,cancel_reason=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *",(action['reason'],child['id']))
                db.execute("INSERT INTO quantity_movements(run_card_id,disposition,qty,target_card_id,actor_id,reason) VALUES(%s,'revoke-defect',%s,%s,%s,%s)",(child['id'],child['current_qty'],card_id,actor['id'],action['reason']))
                audit(db,actor,'revoke-defect',child,revoked,action['reason'])
                notify(db,'DEFECT_VOIDED',child['id'],body=child['card_no'])
    if kind=='revoke':
        # The prior record now describes the restored card, despite its newer version.
        previous=one(db,'SELECT id FROM work_records WHERE run_card_id=%s AND revoked_at IS NULL ORDER BY started_at DESC,id DESC LIMIT 1',(card_id,))
        if previous:
            db.execute('UPDATE work_records SET after_card_version=%s WHERE id=%s',(stored['version'],previous['id']))
    audit(db,actor,kind,card,stored,action.get('reason'),{'record_id':record_id,'children':[c['id'] for c in children]})
    event={'temporary':'TEMPORARY_OPERATION','skip':'SKIP_NODE','split':'SPLIT','rework':'REWORK','scrap':'QUALITY_SCRAP'}.get(kind)
    if stored['status']=='QUALITY_HOLD' and card['status']!='QUALITY_HOLD': event='QUALITY_HOLD'
    if stored['status']=='ABNORMAL' and card['status']!='ABNORMAL': event='RUNCARD_ABNORMAL'
    if event: notify(db,event,card_id,body=f"{stored['card_no']} · {action.get('reason') or ''}")
    if kind in {'release','revoke'} or stored['status'] in {'SCRAPPED','TRANSFERRED','CANCELLED'}:
        db.execute('UPDATE notification_jobs SET resolved_at=now() WHERE run_card_id=%s AND resolved_at IS NULL',(card_id,))
    gates.after_action(db,actor,card,stored,kind,record_id)
    return {'card':stored,'children':children}


def report_breakdown(db,actor,equipment_id,data):
    allowed(actor,'work_records:abnormal_end')
    allowed(actor,'equipment_status:set')
    require(bool(data['reason'].strip()),'REASON_REQUIRED','Breakdown reason required')
    reason=one(db,"SELECT * FROM equipment_reasons WHERE code=%s AND state='MAINTENANCE' AND code LIKE 'DU_%%'",(data['reason_code'],))
    require(reason,'EQUIPMENT_REASON','Choose an unplanned downtime reason DU_1…DU_5')
    cards=all_rows(db,'SELECT c.* FROM run_cards c JOIN work_records w ON w.run_card_id=c.id WHERE w.equipment_id=%s AND w.ended_at IS NULL AND w.revoked_at IS NULL ORDER BY c.id',(equipment_id,))
    supplied=data['card_versions']
    require(set(supplied)=={str(c['id']) for c in cards},'VERSION_CONFLICT','Machine occupancy changed; review affected cards')
    results=[]
    for c in cards:
        results.append(card_action(db,actor,c['id'],{'type':'abnormal-end','version':supplied[str(c['id'])],'disposition':'keep','reason':data['reason']}))
    equipment=set_equipment(db,actor,equipment_id,data['reason_code'],data['reason'])
    notify(db,'EQUIPMENT_ABNORMAL',equipment_id=equipment_id,body=data['reason'])
    return {'equipment':equipment,'cards':results}
