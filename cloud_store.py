"""Supabase PostgreSQL adapter for the shared application, with bounded delivery."""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from contextlib import contextmanager
import delivery
from server import Store, DEFAULT_FORMAT, ROLES


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    # SET/INSERT/UPDATE/DELETE have no result columns. Psycopg still invokes
    # the row factory for these commands, including the SETs during login.
    columns = [c.name for c in (cursor.description or ())]
    return lambda values: Row(zip(columns, values))


def postgres_sql(sql):
    # Only internal, fixed SQL passes here. User values stay separate parameters.
    if sql == 'BEGIN IMMEDIATE':
        return 'SELECT pg_advisory_xact_lock(808017)'
    sql = sql.replace('?', '%s').replace('"', "'")
    sql = sql.replace('instr(lower(nama),lower(%s))', 'strpos(lower(nama),lower(%s))')
    if sql.startswith('INSERT OR REPLACE INTO attempts'):
        sql = sql.replace('INSERT OR REPLACE', 'INSERT') + ' ON CONFLICT(key) DO UPDATE SET count=EXCLUDED.count, until=EXCLUDED.until'
    return sql


class Connection:
    def __init__(self, connection): self.connection = connection
    def execute(self, sql, values=()):
        return self.connection.execute(postgres_sql(sql), values)


class CloudStore(Store):
    def __init__(self):
        self.folder = Path('/tmp/arhanud-unused')
        self.pin_warning = ''
        self.url = os.environ.get('DATABASE_URL', '').strip()
        if not self.url:
            raise RuntimeError('DATABASE_URL belum diisi di Environment Variables Vercel (Production).')
        if not self.url.startswith(('postgres://', 'postgresql://')):
            raise RuntimeError('DATABASE_URL tidak valid. Gunakan connection string Transaction pooler Supabase yang diawali postgresql://, bukan Project URL atau anon key.')
        try:
            revision=int(os.environ.get('PIN_VERSION','1').strip() or '1')
        except ValueError:
            raise RuntimeError('PIN_VERSION harus berupa angka, misalnya 1.') from None
        pins={}
        for role in ROLES:
            pin=os.environ.get('PIN_'+role.upper(), '')
            if len(pin) < 8:
                raise RuntimeError('PIN_'+role.upper()+' belum diisi atau kurang dari 8 karakter di Environment Variables Vercel.')
            pins[role]=pin
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute("SELECT value FROM settings WHERE key='pin_version'").fetchone()
            saved_revision=int(previous['value']) if previous else 0
            # A PIN mismatch must never take the guest pages down. Unsafe PIN changes are
            # skipped (the stored PINs keep working) and only reported on admin login.
            updates=[]; mismatch=[]
            for role in ROLES:
                existing = db.execute('SELECT * FROM accounts WHERE role=?', (role,)).fetchone()
                salt = existing['salt'] if existing else secrets.token_hex(16)
                digest = hashlib.scrypt(pins[role].encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                if not existing or not hmac.compare_digest(existing['hash'],digest):
                    updates.append((role,salt,digest))
                    if existing: mismatch.append('PIN_'+role.upper())
            if revision < 1 or revision < saved_revision:
                self.pin_warning=('PIN_VERSION di Vercel (%d) lebih kecil dari versi tersimpan (%d), sehingga perubahan PIN diabaikan dan PIN lama tetap berlaku. '
                                  'Untuk memakai PIN di Vercel, set PIN_VERSION ke %d lalu Redeploy.' % (revision, saved_revision, saved_revision+1)) if mismatch or revision < 1 else ''
            elif mismatch and revision == saved_revision:
                self.pin_warning=(', '.join(mismatch)+' di Vercel berbeda dari PIN tersimpan, sehingga PIN lama tetap berlaku. '
                                  'Untuk memakai PIN baru, set PIN_VERSION ke %d lalu Redeploy.' % (saved_revision+1))
            else:
                for role,salt,digest in updates:
                    db.execute('INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(role) DO UPDATE SET salt=EXCLUDED.salt, hash=EXCLUDED.hash',(role,salt,digest))
                if mismatch: db.execute('DELETE FROM sessions')
                db.execute("INSERT INTO settings VALUES('pin_version',?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(str(revision),))
            for key, value in [('formats',json.dumps(DEFAULT_FORMAT)),('public_url','')]:
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO NOTHING',(key,value))

    @contextmanager
    def connect(self):
        import psycopg
        with psycopg.connect(self.url, sslmode='require', connect_timeout=8,
                             prepare_threshold=None, row_factory=row_factory) as conn:
            conn.execute("SET LOCAL search_path TO arhanud, pg_catalog")
            conn.execute("SET LOCAL statement_timeout = '8000ms'")
            yield Connection(conn)

    def call(self, fn, args, session='', peer='local', active=False):
        result, cookie = super().call(fn,args,session,peer,active)
        if fn=='cekPin' and isinstance(result,dict) and not result.get('ok') and getattr(self,'pin_warning',''):
            result['error']=result.get('error','')+' Catatan: '+self.pin_warning
        # The confirmation transaction commits BEFORE any provider call.
        targets = {'kirimUndangan': 'invite', 'kirimQr': 'qr'}
        should_process = fn in targets or fn in ('kirimSemua','prosesAntrean') or (fn=='simpanKonfirmasi' and result.get('hadir')=='HADIR')
        if fn=='simpanKonfirmasi' and not delivery.configuration(self.folder):
            should_process = False  # Manual WhatsApp mode: the QR card is shown on the guest's screen.
        if should_process:
            token = args[0] if fn in targets else args[0]['tok'] if fn=='simpanKonfirmasi' else None
            kind = targets.get(fn, 'qr' if fn=='simpanKonfirmasi' else None)
            try:
                with self.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE guests SET invite_status='unknown' WHERE token IN (SELECT token FROM outbox WHERE status='sending' AND kind='invite' AND updated<?)",(time.time()-120,))
                    db.execute("UPDATE outbox SET status='unknown',error='Proses terputus. Periksa riwayat Fonnte sebelum mengirim ulang.' WHERE status='sending' AND updated<?",(time.time()-120,))
                delivery.process_one(self, token=token, kind=kind)
                if token:
                    with self.connect() as db:
                        row=db.execute('SELECT status FROM outbox WHERE token=? AND kind=?',(token,kind)).fetchone()
                    result['delivery_status']=row['status'] if row else ''
                    if fn=='simpanKonfirmasi':
                        result['qr_message']='Tautan QR diterima antrean layanan WhatsApp.' if result['delivery_status']=='accepted' else 'Konfirmasi tersimpan. Simpan QR di halaman ini; pengiriman WhatsApp masih menunggu atau perlu diperiksa panitia.'
            except Exception:
                # Never turn a saved confirmation into a false save failure or replay a send.
                result['delivery_warning']='Data tersimpan. Pengiriman belum dapat dipastikan; periksa status sebelum mengulang.'
        return result,cookie
