import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer

spec=importlib.util.spec_from_file_location('backend',Path(__file__).resolve().parents[1]/'server.py')
backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=backend.Store(self.temp.name)
        self.pins=json.loads((Path(self.temp.name)/'admin-pins.json').read_text())
    def tearDown(self):self.temp.cleanup()
    def login(self,role):return self.store.call('cekPin',[role,self.pins[role]])[1]
    def call(self,fn,args=None,session=''):return self.store.call(fn,args or [],session)[0]
    def add(self):return self.call('tambahTamu',[{'nama':"Bapak O'Connor",'hp':'081234567890','org':'Contoh'}],self.login('panitia'))['token']
    def test_authorization(self):
        with self.assertRaises(PermissionError):self.call('dataPanitia')
        with self.assertRaises(PermissionError):self.call('hapusSemuaTamu',[],self.login('petugas'))
        with self.assertRaises(PermissionError):self.call('tambahTamu',[{}],self.login('koordinator'))
    def test_pin_and_rate_limit(self):
        for _ in range(8):self.assertFalse(self.call('cekPin',['panitia','wrong'])['ok'])
        result=self.call('cekPin',['panitia',self.pins['panitia']])
        self.assertIn('Terlalu banyak',result['error'])
    def test_tokens_and_phone(self):
        token=self.add();self.assertGreaterEqual(len(token),32)
        rows=self.call('dataPanitia',[],self.login('panitia'))['rows']
        self.assertEqual(rows[0]['hp'],'6281234567890')
        with self.assertRaises(ValueError):backend.phone('0812ABC')
    def test_persistence(self):
        self.add();reopened=backend.Store(self.temp.name)
        sid=reopened.call('cekPin',['panitia',self.pins['panitia']])[1]
        self.assertEqual(reopened.call('dataPanitia',[],sid)[0]['stats']['total'],1)
    def test_full_guest_flow(self):
        token=self.add()
        result=self.call('simpanKonfirmasi',[{'tok':token,'nama':'Tamu Uji','kehadiran':'HADIR','jumlah':2}])
        self.assertEqual(result['qr'],token)
        self.call('setMeja',[token,'b03','i03'],self.login('koordinator'))
        sid=self.login('petugas')
        first=self.call('checkinTamu',[token,'Pos 1'],sid)['guest']
        again=self.call('checkinTamu',[token,'Pos 2'],sid)['guest']
        self.assertEqual(first,again)
        tv=self.call('tvData',['Pos 1']);self.assertEqual(tv['total'],1)
        self.assertEqual(tv['rows'][0]['meja_utama'],'B03')
        self.assertNotEqual(tv['rows'][0]['token'],token)
        self.assertNotIn('hp',tv['rows'][0])
        self.assertEqual(self.call('tvData',['Pos 2'])['total'],0)
    def test_reject_unconfirmed_checkin(self):
        with self.assertRaises(ValueError):self.call('checkinTamu',[self.add(),'Pos 1'],self.login('petugas'))
    def test_invalid_confirmation(self):
        token=self.add()
        for n in [0,21,1.5,True]:
            with self.assertRaises(ValueError):self.call('simpanKonfirmasi',[{'tok':token,'nama':'Tamu','kehadiran':'HADIR','jumlah':n}])
        with self.assertRaises(ValueError):self.call('statusKonfirmasi',['unknown'])
    def test_declined_has_no_qr(self):
        token=self.add()
        res=self.call('simpanKonfirmasi',[{'tok':token,'nama':'Tamu','kehadiran':'TIDAK HADIR','jumlah':0}])
        self.assertEqual(res['qr'],'')
    def test_local_invitation_not_shareable(self):
        token=self.add();sid=self.login('panitia');res=self.call('siapkanUndangan',[token],sid)
        self.assertFalse(res['ready']);self.assertEqual(res['wa_url'],'')
        self.assertIn(token,res['link'])
    def test_whatsapp_link_and_honest_status(self):
        token=self.add();sid=self.login('panitia')
        self.call('simpanPengaturan',['https://undangan.example.org'],sid)
        res=self.call('siapkanUndangan',[token],sid)
        self.assertTrue(res['ready']);self.assertTrue(res['wa_url'].startswith('https://wa.me/6281234567890?'))
        self.assertEqual(self.call('dataPanitia',[],sid)['rows'][0]['invite_status'],'')
        self.call('tandaiDikirim',[token],sid)
        data=self.call('dataPanitia',[],sid)
        self.assertEqual(data['rows'][0]['invite_status'],'manual')
        self.assertEqual(data['stats']['terkirim'],1);self.assertEqual(data['stats']['belum'],0)
        from urllib.parse import urlsplit, parse_qs
        text=parse_qs(urlsplit(res['wa_url']).query)['text'][0]
        self.assertIn(res['link'],text);self.assertNotIn('+',res['wa_url'].split('?',1)[1])
    def test_manual_qr_whatsapp(self):
        token=self.add();sid=self.login('panitia')
        self.call('simpanPengaturan',['https://undangan.example.org'],sid)
        with self.assertRaises(ValueError):self.call('siapkanQr',[token],sid)
        self.call('simpanKonfirmasi',[{'tok':token,'nama':'Tamu Uji','kehadiran':'HADIR','jumlah':1}])
        res=self.call('siapkanQr',[token],sid)
        self.assertTrue(res['ready']);self.assertIn('/k.html?qr='+token,res['link'])
        from urllib.parse import urlsplit, parse_qs
        self.assertIn(res['link'],parse_qs(urlsplit(res['wa_url']).query)['text'][0])
        self.call('tandaiQrDikirim',[token],sid)
        self.assertEqual(self.call('dataPanitia',[],sid)['rows'][0]['qr_delivery'],'manual')
        coord=self.login('koordinator')
        self.assertTrue(self.call('siapkanQr',[token],coord)['ok'])
        self.call('tandaiQrDikirim',[token],coord)
        petugas=self.login('petugas')
        with self.assertRaises(PermissionError):self.call('siapkanQr',[token],petugas)
    def test_remembered_login_and_idle_expiry(self):
        from unittest.mock import patch
        import time as _t
        self.assertEqual(self.call('sesi',[])['roles'],[])
        sid=self.login('panitia')
        self.assertEqual(self.call('sesi',[],sid)['roles'],['panitia'])
        # Signing in to another role in the same browser keeps both.
        sid2=self.store.call('cekPin',['koordinator',self.pins['koordinator']],sid)[1]
        self.assertEqual(self.call('sesi',[],sid2)['roles'],['koordinator','panitia'])
        start=_t.time()
        with patch('time.time',return_value=start+5*3600):
            self.store.call('tambahTamu',[{'nama':'Aktif','hp':'081200000001'}],sid2,'local',True)  # activity
        with patch('time.time',return_value=start+5*3600+5.9*3600):
            self.assertEqual(self.call('sesi',[],sid2)['roles'],['koordinator','panitia'])
            self.store.call('dataPanitia',[],sid2,'local',False)  # background refresh: no extension
        with patch('time.time',return_value=start+5*3600+6.1*3600):
            self.assertEqual(self.call('sesi',[],sid2)['roles'],[])
            with self.assertRaises(PermissionError):self.call('dataPanitia',[],sid2)
    def test_logout(self):
        sid=self.login('petugas')
        self.assertEqual(self.call('sesi',[],sid)['roles'],['petugas'])
        self.call('keluar',[],sid)
        self.assertEqual(self.call('sesi',[],sid)['roles'],[])
    def test_invalid_public_url(self):
        sid=self.login('panitia')
        for url in ['http://example.org','https://localhost','https://x.com/?foo=1','https://user:password@x.com','javascript:alert(1)']:
            with self.assertRaises(ValueError):self.call('simpanPengaturan',[url],sid)
    def test_template_validation(self):
        with self.assertRaises(ValueError):self.call('saveWaFormat',['Pesan tanpa tautan','qr'],self.login('panitia'))
    def test_duplicate_guest(self):
        self.add()
        with self.assertRaises(ValueError):self.add()
    def test_auto_tables_unique(self):
        first=self.add();sid=self.login('panitia')
        self.call('tambahTamu',[{'nama':'Tamu dua','hp':'081234567891'}],sid)
        co=self.login('koordinator');self.call('setMeja',[first,'1',''],co)
        self.call('susunMejaOtomatis',[True],co)
        self.assertEqual({g['meja_utama'] for g in self.call('dataKoordinator',[],co)['rows']},{'1','2'})
    def test_static_files_and_csrf(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),backend.Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            for p in ['/.local/admin-pins.json','/server.py','/tests/test_backend.py','/assets/../server.py','/assets/%2e%2e/server.py','/.git/config']:
                with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(base+p)
                self.assertEqual(cm.exception.code,404)
            request=urllib.request.Request(base+'/api/rpc',data=json.dumps({'fn':'tvData','args':[]}).encode(),headers={'Content-Type':'application/json','Origin':'https://evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(request)
            self.assertEqual(cm.exception.code,403)
            request=urllib.request.Request(base+'/api/rpc',data=json.dumps({'fn':'cekPin','args':['panitia',self.pins['panitia']]}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:
                self.assertIn('HttpOnly',response.headers['Set-Cookie'])
                self.assertTrue(json.load(response)['ok'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
