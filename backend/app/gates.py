"""Hooks the RunCard command service calls around each action.

precheck  — production gates checked before a start/finish (skill, first article, PM, tool life, material).
after_*   — side effects after the card is stored (tool usage, schedule, work order status, maintenance orders).
Every gate is off by default and switched on in app_settings, so the original rules keep working unchanged.
"""
import os
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from .db import one, all_rows
from .domain import require, number

START = {'check-in', 'temporary', 'skip'}


def setting(db, key, default=None):
    row = one(db, 'SELECT value FROM app_settings WHERE key=%s', (key,))
    return row['value'] if row else default


def factory_tz():
    return ZoneInfo(os.environ.get('FACTORY_TZ', 'Asia/Taipei'))


def current_shift(db, now=None):
    """Return the shift containing `now` and the timestamp at which that shift instance started."""
    now = (now or datetime.now(factory_tz())).astimezone(factory_tz())
    for s in all_rows(db, 'SELECT * FROM shifts WHERE is_active ORDER BY start_time'):
        start, end, t = s['start_time'], s['end_time'], now.time()
        if start < end and start <= t < end:
            began = datetime.combine(now.date(), start, now.tzinfo)
        elif start >= end and (t >= start or t < end):
            day = now.date() if t >= start else now.date() - timedelta(days=1)
            began = datetime.combine(day, start, now.tzinfo)
        else:
            continue
        return {'code': s['code'], 'name': s['name'], 'started_at': began}
    return {'code': None, 'name': None, 'started_at': datetime.combine(now.date(), time(0), now.tzinfo)}


def precheck(db, actor, card, action, ctx, kind):
    start = kind in START or (kind == 'backfill' and card['status'] == 'WAITING')
    finish = kind == 'check-out' or (kind == 'backfill' and card['status'] == 'IN_PROGRESS')
    op, equipment = ctx.get('operation'), ctx.get('equipment')
    if start and op:
        worker = action.get('actual_operator_id') if kind == 'backfill' else actor['id']
        if setting(db, 'skill_enforced') and op.get('requires_skill'):
            skill = one(db, 'SELECT 1 FROM operator_skills WHERE account_id=%s AND operation_id=%s AND (expires_at IS NULL OR expires_at>=current_date)',
                        (worker, op['id']))
            require(skill, 'SKILL', 'Nhân viên chưa có chứng chỉ tay nghề còn hạn cho công đoạn này / 作業員無此工序有效資格')
        if equipment and op['requires_equipment']:
            if setting(db, 'fai_enforced') and op.get('requires_first_article'):
                shift = current_shift(db)
                fai = one(db, "SELECT 1 FROM first_article_checks WHERE equipment_id=%s AND operation_id=%s AND result='PASS' AND approved_at>=%s",
                          (equipment['id'], op['id'], shift['started_at']))
                require(fai, 'FIRST_ARTICLE', 'Cần kiểm đầu chuyền (首件) đạt và được duyệt trong ca này / 本班首件須合格並經核准')
            if setting(db, 'pm_block_overdue'):
                overdue = one(db, 'SELECT name FROM pm_plans WHERE equipment_id=%s AND is_active AND next_due_at<now() LIMIT 1', (equipment['id'],))
                require(not overdue, 'PM_OVERDUE', 'Máy đã quá hạn bảo dưỡng định kỳ / 設備保養逾期')
            worn = one(db, "SELECT code FROM tools WHERE equipment_id=%s AND status='MOUNTED' AND life_limit IS NOT NULL AND used>=life_limit LIMIT 1", (equipment['id'],))
            require(not worn, 'TOOL_LIFE', f"Dụng cụ {worn['code'] if worn else ''} đã hết tuổi thọ / 治具 {worn['code'] if worn else ''} 壽命已到")
    if finish and setting(db, 'material_enforced') and ctx.get('open_record'):
        record = ctx['open_record']
        missing = one(db, '''SELECT m.code, l.qty_per*%s AS need, COALESCE(SUM(c.qty),0) AS got
            FROM boms b JOIN bom_lines l ON l.bom_id=b.id JOIN materials m ON m.id=l.material_id
            LEFT JOIN material_consumptions c ON c.work_record_id=%s AND c.material_id=l.material_id
            WHERE b.item_no=%s AND b.status='ACTIVE' AND l.operation_id=%s
            GROUP BY m.code,l.qty_per HAVING COALESCE(SUM(c.qty),0) < l.qty_per*%s LIMIT 1''',
                      (record['input_qty'], record['id'], card['item_no'], record['operation_id'], record['input_qty']))
        require(not missing, 'MATERIAL', f"Chưa cấp đủ vật tư {missing['code'] if missing else ''} theo BOM / 物料 {missing['code'] if missing else ''} 未依 BOM 投料")


