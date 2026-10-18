import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from cloud_store import CloudStore, postgres_sql, Row, row_factory
from server import Store
import delivery


class RowFactoryTests(unittest.TestCase):
    def test_commands_without_result_columns(self):
        factory=row_factory(SimpleNamespace(description=None))
        self.assertEqual(factory(()),{})

    def test_select_retains_named_and_indexed_access(self):
        factory=row_factory(SimpleNamespace(description=[SimpleNamespace(name='role'),SimpleNamespace(name='hash')]))
        row=factory(('koordinator','test-hash'))
        self.assertEqual(row['role'],'koordinator')
        self.assertEqual(row[0],'koordinator')
        self.assertEqual(dict(row),{'role':'koordinator','hash':'test-hash'})


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.local=Store(self.temp.name)
        self.cloud=CloudStore.__new__(CloudStore)
        self.cloud.folder=self.local.folder
        self.cloud.connect=self.local.connect
        pins=json.loads((Path(self.temp.name)/'admin-pins.json').read_text())
        self.session=self.local.call('cekPin',['panitia',pins['panitia']])[1]
        self.local.call('simpanPengaturan',['https://example.org'],self.session)
        self.env=patch.dict('os.environ',{'FONNTE_TOKEN':'test-only'})
        self.env.start()

    def tearDown(self): self.env.stop();self.temp.cleanup()

    def add(self,name):
        return self.local.call('tambahTamu',[{'nama':name,'hp':'081234567890'}],self.session)[0]['token']

    def test_postgres_translation(self):
        self.assertEqual(postgres_sql('BEGIN IMMEDIATE'),'SELECT pg_advisory_xact_lock(808017)')
        self.assertIn('ON CONFLICT(key)',postgres_sql('INSERT OR REPLACE INTO attempts VALUES(?,?,?)'))
        self.assertEqual(postgres_sql('SELECT * FROM guests WHERE hadir="HADIR" AND instr(lower(nama),lower(?))>0'),"SELECT * FROM guests WHERE hadir='HADIR' AND strpos(lower(nama),lower(%s))>0")
        self.assertEqual(Row(a='value')[0],'value')

    def test_confirm_committed_before_delivery(self):
        token=self.add('Tamu')
        def process(store,**kwargs):
            self.assertEqual(self.local.call('statusKonfirmasi',[token])[0]['hadir'],'HADIR')
            self.assertEqual(kwargs,{'token':token,'kind':'qr'})
            raise TimeoutError()
        with patch('delivery.process_one',side_effect=process):
            result,_=self.cloud.call('simpanKonfirmasi',[{'tok':token,'nama':'Tamu','kehadiran':'HADIR','jumlah':1}])
        self.assertTrue(result['ok']);self.assertIn('delivery_warning',result)

    def test_public_cannot_drain_queue(self):
        with patch('delivery.process_one') as process:
            with self.assertRaises(PermissionError): self.cloud.call('prosesAntrean',[])
            process.assert_not_called()

    def test_drain_is_bounded_to_one_message(self):
        with patch('delivery.process_one') as process:
            self.cloud.call('prosesAntrean',[],self.session)
            process.assert_called_once_with(self.cloud,token=None,kind=None)

    def test_targeted_delivery_does_not_send_other_guests(self):
        a,b=self.add('A'),self.add('B')
        for token in (a,b): self.local.call('kirimUndangan',[token],self.session)
        calls=[]
        delivery.process_one(self.local,sender=lambda *args:calls.append(args) or 'id',token=b,kind='invite')
        with self.local.connect() as db:
            statuses={r['token']:r['status'] for r in db.execute('SELECT * FROM outbox')}
        self.assertEqual(statuses[a],'queued');self.assertEqual(statuses[b],'accepted')
        self.assertEqual(len(calls),1)

    def test_private_schema_and_build_allowlist(self):
        root=Path(__file__).resolve().parents[1]
        sql=(root/'supabase/schema.sql').read_text()
        self.assertIn('REVOKE ALL ON SCHEMA arhanud FROM PUBLIC, anon, authenticated',sql)
        self.assertIn('ALTER TABLE guests ENABLE ROW LEVEL SECURITY',sql)
        import runpy
        runpy.run_path(str(root/'scripts/build_static.py'))
        self.assertEqual({p.name for p in (root/'public').iterdir()}, {'index.html','p.html','o.html','t.html','k.html','tv.html','logo.png','assets'})

    def test_no_credentials_fails_closed(self):
        with patch.dict('os.environ',{'DATABASE_URL':''}):
            with self.assertRaises(RuntimeError):CloudStore()

    def test_configuration_errors_name_the_cause(self):
        base={'DATABASE_URL':'postgresql://test-only','PIN_VERSION':'1','PIN_PANITIA':'panitia-123','PIN_KOORDINATOR':'koordinator-123','PIN_PETUGAS':'petugas-123'}
        cases=[({'DATABASE_URL':''},'DATABASE_URL'),({'DATABASE_URL':'https://x.supabase.co'},'postgresql://'),({'PIN_VERSION':'satu'},'PIN_VERSION'),({'PIN_KOORDINATOR':'short'},'PIN_KOORDINATOR')]
        for change,expected in cases:
            with patch.dict('os.environ',{**base,**change}):
                with self.assertRaises(RuntimeError) as caught: CloudStore()
                self.assertIn(expected,str(caught.exception))

    def test_pin_rotation_and_mismatch_keeps_site_available(self):
        config={'DATABASE_URL':'postgresql://test-only','PIN_VERSION':'1','PIN_PANITIA':'new-panitia-123','PIN_KOORDINATOR':'new-koordinator-123','PIN_PETUGAS':'new-petugas-123'}
        with patch.object(CloudStore,'connect',lambda ignored:self.local.connect()),patch.dict('os.environ',config):
            CloudStore()
            self.assertTrue(self.local.call('cekPin',['panitia',config['PIN_PANITIA']])[0]['ok'])
            # Changed PIN without a new version: ignored, old PIN still valid, guest pages still work.
            with patch.dict('os.environ',{'PIN_PANITIA':'changed-without-version'}):
                store=CloudStore()
                self.assertTrue(self.local.call('cekPin',['panitia',config['PIN_PANITIA']])[0]['ok'])
                failed=store.call('cekPin',['panitia','changed-without-version'],'','other')[0]
                self.assertFalse(failed['ok']);self.assertIn('PIN_VERSION ke 2',failed['error'])
                self.assertTrue(store.call('tvData',[])[0]['ok'])
            with patch.dict('os.environ',{'PIN_VERSION':'2','PIN_PANITIA':'rotated-panitia-123'}):
                CloudStore()
                self.assertTrue(self.local.call('cekPin',['panitia','rotated-panitia-123'])[0]['ok'])
            # An older deployment cannot restore old PINs, but still serves guests.
            store=CloudStore()
            self.assertIn('lebih kecil',store.pin_warning)
            self.assertFalse(self.local.call('cekPin',['panitia',config['PIN_PANITIA']],'','x')[0]['ok'])
            self.assertTrue(self.local.call('cekPin',['panitia','rotated-panitia-123'])[0]['ok'])
            self.assertTrue(store.call('tvData',[])[0]['ok'])

if __name__=='__main__': unittest.main()
