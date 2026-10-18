"""Arhanud standalone backend. Python 3.11+, standard library only."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import traceback
import threading
import delivery
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlencode, quote

ROOT = Path(__file__).resolve().parent
DEFAULT_FORMAT = {'broadcast': 'Yth. {nama},\n\nKami mengundang Bapak/Ibu pada HUT ke-80 Arhanud.\n{tanggal}, {waktu}\n{tempat}\n\nKonfirmasi kehadiran: {link}\n\n{panitia}', 'qr': 'Yth. {nama},\nKartu kehadiran HUT ke-80 Arhanud: {link}\nTunjukkan QR kepada petugas saat tiba di lokasi.'}
ROLES = {
 'panitia': {'dataPanitia','tambahTamu','hapusTamu','hapusSemuaTamu','logBroadcast','getWaFormat','saveWaFormat','resetWaFormat','pengaturan','simpanPengaturan','siapkanUndangan','tandaiDikirim','siapkanQr','tandaiQrDikirim','kirimUndangan','kirimSemua','kirimQr','prosesAntrean'},
 'koordinator': {'dataKoordinator','setMeja','susunMejaOtomatis','hapusTamu','logBroadcast','siapkanUndangan','siapkanQr','tandaiQrDikirim','kirimQr'},
 'petugas': {'cariTamu','checkinTamu'},
}
PUBLIC = {'simpanKonfirmasi','statusKonfirmasi','tvData','sesi','keluar'}
# A login stays valid until this many seconds pass without user activity.
SESSION_IDLE = 6*3600

def now():
    return datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S WIB')

def phone(value):
    raw = str(value).strip()
    if not re.fullmatch(r'\+?[\d ().-]+', raw):
        raise ValueError('Nomor WhatsApp tidak valid.')
    n = re.sub(r'\D', '', raw)
    if n.startswith('08'): n = '62' + n[1:]
    elif n.startswith('8'): n = '62' + n
    if not re.fullmatch(r'[1-9]\d{7,14}', n): raise ValueError('Nomor WhatsApp harus lengkap dengan kode negara.')
    if not raw.startswith('+') and not n.startswith('628'): raise ValueError('Gunakan nomor 08…, 628…, atau +kode negara.')
    return n

def clean(value, limit=200):
    value = str(value or '').strip()
    if len(value) > limit: raise ValueError('Isian terlalu panjang.')
    return value

class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'arhanud.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS guests(token TEXT PRIMARY KEY, nama TEXT NOT NULL, hp TEXT NOT NULL, org TEXT DEFAULT '', kategori TEXT DEFAULT 'Undangan', gender TEXT DEFAULT '', jumlah INTEGER DEFAULT 1, hadir TEXT DEFAULT '', keterangan TEXT DEFAULT '', meja_utama TEXT DEFAULT '', meja_istri TEXT DEFAULT '', invite_status TEXT DEFAULT '', confirm_waktu TEXT DEFAULT '', checkin_waktu TEXT DEFAULT '', petugas TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts(role TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, roles TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, waktu TEXT, jenis TEXT, pesan TEXT);
            CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY, count INTEGER, until REAL);
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, token TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, error TEXT DEFAULT '', provider_id TEXT DEFAULT '', updated REAL NOT NULL, UNIQUE(token,kind));
            ''')
            pins = {}
            for role in ROLES:
                if not db.execute('SELECT 1 FROM accounts WHERE role=?', (role,)).fetchone():
                    pin, salt = secrets.token_urlsafe(9), secrets.token_hex(16)
                    digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                    db.execute('INSERT INTO accounts VALUES(?,?,?)', (role, salt, digest))
                    pins[role] = pin
            if pins:
                credentials = self.folder / 'admin-pins.json'
                credentials.write_text(json.dumps(pins, indent=2), encoding='utf-8')
                try: credentials.chmod(0o600)
                except OSError: pass
            for key, value in [('formats', json.dumps(DEFAULT_FORMAT)), ('public_url', '')]:
                db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (key,value))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def call(self, fn, args, session='', peer='local', active=False):
        new_session = None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            def log(kind, message): db.execute('INSERT INTO logs(waktu,jenis,pesan) VALUES(?,?,?)',(now(),kind,message))
            def setting(key): return db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()[0]
            def guest(token):
                row=db.execute('SELECT * FROM guests WHERE token=?',(str(token),)).fetchone()
                if not row: raise ValueError('Tamu atau tautan undangan tidak ditemukan.')
                return dict(row)
            def write(sql, values=()): db.execute(sql, values)
            session_hash=hashlib.sha256(session.encode()).hexdigest()
            sess=db.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(session_hash,time.time())).fetchone()
            roles=json.loads(sess['roles']) if sess else []
            if sess and active and fn!='keluar':
                # Sliding expiry: only real user activity (not background refresh) keeps the login alive.
                write('UPDATE sessions SET expires=? WHERE token=?',(time.time()+SESSION_IDLE,session_hash))
            if fn=='cekPin':
                role,pin=args
                key=peer+':'+str(role)
                attempt=db.execute('SELECT * FROM attempts WHERE key=?',(key,)).fetchone()
                if attempt and attempt['until']>time.time() and attempt['count']>=8:
                    return {'ok':False,'error':'Terlalu banyak percobaan. Tunggu 15 menit.'},None
                account=db.execute('SELECT * FROM accounts WHERE role=?',(str(role),)).fetchone()
                digest=hashlib.scrypt(str(pin).encode(),salt=bytes.fromhex(account['salt']),n=16384,r=8,p=1).hex() if account else ''
                if not account or not hmac.compare_digest(digest,account['hash']):
                    count=attempt['count']+1 if attempt and attempt['until']>time.time() else 1
                    write('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(key,count,time.time()+900))
                    return {'ok':False,'error':'PIN salah.'},None
                write('DELETE FROM attempts WHERE key=?',(key,))
                roles=list(set(roles+[role]))
                new_session=secrets.token_urlsafe(32)
                write('DELETE FROM sessions WHERE token=? OR expires<?',(session_hash,time.time()))
                write('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(new_session.encode()).hexdigest(),json.dumps(roles),time.time()+SESSION_IDLE))
                return {'ok':True},new_session
            if fn not in PUBLIC and not any(fn in ROLES.get(role,set()) for role in roles):
                raise PermissionError('Silakan masuk dengan PIN peran yang berwenang.')
            if fn=='sesi':
                result={'ok':True,'roles':sorted(roles)}
            elif fn=='keluar':
                write('DELETE FROM sessions WHERE token=?',(session_hash,));result={'ok':True}
            elif fn in ('dataPanitia','dataKoordinator'):
                rows=[dict(r) for r in db.execute('SELECT * FROM guests ORDER BY nama')]
                deliveries={(r['token'],r['kind']):dict(r) for r in db.execute('SELECT * FROM outbox')}
                for g in rows:
                    for kind in ('invite','qr'):
                        job=deliveries.get((g['token'],kind),{})
                        g[kind+'_delivery']=job.get('status','')
                        g[kind+'_error']=job.get('error','')
                stats={'total':len(rows),'terkirim':sum(g['invite_status'] in ('accepted','manual') for g in rows),'gagal':sum(g['invite_status'] in ('failed','unknown') for g in rows),'belum':sum(g['invite_status'] not in ('accepted','manual') for g in rows),'hadir':sum(g['hadir']=='HADIR' for g in rows),'tidak':sum(g['hadir']=='TIDAK HADIR' for g in rows),'belumk':sum(not g['hadir'] for g in rows),'orang':sum(g['jumlah'] for g in rows if g['hadir']=='HADIR'),'checkin':sum(bool(g['checkin_waktu']) for g in rows)}
                result={'ok':True,'rows':rows,'stats':stats,'empty':not rows,'terbaru':[dict(r) for r in db.execute('SELECT nama,meja_utama,meja_istri,checkin_waktu AS waktu,petugas FROM guests WHERE checkin_waktu!="" ORDER BY checkin_waktu DESC LIMIT 8')]}
            elif fn=='tambahTamu':
                d=args[0]; nama=clean(d.get('nama'))
                if not nama: raise ValueError('Nama wajib diisi.')
                hp=phone(d.get('hp','')); token=secrets.token_urlsafe(24)
                if db.execute('SELECT 1 FROM guests WHERE hp=? AND nama=?',(hp,nama)).fetchone(): raise ValueError('Tamu dengan nama dan nomor ini sudah ada.')
                write('INSERT INTO guests(token,nama,hp,org) VALUES(?,?,?,?)',(token,nama,hp,clean(d.get('org'))))
                log('panitia','Tamu ditambahkan: '+nama);result={'ok':True,'nama':nama,'token':token}
            elif fn=='hapusTamu':
                g=guest(args[0]);write('DELETE FROM outbox WHERE token=?',(g['token'],));write('DELETE FROM guests WHERE token=?',(g['token'],));log('panitia','Tamu dihapus: '+g['nama']);result={'ok':True,'nama':g['nama']}
            elif fn=='hapusSemuaTamu':
                count=db.execute('SELECT COUNT(*) FROM guests').fetchone()[0];write('DELETE FROM outbox');write('DELETE FROM guests');log('panitia','Seluruh tamu dihapus');result={'ok':True,'jumlah':count}
            elif fn=='logBroadcast': result=[dict(r) for r in db.execute('SELECT waktu,jenis,pesan FROM logs ORDER BY id DESC LIMIT 50')]
            elif fn=='getWaFormat': result=json.loads(setting('formats'))
            elif fn in ('saveWaFormat','resetWaFormat'):
                fmt=DEFAULT_FORMAT if fn=='resetWaFormat' else {'broadcast':clean(args[0],4000),'qr':clean(args[1],4000)}
                if any('{link}' not in fmt[k] for k in ('broadcast','qr')): raise ValueError('Format undangan dan QR harus memuat {link}.')
                write('UPDATE settings SET value=? WHERE key="formats"',(json.dumps(fmt),));result={'ok':True}
            elif fn=='pengaturan': result={'ok':True,'public_url':setting('public_url'),'mode':'otomatis','provider':'Fonnte','configured':bool(delivery.configuration(self.folder)),'cloud':bool(os.environ.get('VERCEL'))}
            elif fn=='prosesAntrean': result={'ok':True}
            elif fn in ('kirimUndangan','kirimSemua','kirimQr'):
                if not setting('public_url'): raise ValueError('Simpan alamat website HTTPS publik sebelum mengirim undangan.')
                if not delivery.configuration(self.folder): raise ValueError('Layanan WhatsApp belum terhubung. Isi token Fonnte di konfigurasi privat server terlebih dahulu.')
                if fn=='kirimSemua':
                    pending=db.execute("SELECT token FROM guests WHERE invite_status='' AND token NOT IN (SELECT token FROM outbox WHERE kind='invite')").fetchall()
                    for row in pending: delivery.enqueue(db,row['token'],'invite')
                    result={'ok':True,'jumlah':len(pending),'message':str(len(pending))+' undangan masuk antrean.'}
                else:
                    g=guest(args[0]);kind='qr' if fn=='kirimQr' else 'invite'
                    if kind=='qr' and g['hadir']!='HADIR': raise ValueError('QR hanya dapat dikirim untuk tamu yang mengonfirmasi hadir.')
                    status=delivery.enqueue(db,g['token'],kind,bool(args[1]) if len(args)>1 else False)
                    result={'ok':True,'status':status,'message':'Status pengiriman: '+status+'. Pantau hasil pada daftar tamu dan log.'}
                log('antrean',result['message'])
            elif fn=='simpanPengaturan':
                url=clean(args[0],300).rstrip('/'); parsed=urlsplit(url)
                if url and (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or parsed.hostname in ('localhost','127.0.0.1','::1')): raise ValueError('Gunakan alamat HTTPS publik tanpa path, misalnya https://undangan.domain-anda.id.')
                write('UPDATE settings SET value=? WHERE key="public_url"',(url,));result={'ok':True}
            elif fn=='siapkanUndangan':
                g=guest(args[0]);base=setting('public_url')
                link=(base or '')+'/k.html?'+urlencode({'t':g['token'],'nama':g['nama']})
                text=json.loads(setting('formats'))['broadcast']
                values={'nama':g['nama'],'tanggal':'Selasa, 17 November 2026','waktu':'08.00 WIB — selesai','tempat':'Aula R. Harsoyo Mapussenarhanud, Cimahi','panitia':'Panitia HUT ke-80 Arhanud','link':link}
                for k,v in values.items(): text=text.replace('{'+k+'}',v)
                result={'ok':True,'nama':g['nama'],'link':link,'text':text,'ready':bool(base),'wa_url':'https://wa.me/'+g['hp']+'?text='+quote(text,safe='') if base else '', 'token':g['token']}
            elif fn=='siapkanQr':
                g=guest(args[0]);base=setting('public_url')
                if g['hadir']!='HADIR': raise ValueError('Kartu QR hanya tersedia untuk tamu yang sudah mengonfirmasi hadir.')
                link=(base or '')+'/k.html?'+urlencode({'qr':g['token']})
                text=json.loads(setting('formats'))['qr']
                values={'nama':g['nama'],'tanggal':'Selasa, 17 November 2026','waktu':'08.00 WIB — selesai','tempat':'Aula R. Harsoyo Mapussenarhanud, Cimahi','panitia':'Panitia HUT ke-80 Arhanud','link':link}
                for k,v in values.items(): text=text.replace('{'+k+'}',v)
                result={'ok':True,'nama':g['nama'],'link':link,'text':text,'ready':bool(base),'wa_url':'https://wa.me/'+g['hp']+'?text='+quote(text,safe='') if base else '','token':g['token']}
            elif fn=='tandaiQrDikirim':
                g=guest(args[0])
                if g['hadir']!='HADIR': raise ValueError('Kartu QR hanya tersedia untuk tamu yang sudah mengonfirmasi hadir.')
                write("INSERT INTO outbox(token,kind,status,error,updated) VALUES(?,'qr','manual','',?) ON CONFLICT(token,kind) DO UPDATE SET status='manual',error='',updated=excluded.updated",(g['token'],time.time()))
                log('manual','Kartu QR dikirim manual via WhatsApp: '+g['nama']);result={'ok':True}
            elif fn=='tandaiDikirim':
                g=guest(args[0]);write('UPDATE guests SET invite_status="manual" WHERE token=?',(g['token'],));log('manual','Panitia menandai undangan sudah dikirim: '+g['nama']);result={'ok':True}
            elif fn=='simpanKonfirmasi':
                d=args[0];g=guest(d.get('tok'));hadir=d.get('kehadiran');nama=clean(d.get('nama'))
                if hadir not in ('HADIR','TIDAK HADIR') or not nama: raise ValueError('Lengkapi nama dan pilihan kehadiran.')
                jumlah=d.get('jumlah')
                if isinstance(jumlah,bool) or not isinstance(jumlah,int) or (hadir=='HADIR' and not 1<=jumlah<=20): raise ValueError('Jumlah hadir harus bilangan bulat 1–20.')
                if g['checkin_waktu']: raise ValueError('Tamu sudah check-in. Hubungi koordinator untuk perubahan.')
                write('UPDATE guests SET nama=?,hadir=?,jumlah=?,keterangan=?,confirm_waktu=? WHERE token=?',(nama,hadir,jumlah if hadir=='HADIR' else 0,clean(d.get('keterangan'),1000),now(),g['token']))
                log('konfirmasi',nama+': '+hadir);result={'ok':True,'hadir':hadir,'qr':g['token'] if hadir=='HADIR' else ''}
                if hadir=='HADIR':
                    previous=db.execute("SELECT status FROM outbox WHERE token=? AND kind='qr'",(g['token'],)).fetchone()
                    if previous and previous['status']=='cancelled':
                        write("DELETE FROM outbox WHERE token=? AND kind='qr'",(g['token'],))
                    result['qr_delivery']=delivery.enqueue(db,g['token'],'qr')
                    result['qr_message']='Tautan kartu QR masuk antrean WhatsApp.' if setting('public_url') and delivery.configuration(self.folder) else 'Simpan atau screenshot kartu QR ini dan tunjukkan kepada petugas saat tiba di lokasi.'
                else:
                    write("UPDATE outbox SET status='cancelled' WHERE token=? AND kind='qr' AND status='queued'",(g['token'],))
            elif fn=='statusKonfirmasi':
                g=guest(args[0]);result={'ok':True,'ada':True,**{k:g[k] for k in ('hadir','jumlah','confirm_waktu')}}
            elif fn=='cariTamu':
                q=clean(args[0],500)
                if '/k.html?' in q:
                    from urllib.parse import parse_qs
                    params=parse_qs(urlsplit(q).query);q=(params.get('t') or params.get('qr') or [''])[0]
                result={'ok':True,'rows':[dict(r) for r in db.execute('SELECT * FROM guests WHERE token=? OR instr(lower(nama),lower(?))>0 LIMIT 50',(q,q))]} if q else {'ok':True,'rows':[]}
            elif fn=='setMeja':
                g=guest(args[0]);utama,pasangan=clean(args[1],30).upper(),clean(args[2],30).upper()
                write('UPDATE guests SET meja_utama=?,meja_istri=? WHERE token=?',(utama,pasangan,g['token']));result={'ok':True,'utama':utama,'pasangan':pasangan}
            elif fn=='susunMejaOtomatis':
                used={r[0] for r in db.execute('SELECT meja_utama FROM guests')};count=0;n=1
                for row in db.execute('SELECT token FROM guests WHERE meja_utama="" ORDER BY nama').fetchall():
                    while str(n) in used:n+=1
                    write('UPDATE guests SET meja_utama=? WHERE token=?',(str(n),row[0]));used.add(str(n));count+=1
                result={'ok':True,'jumlah':count,'terisi':count}
            elif fn=='checkinTamu':
                g=guest(args[0])
                if g['hadir']!='HADIR': raise ValueError('Tamu belum mengonfirmasi hadir.')
                pg=clean(args[1],80)
                if not pg: raise ValueError('Pilih nama petugas atau pos.')
                if not g['checkin_waktu']:
                    write('UPDATE guests SET checkin_waktu=?,petugas=? WHERE token=?',(now(),pg,g['token']));log('checkin','Tamu tiba: '+g['nama'])
                result={'ok':True,'guest':guest(g['token'])}
            elif fn=='tvData':
                # Public reception display intentionally excludes phones and invitation tokens.
                pg=clean(args[0] if args else '',80)
                sql='SELECT token,nama,gender,jumlah,meja_utama,meja_istri,checkin_waktu AS waktu,petugas FROM guests WHERE checkin_waktu!=""'
                rows=[dict(r) for r in db.execute(sql+(' AND petugas=?' if pg else '')+' ORDER BY checkin_waktu DESC LIMIT 8',(pg,) if pg else ())]
                for r in rows:r['token']=hashlib.sha256(r['token'].encode()).hexdigest()[:24]
                total=db.execute('SELECT COUNT(*) FROM guests WHERE checkin_waktu!=""'+(' AND petugas=?' if pg else ''),(pg,) if pg else ()).fetchone()[0]
                result={'ok':True,'rows':rows,'total':total}
            else: raise ValueError('Fungsi tidak tersedia pada backend mandiri: '+str(fn))
            return result,new_session

def server_error_message(error):
    module=type(error).__module__ or ''
    if module.startswith('psycopg'):
        name=type(error).__name__
        if name in ('UndefinedTable','InvalidSchemaName','UndefinedFunction'):
            return 'Tabel database belum dibuat. Jalankan seluruh supabase/schema.sql di Supabase SQL Editor, lalu coba lagi.'
        if name in ('OperationalError','ConnectionTimeout'):
            return 'Tidak dapat terhubung ke database Supabase. Periksa DATABASE_URL (Transaction pooler port 6543, password benar) dan pastikan proyek Supabase aktif.'
        return 'Kesalahan database ('+name+'). Lihat Vercel Logs untuk detail.'
    return 'Terjadi kesalahan server. Data belum dapat diproses.'

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,fmt,*args): pass  # Do not log guest tokens or names from URLs.
    def do_GET(self):
        path=urlsplit(self.path).path
        allowed={'/','/index.html','/p.html','/o.html','/t.html','/k.html','/tv.html','/logo.png'}
        if path not in allowed and not (path.startswith('/assets/') and Path(self.translate_path(path)).resolve().is_relative_to((ROOT/'assets').resolve())):
            self.send_error(404);return
        super().do_GET()
    def list_directory(self,path): self.send_error(404);return None
    def do_HEAD(self):
        path=urlsplit(self.path).path
        allowed={'/','/index.html','/p.html','/o.html','/t.html','/k.html','/tv.html','/logo.png'}
        if path not in allowed and not (path.startswith('/assets/') and Path(self.translate_path(path)).resolve().is_relative_to((ROOT/'assets').resolve())):
            self.send_error(404);return
        super().do_HEAD()
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Cache-Control','no-store' if not self.path.startswith('/assets/') else 'public, max-age=300')
        super().end_headers()
    def get_store(self): return self.server.store
    def do_POST(self):
        if self.path!='/api/rpc': self.send_error(404);return
        status=200;cookie=None
        try:
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'): raise PermissionError('Origin tidak diizinkan.')
            if self.headers.get_content_type()!='application/json': raise ValueError('Gunakan JSON.')
            length=int(self.headers.get('Content-Length',0))
            if length<=0 or length>32768: raise ValueError('Ukuran permintaan tidak valid.')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict) or not isinstance(payload.get('args',[]),list): raise ValueError('Permintaan tidak valid.')
            cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
            session=cookies['arhanud_session'].value if 'arhanud_session' in cookies else ''
            result,cookie=self.get_store().call(payload.get('fn',''),payload.get('args',[]),session,self.client_address[0],payload.get('aktif') is True)
        except RuntimeError as e: status=503;result={'ok':False,'error':'Konfigurasi backend belum siap: '+str(e)}
        except PermissionError as e: status=403;result={'ok':False,'error':str(e)}
        except (ValueError,TypeError,KeyError,IndexError) as e: status=400;result={'ok':False,'error':str(e)}
        except Exception as e:
            status=500;result={'ok':False,'error':server_error_message(e)}
            traceback.print_exc()  # Visible in Vercel Logs; never sent to the browser.
        raw=json.dumps(result,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
        if cookie:self.send_header('Set-Cookie','arhanud_session='+cookie+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000'+('; Secure' if os.environ.get('COOKIE_SECURE')=='1' or os.environ.get('VERCEL') else ''))
        self.end_headers();self.wfile.write(raw)

def serve():
    store=Store(os.environ.get('ARHANUD_DATA_DIR',str(ROOT/'.local')))
    server=ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','4180'))),Handler)
    server.store=store
    delivery.recover(store)
    stop=threading.Event()
    threading.Thread(target=delivery.worker,args=(store,stop),daemon=True).start()
    print('Portal mandiri: http://127.0.0.1:'+str(server.server_port),flush=True)
    print('PIN awal ada di '+str(store.folder/'admin-pins.json'),flush=True)
    try: server.serve_forever()
    finally: stop.set();server.server_close()

if __name__=='__main__':serve()
