# Implementasi domain iterasi 1

Sumber utama: [Database Design Baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md),
khususnya bagian 3.1, 3.2, indeks/relasi, dan keputusan terbuka bagian 1.1/9.
Implementasi terbatas pada Participant, Activity, ActivityParticipation,
dan TrainingSession. Custom User serta migrasi autentikasi awal dipertahankan.

## Model dan integritas

- Participant berada di accounts; account bersifat One-to-One opsional.
  Nama dan nomor identitas tidak unik global. Status verified memerlukan
  verified_by dan verified_at.
- Activity, ActivityParticipation, dan TrainingSession berada di activities.
  Kegiatan bernama sama diperbolehkan. Entry code bersifat opsional, unik
  bila terisi, dan memerlukan jendela akses dengan akhir setelah awal.
  Waktu akhir yang tidak lebih besar dari awal ditolak juga saat kode belum diisi.
  Kode kegiatan bukan autentikasi dan tidak memberikan hak membaca hasil.
- Satu pasangan activity/participant hanya mempunyai satu participation.
  Identifier unik dalam kegiatan saat terisi. Whitespace awal/akhir dipangkas;
  nilai kosong disimpan NULL. Kapitalisasi dipertahankan karena kebijakan
  case-insensitive untuk identifier belum disepakati.
- Approved/rejected memerlukan pengambil keputusan dan waktu. Approved memerlukan
  identifier. Rejected/withdrawn memerlukan alasan nonblank. Relasi kegiatan dan
  peserta tidak dapat diganti melalui save() setelah sesi terkait ada.
- Sesi merujuk hanya participation; tidak ada FK activity/participant duplikat.
  Nomor sesi unik per participation, nomor/edit_version minimal satu.
  Jarak dan kaliber wajib positif, finite, sesuai presisi DecimalField.
  Tidak ada default tersembunyi untuk senjata, jarak, kaliber, atau sikap.
- Semua FK historis memakai PROTECT. Unik, status, metadata kondisional,
  dan besaran positif mempunyai constraint database yang relevan.
  Indeks activity/registration_status dan lookup assessor tersedia; FK prefix
  yang sudah dicakup unique komposit tidak diberi indeks identik tambahan.
- save() model baru menjalankan full_clean(). Penyimpanan participation mengunci
  record yang ada dalam transaksi; penyimpanan sesi mengunci participation,
  activity, dan participant melalui join sebelum memvalidasi konteks.
  QuerySet.update(), bulk_create(), bulk_update(), dan SQL langsung melewati
  validasi Python serta penguncian model; constraint DB tetap berlaku.
  Jalur tersebut bukan API perubahan workflow yang boleh menerima input tidak
  tervalidasi. SQLite tidak membuktikan efektivitas row locking PostgreSQL.

## Penyesuaian eksplisit untuk registrasi mandiri

registered_by pada ActivityParticipation nullable. Ini penyesuaian yang diminta
pengguna terhadap asumsi petugas wajib pada baseline 3.2; dukungan registrasi
mandiri adalah kebutuhan yang sudah diputuskan. Participation pending dapat
dibuat tanpa account Participant maupun registered_by. Pendaftaran melalui
petugas dapat menyertakan registered_by User yang sebenarnya.

NULL hanya berarti tidak ada aktor Django yang ditautkan; NULL tidak membuktikan
bahwa pendaftaran benar dilakukan peserta sendiri. Jangan mengarang akun staff
atau dummy untuk mengisi atribusi. Identifikasi actor/source, verifikasi identitas,
persetujuan registrasi, dan audit peserta tanpa User masih harus dirancang sebelum
fitur registrasi atau akses peserta dibuka. Tidak ada endpoint registrasi,
autentikasi peserta, token/PIN, atau AuditEvent dalam iterasi ini.

## Aturan sementara dan keputusan yang tetap terbuka

- Prasyarat pembuatan sesi memakai asumsi baseline: activity active,
  participation approved, identitas verified. Perubahan menjadi closed/withdrawn
  tidak menghapus atau membuat penyimpanan sesi lama otomatis gagal.
- Kandidat senjata laras_panjang/pistol, sikap berdiri/duduk/tiarap,
  kaliber 5.56/9.00, dan pasangan laras panjang-5.56 / pistol-9.00 mengikuti
  baseline/SRS; belum menjadi standar lapangan yang disahkan.
  Kombinasi senjata-kaliber divalidasi pada server, bukan melalui constraint
  permanen pasangan pada DB. Nilai jarak/sikap tidak diikat ke jenis senjata.
- Presisi jarak (8,2), kaliber (5,2), nama 255 dan nomor identitas 64 adalah
  batas penyimpanan teknis iterasi ini. Format/otoritas nomor identitas dan
  kebutuhan ukuran lain harus dikonfirmasi; tidak ada unique identitas global.
- FK petugas User tidak otomatis membuktikan kewenangan bisnis.
  Pemetaan peran/permissions, petugas yang boleh mengesahkan, dan penugasan
  assessor belum diputuskan. is_staff tidak diperlakukan sebagai role bisnis.
- Transisi workflow, audit perubahan, kenaikan edit_version dan penolakan versi
  request lama harus ditambahkan secara atomik dalam layanan domain berikutnya.
  Pada iterasi ini edit_version disediakan dan dibatasi positif, belum menjadi
  mekanisme optimistic concurrency maupun audit trail.
- Perubahan metadata sesi setelah hasil dikunci, aturan retensi, akses setelah
  withdrawn, dan perubahan penugasan menunggu modul layanan/audit/assessments.
- Target/ring, scoring, foto, inferensi YOLO26s, UI, dan autentikasi peserta
  tidak diimplementasikan. PostgreSQL nyata dan database Laravel tidak diakses.

## Verifikasi dan batas pengujian

Jalankan dari src/django-app menggunakan .venv yang sudah tersedia:

~~~powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& .venv/Scripts/python.exe -B manage.py check --settings=config.settings.test
& .venv/Scripts/python.exe -B manage.py test --settings=config.settings.test
& .venv/Scripts/python.exe -B manage.py makemigrations --check --dry-run --settings=config.settings.test
~~~

Settings test memakai SQLite dalam memori. Tes menerapkan migrasi hanya pada
database sementara ini dan memeriksa constraint SQL melalui operasi yang
melewati save(), selain validasi Python dan FK PROTECT.

Sebelum produksi, uji ulang migrasi, partial unique index, collation/Unicode,
presisi numeric/nonfinite, batas panjang varchar, row locking, dan race condition
pada PostgreSQL baru yang terisolasi. SQLite tidak menegakkan semua karakteristik
tipe maupun perilaku konkurensi PostgreSQL. Tidak ada klaim pengujian PostgreSQL
dari hasil SQLite.
