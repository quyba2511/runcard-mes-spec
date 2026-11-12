"""Extended MES functions: work orders, scheduling, materials/BOM, traceability, quality, reports/OEE,
maintenance, skills, documents, connected devices, ERP inbound, labels.

All writes go through run_command (global command lock + Idempotency-Key + one-shot operator sessions),
exactly like RunCard actions, and are written to audit_logs/sync_outbox."""
import hmac
import math
import os
import secrets
import statistics
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, ConfigDict, Field

from .db import connection, one, all_rows, clean, jb, lock
from .domain import require
from .gates import current_shift, factory_tz, next_no, refresh_work_order, setting
from .security import allowed, authenticate, digest
from .service import audit, create_card, notify, run_command, set_equipment

router = APIRouter(prefix='/api')
TERMINAL = ('COMPLETED', 'SCRAPPED', 'CANCELLED', 'TRANSFERRED')


# ---------- auth helpers ----------
def actor(authorization: Annotated[str | None, Header()] = None):
    require(authorization and authorization.startswith('Bearer '), 'UNAUTHORIZED', 'Bearer token required')
    with connection() as db:
        return authenticate(db, authorization[7:])


Actor = Annotated[dict, Depends(actor)]
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=8, max_length=160)]


def admin(a, permission='backend:access'):
    require(a['mode'] == 'admin', 'FORBIDDEN', 'Admin session required')
    allowed(a, 'backend:access')
    allowed(a, permission)


def device(db, token, kind):
    row = one(db, 'SELECT * FROM devices WHERE token_hash=%s AND is_active AND kind=%s', (digest(token or ''), kind))
    require(row, 'UNAUTHORIZED', f'Active {kind} device token required')
    db.execute('UPDATE devices SET last_seen_at=now() WHERE id=%s', (row['id'],))
    return row


def command(a, key, payload, fn):
    with connection() as db:
        return run_command(db, a, key, payload, lambda: fn(db))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


def window(frm, to, days=7):
    to = to or datetime.now(timezone.utc)
    frm = frm or to - timedelta(days=days)
    require(frm < to, 'RANGE', 'from must be before to')
    return frm, to


# ======================================================================
# 1. Work orders (工單)
# ======================================================================
class WorkOrder(Strict):
    wo_no: str = Field(min_length=1, max_length=80)
    item_no: str | None = None
    item_name: str | None = None
    planned_qty: Decimal = Field(gt=0)
    unit: str = 'pcs'
    lot_size: Decimal | None = Field(default=None, gt=0)
    route_version_id: int | None = None
    due_date: date | None = None
    priority: int = Field(default=5, ge=1, le=9)


WO_PROGRESS = '''SELECT w.*, r.name AS route_name, v.version AS route_version,
  COALESCE(sum(c.current_qty) FILTER (WHERE c.status='COMPLETED' AND c.source_type<>'DEFECT'),0) AS good_qty,
  COALESCE(sum(c.current_qty) FILTER (WHERE c.status NOT IN ('COMPLETED','SCRAPPED','CANCELLED','TRANSFERRED') AND c.source_type<>'DEFECT'),0) AS wip_qty,
  COALESCE(sum(c.scrap_qty),0) AS scrap_qty, COALESCE(sum(c.defect_qty),0) AS defect_qty,
  count(c.id) FILTER (WHERE c.source_type<>'DEFECT') AS card_count
  FROM work_orders w LEFT JOIN process_route_versions v ON v.id=w.route_version_id LEFT JOIN process_routes r ON r.id=v.route_id
  LEFT JOIN run_cards c ON c.work_order_id=w.id'''


@router.get('/work-orders', tags=['work-orders'])
def list_work_orders(a: Actor, status: str | None = None):
    with connection() as db:
        return clean(all_rows(db, WO_PROGRESS + ' WHERE (%s::text IS NULL OR w.status=%s) GROUP BY w.id,r.name,v.version ORDER BY w.priority, w.due_date NULLS LAST, w.created_at', (status, status)))


@router.get('/work-orders/{wo_id}', tags=['work-orders'])
def work_order(wo_id: UUID, a: Actor):
    with connection() as db:
        wo = one(db, WO_PROGRESS + ' WHERE w.id=%s GROUP BY w.id,r.name,v.version', (wo_id,))
        require(wo, 'NOT_FOUND', 'Work order not found')
        return clean({'work_order': wo, 'cards': all_rows(db, 'SELECT * FROM run_cards WHERE work_order_id=%s ORDER BY card_no', (wo_id,))})


def upsert_work_order(db, a, d, source='SYSTEM', erp_ref=None):
    if d.get('route_version_id') is None:
        v = one(db, "SELECT v.id FROM process_route_versions v WHERE v.status='ACTIVE' ORDER BY v.id LIMIT 1")
        d['route_version_id'] = v and v['id']
    existing = one(db, 'SELECT * FROM work_orders WHERE wo_no=%s FOR UPDATE', (d['wo_no'],))
    cols = ('item_no', 'item_name', 'planned_qty', 'unit', 'lot_size', 'route_version_id', 'due_date', 'priority')
    if existing:
        require(existing['status'] == 'PLANNED', 'WORK_ORDER', 'Only PLANNED work orders can be changed')
        wo = one(db, f"UPDATE work_orders SET {','.join(c + '=%s' for c in cols)} WHERE id=%s RETURNING *", (*[d.get(c) for c in cols], existing['id']))
    else:
        wo = one(db, f"INSERT INTO work_orders(wo_no,source,erp_ref,{','.join(cols)}) VALUES(%s,%s,%s,{','.join(['%s'] * len(cols))}) RETURNING *",
                 (d['wo_no'], source, erp_ref, *[d.get(c) for c in cols]))
    audit(db, a, 'work-order-upsert', payload={'work_order': wo})
    return wo


@router.post('/work-orders', tags=['work-orders'])
def create_work_order(body: WorkOrder, a: Actor, key: Key):
    admin(a, 'run_cards:create')
    d = body.model_dump()
    return command(a, key, {'wo': d}, lambda db: upsert_work_order(db, a, d))


@router.post('/work-orders/{wo_id}/release', tags=['work-orders'])
def release_work_order(wo_id: UUID, a: Actor, key: Key):
    """Split the planned quantity into RunCards of lot_size (last card takes the remainder)."""
    admin(a, 'run_cards:create')

    def run(db):
        wo = one(db, 'SELECT * FROM work_orders WHERE id=%s FOR UPDATE', (wo_id,))
        require(wo and wo['status'] == 'PLANNED', 'WORK_ORDER', 'Only PLANNED work orders can be released')
        require(wo['route_version_id'], 'ROUTE', 'Work order needs a route version')
        lot = wo['lot_size'] or wo['planned_qty']
        n = math.ceil(wo['planned_qty'] / lot)
        cards = []
        for i in range(n):
            qty = min(lot, wo['planned_qty'] - lot * i)
            cards.append(create_card(db, a, {'card_no': f"{wo['wo_no']}-{i + 1:03d}", 'work_order_no': wo['wo_no'], 'item_no': wo['item_no'],
                                             'item_name': wo['item_name'], 'unit': wo['unit'], 'original_qty': qty,
                                             'route_version_id': wo['route_version_id'], 'work_order_id': wo['id']}))
        wo = one(db, "UPDATE work_orders SET status='RELEASED', released_at=now() WHERE id=%s RETURNING *", (wo_id,))
        audit(db, a, 'work-order-release', payload={'work_order': wo, 'cards': [c['card_no'] for c in cards]})
        return {'work_order': wo, 'cards': cards}
    return command(a, key, {'release': str(wo_id)}, run)


class WoClose(Strict):
    status: Literal['CLOSED', 'CANCELLED']
    reason: str = Field(min_length=1)


@router.post('/work-orders/{wo_id}/close', tags=['work-orders'])
def close_work_order(wo_id: UUID, body: WoClose, a: Actor, key: Key):
    admin(a, 'run_cards:create')

    def run(db):
        wo = one(db, 'SELECT * FROM work_orders WHERE id=%s FOR UPDATE', (wo_id,))
        require(wo, 'NOT_FOUND', 'Work order not found')
        if body.status == 'CANCELLED':
            require(wo['status'] == 'PLANNED', 'WORK_ORDER', 'Cancel is only for PLANNED orders; cancel released cards one by one')
        else:
            open_cards = one(db, f"SELECT count(*) AS n FROM run_cards WHERE work_order_id=%s AND status NOT IN {TERMINAL}", (wo_id,))['n']
            require(open_cards == 0, 'WORK_ORDER', f'{open_cards} RunCard(s) still open')
        wo = one(db, 'UPDATE work_orders SET status=%s, closed_at=now() WHERE id=%s RETURNING *', (body.status, wo_id))
        audit(db, a, 'work-order-' + body.status.lower(), reason=body.reason, payload={'work_order': wo})
        return wo
    return command(a, key, {'close': str(wo_id), **body.model_dump()}, run)


# ======================================================================
# 2. Scheduling / dispatch
# ======================================================================
class AutoSchedule(Strict):
    default_cycle_sec: Decimal = Field(default=Decimal(30), gt=0)


