import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import delivery
from server import Store


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.pins = json.loads((Path(self.temp.name)/'admin-pins.json').read_text())
        self.session = self.store.call('cekPin', ['panitia', self.pins['panitia']])[1]
        self.token = self.call('tambahTamu', [{'nama': 'Tamu uji', 'hp': '081234567890'}])['token']
        self.call('simpanPengaturan', ['https://undangan.example.org'])
        self.env = patch.dict('os.environ', {'FONNTE_TOKEN': 'test-secret'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def call(self, fn, args=None):
        return self.store.call(fn, args or [], self.session)[0]

    def confirm(self, hadir='HADIR'):
        return self.store.call('simpanKonfirmasi', [{'tok': self.token, 'nama': 'Tamu uji', 'kehadiran': hadir, 'jumlah': 1}])[0]

    def job(self, kind):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM outbox WHERE kind=?', (kind,)).fetchone()
            return dict(row) if row else None

    def test_invitation_sent_once_and_persists(self):
        self.call('kirimUndangan', [self.token])
        self.call('kirimUndangan', [self.token])
        calls=[]
        def sender(secret, target, message):
            calls.append((target, message))
            return 'provider-123'
        self.assertTrue(delivery.process_one(Store(self.temp.name), sender))
        self.assertFalse(delivery.process_one(self.store, sender))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], '6281234567890')
        self.assertIn('https://undangan.example.org/k.html?t=', calls[0][1])
        self.assertEqual(self.job('invite')['status'], 'accepted')
        self.assertEqual(self.call('dataPanitia')['stats']['terkirim'], 1)

    def test_confirmation_queues_qr_once_without_admin(self):
        self.confirm();self.confirm()
        messages=[]
        delivery.process_one(self.store, lambda credential, target, message: messages.append(message) or 'qr-id')
        self.assertEqual(len(messages),1)
        self.assertIn('/k.html?qr='+self.token,messages[0])
        self.confirm()
        self.assertFalse(delivery.process_one(self.store, lambda *args: self.fail('Duplicate QR')))

    def test_decline_cancels_pending_qr(self):
        self.confirm();self.confirm('TIDAK HADIR')
        self.assertEqual(self.job('qr')['status'],'cancelled')
        self.assertFalse(delivery.process_one(self.store, lambda *args: self.fail('Declined QR')))

    def test_failure_keeps_confirmation_and_is_not_retried(self):
        self.confirm()
        def reject(*args): raise delivery.Rejected('Kuota habis')
        delivery.process_one(self.store,reject)
        self.assertEqual(self.job('qr')['status'],'failed')
        self.assertEqual(self.call('statusKonfirmasi',[self.token])['hadir'],'HADIR')
        self.assertFalse(delivery.process_one(self.store,reject))

    def test_timeout_is_unknown_without_retry(self):
        self.call('kirimUndangan',[self.token])
        def timeout(*args):raise TimeoutError('private secret must not appear')
        delivery.process_one(self.store,timeout)
        self.assertEqual(self.job('invite')['status'],'unknown')
        self.assertNotIn('private secret',self.job('invite')['error'])
        self.assertFalse(delivery.process_one(self.store,timeout))

    def test_crash_recovery_never_resends_claimed_job(self):
        self.call('kirimUndangan',[self.token])
        with self.store.connect() as db:db.execute("UPDATE outbox SET status='sending'")
        delivery.recover(self.store)
        self.assertEqual(self.job('invite')['status'],'unknown')
        self.assertFalse(delivery.process_one(self.store,lambda *args:self.fail('Duplicate')))

    def test_configuration_guard_and_qr_waits_safely(self):
        with patch.dict('os.environ',{'FONNTE_TOKEN':''}):
            with self.assertRaisesRegex(ValueError,'belum terhubung'):self.call('kirimUndangan',[self.token])
            result=self.confirm()
            self.assertIn('screenshot kartu QR',result['qr_message'])
            self.assertFalse(delivery.process_one(self.store,lambda *args:self.fail('Unconfigured')))
        self.assertEqual(self.job('qr')['status'],'queued')

    def test_bulk_does_not_resend_and_role_guard(self):
        self.assertEqual(self.call('kirimSemua')['jumlah'],1)
        self.assertEqual(self.call('kirimSemua')['jumlah'],0)
        with self.assertRaises(PermissionError):self.store.call('kirimUndangan',[self.token])

    def test_retry_requires_explicit_action_and_cooldown(self):
        self.call('kirimUndangan',[self.token])
        delivery.process_one(self.store,lambda *args:'id')
        with self.assertRaises(ValueError):self.call('kirimUndangan',[self.token,True])
        with self.store.connect() as db:db.execute('UPDATE outbox SET updated=0')
        self.assertEqual(self.call('kirimUndangan',[self.token])['status'],'accepted')
        self.assertEqual(self.call('kirimUndangan',[self.token,True])['status'],'queued')

    def test_delete_removes_pending_messages(self):
        self.call('kirimUndangan',[self.token]);self.confirm()
        self.call('hapusTamu',[self.token])
        self.assertIsNone(self.job('invite'));self.assertIsNone(self.job('qr'))

    def test_provider_transport_uses_secret_header_and_encoded_body(self):
        from io import BytesIO
        with patch('urllib.request.urlopen',return_value=BytesIO(b'{"status":true,"id":["123"]}')) as request:
            result=delivery.send_fonnte('private-token','6281234567890','Halo & selamat datang')
        self.assertIn('123',result)
        sent=request.call_args.args[0]
        self.assertEqual(sent.get_header('Authorization'),'private-token')
        self.assertNotIn('private-token',sent.data.decode())
        self.assertIn('message=Halo+%26+selamat+datang',sent.data.decode())


if __name__=='__main__':unittest.main()
