"""Transactional local configurations and autosaved draft history."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time


class LocalStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS configs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS config_versions(id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS drafts(client TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS draft_versions(client TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(client,revision));
                CREATE TABLE IF NOT EXISTS deleted_configs(id TEXT PRIMARY KEY, deleted REAL NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def save_config(self,identifier,payload):
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False);now=time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM deleted_configs WHERE id=?',(identifier,)).fetchone():
                raise ValueError('配置已在回收站中，请恢复后编辑，或另存为新配置')
            old=db.execute('SELECT payload FROM configs WHERE id=?',(identifier,)).fetchone()
            if old and old['payload']==encoded:
                return db.execute('SELECT MAX(revision) FROM config_versions WHERE id=?',(identifier,)).fetchone()[0]
            revision=db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM config_versions WHERE id=?',(identifier,)).fetchone()[0]
            db.execute('INSERT INTO config_versions VALUES(?,?,?,?)',(identifier,revision,encoded,now))
            db.execute('INSERT INTO configs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',
                       (identifier,encoded,now))
            db.execute('DELETE FROM config_versions WHERE id=? AND revision<=?',(identifier,revision-20))
            return revision

    def config(self,identifier):
        with self.connection() as db:row=db.execute('SELECT payload FROM configs WHERE id=? AND id NOT IN (SELECT id FROM deleted_configs)',(identifier,)).fetchone()
        return json.loads(row['payload']) if row else None

    def configurations(self):
        with self.connection() as db:rows=db.execute('SELECT id,payload,updated FROM configs WHERE id NOT IN (SELECT id FROM deleted_configs) ORDER BY updated DESC').fetchall()
        return [{'id':r['id'],'updated':r['updated'],'config':json.loads(r['payload'])} for r in rows]

    def history(self,identifier):
        with self.connection() as db:
            rows=db.execute('SELECT revision,payload,created FROM config_versions WHERE id=? ORDER BY revision DESC',(identifier,)).fetchall()
        return [{'revision':r['revision'],'created':r['created'],'config':json.loads(r['payload'])} for r in rows]

    def save_draft(self,client,sequence,payload):
        if not isinstance(client,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',client):raise ValueError('草稿客户端ID无效')
        if isinstance(sequence,bool) or not isinstance(sequence,int) or not 0<=sequence<=9007199254740991:raise ValueError('草稿序号无效')
        if not isinstance(payload,dict) or not isinstance(payload.get('config'),dict):raise ValueError('草稿格式无效')
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False);now=time.time()
        if len(encoded)>500000:raise ValueError('草稿过大')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM drafts WHERE client=?',(client,)).fetchone()
            if db.execute('SELECT 1 FROM deleted_configs WHERE id=?',(payload.get('config_id'),)).fetchone():
                return {'ignored':True,'reason':'配置已删除，旧页面不能恢复它'}
            if old and sequence<=old['sequence']:return {'revision':old['revision'],'updated':old['updated'],'ignored':True}
            if old and old['payload']==encoded:
                db.execute('UPDATE drafts SET sequence=? WHERE client=?',(sequence,client))
                return {'revision':old['revision'],'updated':old['updated'],'ignored':False}
            revision=(old['revision'] if old else 0)+1
            db.execute('INSERT INTO drafts VALUES(?,?,?,?,?) ON CONFLICT(client) DO UPDATE SET sequence=excluded.sequence,revision=excluded.revision,payload=excluded.payload,updated=excluded.updated',
                       (client,sequence,revision,encoded,now))
            db.execute('INSERT INTO draft_versions VALUES(?,?,?,?)',(client,revision,encoded,now))
            db.execute('DELETE FROM draft_versions WHERE client=? AND revision<=?',(client,revision-20))
            return {'revision':revision,'updated':now,'ignored':False}

    def latest_draft(self):
        with self.connection() as db:
            deleted={r[0] for r in db.execute('SELECT id FROM deleted_configs')}
            row=next((r for r in db.execute('SELECT * FROM drafts ORDER BY updated DESC')
                      if json.loads(r['payload']).get('config_id') not in deleted),None)
        if not row:return None
        return {'client':row['client'],'revision':row['revision'],'updated':row['updated'],
                'sequence':row['sequence'],'payload':json.loads(row['payload'])}

    def draft_history(self,client):
        with self.connection() as db:
            rows=db.execute('SELECT revision,payload,created FROM draft_versions WHERE client=? ORDER BY revision DESC',(client,)).fetchall()
        return [{'revision':r['revision'],'created':r['created'],'payload':json.loads(r['payload'])} for r in rows]

    def is_deleted(self,identifier):
        with self.connection() as db:return bool(db.execute('SELECT 1 FROM deleted_configs WHERE id=?',(identifier,)).fetchone())

    def trash(self):
        with self.connection() as db:
            return [{'id':r['id'],'deleted':r['deleted'],'config':json.loads(r['payload'])}
                    for r in db.execute('SELECT c.id,c.payload,d.deleted FROM configs c JOIN deleted_configs d ON c.id=d.id ORDER BY d.deleted DESC')]

    def archive(self,identifier):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM configs WHERE id=?',(identifier,)).fetchone():raise ValueError('配置不存在')
            db.execute('INSERT OR IGNORE INTO deleted_configs VALUES(?,?)',(identifier,time.time()))

    def restore(self,identifier):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM configs WHERE id=?',(identifier,)).fetchone():raise ValueError('配置已彻底删除')
            db.execute('DELETE FROM deleted_configs WHERE id=?',(identifier,))

    def purge(self,identifier):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM deleted_configs WHERE id=?',(identifier,)).fetchone():raise ValueError('先将配置移入回收站')
            db.execute('DELETE FROM configs WHERE id=?',(identifier,))
            db.execute('DELETE FROM config_versions WHERE id=?',(identifier,))
            for table in ('drafts','draft_versions'):
                for r in db.execute(f'SELECT rowid,payload FROM {table}').fetchall():
                    if json.loads(r['payload']).get('config_id')==identifier:
                        db.execute(f'DELETE FROM {table} WHERE rowid=?',(r['rowid'],))

    def compact(self):
        with self.connection() as db:
            db.execute('DELETE FROM config_versions WHERE revision <= (SELECT MAX(v.revision)-20 FROM config_versions v WHERE v.id=config_versions.id)')
            db.execute('DELETE FROM draft_versions WHERE revision <= (SELECT MAX(v.revision)-20 FROM draft_versions v WHERE v.client=draft_versions.client)')
        # VACUUM must run outside a transaction. It returns free pages to the OS.
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.execute('VACUUM')