@router.post('/schedule/auto', tags=['schedule'])
def auto_schedule(body: AutoSchedule, a: Actor, key: Key):
    """Greedy finite-capacity plan for the current operation of every waiting card:
    priority → due date → age, each to the eligible enabled machine that frees up first."""
    admin(a, 'run_cards:manage')

    def run(db):
        db.execute("UPDATE schedule_slots SET status='CANCELLED' WHERE status='PLANNED'")
        now = datetime.now(timezone.utc)
        free = {}
        for r in all_rows(db, '''SELECT w.equipment_id, w.started_at + (w.input_qty*COALESCE(o.std_cycle_sec,%s)) * interval '1 second' AS ends
                                  FROM work_records w JOIN operations o ON o.id=w.operation_id WHERE w.ended_at IS NULL AND w.revoked_at IS NULL AND w.equipment_id IS NOT NULL''',
                              (body.default_cycle_sec,)):
            free[r['equipment_id']] = max(now, r['ends'])
        cards = all_rows(db, '''SELECT c.id, c.card_no, c.current_qty, n.operation_id, o.std_cycle_sec, o.requires_equipment, w.due_date
            FROM run_cards c JOIN route_nodes n ON n.id=c.current_node_id JOIN operations o ON o.id=n.operation_id
            LEFT JOIN work_orders w ON w.id=c.work_order_id
            WHERE c.status='WAITING' AND c.current_qty>0
              AND NOT EXISTS (SELECT 1 FROM rework_returns r WHERE r.target_card_id=c.id AND r.finished_at IS NULL)
            ORDER BY COALESCE(w.priority,5), w.due_date NULLS LAST, c.created_at''')
        slots, unplanned = [], []
        for c in cards:
            if not c['requires_equipment']:
                continue
            machines = all_rows(db, '''SELECT e.id, e.mes_code FROM equipment_operations eo JOIN equipments e ON e.id=eo.equipment_id
                                       WHERE eo.operation_id=%s AND e.is_active AND e.state='ENABLED' ''', (c['operation_id'],))
            if not machines:
                unplanned.append({'card_no': c['card_no'], 'reason': 'NO_ENABLED_EQUIPMENT'})
                continue
            m = min(machines, key=lambda x: free.get(x['id'], now))
            start = free.get(m['id'], now)
            end = start + timedelta(seconds=float(c['current_qty'] * (c['std_cycle_sec'] or body.default_cycle_sec)))
            free[m['id']] = end
            slots.append(one(db, 'INSERT INTO schedule_slots(equipment_id,run_card_id,operation_id,planned_start,planned_end) VALUES(%s,%s,%s,%s,%s) RETURNING *',
                             (m['id'], c['id'], c['operation_id'], start, end)))
        audit(db, a, 'schedule-auto', payload={'planned': len(slots), 'unplanned': unplanned})
        return {'planned': len(slots), 'unplanned': unplanned}
    return command(a, key, {'auto': body.model_dump()}, run)


SLOTS = '''SELECT s.*, c.card_no, c.current_qty, c.unit, e.mes_code, e.name AS equipment_name, o.code AS operation_code, o.name AS operation_name,
  w.wo_no, w.due_date, w.priority, (w.due_date IS NOT NULL AND s.planned_end > (w.due_date + 1)::timestamptz) AS late
  FROM schedule_slots s JOIN run_cards c ON c.id=s.run_card_id JOIN equipments e ON e.id=s.equipment_id
  JOIN operations o ON o.id=s.operation_id LEFT JOIN work_orders w ON w.id=c.work_order_id'''


@router.get('/schedule', tags=['schedule'])
def schedule(a: Actor):
    with connection() as db:
        return clean(all_rows(db, SLOTS + " WHERE s.status='PLANNED' ORDER BY e.mes_code, s.planned_start"))


@router.get('/dispatch/{equipment_id}', tags=['schedule'])
def dispatch(equipment_id: UUID, a: Actor):
    with connection() as db:
        return clean(all_rows(db, SLOTS + " WHERE s.status='PLANNED' AND s.equipment_id=%s ORDER BY s.planned_start LIMIT 20", (equipment_id,)))


