"""Pure RunCard decisions. No clock, UUID generation, I/O or database calls here."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

QTY = ('current_qty', 'scrap_qty', 'rework_qty', 'split_qty', 'defect_qty', 'returned_qty', 'transferred_qty')
TERMINAL = {'COMPLETED', 'SCRAPPED', 'CANCELLED', 'TRANSFERRED'}
ADMIN_ACTIONS = {'split', 'rework', 'backfill', 'revoke', 'release', 'cancel', 'scrap', 'edit'}

class RuleError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def require(condition, code, message):
    if not condition:
        raise RuleError(code, message)

def number(value):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuleError('QUANTITY', 'Invalid number')
    require(n.is_finite(), 'QUANTITY', 'Number must be finite')
    return n

def invariant(card):
    values = [number(card.get(k, 0)) for k in QTY]
    require(all(x >= 0 for x in values), 'BR-01', 'Quantities cannot be negative')
    require(number(card['original_qty']) > 0 and sum(values) == number(card['original_qty']), 'BR-01', 'Quantity balance is invalid')
    require((card['route_free'] and card.get('route_version_id') is None and card.get('current_node_id') is None) or
            (not card['route_free'] and card.get('route_version_id') is not None and card.get('current_node_id') is not None), 'BR-11', 'Route and node are required only for routed cards')

def equipment_transition(equipment, reason, ctx):
    require(ctx.get('actor_id') and (ctx['mode'] != 'operator' or ctx.get('employee_no')), 'BR-22', 'Employee identity required')
    require(equipment['is_active'], 'BR-18', 'Equipment is inactive')
    require(not ctx.get('whitelist') or reason['code'] in ctx['whitelist'], 'EQUIPMENT_REASON', 'Reason is not permitted on this equipment')
    if ctx['mode'] == 'operator' and ctx.get('occupied'):
        require(reason['code'] in {'UP', 'US_2', 'US_5'} and reason['state'] == 'ENABLED', 'BR-20', 'End active work before changing equipment state')
    result = deepcopy(equipment)
    result.update(state=reason['state'], reason_code=reason['code'])
    return {'equipment': result, 'effects': [{'type': 'equipment_history', 'close_previous': True}, {'type': 'audit'}, {'type': 'outbox'}]}

def transition(card, action, ctx):
    c = deepcopy(card)
    invariant(c)
    kind = action['type']
    original_kind = kind
    effects = []
    require(ctx.get('actor_id') and (ctx['mode'] != 'operator' or ctx.get('employee_no')), 'BR-22', 'Employee identity required')
    require(action.get('version') == c['version'], 'VERSION_CONFLICT', 'Card changed; refresh and review')
    if kind in ADMIN_ACTIONS:
        require(ctx['mode'] == 'admin', 'FORBIDDEN', 'Administrator action required')
    reason = str(action.get('reason') or '').strip()
    if kind in {'cancel', 'revoke', 'backfill', 'abnormal-end', 'temporary'} or (kind == 'release' and ctx.get('release_reason_required', True)):
        require(bool(reason), 'BR-24' if kind in {'cancel', 'revoke', 'backfill', 'release'} else 'REASON_REQUIRED', 'Reason is required')
    if kind == 'backfill':
        require(action.get('actual_operator_id'), 'BR-22', 'Actual operator is required')
        kind = 'check-in' if c['status'] == 'WAITING' else 'check-out'
    require(c['status'] not in TERMINAL, 'STATE', 'This card is read-only')
    open_record = ctx.get('open_record')

    def state(*allowed):
        require(c['status'] in allowed, 'STATE', 'Action is not available in this state')
    def child(source, qty, status='WAITING', node=None):
        x = deepcopy(c)
        x.update({k: Decimal(0) for k in QTY})
        x.update(id=None, card_no=None, original_qty=qty, current_qty=qty, status=status, source_type=source,
                 parent_card_id=c['id'], data_source='SYSTEM', version=1, abnormal_counts={}, current_node_id=c['current_node_id'] if node is None else node)
        effects.append({'type': 'child', 'card': x})
    def idle():
        if open_record and open_record.get('equipment_id'):
            effects.append({'type': 'equipment', 'id': open_record['equipment_id'], 'reason_code': 'US_4'})
    def take(qty, bucket):
        qty = number(qty)
        require(0 < qty <= number(c['current_qty']), 'QUANTITY', 'Quantity must be positive and no greater than current quantity')
        c['current_qty'] = number(c['current_qty']) - qty
        c[bucket] = number(c.get(bucket, 0)) + qty
        return qty

    if kind in {'check-in', 'temporary', 'skip'}:
        state('WAITING')
        require(number(c['current_qty']) > 0, 'QUANTITY', 'Card has no quantity to process')
        require(open_record is None, 'BR-05', 'Card already has an open work record')
        operation = ctx.get('operation')
        require(operation and operation['is_active'], 'OPERATION', 'Active operation required')
        if kind == 'skip':
            require(ctx.get('authorized_by') and ctx.get('skip_authorized'), 'BR-10', 'One-time supervisor authorization required')
            require(not c['route_free'] and ctx.get('target_node_valid'), 'BR-10', 'Skip target must be a later node of the fixed route version')
            c['current_node_id'] = action['target_node_id']
        if operation['requires_equipment']:
            e = ctx.get('equipment')
            require(e and operation['id'] in ctx.get('equipment_operations', []), 'BR-17', 'Equipment cannot perform this operation')
            require(e['is_active'] and e['state'] == 'ENABLED', 'BR-18', 'Equipment must be enabled')
            effects.append({'type': 'equipment', 'id': e['id'], 'reason_code': 'UP'})
        else:
            require(action.get('operation_code') == operation['code'], 'OPERATION_SCAN', 'Scan the operation code')
        effects.append({'type': 'open_record', 'input_qty': c['current_qty'], 'operation_id': operation['id'],
                        'is_temporary': kind == 'temporary', 'is_skip': kind == 'skip', 'node_id': c['current_node_id']})
        c['status'] = 'IN_PROGRESS'
    elif kind == 'check-out':
        state('IN_PROGRESS')
        require(open_record, 'BR-05', 'Open work record required')
        output = number(action.get('output_qty'))
        input_qty = number(open_record['input_qty'])
        require(0 <= output <= input_qty, 'BR-03', 'Output must be between zero and input')
        defects = action.get('defects', [])
        ids = [d['defect_code_id'] for d in defects]
        require(len(set(ids)) == len(ids), 'BR-03', 'Duplicate defect codes')
        require(all(d['defect_code_id'] in ctx.get('defect_code_ids', []) and number(d['qty']) > 0 for d in defects), 'BR-03', 'Use positive quantities and allowed defect codes')
        missing = input_qty - output
        require(sum((number(d['qty']) for d in defects), Decimal(0)) == missing, 'BR-03', 'Allocate all missing quantity to defects')
        qc = action.get('qc', [])
        values = {v['qc_item_id']: number(v['value']) for v in qc}
        items = ctx.get('qc_items', [])
        require(len(values) == len(qc) and set(values) == {i['id'] for i in items}, 'QC_REQUIRED', 'Complete every configured QC item exactly once')
        checks = [{'qc_item_id': i['id'], 'value': values[i['id']], 'in_spec':
                   (i['lower_limit'] is None or values[i['id']] >= number(i['lower_limit'])) and
                   (i['upper_limit'] is None or values[i['id']] <= number(i['upper_limit']))} for i in items]
        failed = any(not i['in_spec'] for i in checks)
        if missing:
            child('DEFECT', missing, 'DEFECT_HOLD')
            c['defect_qty'] = number(c['defect_qty']) + missing
        c['current_qty'] = output
        if failed:
            c['status'] = 'QUALITY_HOLD'
        elif open_record['is_temporary']:
            c['status'] = 'WAITING'
        elif c['route_free']:
            c['status'] = 'COMPLETED' if action.get('finish_route_free') else 'WAITING'
        elif ctx.get('next_node_id') is not None:
            c.update(status='WAITING', current_node_id=ctx['next_node_id'])
        else:
            c['status'] = 'COMPLETED'
        effects.append({'type': 'close_record', 'output_qty': output, 'defects': defects, 'qc': checks,
                        'quality_result': 'FAIL' if failed else 'PASS', 'end_type': 'BACKFILL' if original_kind == 'backfill' else 'NORMAL'})
        idle()
    elif kind == 'abnormal-end':
        state('IN_PROGRESS')
        require(open_record, 'BR-05', 'Open work record required')
        require(bool(reason), 'BR-14', 'Reason required')
        require(action.get('disposition') in {'keep', 'mark'}, 'BR-14', 'Choose keep or mark')
        keep = action['disposition'] == 'keep'
        c['status'] = 'WAITING' if keep else 'ABNORMAL'
        if keep:
            counts = dict(c.get('abnormal_counts') or {})
            node = str(c.get('current_node_id') or open_record['operation_id'])
            counts[node] = counts.get(node, 0) + 1
            c['abnormal_counts'] = counts
        effects.append({'type': 'close_record', 'output_qty': c['current_qty'], 'defects': [], 'qc': [],
                        'quality_result': None, 'end_type': 'ABNORMAL_KEEP' if keep else 'ABNORMAL_MARK'})
        idle()
    elif kind == 'split':
        state('WAITING')
        qty = number(action.get('qty'))
        require(0 < qty < number(c['current_qty']), 'BR-04', 'Split must leave positive quantity on the parent')
        child('SPLIT', qty)
        take(qty, 'split_qty')
    elif kind == 'rework':
        state('WAITING', 'QUALITY_HOLD', 'ABNORMAL', 'DEFECT_HOLD')
        require(c['route_free'] or ctx.get('target_node_valid'), 'ROUTE', 'Choose a node from the same route version')
        qty = number(action.get('qty'))
        child('REWORK', qty, node=action.get('target_node_id'))
        take(qty, 'rework_qty')
        if not c['current_qty']:
            c['status'] = 'TRANSFERRED'
    elif kind == 'scrap':
        state('QUALITY_HOLD', 'ABNORMAL', 'DEFECT_HOLD')
        require(bool(reason), 'REASON_REQUIRED', 'Scrap reason required')
        take(action.get('qty'), 'scrap_qty')
        if not c['current_qty']:
            c['status'] = 'SCRAPPED'
    elif kind == 'release':
        state('QUALITY_HOLD', 'ABNORMAL')
        require(number(c['current_qty']) > 0, 'QUANTITY', 'No remaining quantity to release')
        c['status'] = 'WAITING'
    elif kind == 'cancel':
        state('WAITING')
        disposition = action.get('disposition')
        require(disposition in {'scrap', 'return', 'transfer'}, 'OPT-04', 'Cancellation disposition required')
        qty = number(c['current_qty'])
        require(qty > 0, 'OPT-04', 'No remaining quantity to cancel')
        if disposition == 'transfer':
            child('NORMAL', qty)
        take(qty, {'scrap': 'scrap_qty', 'return': 'returned_qty', 'transfer': 'transferred_qty'}[disposition])
        c.update(status='CANCELLED', cancel_reason=reason)
        effects.append({'type': 'movement', 'disposition': disposition, 'qty': qty})
    elif kind == 'revoke':
        state('WAITING', 'IN_PROGRESS', 'QUALITY_HOLD', 'ABNORMAL')
        last = ctx.get('last_record')
        require(last and last.get('before_card'), 'BR-13', 'No work record to revoke')
        require(not ctx.get('subsequent_mutation'), 'BR-13', 'Reversal would overwrite later quantity or route changes')
        require(not ctx.get('processed_defects'), 'BR-13', 'A generated defect card has already been processed')
        before = last['before_card']
        for key in (*QTY, 'current_node_id', 'status', 'abnormal_counts'):
            c[key] = deepcopy(before.get(key, {} if key == 'abnormal_counts' else 0))
        effects.append({'type': 'revoke_record', 'record_id': last['id']})
        if last.get('equipment_id'):
            effects.append({'type': 'equipment', 'id': last['equipment_id'], 'reason_code': 'US_4'})
    elif kind == 'edit':
        state('WAITING')
        fields = action.get('fields') or {}
        require(set(fields) <= {'work_order_no', 'item_no', 'item_name', 'unit'}, 'BR-11', 'Route-free and fixed route are immutable')
        require('unit' not in fields or bool(str(fields['unit']).strip()), 'UNIT', 'Unit is required')
        c.update(fields)
    else:
        raise RuleError('ACTION', 'Unknown action')
    c['version'] += 1
    invariant(c)
    effects.extend([{'type': 'audit', 'action': original_kind, 'from_status': card['status'], 'to_status': c['status'], 'reason': reason},
                    {'type': 'outbox', 'action': original_kind}])
    return {'card': c, 'effects': effects}
