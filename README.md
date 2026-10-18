# Portal HUT ke-80 Arhanud

**Deployment Vercel + Supabase:** ikuti [DEPLOY.md](DEPLOY.md). File `vercel.json`, API serverless, dan `supabase/schema.sql` sudah disediakan. Instruksi server SQLite di bawah tetap berlaku untuk mode lokal.

Aplikasi mandiri untuk undangan, konfirmasi kehadiran, pengaturan meja, check-in, dan layar penerima tamu HUT ke-80 Arhanud TNI AD. Backend memakai Python dan SQLite, tanpa Excel, Google Sheets, atau Apps Script. Backend terhubung ke layanan WhatsApp melalui integrasi Fonnte yang disiapkan secara terpisah.

## Halaman

| File | Fungsi |
| --- | --- |
| `index.html` | Portal utama, informasi acara, dan akses sesuai peran |
| `p.html` | Panitia: data tamu, undangan WhatsApp, format pesan, dan log |
| `o.html` | Koordinator: rekap kehadiran dan pengaturan meja |
| `t.html` | Petugas: pencarian tamu, pemindaian QR, dan check-in |
| `k.html?t=TOKEN` | Konfirmasi kehadiran tamu |
| `k.html?qr=TOKEN` | Kartu QR tamu |
| `tv.html` | Layar penerima tamu seluruh pos |
| `tv.html?pg=Pos%201` | Layar penerima tamu untuk pos yang ditentukan |

Nilai `pg` harus sama persis dengan nama pos di halaman petugas.

## Menjalankan lokal

Memerlukan Python 3.11 atau lebih baru. Tidak memerlukan instalasi paket Python atau proses build. Jalankan dari direktori proyek:

```sh
python server.py
```

Buka `http://127.0.0.1:4180`. Jangan menggunakan server statis `python -m http.server`; endpoint backend tidak tersedia di server statis. Kamera memerlukan HTTPS atau localhost dan izin browser. Alternatif Windows: jalankan `start.ps1`.

Pada startup pertama, server membuat database `.local/arhanud.sqlite3` dan PIN acak untuk panitia, koordinator, serta petugas dalam `.local/admin-pins.json`. Buka file PIN tersebut untuk login. File PIN tidak tersedia melalui HTTP dan tidak masuk Git. Mengedit file PIN tidak mengganti PIN akun yang sudah tersimpan; hash akun disimpan di database.

Data baru mulai kosong. Data dari backend lama belum dimigrasikan. Data yang dimasukkan di aplikasi baru tersimpan setelah server dimatikan dan dijalankan kembali. Folder `.local` adalah data privat; jangan dihapus atau diunggah ke Git.

## Alur penggunaan

1. Panitia login dan menambah nama serta nomor WhatsApp tamu.
2. Klik **Kirim undangan** per tamu, atau **Kirim semua yang belum dikirim**. Pesan masuk antrean persisten SQLite dan diproses server setiap dua detik.
3. Tamu membuka tautan dan mengonfirmasi hadir. Konfirmasi langsung tersimpan, QR tampil di halaman, dan sistem otomatis mengantrekan pesan WhatsApp berisi tautan kartu QR. Pesan QR menggunakan format kedua di halaman panitia. Ini tautan kartu, bukan lampiran gambar.
4. Konfirmasi berulang tidak menggandakan pengiriman QR. Tamu yang tidak hadir tidak mendapat pengiriman QR; antrean QR yang belum diproses dibatalkan.
5. Koordinator mengatur meja dan dapat menggunakan **Kirim ulang QR**. Petugas mencari tamu atau memindai QR untuk check-in. Layar TV memperbarui data setiap empat detik.

Status **Diterima layanan WA** berarti API menerima pesan ke antrean penyedia, bukan bukti pesan diterima atau dibaca tamu. Status sampai/dibaca belum dihubungkan melalui webhook. Periksa dashboard Fonnte untuk status tersebut. Respons gagal dan timeout dicatat di log; timeout tidak dicoba ulang otomatis karena pesan mungkin sudah diterima penyedia. Pengiriman ulang perlu tindakan panitia dengan jeda minimal satu menit. Pengiriman massal hanya mengantrekan tamu yang belum pernah diproses.

## Menyambungkan WhatsApp otomatis

Integrasi yang tersedia saat ini: Fonnte. Siapkan akun, nomor pengirim yang terhubung, token perangkat, dan kuota yang sesuai di layanan tersebut. Tidak ada akun, pembelian, atau pengiriman nyata yang dibuat otomatis saat instalasi. Dokumentasi API: https://docs.fonnte.com/api-send-message/.