# ======================================================================
# 3. Materials, BOM, lots, inventory
# ======================================================================
class Material(Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit: str = 'pcs'
    is_active: bool = True


@router.get('/materials', tags=['materials'])
def materials(a: Actor):
    with connection() as db:
        return clean(all_rows(db, '''SELECT m.*, COALESCE(sum(l.remaining_qty) FILTER (WHERE l.status='AVAILABLE'),0) AS on_hand, count(l.id) AS lots
                                     FROM materials m LEFT JOIN material_lots l ON l.material_id=m.id GROUP BY m.id ORDER BY m.code'''))


def upsert_material(db, a, d):
    m = one(db, '''INSERT INTO materials(code,name,unit,is_active) VALUES(%s,%s,%s,%s)
                   ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name, unit=EXCLUDED.unit, is_active=EXCLUDED.is_active RETURNING *''',
            (d['code'], d['name'], d.get('unit') or 'pcs', d.get('is_active', True)))
    audit(db, a, 'material-upsert', payload={'material': m})
    return m


@router.post('/materials', tags=['materials'])
def save_material(body: Material, a: Actor, key: Key):
    admin(a, 'run_cards:manage')
    d = body.model_dump()
    return command(a, key, {'material': d}, lambda db: upsert_material(db, a, d))


class BomLine(Strict):
    material_code: str
    operation_code: str | None = None
    qty_per: Decimal = Field(gt=0)


class Bom(Strict):
    item_no: str = Field(min_length=1)
    version: str = Field(min_length=1)
    lines: list[BomLine] = Field(min_length=1)
    activate: bool = True


@router.get('/boms', tags=['materials'])
def boms(a: Actor, item_no: str | None = None):
    with connection() as db:
        rows = all_rows(db, 'SELECT * FROM boms WHERE (%s::text IS NULL OR item_no=%s) ORDER BY item_no, version', (item_no, item_no))
        for b in rows:
            b['lines'] = all_rows(db, '''SELECT l.*, m.code AS material_code, m.name AS material_name, m.unit, o.code AS operation_code
                                         FROM bom_lines l JOIN materials m ON m.id=l.material_id LEFT JOIN operations o ON o.id=l.operation_id
                                         WHERE l.bom_id=%s ORDER BY o.code, m.code''', (b['id'],))
        return clean(rows)


def save_bom_rows(db, a, d):
    require(not one(db, 'SELECT 1 FROM boms WHERE item_no=%s AND version=%s', (d['item_no'], d['version'])), 'BOM', 'BOM version already exists; create a new version')
    if d.get('activate', True):
        db.execute("UPDATE boms SET status='RETIRED' WHERE item_no=%s AND status='ACTIVE'", (d['item_no'],))
    b = one(db, 'INSERT INTO boms(item_no,version,status) VALUES(%s,%s,%s) RETURNING *', (d['item_no'], d['version'], 'ACTIVE' if d.get('activate', True) else 'DRAFT'))
    for line in d['lines']:
        m = one(db, 'SELECT id FROM materials WHERE code=%s', (line['material_code'],))
        require(m, 'MATERIAL', f"Unknown material {line['material_code']}")
        op = None
        if line.get('operation_code'):
            op = one(db, 'SELECT id FROM operations WHERE code=%s', (line['operation_code'],))
            require(op, 'OPERATION', f"Unknown operation {line['operation_code']}")
        db.execute('INSERT INTO bom_lines(bom_id,material_id,operation_id,qty_per) VALUES(%s,%s,%s,%s)', (b['id'], m['id'], op and op['id'], line['qty_per']))
    audit(db, a, 'bom-create', payload={'bom': b, 'lines': d['lines']})
    return b


@router.post('/boms', tags=['materials'])
def save_bom(body: Bom, a: Actor, key: Key):
    admin(a, 'run_cards:manage')
    d = body.model_dump()
    return command(a, key, {'bom': d}, lambda db: save_bom_rows(db, a, d))


class Receive(Strict):
    lot_no: str = Field(min_length=1)
    material_code: str
    qty: Decimal = Field(gt=0)
    supplier: str | None = None
    expires_at: date | None = None


@router.get('/lots', tags=['materials'])
def lots(a: Actor, material_code: str | None = None, available: bool = False):
    with connection() as db:
        return clean(all_rows(db, '''SELECT l.*, m.code AS material_code, m.name AS material_name, m.unit FROM material_lots l JOIN materials m ON m.id=l.material_id
                                     WHERE (%s::text IS NULL OR m.code=%s) AND (NOT %s OR l.status='AVAILABLE') ORDER BY l.received_at DESC LIMIT 500''',
                              (material_code, material_code, available)))


def receive_lot(db, a, d):
    m = one(db, 'SELECT id FROM materials WHERE code=%s AND is_active', (d['material_code'],))
    require(m, 'MATERIAL', 'Unknown material')
    lot = one(db, 'INSERT INTO material_lots(lot_no,material_id,supplier,received_qty,remaining_qty,expires_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
              (d['lot_no'], m['id'], d.get('supplier'), d['qty'], d['qty'], d.get('expires_at')))
    db.execute("INSERT INTO inventory_transactions(lot_id,txn_type,qty,actor_id,reason) VALUES(%s,'RECEIVE',%s,%s,'receive')", (lot['id'], d['qty'], a['id']))
    audit(db, a, 'lot-receive', payload={'lot': lot})
    return lot


@router.post('/lots/receive', tags=['materials'])
def receive(body: Receive, a: Actor, key: Key):
    admin(a, 'run_cards:manage')
    d = body.model_dump()
    return command(a, key, {'receive': d}, lambda db: receive_lot(db, a, d))


class Adjust(Strict):
    qty: Decimal = Decimal(0)                      # + nhập thêm, − trừ bớt
    txn_type: Literal['ADJUST', 'SCRAP', 'RETURN'] = 'ADJUST'
    status: Literal['AVAILABLE', 'HOLD'] | None = None
    reason: str = Field(min_length=1)


@router.post('/lots/{lot_no}/adjust', tags=['materials'])
def adjust_lot(lot_no: str, body: Adjust, a: Actor, key: Key):
    admin(a, 'run_cards:manage')

    def run(db):
        lot = one(db, 'SELECT * FROM material_lots WHERE lot_no=%s FOR UPDATE', (lot_no,))
        require(lot, 'NOT_FOUND', 'Lot not found')
        qty = -abs(body.qty) if body.txn_type == 'SCRAP' else body.qty
        remaining = lot['remaining_qty'] + qty
        require(remaining >= 0, 'QUANTITY', 'Lot quantity cannot go negative')
        status = body.status or ('CONSUMED' if remaining == 0 else 'AVAILABLE' if lot['status'] == 'CONSUMED' else lot['status'])
        lot = one(db, 'UPDATE material_lots SET remaining_qty=%s, status=%s WHERE id=%s RETURNING *', (remaining, status, lot['id']))
        if qty:
            db.execute('INSERT INTO inventory_transactions(lot_id,txn_type,qty,actor_id,reason) VALUES(%s,%s,%s,%s,%s)', (lot['id'], body.txn_type, qty, a['id'], body.reason))
        audit(db, a, 'lot-adjust', reason=body.reason, payload={'lot': lot, 'qty': qty})
        return lot
    return command(a, key, {'adjust': lot_no, **body.model_dump()}, run)


class Issue(Strict):
    lot_no: str
    qty: Decimal = Field(gt=0)


def bom_for_card(db, card, operation_id):
    return all_rows(db, '''SELECT l.material_id, m.code, m.name, m.unit, l.qty_per FROM boms b JOIN bom_lines l ON l.bom_id=b.id
                           JOIN materials m ON m.id=l.material_id WHERE b.item_no=%s AND b.status='ACTIVE' AND l.operation_id=%s''',
                    (card['item_no'], operation_id))


@router.post('/cards/{card_id}/issue-material', tags=['materials'])
def issue_material(card_id: UUID, body: Issue, a: Actor, key: Key):
    """Operator scans a material lot while the card is in progress: links lot → card → work record (genealogy)."""
    def run(db):
        card = one(db, 'SELECT * FROM run_cards WHERE id=%s FOR UPDATE', (card_id,))
        require(card and card['status'] == 'IN_PROGRESS', 'STATE', 'Issue material while the card is in progress')
        rec = one(db, 'SELECT * FROM work_records WHERE run_card_id=%s AND ended_at IS NULL AND revoked_at IS NULL', (card_id,))
        lot = one(db, 'SELECT * FROM material_lots WHERE lot_no=%s FOR UPDATE', (body.lot_no,))
        require(lot, 'NOT_FOUND', 'Lot not found')
        require(lot['status'] == 'AVAILABLE', 'LOT', f"Lot is {lot['status']}")
        require(not lot['expires_at'] or lot['expires_at'] >= datetime.now(factory_tz()).date(), 'LOT', 'Lot expired')
        require(lot['remaining_qty'] >= body.qty, 'QUANTITY', f"Lot only has {lot['remaining_qty']}")
        bom = bom_for_card(db, card, rec['operation_id'])
        if bom:
            require(any(b['material_id'] == lot['material_id'] for b in bom), 'BOM', 'Material is not in the active BOM for this operation')
        remaining = lot['remaining_qty'] - body.qty
        db.execute('UPDATE material_lots SET remaining_qty=%s, status=%s WHERE id=%s', (remaining, 'CONSUMED' if remaining == 0 else 'AVAILABLE', lot['id']))
        c = one(db, 'INSERT INTO material_consumptions(run_card_id,work_record_id,lot_id,material_id,operation_id,qty,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (card_id, rec['id'], lot['id'], lot['material_id'], rec['operation_id'], body.qty, a['id']))
        db.execute("INSERT INTO inventory_transactions(lot_id,txn_type,qty,run_card_id,work_record_id,actor_id,reason) VALUES(%s,'ISSUE',%s,%s,%s,%s,'issue')",
                   (lot['id'], -body.qty, card_id, rec['id'], a['id']))
        audit(db, a, 'material-issue', before=card, after=card, payload={'lot_no': body.lot_no, 'qty': body.qty, 'record_id': rec['id']})
        return c
    return command(a, key, {'issue': str(card_id), **body.model_dump()}, run)


@router.get('/cards/{card_id}/extras', tags=['operator'])
def card_extras(card_id: UUID, a: Actor):
    """Everything the tablet shows besides the card itself: SOP, BOM progress, first-article state, plan, serials."""
    with connection() as db:
        card = one(db, 'SELECT * FROM run_cards WHERE id=%s', (card_id,))
        require(card, 'NOT_FOUND', 'Card not found')
        rec = one(db, 'SELECT * FROM work_records WHERE run_card_id=%s AND ended_at IS NULL AND revoked_at IS NULL', (card_id,))
        op = one(db, 'SELECT o.* FROM operations o JOIN route_nodes n ON n.operation_id=o.id WHERE n.id=%s', (card['current_node_id'],)) if card['current_node_id'] else None
        op_id = rec['operation_id'] if rec else op and op['id']
        docs = all_rows(db, "SELECT id,doc_no,version,title,file_name FROM documents WHERE status='RELEASED' AND (operation_id=%s OR (item_no IS NOT NULL AND item_no=%s)) ORDER BY doc_no",
                        (op_id, card['item_no']))
        bom = []
        if op_id:
            for b in bom_for_card(db, card, op_id):
                got = one(db, 'SELECT COALESCE(sum(qty),0) AS q FROM material_consumptions WHERE work_record_id=%s AND material_id=%s', (rec and rec['id'], b['material_id']))['q']
                need = b['qty_per'] * (rec['input_qty'] if rec else card['current_qty'])
                bom.append({**b, 'required': need, 'issued': got})
        shift = current_shift(db)
        fai = all_rows(db, '''SELECT f.*, e.mes_code FROM first_article_checks f JOIN equipments e ON e.id=f.equipment_id
                              WHERE f.operation_id=%s AND f.created_at>=%s ORDER BY f.created_at DESC''', (op_id, shift['started_at'])) if op_id else []
        return clean({'operation': op, 'docs': docs, 'bom': bom, 'shift': shift, 'first_articles': fai,
                      'fai_required': bool(op and op['requires_first_article'] and setting(db, 'fai_enforced')),
                      'plan': all_rows(db, SLOTS + " WHERE s.status='PLANNED' AND s.run_card_id=%s", (card_id,)),
                      'serials': one(db, 'SELECT count(*) AS n FROM serial_numbers WHERE run_card_id=%s', (card_id,))['n']})


# ======================================================================
# 4. Serials and traceability
# ======================================================================
@router.post('/cards/{card_id}/serials', tags=['trace'])
def make_serials(card_id: UUID, a: Actor, key: Key):
    admin(a, 'run_cards:manage')

    def run(db):
        card = one(db, 'SELECT * FROM run_cards WHERE id=%s', (card_id,))
        require(card and card['current_qty'] > 0 and card['current_qty'] == int(card['current_qty']), 'SERIAL', 'Serials need a whole positive quantity')
        have = one(db, 'SELECT count(*) AS n FROM serial_numbers WHERE run_card_id=%s', (card_id,))['n']
        new = [f"{card['card_no']}-{i:05d}" for i in range(have + 1, int(card['current_qty']) + 1)]
        for s in new:
            db.execute('INSERT INTO serial_numbers(serial,run_card_id) VALUES(%s,%s)', (s, card_id))
        audit(db, a, 'serials-create', after=card, payload={'count': len(new)})
        return {'created': len(new), 'first': new[0] if new else None, 'last': new[-1] if new else None}
    return command(a, key, {'serials': str(card_id)}, run)


def card_node(db, c):
    return {'card': c,
            'records': all_rows(db, '''SELECT w.id, o.code AS operation_code, o.name AS operation_name, e.mes_code, a.employee_no, a.display_name,
                                   w.input_qty, w.output_qty, w.started_at, w.ended_at, w.end_type, w.revoked_at, w.quality_result
                                   FROM work_records w JOIN operations o ON o.id=w.operation_id LEFT JOIN equipments e ON e.id=w.equipment_id
                                   JOIN accounts a ON a.id=w.operator_id WHERE w.run_card_id=%s ORDER BY w.started_at''', (c['id'],)),
            'materials': all_rows(db, '''SELECT l.lot_no, m.code AS material_code, m.name AS material_name, l.supplier, mc.qty, o.code AS operation_code, mc.at
                                     FROM material_consumptions mc JOIN material_lots l ON l.id=mc.lot_id JOIN materials m ON m.id=mc.material_id
                                     LEFT JOIN operations o ON o.id=mc.operation_id WHERE mc.run_card_id=%s ORDER BY mc.at''', (c['id'],)),
            'ncrs': all_rows(db, 'SELECT ncr_no,status,description FROM ncrs WHERE run_card_id=%s', (c['id'],)),
            'serials': one(db, 'SELECT count(*) AS n FROM serial_numbers WHERE run_card_id=%s', (c['id'],))['n'],
            'children': [card_node(db, x) for x in all_rows(db, 'SELECT * FROM run_cards WHERE parent_card_id=%s ORDER BY card_no', (c['id'],))]}


@router.get('/trace/card/{card_no}', tags=['trace'])
def trace_card(card_no: str, a: Actor):
    """Full genealogy: ancestors up to the root card, then the whole descendant tree with records and material lots."""
    with connection() as db:
        if serial := one(db, 'SELECT run_card_id FROM serial_numbers WHERE serial=%s', (card_no,)):
            c = one(db, 'SELECT * FROM run_cards WHERE id=%s', (serial['run_card_id'],))
        else:
            c = one(db, 'SELECT * FROM run_cards WHERE card_no=%s', (card_no,))
        require(c, 'NOT_FOUND', 'Card or serial not found')
        chain = [c]
        while chain[-1]['parent_card_id']:
            chain.append(one(db, 'SELECT * FROM run_cards WHERE id=%s', (chain[-1]['parent_card_id'],)))
        wo = one(db, 'SELECT * FROM work_orders WHERE id=%s', (c['work_order_id'],)) if c.get('work_order_id') else None
        return clean({'searched': card_no, 'ancestors': [x['card_no'] for x in reversed(chain)], 'work_order': wo, 'tree': card_node(db, chain[-1])})


@router.get('/trace/lot/{lot_no}', tags=['trace'])
def trace_lot(lot_no: str, a: Actor):
    """Forward trace: which cards (and their descendants) used this material lot."""
    with connection() as db:
        lot = one(db, 'SELECT l.*, m.code AS material_code, m.name AS material_name FROM material_lots l JOIN materials m ON m.id=l.material_id WHERE lot_no=%s', (lot_no,))
        require(lot, 'NOT_FOUND', 'Lot not found')
        used = all_rows(db, '''SELECT c.id, c.card_no, c.status, c.work_order_no, c.item_no, mc.qty, o.code AS operation_code, mc.at
                               FROM material_consumptions mc JOIN run_cards c ON c.id=mc.run_card_id LEFT JOIN operations o ON o.id=mc.operation_id
                               WHERE mc.lot_id=%s ORDER BY mc.at''', (lot['id'],))
        for u in used:
            u['descendants'] = [r['card_no'] for r in all_rows(db, '''WITH RECURSIVE d AS (SELECT id, card_no FROM run_cards WHERE parent_card_id=%s
                                UNION ALL SELECT c.id, c.card_no FROM run_cards c JOIN d ON c.parent_card_id=d.id) SELECT card_no FROM d''', (u['id'],))]
        return clean({'lot': lot, 'used_by': used,
                      'transactions': all_rows(db, 'SELECT txn_type, qty, reason, at FROM inventory_transactions WHERE lot_id=%s ORDER BY at', (lot['id'],))})


# ======================================================================
# 5. Quality: SPC, Pareto, NCR, first article
# ======================================================================
@router.get('/quality/spc', tags=['quality'])
def spc(a: Actor, qc_item_id: int, days: int = Query(30, ge=1, le=366)):
    with connection() as db:
        item = one(db, 'SELECT q.*, o.code AS operation_code FROM operation_qc_items q JOIN operations o ON o.id=q.operation_id WHERE q.id=%s', (qc_item_id,))
        require(item, 'NOT_FOUND', 'QC item not found')
        pts = all_rows(db, '''SELECT v.value, v.in_spec, w.ended_at AS at, c.card_no FROM work_record_qc_values v JOIN work_records w ON w.id=v.work_record_id
                              JOIN run_cards c ON c.id=w.run_card_id WHERE v.qc_item_id=%s AND w.revoked_at IS NULL AND w.ended_at>=now()-%s*interval '1 day'
                              ORDER BY w.ended_at''', (qc_item_id, days))
        vals = [float(p['value']) for p in pts]
        stats = {'n': len(vals)}
        if len(vals) >= 2:
            mean, sd = statistics.fmean(vals), statistics.stdev(vals)
            usl = float(item['upper_limit']) if item['upper_limit'] is not None else None
            lsl = float(item['lower_limit']) if item['lower_limit'] is not None else None
            stats.update(mean=mean, sd=sd, ucl=mean + 3 * sd, lcl=mean - 3 * sd,
                         cp=(usl - lsl) / (6 * sd) if sd and usl is not None and lsl is not None else None,
                         cpk=min(x for x in [(usl - mean) / (3 * sd) if usl is not None else None, (mean - lsl) / (3 * sd) if lsl is not None else None] if x is not None) if sd and (usl is not None or lsl is not None) else None)
            stats['out_of_control'] = [i for i, v in enumerate(vals) if sd and abs(v - mean) > 3 * sd]
        return clean({'item': item, 'points': pts, 'stats': stats})


@router.get('/quality/pareto', tags=['quality'])
def pareto(a: Actor, days: int = Query(30, ge=1, le=366)):
    with connection() as db:
        rows = all_rows(db, '''SELECT d.code, d.name, sum(x.qty) AS qty, count(*) AS events FROM work_record_defects x JOIN defect_codes d ON d.id=x.defect_code_id
                               JOIN work_records w ON w.id=x.work_record_id WHERE w.revoked_at IS NULL AND w.ended_at>=now()-%s*interval '1 day'
                               GROUP BY d.code,d.name ORDER BY qty DESC''', (days,))
        total, run = sum(r['qty'] for r in rows) or 1, 0
        for r in rows:
            run += r['qty']
            r['cumulative_pct'] = round(float(run / total * 100), 1)
        return clean(rows)


class Ncr(Strict):
    card_no: str | None = None
    defect_code: str | None = None
    qty: Decimal | None = None
    description: str = Field(min_length=1)
    owner_username: str | None = None


@router.get('/ncrs', tags=['quality'])
def ncrs(a: Actor, status: str | None = None):
    with connection() as db:
        return clean(all_rows(db, '''SELECT n.*, c.card_no, d.code AS defect_code, o.display_name AS owner_name FROM ncrs n LEFT JOIN run_cards c ON c.id=n.run_card_id
                                     LEFT JOIN defect_codes d ON d.id=n.defect_code_id LEFT JOIN accounts o ON o.id=n.owner_id
                                     WHERE (%s::text IS NULL OR n.status=%s) ORDER BY n.opened_at DESC LIMIT 300''', (status, status)))


@router.post('/ncrs', tags=['quality'])
def open_ncr(body: Ncr, a: Actor, key: Key):
    admin(a, 'quality:judge')

    def run(db):
        card = one(db, 'SELECT id FROM run_cards WHERE card_no=%s', (body.card_no,)) if body.card_no else None
        require(card or not body.card_no, 'NOT_FOUND', 'Card not found')
        defect = one(db, 'SELECT id FROM defect_codes WHERE code=%s', (body.defect_code,)) if body.defect_code else None
        owner = one(db, 'SELECT id FROM accounts WHERE username=%s', (body.owner_username,)) if body.owner_username else None
        n = one(db, 'INSERT INTO ncrs(ncr_no,run_card_id,defect_code_id,qty,description,owner_id,opened_by) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (next_no(db, 'NCR', 'ncrs', 'ncr_no'), card and card['id'], defect and defect['id'], body.qty, body.description, owner and owner['id'], a['id']))
        audit(db, a, 'ncr-open', payload={'ncr': n})
        notify(db, 'NCR_OPENED', card and card['id'], body=f"{n['ncr_no']} · {body.description}")
        return n
    return command(a, key, {'ncr': body.model_dump()}, run)


class NcrUpdate(Strict):
    status: Literal['OPEN', 'INVESTIGATING', 'CLOSED']
    root_cause: str | None = None
    corrective_action: str | None = None
    preventive_action: str | None = None


@router.post('/ncrs/{ncr_id}', tags=['quality'])
def update_ncr(ncr_id: int, body: NcrUpdate, a: Actor, key: Key):
    admin(a, 'quality:judge')

    def run(db):
        n = one(db, 'SELECT * FROM ncrs WHERE id=%s FOR UPDATE', (ncr_id,))
        require(n and n['status'] != 'CLOSED', 'NCR', 'NCR not found or already closed')
        root = body.root_cause or n['root_cause']
        corr = body.corrective_action or n['corrective_action']
        if body.status == 'CLOSED':
            require(root and corr, 'NCR', 'Root cause and corrective action are required to close')
        n = one(db, '''UPDATE ncrs SET status=%s, root_cause=%s, corrective_action=%s, preventive_action=COALESCE(%s,preventive_action),
                       closed_at=CASE WHEN %s='CLOSED' THEN now() END WHERE id=%s RETURNING *''', (body.status, root, corr, body.preventive_action, body.status, ncr_id))
        audit(db, a, 'ncr-update', payload={'ncr': n})
        return n
    return command(a, key, {'ncr_update': ncr_id, **body.model_dump()}, run)


class FirstArticle(Strict):
    equipment_id: UUID
    operation_id: int
    run_card_id: UUID | None = None
    result: Literal['PASS', 'FAIL']
    note: str | None = None


@router.get('/first-articles', tags=['quality'])
def first_articles(a: Actor, pending: bool = False):
    with connection() as db:
        return clean(all_rows(db, '''SELECT f.*, e.mes_code, o.code AS operation_code, c.card_no, k.employee_no AS checked_by_no, p.display_name AS approver
            FROM first_article_checks f JOIN equipments e ON e.id=f.equipment_id JOIN operations o ON o.id=f.operation_id
            LEFT JOIN run_cards c ON c.id=f.run_card_id JOIN accounts k ON k.id=f.checked_by LEFT JOIN accounts p ON p.id=f.approved_by
            WHERE (NOT %s OR f.approved_at IS NULL) ORDER BY f.created_at DESC LIMIT 200''', (pending,)))


@router.post('/first-articles', tags=['quality'])
def submit_first_article(body: FirstArticle, a: Actor, key: Key):
    def run(db):
        ok = one(db, 'SELECT 1 FROM equipment_operations WHERE equipment_id=%s AND operation_id=%s', (body.equipment_id, body.operation_id))
        require(ok, 'BR-17', 'Equipment cannot perform this operation')
        f = one(db, 'INSERT INTO first_article_checks(equipment_id,operation_id,run_card_id,result,note,checked_by) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
                (body.equipment_id, body.operation_id, body.run_card_id, body.result, body.note, a['id']))
        audit(db, a, 'first-article-submit', payload={'check': f}, equipment_id=body.equipment_id)
        return f
    return command(a, key, {'fai': body.model_dump()}, run)


@router.post('/first-articles/{fai_id}/approve', tags=['quality'])
def approve_first_article(fai_id: int, a: Actor, key: Key):
    admin(a, 'quality:judge')

    def run(db):
        f = one(db, 'SELECT * FROM first_article_checks WHERE id=%s FOR UPDATE', (fai_id,))
        require(f and not f['approved_at'], 'FIRST_ARTICLE', 'Check not found or already approved')
        require(f['checked_by'] != a['id'], 'FIRST_ARTICLE', 'The checker cannot approve their own first article')
        f = one(db, 'UPDATE first_article_checks SET approved_by=%s, approved_at=now() WHERE id=%s RETURNING *', (a['id'], fai_id))
        audit(db, a, 'first-article-approve', payload={'check': f}, equipment_id=f['equipment_id'])
        return f
    return command(a, key, {'approve': fai_id}, run)


# ======================================================================
# 6. Reports: OEE, downtime, production, maintenance KPIs, machine vs reported
# ======================================================================
def report_oee(db, frm, to):
    """Availability = run(UP) / planned time (window − OFF − planned stops DS_*).
    Performance = Σ(std cycle × processed qty) / run time.  Quality = good / processed."""
    rows = all_rows(db, '''
      WITH h AS (SELECT h.equipment_id, h.reason_code, h.state,
                 extract(epoch FROM LEAST(COALESCE(h.ended_at,now()),%(to)s) - GREATEST(h.started_at,%(frm)s)) AS sec
                 FROM equipment_status_history h WHERE h.started_at < %(to)s AND COALESCE(h.ended_at,now()) > %(frm)s),
      t AS (SELECT equipment_id, sum(sec) FILTER (WHERE state<>'OFF' AND reason_code NOT LIKE 'DS_%%') AS planned,
                   sum(sec) FILTER (WHERE reason_code='UP') AS run,
                   sum(sec) FILTER (WHERE state='MAINTENANCE' AND reason_code LIKE 'DU_%%') AS breakdown FROM h GROUP BY equipment_id),
      p AS (SELECT w.equipment_id, sum(w.input_qty) AS processed, sum(w.output_qty) AS good,
                   sum(w.input_qty*COALESCE(o.std_cycle_sec,0)) AS ideal_sec
            FROM work_records w JOIN operations o ON o.id=w.operation_id
            WHERE w.revoked_at IS NULL AND w.ended_at BETWEEN %(frm)s AND %(to)s AND w.end_type IN ('NORMAL','BACKFILL') GROUP BY w.equipment_id)
      SELECT e.id, e.mes_code, e.name, COALESCE(t.planned,0) AS planned_sec, COALESCE(t.run,0) AS run_sec, COALESCE(t.breakdown,0) AS breakdown_sec,
             COALESCE(p.processed,0) AS processed, COALESCE(p.good,0) AS good, COALESCE(p.ideal_sec,0) AS ideal_sec
      FROM equipments e LEFT JOIN t ON t.equipment_id=e.id LEFT JOIN p ON p.equipment_id=e.id
      WHERE e.is_active AND (t.planned>0 OR p.processed>0) ORDER BY e.mes_code''', {'frm': frm, 'to': to})
    for r in rows:
        av = float(r['run_sec']) / float(r['planned_sec']) if r['planned_sec'] else None
        pf = min(1.0, float(r['ideal_sec']) / float(r['run_sec'])) if r['run_sec'] and r['ideal_sec'] else None
        q = float(r['good']) / float(r['processed']) if r['processed'] else None
        r.update(availability=av, performance=pf, quality=q, oee=av * pf * q if None not in (av, pf, q) else None)
    return rows


def report_downtime(db, frm, to):
    return all_rows(db, '''SELECT h.reason_code, r.name, r.state, r.sub_category, count(*) AS events,
        sum(extract(epoch FROM LEAST(COALESCE(h.ended_at,now()),%(to)s) - GREATEST(h.started_at,%(frm)s))) AS seconds
        FROM equipment_status_history h JOIN equipment_reasons r ON r.code=h.reason_code
        WHERE h.reason_code<>'UP' AND h.started_at<%(to)s AND COALESCE(h.ended_at,now())>%(frm)s
        GROUP BY h.reason_code,r.name,r.state,r.sub_category ORDER BY seconds DESC''', {'frm': frm, 'to': to})


def report_production(db, frm, to, group='operation'):
    rows = all_rows(db, '''SELECT w.started_at, o.code AS operation, e.mes_code AS equipment, a.employee_no AS operator,
        w.input_qty, w.output_qty, extract(epoch FROM w.ended_at-w.started_at) AS sec,
        (SELECT COALESCE(sum(qty),0) FROM work_record_defects d WHERE d.work_record_id=w.id) AS defect_qty
        FROM work_records w JOIN operations o ON o.id=w.operation_id LEFT JOIN equipments e ON e.id=w.equipment_id JOIN accounts a ON a.id=w.operator_id
        WHERE w.revoked_at IS NULL AND w.ended_at BETWEEN %s AND %s AND w.end_type IN ('NORMAL','BACKFILL')''', (frm, to))
    out = {}
    for r in rows:
        k = current_shift(db, r['started_at'])['code'] if group == 'shift' else r['started_at'].astimezone(factory_tz()).date().isoformat() if group == 'day' else r[group]
        g = out.setdefault(k or '—', {'key': k or '—', 'records': 0, 'input': Decimal(0), 'output': Decimal(0), 'defect': Decimal(0), 'labor_sec': 0.0})
        g['records'] += 1
        g['input'] += r['input_qty']
        g['output'] += r['output_qty'] or 0
        g['defect'] += r['defect_qty']
        g['labor_sec'] += float(r['sec'] or 0)
    for g in out.values():
        g['yield'] = float(g['output'] / g['input']) if g['input'] else None
    return sorted(out.values(), key=lambda g: str(g['key']))


def report_maintenance(db, frm, to):
    hours = (to - frm).total_seconds() / 3600
    rows = all_rows(db, '''SELECT e.mes_code, e.name, count(m.id) FILTER (WHERE m.kind='BREAKDOWN') AS breakdowns,
        count(m.id) FILTER (WHERE m.kind='PM') AS pm_done,
        COALESCE(sum(extract(epoch FROM m.finished_at-m.reported_at)/3600) FILTER (WHERE m.kind='BREAKDOWN'),0) AS repair_hours
        FROM equipments e LEFT JOIN maintenance_orders m ON m.equipment_id=e.id AND m.status='DONE' AND m.finished_at BETWEEN %s AND %s
        WHERE e.is_active GROUP BY e.id HAVING count(m.id)>0 ORDER BY e.mes_code''', (frm, to))
    for r in rows:
        n = r['breakdowns']
        r['mttr_hours'] = float(r['repair_hours']) / n if n else None
        r['mtbf_hours'] = (hours - float(r['repair_hours'])) / n if n else None
    return rows


def report_machine_vs_reported(db, frm, to):
    return all_rows(db, '''SELECT e.mes_code, (SELECT max(counter)-min(counter) FROM machine_events m WHERE m.equipment_id=e.id AND m.at BETWEEN %(frm)s AND %(to)s) AS machine_count,
        (SELECT COALESCE(sum(input_qty),0) FROM work_records w WHERE w.equipment_id=e.id AND w.revoked_at IS NULL AND w.ended_at BETWEEN %(frm)s AND %(to)s AND w.end_type IN ('NORMAL','BACKFILL')) AS reported
        FROM equipments e WHERE EXISTS (SELECT 1 FROM machine_events m WHERE m.equipment_id=e.id AND m.at BETWEEN %(frm)s AND %(to)s)''', {'frm': frm, 'to': to})


REPORTS = {'oee': report_oee, 'downtime': report_downtime, 'production': report_production,
           'maintenance': report_maintenance, 'machine-vs-reported': report_machine_vs_reported}


def run_report(name, frm, to, group):
    require(name in REPORTS, 'NOT_FOUND', 'Unknown report')
    with connection() as db:
        f, t = window(frm, to)
        return REPORTS[name](db, f, t, group) if name == 'production' else REPORTS[name](db, f, t)


@router.get('/reports/{name}', tags=['reports'])
def report(name: str, a: Actor, frm: datetime | None = Query(None, alias='from'), to: datetime | None = None,
           group: Literal['operation', 'equipment', 'operator', 'shift', 'day'] = 'operation'):
    admin(a)
    if name.endswith('.xlsx'):
        rows = clean(run_report(name[:-5], frm, to, group))
        wb = Workbook()
        ws = wb.active
        ws.title = name[:-5][:30]
        cols = list(rows[0].keys()) if rows else ['empty']
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) if not isinstance(r.get(c), (dict, list)) else str(r.get(c)) for c in cols])
        buf = BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})
    return clean(run_report(name, frm, to, group))


