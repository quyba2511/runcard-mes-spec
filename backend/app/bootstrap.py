"""Apply runtime migrations and initialize demo identities on first startup."""
import os
from pathlib import Path
from .db import connection, one, lock
from .security import PERMISSIONS, hash_password

EVENTS = {'TEMPORARY_OPERATION':'臨時工序放行','DEFECT_CREATED':'瑕疵單開立','DEFECT_VOIDED':'瑕疵單作廢',
 'EQUIPMENT_ABNORMAL':'設備異常','EQUIPMENT_MAINTENANCE':'設備維修','SKIP_NODE':'跳站上工',
 'QUALITY_HOLD':'品質不合格待判定','QUALITY_SCRAP':'品質報廢','REWORK':'重工判定','RUNCARD_ABNORMAL':'RunCard 異常','SPLIT':'拆批',
 'PM_DUE':'保養到期','NCR_OPENED':'不合格品報告','TOOL_LIFE':'治具壽命到期'}


def migrate_extensions(db):
    if one(db, "SELECT 1 FROM app_settings WHERE key='migration_003'"):
        return
    # Older builds ran the extension script through PostgreSQL's init directory.
    if not one(db, """SELECT 1 FROM app_settings WHERE key='erp_format'
                      AND to_regclass('public.erp_inbound_log') IS NOT NULL"""):
        backend_dir = Path(__file__).resolve().parents[1]
        filename = '003_mes_extensions.sql'
        migration = backend_dir / 'db' / filename
        if not migration.is_file():
            migration = backend_dir.parent / 'db' / filename
        db.execute(migration.read_text(encoding='utf-8'))
    db.execute("INSERT INTO app_settings VALUES('migration_003','true')")


def bootstrap():
    with connection() as db:
        lock(db)
        migrate_extensions(db)
        for p in PERMISSIONS:
            db.execute('INSERT INTO permissions(code,description) VALUES(%s,%s) ON CONFLICT DO NOTHING', (p,p))
        standard = ['backend:access','equipment_status:set','quality:judge','work_records:abnormal_end','work_records:backfill']
        roles = {
            'admin':PERMISSIONS, 'operator':['equipment_status:set','work_records:abnormal_end'],
            'operator_measurement':['backend:access','equipment_status:set','rework:create'],
            'qa':['backend:access','quality:judge','rework:create'],
            'engineer_equipment':['backend:access','equipment_status:set','work_records:abnormal_end'],
            'engineer_process':standard, 'engineer_measurement':standard+['rework:create'],
            'engineering_supervisor':standard+['process_routes:manage'], 'manufacturing_supervisor':standard,
            'manufacturing_supervisor_rework':standard+['rework:create'], 'pc_supervisor':standard+['run_cards:create'],
            'qa_supervisor':standard+['rework:create'], 'team_leader':standard+['rework:create']}
        first = not one(db, "SELECT 1 FROM app_settings WHERE key='bootstrapped'")
        if not first:
            return
        for code, perms in roles.items():
            r = one(db, 'INSERT INTO roles(code,name) VALUES(%s,%s) ON CONFLICT(code) DO UPDATE SET code=EXCLUDED.code RETURNING id', (code,code))
            for p in perms:
                db.execute('INSERT INTO role_permissions VALUES(%s,%s) ON CONFLICT DO NOTHING',(r['id'],p))
        for code, name in EVENTS.items():
            severe = code in {'EQUIPMENT_ABNORMAL','EQUIPMENT_MAINTENANCE','RUNCARD_ABNORMAL'}
            db.execute('INSERT INTO notification_rules(event_code,name,severity,channel_email,repeat_enabled,repeat_every_min,repeat_max,escalate_enabled,escalate_after_min) VALUES(%s,%s,%s,%s,%s,30,5,%s,30) ON CONFLICT DO NOTHING',
                       (code,name,'SEVERE' if severe else 'NORMAL',severe,severe,severe))
            db.execute("INSERT INTO notification_rule_targets(event_code,role_id,is_escalation) SELECT %s,id,false FROM roles WHERE code IN ('admin','qa')", (code,))
            db.execute("INSERT INTO notification_rule_targets(event_code,role_id,is_escalation) SELECT %s,id,true FROM roles WHERE code='admin'", (code,))
        if os.environ.get('SEED_DEMO_ACCOUNTS','false').lower() == 'true':
            password = os.environ['DEMO_ADMIN_PASSWORD']
            for user, employee, role in [('admin','ADMIN001','admin'),('operator','OP001','operator'),('qa','QA001','qa')]:
                db.execute('INSERT INTO accounts(username,display_name,employee_no,role_id,password_hash) SELECT %s,%s,%s,id,%s FROM roles WHERE code=%s ON CONFLICT(username) DO NOTHING',
                           (user,user,employee,hash_password(password),role))
            for no, qty in [('DEMO-RC-0001',100),('DEMO-RC-0002',50),('DEMO-RC-0003',200)]:
                db.execute("INSERT INTO run_cards(card_no,work_order_no,item_no,item_name,unit,route_version_id,current_node_id,original_qty,current_qty) SELECT %s,'DEMO-WO-001','PCBA-001','PCBA 控制板','pcs',v.id,n.id,%s,%s FROM process_route_versions v JOIN route_nodes n ON n.route_version_id=v.id WHERE v.status='ACTIVE' AND n.seq=1 ORDER BY v.id LIMIT 1 ON CONFLICT(card_no) DO NOTHING", (no,qty,qty))
            db.execute("INSERT INTO operation_qc_items(operation_id,name,unit,lower_limit,upper_limit) SELECT id,'錫膏厚度 / Độ dày kem hàn','μm',100,150 FROM operations WHERE code='DEMO_PRT'")
        db.execute("INSERT INTO app_settings VALUES('bootstrapped','true')")

if __name__ == '__main__':
    bootstrap()
