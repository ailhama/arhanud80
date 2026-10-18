# Deploy Arhanud ke Vercel + Supabase + Fonnte

Kode sudah disiapkan untuk deployment. Akun eksternal, database, domain, dan pengiriman nyata belum dibuat atau diuji oleh proses ini. Tampilan tetap menggunakan HTML/CSS yang ada, API memakai Python Vercel Functions, data tersimpan di PostgreSQL Supabase. Tidak memerlukan PHP atau Google Sheets.

## 1. Buat database Supabase

1. Buat proyek Supabase dan simpan password database.
2. Buka **SQL Editor**, salin seluruh `supabase/schema.sql`, lalu **Run**. Script membuat schema privat `arhanud`; aman dijalankan ulang tanpa menghapus data.
3. Buka **Connect → Transaction pooler**, salin connection string PostgreSQL port `6543`. Ganti placeholder password dengan password database yang benar. Karakter khusus pada password dalam URL perlu di-URL-encode.
4. Simpan connection string sebagai `DATABASE_URL` di Vercel. Ini bukan Project URL atau anon key. Tidak perlu membuka schema `arhanud` ke Data API atau membuat policy publik.

Backend menggunakan koneksi PostgreSQL dengan SSL dan prepared statements dinonaktifkan untuk transaction pooler. Gunakan connection string milik proyek yang baru; database lokal tidak otomatis dipindahkan. Jangan menghapus `.local` karena itu menyimpan data versi lokal.

## 2. Siapkan Fonnte

Daftar layanan Fonnte, hubungkan nomor pengirim, pastikan perangkat aktif dan kuota sesuai. Simpan token perangkat sebagai `FONNTE_TOKEN` di Vercel. Jangan menaruh token di HTML, GitHub, atau chat.

Pesan QR yang dikirim adalah **tautan halaman kartu QR**, bukan lampiran gambar. API berhasil berarti pesan diterima antrean Fonnte; status diterima/dibaca tamu diperiksa di dashboard Fonnte. Webhook status pengiriman belum diimplementasikan.

## 3. Upload kode ke GitHub

Buat repository privat dan unggah proyek ini melalui Git VS Code. Pastikan `.local`, `.env`, `.vercel`, dan `public` tidak ikut masuk Git. File konfigurasi contoh `.env.example` hanya berisi nama variabel, bukan kredensial.

Build memakai daftar file frontend yang eksplisit: enam halaman HTML, logo, dan assets. Source Python serta SQL tidak disalin ke folder publik.

## 4. Import proyek di Vercel

1. **Add New → Project**, import repository GitHub tadi.
2. Framework **Other**, root directory folder proyek ini. Pengaturan build/output dibaca dari `vercel.json`.
3. Tambahkan Environment Variables untuk **Production**:

| Nama | Isi |
|---|---|
| `DATABASE_URL` | Connection string Transaction pooler Supabase |
| `FONNTE_TOKEN` | Token perangkat Fonnte |
| `PIN_PANITIA` | PIN pilihan Anda, minimal 8 karakter |
| `PIN_KOORDINATOR` | PIN berbeda, minimal 8 karakter |
| `PIN_PETUGAS` | PIN berbeda, minimal 8 karakter |
| `PIN_VERSION` | `1` pada deployment pertama |

4. Klik **Deploy**. Instalasi dependency Python dan build frontend berjalan otomatis.
5. Buka alamat HTTPS deployment, misalnya `https://nama-proyek.vercel.app/p.html`.
6. Login dengan `PIN_PANITIA`. Simpan alamat utama website (tanpa `/p.html`) di **Alamat website publik**. Gunakan alamat Production yang stabil, bukan URL Preview tiap commit.

Untuk mengganti PIN cloud: ubah variabel `PIN_...`, naikkan `PIN_VERSION` (misalnya dari `1` ke `2`), lalu **Redeploy Production**. Pada permintaan pertama, hash PIN diperbarui dan sesi lama dihapus. File `.local/admin-pins.json` hanya digunakan versi lokal. Jangan memakai satu database dengan deployment Preview yang memiliki PIN berbeda; Preview harus memakai database dan token uji terpisah. Deployment dengan PIN_VERSION lebih lama ditolak setelah versi baru diterapkan, sehingga tidak dapat mengembalikan PIN lama. Gunakan domain Production aktif.

## 5. Uji sebelum mengundang tamu

Tambahkan satu tamu dengan nomor Anda sendiri. Klik **Kirim undangan**. Pastikan pesan tiba di WhatsApp, buka link, konfirmasi HADIR, dan periksa pesan tautan QR. Login koordinator untuk mengatur meja, login petugas untuk check-in, lalu buka `tv.html`.

Di Vercel, pengiriman per tamu dan pengiriman QR diproses dalam permintaan API yang sama setelah penyimpanan berhasil. Antrean massal diproses satu pesan per permintaan oleh dashboard panitia. **Biarkan halaman panitia terbuka selama kirim massal**. Jika browser ditutup atau tab ditangguhkan, antrean berhenti dan dapat dilanjutkan saat dashboard dibuka. Ini tidak memakai proses latar yang harus hidup terus atau cron berbayar.

Saat timeout, sistem tidak mengirim ulang secara buta. Periksa riwayat Fonnte sebelum menekan Kirim ulang. Pekerjaan yang terputus ditandai belum pasti setelah dua menit ketika antrean kembali diproses. Konfirmasi tetap tersimpan meskipun pengiriman gagal.

## Lokal dan verifikasi

Versi lokal tetap: `python server.py` lalu `http://127.0.0.1:4180`. Versi lokal memakai SQLite dan worker lokal, bukan Supabase. `.env.example` tidak dimuat otomatis oleh server lokal.

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/api.test.cjs
python scripts/build_static.py
```

Tes cloud memeriksa alur, batas pengiriman, dan adaptor SQL dengan database uji lokal serta pengirim tiruan. Uji koneksi Supabase, build di Vercel, serta WhatsApp nyata tetap harus dilakukan setelah kredensial tersedia. Paket gratis memiliki kuota dan ketentuan penggunaan masing-masing; kode tidak membuat pembelian atau mengaktifkan paket berbayar.

Referensi: [Vercel Python](https://vercel.com/docs/functions/runtimes/python), [Supabase connection pooler](https://supabase.com/docs/guides/database/connecting-to-postgres), [Fonnte API](https://docs.fonnte.com/api-send-message/).