# ======================================================================
# 7. Maintenance: orders, PM plans, tools
# ======================================================================
class MaintenanceOrder(Strict):
    equipment_id: UUID
    kind: Literal['BREAKDOWN', 'PM'] = 'BREAKDOWN'
    description: str = Field(min_length=1)
    pm_plan_id: int | None = None


@router.get('/maintenance-orders', tags=['maintenance'])
def maintenance_orders(a: Actor, status: str | None = None):
    with connection() as db:
        return clean(all_rows(db, '''SELECT m.*, e.mes_code, e.name AS equipment_name, r.name AS reason_name, s.display_name AS assignee
            FROM maintenance_orders m JOIN equipments e ON e.id=m.equipment_id LEFT JOIN equipment_reasons r ON r.code=m.reason_code
            LEFT JOIN accounts s ON s.id=m.assigned_to WHERE (%s::text IS NULL OR m.status=%s)
            ORDER BY (m.status='DONE'), m.reported_at DESC LIMIT 300''', (status, status)))


@router.post('/maintenance-orders', tags=['maintenance'])
def open_maintenance(body: MaintenanceOrder, a: Actor, key: Key):
    admin(a, 'equipment_status:set')

    def run(db):
        m = one(db, 'INSERT INTO maintenance_orders(mo_no,equipment_id,kind,pm_plan_id,description,reported_by) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
                (next_no(db, 'MO', 'maintenance_orders', 'mo_no'), body.equipment_id, body.kind, body.pm_plan_id, body.description, a['id']))
        audit(db, a, 'maintenance-open', payload={'order': m}, equipment_id=body.equipment_id)
        return m
    return command(a, key, {'mo': body.model_dump()}, run)


