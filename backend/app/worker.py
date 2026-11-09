"""Durable at-least-once delivery. ERP must deduplicate the Idempotency-Key."""
import json
import logging
import os
import smtplib
import time
import urllib.request
from email.message import EmailMessage
from .db import connection, one, all_rows, clean
from .service import deliver_notification
from .mes import format_outbox, pm_tick

log=logging.getLogger('mes.worker')


def tick():
    # A separate transaction per delivery. Locks prevent two workers sending the same row
    # concurrently; a crash after external acceptance can still replay the stable key.
    with connection() as db:
        event=one(db,"SELECT * FROM sync_outbox WHERE status<>'SENT' AND next_attempt_at<=now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
        endpoint=os.environ.get('ERP_WEBHOOK_URL')
        if event and endpoint:
            try:
                headers={'Content-Type':'application/json','Idempotency-Key':event['idempotency_key']}
                if os.environ.get('ERP_WEBHOOK_TOKEN'): headers['Authorization']='Bearer '+os.environ['ERP_WEBHOOK_TOKEN']
                req=urllib.request.Request(endpoint,data=json.dumps(format_outbox(db,event)).encode(),headers=headers,method='POST')
                with urllib.request.urlopen(req,timeout=10) as response:
                    if not 200<=response.status<300: raise RuntimeError('ERP rejected delivery')
                db.execute("UPDATE sync_outbox SET status='SENT',sent_at=now(),attempts=attempts+1,last_error=NULL WHERE id=%s",(event['id'],))
            except Exception as exc:
                db.execute("UPDATE sync_outbox SET status='FAILED',attempts=attempts+1,last_error=%s,next_attempt_at=now()+(%s * interval '1 second') WHERE id=%s",(type(exc).__name__,min(3600,2**min(event['attempts']+1,12)),event['id']))
    with connection() as db:
        pm_tick(db)
    with connection() as db:
        jobs=all_rows(db,'SELECT j.*,r.repeat_enabled,r.repeat_every_min,r.repeat_max,r.escalate_enabled,r.escalate_after_min FROM notification_jobs j JOIN notification_rules r ON r.event_code=j.event_code WHERE j.resolved_at IS NULL AND (j.next_at<=now() OR (r.escalate_enabled AND NOT j.escalated AND j.created_at+(r.escalate_after_min * interval \'1 minute\')<=now())) ORDER BY j.id FOR UPDATE OF j SKIP LOCKED LIMIT 20')
        now=one(db,'SELECT now() AS at')['at']
        for job in jobs:
            if job['repeat_enabled'] and job['repeat_count']<job['repeat_max'] and job['next_at']<=now:
                deliver_notification(db,job,False)
                db.execute("UPDATE notification_jobs SET repeat_count=repeat_count+1,next_at=now()+(%s * interval '1 minute') WHERE id=%s",(job['repeat_every_min'],job['id']))
            elif job['next_at']<=now:
                db.execute("UPDATE notification_jobs SET next_at='9999-12-31T00:00:00Z' WHERE id=%s",(job['id'],))
            if job['escalate_enabled'] and not job['escalated'] and (now-job['created_at']).total_seconds()>=job['escalate_after_min']*60:
                deliver_notification(db,job,True)
                db.execute('UPDATE notification_jobs SET escalated=true WHERE id=%s',(job['id'],))
    if not os.environ.get('SMTP_HOST'): return
    with connection() as db:
        mail=one(db,'SELECT d.*,a.email FROM email_deliveries d JOIN accounts a ON a.id=d.account_id WHERE d.sent_at IS NULL AND d.next_at<=now() ORDER BY d.id FOR UPDATE OF d SKIP LOCKED LIMIT 1')
        if not mail: return
        try:
            if not mail['email']: raise ValueError('Recipient email is not configured')
            msg=EmailMessage(); msg['From']=os.environ['SMTP_FROM']; msg['To']=mail['email']; msg['Subject']=mail['title']; msg.set_content(mail['body'])
            with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.environ.get('SMTP_PORT','587')),timeout=10) as smtp:
                if os.environ.get('SMTP_STARTTLS','true')=='true': smtp.starttls()
                if os.environ.get('SMTP_USER'): smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD'])
                smtp.send_message(msg)
            db.execute('UPDATE email_deliveries SET sent_at=now(),attempts=attempts+1,last_error=NULL WHERE id=%s',(mail['id'],))
        except Exception as exc:
            db.execute("UPDATE email_deliveries SET attempts=attempts+1,last_error=%s,next_at=now()+(%s * interval '1 second') WHERE id=%s",(type(exc).__name__,min(3600,2**min(mail['attempts']+1,12)),mail['id']))

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    while True:
        try: tick()
        except Exception: log.exception('Worker tick failed')
        time.sleep(2)
