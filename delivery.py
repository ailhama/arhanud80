"""Durable WhatsApp outbox. No automatic retry after an uncertain network result."""
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode


class Rejected(Exception):
    pass


def configuration(folder):
    if os.environ.get('VERCEL'): return os.environ.get('FONNTE_TOKEN','').strip()
    path = folder / 'whatsapp.json'
    data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    return os.environ.get('FONNTE_TOKEN', data.get('token', '')).strip()


def send_fonnte(token, target, message):
    request = urllib.request.Request('https://api.fonnte.com/send',
        data=urlencode({'target': target, 'message': message, 'countryCode': '0'}).encode(),
        headers={'Authorization': token, 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (400, 401, 403, 422, 429):
            raise Rejected('Layanan menolak permintaan (HTTP %s). Periksa token, perangkat, dan kuota Fonnte.' % error.code) from None
        raise RuntimeError('Respons layanan tidak pasti. Periksa riwayat Fonnte sebelum mengirim ulang.') from None
    if result.get('status', result.get('Status')) is not True:
        raise Rejected('Fonnte menolak pesan. Periksa token, koneksi nomor, kuota, dan riwayat pada dashboard Fonnte.')
    return json.dumps(result.get('id', []))


def enqueue(db, token, kind, force=False):
    old = db.execute('SELECT * FROM outbox WHERE token=? AND kind=?', (token, kind)).fetchone()
    if old:
        if not force or old['status'] in ('queued', 'sending'):
            return old['status']
        if time.time() - old['updated'] < 60:
            raise ValueError('Tunggu satu menit sebelum mengirim ulang pesan ini.')
        db.execute("UPDATE outbox SET status='queued', error='', provider_id='', updated=? WHERE token=? AND kind=?", (time.time(), token, kind))
    else:
        db.execute("INSERT INTO outbox(token,kind,status,updated) VALUES(?,?,'queued',?)", (token, kind, time.time()))
    if kind == 'invite':
        db.execute("UPDATE guests SET invite_status='queued' WHERE token=?", (token,))
    return 'queued'


def recover(store):
    with store.connect() as db:
        db.execute("UPDATE guests SET invite_status='unknown' WHERE token IN (SELECT token FROM outbox WHERE status='sending' AND kind='invite')")
        db.execute("UPDATE outbox SET status='unknown', error='Server berhenti saat pengiriman. Periksa riwayat Fonnte sebelum mengirim ulang.' WHERE status='sending'")


def process_one(store, sender=send_fonnte, token=None, kind=None):
    credential = configuration(store.folder)
    if not credential:
        return False
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        base = db.execute("SELECT value FROM settings WHERE key='public_url'").fetchone()[0]
        if not base:
            return False
        filters, values = "status='queued'", []
        if token is not None: filters += ' AND token=?'; values.append(token)
        if kind is not None: filters += ' AND kind=?'; values.append(kind)
        job = db.execute('SELECT * FROM outbox WHERE '+filters+' ORDER BY updated LIMIT 1',values).fetchone()
        if not job:
            return False
        guest = db.execute('SELECT * FROM guests WHERE token=?', (job['token'],)).fetchone()
        if not guest or (job['kind'] == 'qr' and guest['hadir'] != 'HADIR'):
            db.execute("UPDATE outbox SET status='cancelled' WHERE id=?", (job['id'],))
            return True
        formats = json.loads(db.execute("SELECT value FROM settings WHERE key='formats'").fetchone()[0])
        link = base + '/k.html?' + urlencode({'qr': guest['token']} if job['kind'] == 'qr' else {'t': guest['token'], 'nama': guest['nama']})
        message = formats['qr' if job['kind'] == 'qr' else 'broadcast']
        values = {'nama': guest['nama'], 'link': link, 'tanggal': 'Selasa, 17 November 2026', 'waktu': '08.00 WIB — selesai', 'tempat': 'Aula R. Harsoyo Mapussenarhanud, Cimahi', 'panitia': 'Panitia HUT ke-80 Arhanud'}
        for key, value in values.items():
            message = message.replace('{' + key + '}', value)
        db.execute("UPDATE outbox SET status='sending', updated=? WHERE id=?", (time.time(), job['id']))
    status, error, provider_id = 'accepted', '', ''
    try:
        provider_id = sender(credential, guest['hp'], message)
    except Rejected as exc:
        status, error = 'failed', str(exc)
    except Exception:
        status, error = 'unknown', 'Hasil pengiriman belum pasti. Periksa riwayat Fonnte sebelum mengirim ulang; sistem tidak mencoba ulang otomatis.'
    with store.connect() as db:
        db.execute('UPDATE outbox SET status=?,error=?,provider_id=?,updated=? WHERE id=?', (status, error, provider_id, time.time(), job['id']))
        if job['kind'] == 'invite':
            db.execute('UPDATE guests SET invite_status=? WHERE token=?', (status, job['token']))
        from server import now
        db.execute('INSERT INTO logs(waktu,jenis,pesan) VALUES(?,?,?)', (now(), 'WhatsApp ' + job['kind'], guest['nama'] + ': ' + (error or 'Pesan diterima antrean Fonnte; belum merupakan bukti sampai ke penerima.')))
    return True


def worker(store, stop):
    while not stop.is_set():
        try:
            process_one(store)
        except Exception:
            # Never log credentials or blindly retry a claimed job.
            pass
        stop.wait(2)