class MaintenanceStep(Strict):
    action: Literal['start', 'finish']
    findings: str | None = None
    parts_used: str | None = None
    restore_reason_code: str | None = 'US_1'     # sau sửa: 設備點檢/調機 trước khi chạy lại


@router.post('/maintenance-orders/{mo_id}', tags=['maintenance'])
def step_maintenance(mo_id: int, body: MaintenanceStep, a: Actor, key: Key):
    admin(a, 'equipment_status:set')

    def run(db):
        m = one(db, 'SELECT * FROM maintenance_orders WHERE id=%s FOR UPDATE', (mo_id,))
        require(m and m['status'] != 'DONE', 'MAINTENANCE', 'Order not found or already done')
        if body.action == 'start':
            m = one(db, "UPDATE maintenance_orders SET status='IN_PROGRESS', started_at=now(), assigned_to=%s WHERE id=%s RETURNING *", (a['id'], mo_id))
        else:
            require(body.findings, 'MAINTENANCE', 'Findings are required to finish')
            m = one(db, "UPDATE maintenance_orders SET status='DONE', finished_at=now(), started_at=COALESCE(started_at,now()), findings=%s, parts_used=%s, assigned_to=COALESCE(assigned_to,%s) WHERE id=%s RETURNING *",
                    (body.findings, body.parts_used, a['id'], mo_id))
            if m['pm_plan_id']:
                db.execute("UPDATE pm_plans SET last_done_at=now(), next_due_at=now()+interval_days*interval '1 day' WHERE id=%s", (m['pm_plan_id'],))
            e = one(db, 'SELECT * FROM equipments WHERE id=%s', (m['equipment_id'],))
            if body.restore_reason_code and e['state'] == 'MAINTENANCE':
                set_equipment(db, a, e['id'], body.restore_reason_code, f"{m['mo_no']} done")
        audit(db, a, 'maintenance-' + body.action, payload={'order': m}, equipment_id=m['equipment_id'])
        return m
    return command(a, key, {'mo_step': mo_id, **body.model_dump()}, run)