def after_action(db, actor, before, stored, kind, record_id):
    if record_id and stored['status'] != before['status'] and before['status'] == 'IN_PROGRESS':
        rec = one(db, 'SELECT * FROM work_records WHERE id=%s', (record_id,))
        if rec and rec['ended_at'] and rec['equipment_id'] and rec['output_qty']:
            db.execute("UPDATE tools SET used=used+%s WHERE equipment_id=%s AND status='MOUNTED'", (rec['output_qty'], rec['equipment_id']))
            db.execute("UPDATE schedule_slots SET status='DONE' WHERE run_card_id=%s AND operation_id=%s AND status='PLANNED'",
                       (stored['id'], rec['operation_id']))
    refresh_work_order(db, stored.get('work_order_id'))


def refresh_work_order(db, wo_id):
    if not wo_id:
        return
    wo = one(db, 'SELECT * FROM work_orders WHERE id=%s FOR UPDATE', (wo_id,))
    if not wo or wo['status'] in ('PLANNED', 'CLOSED', 'CANCELLED'):
        return
    s = one(db, '''SELECT count(*) AS n,
        count(*) FILTER (WHERE status IN ('COMPLETED','SCRAPPED','CANCELLED','TRANSFERRED')) AS done,
        count(*) FILTER (WHERE status='COMPLETED') AS completed,
        count(*) FILTER (WHERE status<>'WAITING' OR EXISTS (SELECT 1 FROM work_records w WHERE w.run_card_id=run_cards.id AND w.revoked_at IS NULL)) AS started
        FROM run_cards WHERE work_order_id=%s AND source_type<>'DEFECT' ''', (wo_id,))
    status = 'COMPLETED' if s['n'] and s['done'] == s['n'] and s['completed'] else 'IN_PROGRESS' if s['started'] else 'RELEASED'
    if status != wo['status']:
        db.execute('UPDATE work_orders SET status=%s WHERE id=%s', (status, wo_id))


def next_no(db, prefix, table, column):
    stamp = datetime.now(factory_tz()).strftime('%y%m')
    n = one(db, f"SELECT count(*)+1 AS n FROM {table} WHERE {column} LIKE %s", (f'{prefix}{stamp}%',))['n']
    return f'{prefix}{stamp}{n:04d}'


def on_equipment_change(db, actor, before, after, reason, note):
    """Breakdown status opens a maintenance order automatically; leaving maintenance does not close it."""
    if after['state'] == 'MAINTENANCE' and reason['code'].startswith('DU_'):
        open_mo = one(db, "SELECT id FROM maintenance_orders WHERE equipment_id=%s AND kind='BREAKDOWN' AND status<>'DONE'", (after['id'],))
        if not open_mo:
            db.execute('INSERT INTO maintenance_orders(mo_no,equipment_id,kind,reason_code,description,reported_by) VALUES(%s,%s,%s,%s,%s,%s)',
                       (next_no(db, 'MO', 'maintenance_orders', 'mo_no'), after['id'], 'BREAKDOWN', reason['code'], note or reason['name'], actor['id']))


def quantity(value):
    return number(value)
