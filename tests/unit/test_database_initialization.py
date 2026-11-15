"""Parallel workers must not race on schema creation or additive migrations."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from jobagent import db


@pytest.mark.parametrize('existing_schema',[False,True])
def test_parallel_database_open_migrates_once(tmp_path,existing_schema):
    path=tmp_path/'jobs.sqlite'
    if existing_schema:
        with sqlite3.connect(path) as connection:
            connection.executescript(db.SCHEMA)
    start=threading.Barrier(8,timeout=10)

    def open_worker(_):
        start.wait()
        connection=db.connect(path)
        try:
            return (
                {r[1] for r in connection.execute('PRAGMA table_info(applications)')},
                connection.execute('PRAGMA foreign_keys').fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='relationships_v1'").fetchone()[0],
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(open_worker,range(8)))
    assert all('ats_scores' in columns and foreign_keys==1 and versions==1 for columns,foreign_keys,versions in results)


def test_initialized_database_open_does_not_require_a_write_lock(tmp_path,monkeypatch):
    path=tmp_path/'jobs.sqlite'
    writer=db.connect(path)
    writer.execute("INSERT INTO kv(key,value) VALUES ('uncommitted','value')")
    real_connect=sqlite3.connect
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:real_connect(*a,**(kw|{'timeout':0.1})))
    try:
        reader=db.connect(path)
        try:
            assert reader.execute("SELECT value FROM kv WHERE key='uncommitted'").fetchone() is None
        finally:
            reader.close()
    finally:
        writer.rollback()
        writer.close()