class PmPlan(Strict):
    equipment_id: UUID
    name: str = Field(min_length=1)
    interval_days: int = Field(gt=0)
    next_due_at: datetime | None = None


@router.get('/pm-plans', tags=['maintenance'])
def pm_plans(a: Actor):
    with connection() as db:
        return clean(all_rows(db, '''SELECT p.*, e.mes_code, (p.next_due_at<now()) AS overdue FROM pm_plans p JOIN equipments e ON e.id=p.equipment_id
                                     WHERE p.is_active ORDER BY p.next_due_at'''))


@router.post('/pm-plans', tags=['maintenance'])
def save_pm_plan(body: PmPlan, a: Actor, key: Key):
    admin(a, 'equipments:manage')

    def run(db):
        p = one(db, "INSERT INTO pm_plans(equipment_id,name,interval_days,next_due_at) VALUES(%s,%s,%s,COALESCE(%s,now()+%s*interval '1 day')) RETURNING *",
                (body.equipment_id, body.name, body.interval_days, body.next_due_at, body.interval_days))
        audit(db, a, 'pm-plan-create', payload={'plan': p}, equipment_id=body.equipment_id)
        return p
    return command(a, key, {'pm': body.model_dump()}, run)


def pm_tick(db):
    """Called by the worker: open a PM order and notify once per due plan."""
    created = 0
    for p in all_rows(db, '''SELECT p.* FROM pm_plans p WHERE p.is_active AND p.next_due_at<=now()+interval '1 day'
                             AND NOT EXISTS (SELECT 1 FROM maintenance_orders m WHERE m.pm_plan_id=p.id AND m.status<>'DONE') FOR UPDATE SKIP LOCKED'''):
        db.execute("INSERT INTO maintenance_orders(mo_no,equipment_id,kind,pm_plan_id,description) VALUES(%s,%s,'PM',%s,%s)",
                   (next_no(db, 'MO', 'maintenance_orders', 'mo_no'), p['equipment_id'], p['id'], p['name']))
        notify(db, 'PM_DUE', equipment_id=p['equipment_id'], body=p['name'])
        created += 1
    return created


