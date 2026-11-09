import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from .db import one, all_rows
from .domain import require

PERMISSIONS = ['backend:access','audit:view_system','accounts:manage','operations:manage','operations:update',
 'defect_codes:manage','equipments:manage','process_routes:manage','run_cards:create','run_cards:manage',
 'notifications:manage','mes_sync:manage','equipment_status:set','quality:judge','rework:create',
 'run_cards:revoke_work','work_records:abnormal_end','work_records:backfill','work_records:skip_node']
ACTION_PERMISSION = {'split':'run_cards:manage','edit':'run_cards:manage','cancel':'run_cards:manage',
 'rework':'rework:create','scrap':'quality:judge','release':'quality:judge','revoke':'run_cards:revoke_work',
 'backfill':'work_records:backfill','abnormal-end':'work_records:abnormal_end','report-breakdown':'work_records:abnormal_end'}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password):
    require(len(password) >= 8, 'PASSWORD', 'Password must contain at least eight characters')
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
    return f'pbkdf2_sha256$310000${salt}${value}'


def verify(password, encoded):
    try:
        _, iterations, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False


def account_context(db, account_id, mode):
    a = one(db, 'SELECT a.id,a.username,a.display_name,a.employee_no,a.is_active,r.code AS role FROM accounts a JOIN roles r ON r.id=a.role_id WHERE a.id=%s', (account_id,))
    require(a and a['is_active'], 'UNAUTHORIZED', 'Account is inactive')
    a['permissions'] = [p['permission_code'] for p in all_rows(db, 'SELECT rp.permission_code FROM role_permissions rp JOIN accounts a ON a.role_id=rp.role_id WHERE a.id=%s', (account_id,))]
    a.update(actor_id=a['id'], mode=mode)
    return a


def allowed(actor, permission):
    require(permission in actor['permissions'], 'FORBIDDEN', f'Permission required: {permission}')


def session(db, account_id, mode, reuse=True):
    actor = account_context(db, account_id, mode)
    if mode == 'admin':
        allowed(actor, 'backend:access')
    else:
        require(actor['employee_no'], 'BR-22', 'Employee number required')
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=5 if mode == 'operator' else 480)
    db.execute('INSERT INTO auth_sessions(token_hash,account_id,mode,expires_at,single_use) VALUES(%s,%s,%s,%s,%s)',
               (digest(token), account_id, mode, expires, mode == 'operator' and not reuse))
    return {'token': token, 'expires_at': expires, 'actor': actor}


def authenticate(db, token):
    s = one(db, 'SELECT * FROM auth_sessions WHERE token_hash=%s AND expires_at>now()', (digest(token),))
    require(s, 'UNAUTHORIZED', 'Session expired; identify yourself again')
    a = account_context(db, s['account_id'], s['mode'])
    a.update(token_hash=s['token_hash'], single_use=s['single_use'])
    return a


def consume_session(db, actor):
    # Recheck under the command lock: two concurrent writes cannot reuse a one-shot session.
    s = one(db, 'SELECT * FROM auth_sessions WHERE token_hash=%s AND expires_at>now()', (actor['token_hash'],))
    require(s, 'UNAUTHORIZED', 'Session expired; identify yourself again')
    if s['single_use']:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (s['token_hash'],))