Pada komputer lokal, isi file privat `.local/whatsapp.json` dengan token perangkat Anda:

```json
{"token": "TOKEN_PERANGKAT_ANDA"}
```

Alternatif server/Docker: set variabel lingkungan `FONNTE_TOKEN`, yang mengambil prioritas atas file. Token hanya dibaca backend, tidak dikirim ke browser, tidak disimpan di HTML, dan file privat tidak dapat diakses melalui HTTP. Jangan memasukkan token pada kolom alamat website atau ke Git. Konfigurasi file dibaca ulang otomatis. Jangan gunakan token contoh untuk pengiriman nyata.

Pasang backend pada hosting HTTPS dengan penyimpanan persisten, lalu simpan alamat publiknya pada halaman panitia. Pengiriman undangan ditolak dengan pesan jelas jika token atau alamat publik belum diatur. Konfirmasi tamu tetap tersimpan meski layanan WhatsApp belum siap; antrean QR akan menunggu konfigurasi lengkap. **Sebelum mengaktifkan kredensial, periksa dan hapus data tamu uji yang tidak boleh menerima pesan.**

Tanpa konfigurasi layanan WhatsApp dan hosting publik, aplikasi hanya dapat diuji lokal. Menambahkan token saja tidak mempublikasikan website. Pengujian otomatis menggunakan layanan tiruan dan tidak membuktikan pengiriman ke nomor nyata.

## Pengujian pengiriman

```sh
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/api.test.cjs
```

Tes backend dan antrean menggunakan database sementara, pengirim tiruan, dan HTTP lokal: autentikasi/peran, pembatasan percobaan PIN, normalisasi nomor, penyimpanan, konfirmasi, meja, check-in, TV, tautan WhatsApp, serta perlindungan file privat. Tes JavaScript menguji transport JSON POST dan memastikan permintaan tulis tidak diulang otomatis. Tidak ada pesan WhatsApp dikirim oleh tes.

## Desain dan aset

- Seluruh halaman memakai `assets/site.css` dengan variabel warna bersama.
- Font lokal: Barlow Condensed untuk judul dan Manrope untuk isi. Lisensi tersedia di `assets/fonts/`.
- `logo.png` mempertahankan lambang yang sudah ada.
- `assets/ceremony-landscape.webp` merupakan ilustrasi latar yang dibuat dengan AI dan dikompresi untuk web. Catatan aset dan prompt terdapat di `assets/README.md`.
- Desain mendukung ponsel, tablet, desktop, TV, navigasi keyboard, indikator fokus, dan preferensi reduced motion.
- Semua HTML, folder `assets/`, `logo.png`, `server.py`, dan `delivery.py` harus berada di server yang sama.

## Hosting

Backend baru belum dipublikasikan. Domain lama dan DNS belum diubah. GitHub Pages hanya melayani file statis dan tidak dapat menjalankan `server.py`; gunakan server/hosting yang menjalankan Python atau Docker dengan penyimpanan persisten dan HTTPS.

Contoh Docker (letakkan di belakang reverse proxy HTTPS):

```sh
docker build -t arhanud80 .
docker run -d --name arhanud80 --restart unless-stopped -p 127.0.0.1:4180:4180 -v arhanud-data:/data -e COOKIE_SECURE=1 arhanud80
```

Konfigurasi reverse proxy harus meneruskan Host asli. PIN awal tersedia di `/data/admin-pins.json` dalam volume. Set `COOKIE_SECURE=1` hanya untuk deployment HTTPS; pada localhost HTTP biarkan tidak disetel. Variabel opsional: `HOST`, `PORT`, `ARHANUD_DATA_DIR`. Gunakan satu database persisten; jangan meletakkannya pada filesystem deployment yang dihapus setiap restart.

Simpan alamat publik yang benar pada halaman panitia **setelah** domain mengarah ke backend baru. Menyimpan alamat domain saja tidak melakukan deployment atau mengubah DNS. Tautan dengan alamat localhost tidak dapat dibuka tamu dari luar komputer ini.

Untuk backup konsisten saat server berjalan, gunakan fasilitas SQLite backup (bukan menyalin file database tanpa WAL). Contoh dari direktori proyek:

```sh
python -c "import sqlite3; source=sqlite3.connect('.local/arhanud.sqlite3'); target=sqlite3.connect('.local/backup.sqlite3'); source.backup(target); target.close(); source.close()"
```

File `CNAME` adalah peninggalan konfigurasi domain sebelumnya; deployment Python/Docker tidak menggunakan file tersebut.