class Tool(Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = 'TOOL'
    life_limit: Decimal | None = Field(default=None, gt=0)


class ToolStep(Strict):
    action: Literal['mount', 'unmount', 'reset', 'repair', 'retire']
    equipment_id: UUID | None = None


@router.get('/tools', tags=['maintenance'])
def tools(a: Actor):
    with connection() as db:
        return clean(all_rows(db, '''SELECT t.*, e.mes_code, CASE WHEN t.life_limit IS NULL THEN NULL ELSE t.used/t.life_limit END AS life_used
                                     FROM tools t LEFT JOIN equipments e ON e.id=t.equipment_id ORDER BY t.code'''))


@router.post('/tools', tags=['maintenance'])
def save_tool(body: Tool, a: Actor, key: Key):
    admin(a, 'equipments:manage')

    def run(db):
        t = one(db, '''INSERT INTO tools(code,name,kind,life_limit) VALUES(%s,%s,%s,%s)
                       ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, life_limit=EXCLUDED.life_limit RETURNING *''',
                (body.code, body.name, body.kind, body.life_limit))
        audit(db, a, 'tool-upsert', payload={'tool': t})
        return t
    return command(a, key, {'tool': body.model_dump()}, run)


@router.post('/tools/{code}/step', tags=['maintenance'])
def tool_step(code: str, body: ToolStep, a: Actor, key: Key):
    admin(a, 'equipment_status:set')

    def run(db):
        t = one(db, 'SELECT * FROM tools WHERE code=%s FOR UPDATE', (code,))
        require(t, 'NOT_FOUND', 'Tool not found')
        if body.action == 'mount':
            require(body.equipment_id and t['status'] in ('AVAILABLE', 'MOUNTED'), 'TOOL', 'Choose equipment; tool must be available')
            t = one(db, "UPDATE tools SET equipment_id=%s, status='MOUNTED' WHERE id=%s RETURNING *", (body.equipment_id, t['id']))
        else:
            status = {'unmount': 'AVAILABLE', 'reset': t['status'], 'repair': 'REPAIR', 'retire': 'RETIRED'}[body.action]
            t = one(db, "UPDATE tools SET status=%s, equipment_id=CASE WHEN %s IN ('unmount','repair','retire') THEN NULL ELSE equipment_id END, used=CASE WHEN %s='reset' THEN 0 ELSE used END WHERE id=%s RETURNING *",
                    (status, body.action, body.action, t['id']))
        audit(db, a, 'tool-' + body.action, payload={'tool': t})
        return t
    return command(a, key, {'tool_step': code, **body.model_dump(mode='json')}, run)


# ======================================================================
# 8. People: shifts, skills
# ======================================================================
class Shift(Strict):
    code: str = Field(min_length=1)
    name: str
    start_time: str = Field(pattern=r'^\d{2}:\d{2}$')
    end_time: str = Field(pattern=r'^\d{2}:\d{2}$')
    is_active: bool = True


@router.get('/shifts', tags=['people'])
def shifts(a: Actor):
    with connection() as db:
        return clean({'shifts': all_rows(db, 'SELECT * FROM shifts ORDER BY start_time'), 'current': current_shift(db)})


@router.post('/shifts', tags=['people'])
def save_shift(body: Shift, a: Actor, key: Key):
    admin(a, 'accounts:manage')

    def run(db):
        s = one(db, '''INSERT INTO shifts VALUES(%s,%s,%s,%s,%s) ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,
                       start_time=EXCLUDED.start_time, end_time=EXCLUDED.end_time, is_active=EXCLUDED.is_active RETURNING *''',
                (body.code, body.name, body.start_time, body.end_time, body.is_active))
        audit(db, a, 'shift-upsert', payload={'shift': s})
        return s
    return command(a, key, {'shift': body.model_dump()}, run)


class Skill(Strict):
    employee_no: str
    operation_code: str
    level: int = Field(default=1, ge=1, le=3)
    expires_at: date | None = None
    remove: bool = False


@router.get('/skills', tags=['people'])
def skills(a: Actor):
    with connection() as db:
        return clean(all_rows(db, '''SELECT s.*, a.employee_no, a.display_name, o.code AS operation_code, o.name AS operation_name,
            (s.expires_at IS NOT NULL AND s.expires_at<current_date) AS expired
            FROM operator_skills s JOIN accounts a ON a.id=s.account_id JOIN operations o ON o.id=s.operation_id ORDER BY a.employee_no, o.code'''))


@router.post('/skills', tags=['people'])
def save_skill(body: Skill, a: Actor, key: Key):
    admin(a, 'accounts:manage')

    def run(db):
        acc = one(db, 'SELECT id FROM accounts WHERE employee_no=%s', (body.employee_no,))
        op = one(db, 'SELECT id FROM operations WHERE code=%s', (body.operation_code,))
        require(acc and op, 'NOT_FOUND', 'Employee or operation not found')
        if body.remove:
            db.execute('DELETE FROM operator_skills WHERE account_id=%s AND operation_id=%s', (acc['id'], op['id']))
            s = {'removed': True}
        else:
            s = one(db, '''INSERT INTO operator_skills(account_id,operation_id,level,expires_at) VALUES(%s,%s,%s,%s)
                           ON CONFLICT(account_id,operation_id) DO UPDATE SET level=EXCLUDED.level, expires_at=EXCLUDED.expires_at, certified_at=current_date RETURNING *''',
                    (acc['id'], op['id'], body.level, body.expires_at))
        audit(db, a, 'skill-' + ('remove' if body.remove else 'grant'), payload={'employee_no': body.employee_no, 'operation': body.operation_code})
        return s
    return command(a, key, {'skill': body.model_dump(mode='json')}, run)


# ======================================================================
# 9. Documents / SOP
# ======================================================================
MAX_DOC = 10 * 1024 * 1024


@router.get('/documents', tags=['documents'])
def documents(a: Actor):
    with connection() as db:
        return clean(all_rows(db, '''SELECT d.id, d.doc_no, d.version, d.title, d.status, d.file_name, d.content_type, length(d.content) AS size,
            o.code AS operation_code, d.item_no, d.created_at, d.released_at FROM documents d LEFT JOIN operations o ON o.id=d.operation_id
            ORDER BY d.doc_no, d.created_at DESC'''))


@router.post('/documents', tags=['documents'])
async def upload_document(a: Actor, key: Key, doc_no: Annotated[str, Form(min_length=1)], version: Annotated[str, Form(min_length=1)],
                          title: Annotated[str, Form(min_length=1)], file: Annotated[UploadFile, File()],
                          operation_code: Annotated[str | None, Form()] = None, item_no: Annotated[str | None, Form()] = None):
    admin(a, 'operations:manage')
    content = await file.read(MAX_DOC + 1)
    require(0 < len(content) <= MAX_DOC, 'DOCUMENT', 'File must be 1 byte to 10 MB')
    meta = {'doc_no': doc_no, 'version': version, 'title': title, 'operation_code': operation_code, 'item_no': item_no,
            'file': file.filename, 'sha': digest(content.hex()[:2048])}

    def run(db):
        op = one(db, 'SELECT id FROM operations WHERE code=%s', (operation_code,)) if operation_code else None
        require(op or not operation_code, 'OPERATION', 'Unknown operation')
        d = one(db, '''INSERT INTO documents(doc_no,version,title,operation_id,item_no,file_name,content_type,content,uploaded_by)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,doc_no,version,title,status''',
                (doc_no, version, title, op and op['id'], item_no or None, file.filename or 'file', file.content_type or 'application/octet-stream', content, a['id']))
        audit(db, a, 'document-upload', payload={'document': d})
        return d
    return command(a, key, meta, run)


@router.post('/documents/{doc_id}/release', tags=['documents'])
def release_document(doc_id: int, a: Actor, key: Key):
    admin(a, 'operations:manage')

    def run(db):
        d = one(db, 'SELECT id,doc_no,status FROM documents WHERE id=%s FOR UPDATE', (doc_id,))
        require(d and d['status'] == 'DRAFT', 'DOCUMENT', 'Only drafts can be released')
        db.execute("UPDATE documents SET status='OBSOLETE' WHERE doc_no=%s AND status='RELEASED'", (d['doc_no'],))
        d = one(db, "UPDATE documents SET status='RELEASED', released_at=now() WHERE id=%s RETURNING id,doc_no,version,status", (doc_id,))
        audit(db, a, 'document-release', payload={'document': d})
        return d
    return command(a, key, {'release_doc': doc_id}, run)


@router.get('/documents/{doc_id}/file', tags=['documents'])
def document_file(doc_id: int, a: Actor):
    with connection() as db:
        d = one(db, 'SELECT file_name, content_type, content, status FROM documents WHERE id=%s', (doc_id,))
        require(d and (d['status'] == 'RELEASED' or a['mode'] == 'admin'), 'NOT_FOUND', 'Document not available')
        safe = ''.join(ch for ch in d['file_name'] if ch.isalnum() or ch in '._-') or 'file'
        return Response(bytes(d['content']), media_type=d['content_type'], headers={'Content-Disposition': f'inline; filename="{safe}"'})


# ======================================================================
# 10. Connected devices: tablets, machines (telemetry), ERP
# ======================================================================
class Device(Strict):
    name: str = Field(min_length=1)
    kind: Literal['TABLET', 'MACHINE', 'ERP']
    equipment_mes_code: str | None = None


@router.get('/devices', tags=['integration'])
def devices(a: Actor):
    admin(a, 'mes_sync:manage')
    with connection() as db:
        return clean(all_rows(db, 'SELECT d.id,d.name,d.kind,d.is_active,d.created_at,d.last_seen_at,e.mes_code FROM devices d LEFT JOIN equipments e ON e.id=d.equipment_id ORDER BY d.kind,d.name'))


@router.post('/devices', tags=['integration'])
def create_device(body: Device, a: Actor, key: Key):
    """The token is shown once; only its hash is stored. Replay of the same key returns no token."""
    admin(a, 'mes_sync:manage')
    token = secrets.token_urlsafe(24)

    def run(db):
        e = one(db, 'SELECT id FROM equipments WHERE mes_code=%s', (body.equipment_mes_code,)) if body.equipment_mes_code else None
        require(body.kind != 'MACHINE' or e, 'DEVICE', 'Machine devices must be bound to equipment')
        d = one(db, 'INSERT INTO devices(name,kind,token_hash,equipment_id) VALUES(%s,%s,%s,%s) RETURNING id,name,kind', (body.name, body.kind, digest(token), e and e['id']))
        audit(db, a, 'device-create', payload={'device': d})
        return d
    with connection() as db:
        replay = one(db, 'SELECT 1 FROM command_receipts WHERE actor_id=%s AND key=%s', (a['id'], key))
        result = run_command(db, a, key, {'device': body.model_dump()}, lambda: run(db))
    return result if replay else {**result, 'token': token}


@router.post('/devices/{device_id}/revoke', tags=['integration'])
def revoke_device(device_id: int, a: Actor, key: Key):
    admin(a, 'mes_sync:manage')

    def run(db):
        d = one(db, 'UPDATE devices SET is_active=false WHERE id=%s RETURNING id,name,kind', (device_id,))
        require(d, 'NOT_FOUND', 'Device not found')
        audit(db, a, 'device-revoke', payload={'device': d})
        return d
    return command(a, key, {'revoke_device': device_id}, run)


class Telemetry(Strict):
    state: Literal['RUN', 'IDLE', 'DOWN', 'OFF'] | None = None
    counter: Decimal | None = None
    good: Decimal | None = None
    scrap: Decimal | None = None
    params: dict = Field(default_factory=dict)


@router.post('/machine/telemetry', tags=['integration'])
def telemetry(body: Telemetry, x_device_token: Annotated[str | None, Header()] = None):
    """PLC/gateway push (HTTP). MQTT/OPC-UA gateways post here. A RUN→DOWN edge raises 設備異常 for a human to confirm."""
    with connection() as db:
        d = device(db, x_device_token, 'MACHINE')
        prev = one(db, 'SELECT state FROM machine_events WHERE equipment_id=%s AND state IS NOT NULL ORDER BY at DESC, id DESC LIMIT 1', (d['equipment_id'],))
        ev = one(db, 'INSERT INTO machine_events(equipment_id,state,counter,good,scrap,params) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id',
                 (d['equipment_id'], body.state, body.counter, body.good, body.scrap, jb(body.params)))
        if body.state == 'DOWN' and (not prev or prev['state'] != 'DOWN'):
            e = one(db, 'SELECT mes_code FROM equipments WHERE id=%s', (d['equipment_id'],))
            notify(db, 'EQUIPMENT_ABNORMAL', equipment_id=d['equipment_id'], body=f"{e['mes_code']} · machine signal DOWN")
        return {'id': ev['id']}


@router.get('/equipments/{equipment_id}/telemetry', tags=['integration'])
def equipment_telemetry(equipment_id: UUID, a: Actor):
    with connection() as db:
        return clean(all_rows(db, 'SELECT * FROM machine_events WHERE equipment_id=%s ORDER BY at DESC, id DESC LIMIT 50', (equipment_id,)))


# ---------- ERP inbound ----------
class ErpWorkOrder(WorkOrder):
    external_id: str = Field(min_length=1)


class ErpBom(Bom):
    external_id: str = Field(min_length=1)
    materials: list[Material] = Field(default_factory=list)


class ErpLot(Receive):
    external_id: str = Field(min_length=1)


ERP_ACTOR = {'id': None}


def erp_apply(kind, body, fn, token):
    with connection() as db:
        device(db, token, 'ERP')
        lock(db)
        seen = one(db, 'SELECT status, error FROM erp_inbound_log WHERE kind=%s AND external_id=%s', (kind, body.external_id))
        if seen:
            return {'status': seen['status'], 'error': seen['error'], 'duplicate': True}
        payload = body.model_dump(mode='json')
        try:
            with db.transaction():
                result = fn(db, payload)
            status, error = 'APPLIED', None
        except Exception as exc:
            result, status, error = None, 'REJECTED', str(exc)[:500]
        db.execute('INSERT INTO erp_inbound_log(kind,external_id,payload,status,error) VALUES(%s,%s,%s,%s,%s)', (kind, body.external_id, jb(payload), status, error))
        return clean({'status': status, 'error': error, 'result': result})


@router.post('/erp/work-orders', tags=['integration'])
def erp_work_order(body: ErpWorkOrder, x_device_token: Annotated[str | None, Header()] = None):
    return erp_apply('work-order', body, lambda db, p: upsert_work_order(db, ERP_ACTOR, {k: v for k, v in p.items() if k != 'external_id'}, 'ERP', body.external_id), x_device_token)


@router.post('/erp/boms', tags=['integration'])
def erp_bom(body: ErpBom, x_device_token: Annotated[str | None, Header()] = None):
    def fn(db, p):
        for m in p['materials']:
            upsert_material(db, ERP_ACTOR, m)
        return save_bom_rows(db, ERP_ACTOR, p)
    return erp_apply('bom', body, fn, x_device_token)


@router.post('/erp/material-lots', tags=['integration'])
def erp_lot(body: ErpLot, x_device_token: Annotated[str | None, Header()] = None):
    return erp_apply('material-lot', body, lambda db, p: receive_lot(db, ERP_ACTOR, p), x_device_token)


@router.get('/erp/reconcile', tags=['integration'])
def reconcile(a: Actor):
    admin(a, 'mes_sync:manage')
    with connection() as db:
        return clean({'outbox': all_rows(db, 'SELECT status, count(*) AS n, min(created_at) AS oldest FROM sync_outbox GROUP BY status'),
                      'failed': all_rows(db, "SELECT id, event_type, attempts, last_error, created_at FROM sync_outbox WHERE status='FAILED' ORDER BY id LIMIT 50"),
                      'inbound': all_rows(db, 'SELECT kind, status, count(*) AS n FROM erp_inbound_log GROUP BY kind, status'),
                      'rejected': all_rows(db, "SELECT kind, external_id, error, at FROM erp_inbound_log WHERE status='REJECTED' ORDER BY at DESC LIMIT 50"),
                      'format': setting(db, 'erp_format', 'generic')})


def format_outbox(db, event):
    """Outbound payload. 'wip' mirrors the CheckIn/CheckOut write-back layout (one transaction per work record,
    ExternalTransactionID = idempotency key); everything else is sent in the generic envelope."""
    if setting(db, 'erp_format', 'generic') != 'wip':
        return clean(event)
    p = event['payload']
    rid = (p.get('detail') or {}).get('record_id')
    if event['event_type'] not in ('check-in', 'check-out', 'backfill') or not rid:
        return clean(event)
    r = one(db, '''SELECT w.*, o.code AS step, e.mes_code, a.employee_no, c.card_no, c.work_order_no, c.item_no,
                   (SELECT COALESCE(sum(qty),0) FROM work_record_defects d WHERE d.work_record_id=w.id) AS defect_qty
                   FROM work_records w JOIN operations o ON o.id=w.operation_id LEFT JOIN equipments e ON e.id=w.equipment_id
                   JOIN accounts a ON a.id=w.operator_id JOIN run_cards c ON c.id=w.run_card_id WHERE w.id=%s''', (rid,))
    if not r:
        return clean(event)
    out = r['ended_at'] is not None and event['event_type'] != 'check-in'
    return clean({'ExternalTransactionID': event['idempotency_key'], 'TransactionType': 'CHECKOUT' if out else 'CHECKIN',
                  'LotSerial': r['card_no'], 'WorkOrder': r['work_order_no'], 'PartNo': r['item_no'], 'StepCode': r['step'],
                  'EquipmentCode': r['mes_code'], 'Operator': r['employee_no'], 'InQty': r['input_qty'],
                  'GoodQty': r['output_qty'] if out else None, 'DefectQty': r['defect_qty'] if out else None,
                  'TxnTime': (r['ended_at'] if out else r['started_at'])})


# ======================================================================
# 11. Labels (ZPL) and settings
# ======================================================================
def zpl(title, code, lines):
    body = ''.join(f'^FO30,{140 + i * 40}^A0N,30,30^FD{str(t)[:40]}^FS' for i, t in enumerate(lines))
    return f'^XA^CI28^FO30,20^A0N,40,40^FD{title}^FS^FO30,70^BQN,2,5^FDQA,{code}^FS^FO250,80^A0N,36,36^FD{code}^FS{body}^XZ'


@router.get('/labels/card/{card_id}', tags=['labels'], response_class=PlainTextResponse)
def card_label(card_id: UUID, a: Actor):
    with connection() as db:
        c = one(db, 'SELECT * FROM run_cards WHERE id=%s', (card_id,))
        require(c, 'NOT_FOUND', 'Card not found')
        return zpl('RunCard', 'RC:' + c['card_no'], [f"WO {c['work_order_no'] or '-'}", f"{c['item_no'] or ''} {c['item_name'] or ''}", f"QTY {c['current_qty']} {c['unit']}"])


@router.get('/labels/lot/{lot_no}', tags=['labels'], response_class=PlainTextResponse)
def lot_label(lot_no: str, a: Actor):
    with connection() as db:
        l = one(db, 'SELECT l.*, m.code, m.name FROM material_lots l JOIN materials m ON m.id=l.material_id WHERE lot_no=%s', (lot_no,))
        require(l, 'NOT_FOUND', 'Lot not found')
        return zpl('Material lot', l['lot_no'], [f"{l['code']} {l['name']}", f"QTY {l['remaining_qty']}", f"EXP {l['expires_at'] or '-'}"])


SETTING_KEYS = ('fai_enforced', 'skill_enforced', 'pm_block_overdue', 'material_enforced', 'erp_format')


class Settings(Strict):
    fai_enforced: bool | None = None
    skill_enforced: bool | None = None
    pm_block_overdue: bool | None = None
    material_enforced: bool | None = None
    erp_format: Literal['generic', 'wip'] | None = None


@router.get('/mes/settings', tags=['settings'])
def get_settings(a: Actor):
    with connection() as db:
        return clean({k: setting(db, k) for k in SETTING_KEYS})


@router.put('/mes/settings', tags=['settings'])
def put_settings(body: Settings, a: Actor, key: Key):
    admin(a, 'mes_sync:manage')

    def run(db):
        for k, v in body.model_dump(exclude_none=True).items():
            db.execute('INSERT INTO app_settings VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value', (k, jb(v)))
        audit(db, a, 'mes-settings', payload=body.model_dump(exclude_none=True))
        return {k: setting(db, k) for k in SETTING_KEYS}
    return command(a, key, {'settings': body.model_dump()}, run)
