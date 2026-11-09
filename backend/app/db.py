import os
import json
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def serialize(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(type(value).__name__)


def clean(value):
    return json.loads(json.dumps(value, default=serialize))


def jb(value):
    return Jsonb(clean(value))


@contextmanager
def connection():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, prepare_threshold=None) as db:
        yield db


def one(db, query, args=()):
    return db.execute(query, args).fetchone()


def all_rows(db, query, args=()):
    return db.execute(query, args).fetchall()


def lock(db):
    db.execute('SELECT pg_advisory_xact_lock(78012511)')
