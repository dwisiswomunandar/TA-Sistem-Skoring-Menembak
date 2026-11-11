# ITER-09C — Authorized Training Session Creation

Baseline: `feat/django-migration` pada `bbd8391`.

## Cakupan dan status operasional

Service `activities.session_creation.create_training_session` dan form
`/activities/sessions/new/` tersedia. **Pembuatan operasional tetap default-deny**:
`TRAINING_SESSION_CREATION_APPROVALS = ()` pada settings yang dikirimkan. Tidak ada
user/kegiatan/partisipasi operasional atau referensi persetujuan nyata yang diisi.

ITER-09B mensyaratkan sesi yang sudah ditugaskan untuk memberi akses baca. Hak itu
tidak dapat dipakai untuk menyimpulkan kewenangan membuat sesi pertama. Iterasi
ini menambahkan gerbang persetujuan terpisah yang harus diisi di **protected
deployment settings** hanya setelah kebijakan dan persetujuan yang sah tersedia.
Tidak membaca persetujuan dari payload, querystring, environment bebas atau UI.

Komponen persetujuan ini adalah mekanisme provisional untuk menyimpan keputusan
eksplisit; bukan klaim bahwa kebijakan organisasi sudah disepakati. Referensi
persetujuan hanya metadata penelusuran, bukan bukti kriptografis atau validasi
keaslian dokumen persetujuan.

## Otorisasi

Semua syarat berikut wajib, termasuk pada receipt replay:

1. Actor adalah User autentikasi server yang tersimpan dan aktif; dimuat ulang
   dengan helper existing, bukan ID/flag dari klien.
2. Ada persetujuan tepat untuk kombinasi user + kegiatan + partisipasi.
   Setiap entry memiliki field persis `user_id`, `activity_id`, `participation_id`
   (integer positif, bukan bool) dan `approval_reference` (nonblank, maks. 255).
   Entry tidak lengkap/berlebih, konfigurasi salah tipe, atau duplikat user +
   partisipasi ditolak secara fail-closed. Tidak ada wildcard atau allow-all.
3. Permission Django **existing** `activities.add_trainingsession` dan
   `assessments.change_assessment` benar-benar diberikan langsung atau lewat
   group. Resolusi memakai content type lengkap dan tidak memakai permission
   cache ataupun shortcut superuser.
4. Grant `change_assessment` aktif untuk user dan kegiatan yang sama. Registry
   grant tidak ditambah dengan permission pembuatan baru.
5. Partisipasi tersimpan memang berada pada kegiatan yang disetujui; kegiatan
   aktif, pendaftaran approved, dan identitas peserta verified.

`is_staff`, `is_superuser`, creator, assignment existing, permission saja atau
grant saja tidak menggantikan persetujuan eksplisit. Global permission, group,
grant dan persetujuan yang dicabut diperiksa kembali pada request berikutnya
serta di dalam transaksi sebelum perubahan/replay. Tidak ada pemberian izin
atau provisioning akun/grant dalam iterasi ini.

Persetujuan mencakup hanya proyeksi nama peserta/kegiatan/nomor peserta untuk
form pembuatan dan pembuatan **sesi yang ditugaskan kepada actor sendiri**. Actor
juga menjadi `created_by`, keduanya ditetapkan server. Memilih petugas lain tidak
didukung. Selector baca ITER-09B tidak diubah: sebelum sesi pertama, konteks ini
tetap tidak muncul pada worklist baca umum; sesudah berhasil, sesi baru memenuhi
assignment dan hak baca existing.

Tanpa persetujuan atau syarat akses yang lengkap, GET menampilkan keadaan akses
belum tersedia, tanpa membaca tabel operasional; POST ditolak 403. Konteks di luar
scope tidak masuk pilihan form dan tidak dapat dipilih melalui IDOR.

## Validasi, transaksi dan audit

Kontrak keyword-only: `actor`, `participation_id`, `data`, `operation_id` (UUID),
`reason` (nonblank, maks. 2000), optional `using`. `data` hanya empat parameter
existing. Tidak menerima nomor sesi, actor/assessor/creator ID, timestamp, versi,
status atau bypass flag dari klien.

Input diperiksa dengan validator field model sebelum resolver ITER-05. Konflik,
nonfinite, boolean numerik dan presisi berlebih ditolak. Nilai resolusi numerik
dikanonisasi sesuai presisi storage untuk fingerprint, tanpa memperbaiki input
yang semula invalid. Laras panjang memerlukan sikap eksplisit dan menghasilkan
100 m / 5,56 mm. Pistol memerlukan 15/20/25 m dan menghasilkan berdiri / 9,00 mm.
Tidak memilih default senjata, sikap laras panjang atau jarak pistol.

Transaksi pembuatan mengunci partisipasi, kegiatan, identitas peserta, User dan
grant (PK order), sesuai urutan jalur pembuatan parent existing. Nomor sesi
adalah `MAX(session_number) + 1` pada **seluruh** sesi partisipasi, di bawah lock
partisipasi. Semua model/unique/eligibility/version guards tetap dijalankan lewat
`TrainingSession.save()` normal; tidak ada update/bulk/SQL bypass.

Satu `AuditEvent` append-only `training_session.created` dibuat dalam transaksi
yang sama. Scope hanya `session`, actor User server, before session null,
version null -> 1. After menyimpan parameter dan konteks server, metadata
persetujuan/policy, kondisi eligibility, fingerprint request dan receipt.
Tidak menyimpan identitas global, kredensial atau payload bebas. Gagal validasi,
insert atau audit me-rollback seluruh sesi baru; parent/riwayat lama tidak ditulis.

Audit menyentuh sesi baru yang masih dimiliki transaksi yang sama sehingga
penguncian ulang oleh `AuditEvent.save()` tidak mengambil sesi existing milik
transaksi lain. Replay membaca receipt tanpa mengambil lock sesi existing
setelah lock partisipasi. Snapshot dan audit lama tidak diperbarui.

UUID unik dan fingerprint mengikat actor, konteks, parameter normal, alasan dan
persetujuan. Request identik mengembalikan receipt yang sama tanpa menulis nomor,
timestamp atau audit baru. UUID sama dengan isi berbeda ditolak. Collision insert
unik/validasi audit diatasi dengan rollback penuh lalu maksimum satu percobaan
ulang, selalu melalui pemeriksaan kewenangan saat ini. UUID baru berarti intent
sesi baru; tidak melakukan deduplikasi semantik seluruh sesi.

Audit pembuatan langsung mengaktifkan historical guards existing pada konteks
sesi baru. Mengubah assignment/parameter setelah audit tidak diizinkan. Reassignment
atau pembuatan atas nama petugas lain memerlukan desain/otorisasi terpisah.

## UI dan batas implementasi

Link Buat sesi tersedia dari daftar kegiatan. Form Django memakai pilihan konteks
DB yang telah difilter, senjata, sikap laras panjang, jarak pistol, alasan dan UUID
tersembunyi. Parameter otomatis hanya informasi dari resolver server, bukan input.
Script lokal kecil menampilkan pilihan relevan; form tetap berfungsi tanpa JS.
Validasi backend tetap menolak parameter konflik yang dikirim manual. Field asing
atau field berulang ditolak. GET tidak membuat sesi/audit.

CSRF, autoescape, autentikasi existing, `never_cache`, pesan sukses dan
Post/Redirect/Get ke daftar sesi dipakai. UUID dipertahankan pada error form untuk
retry; setelah sukses, POST ulang mengembalikan receipt yang sama. Layout/CSS
Tactical Command Center dipakai kembali tanpa dependency baru.

Tidak ada perubahan model, migration, AI engine, scoring, kamera, upload, PDF,
Django Admin, database operasional, atau dummy/seed data produksi.

## Keputusan yang masih diperlukan

Pihak berwenang harus menentukan user, kegiatan, partisipasi dan referensi
persetujuan nyata yang sah serta menerima batas self-assignment. Sampai itu
tersedia, settings tetap kosong dan penggunaan operasional terblokir. Tidak
menyimpulkan kewenangan dari role atau menyebarkan izin baca lintas kegiatan.

Penguncian/penomoran dirancang untuk PostgreSQL, tetapi konkurensi PostgreSQL
nyata belum diverifikasi. Tes SQLite in-memory membuktikan alur, rollback,
collision yang disimulasikan dan regresi guard, bukan sertifikasi konkurensi DB.
Browser visual/JavaScript interaktif belum diuji; smoke test route memakai Django
test client tanpa menjalankan server.

## File implementasi dan pengujian

- `activities/session_creation_policy.py`: approval gate dan pilihan konteks sempit.
- `activities/session_creation.py`: service transaksi/audit/idempotensi.
- `activities/session_forms.py`, `activities/views.py`, `activities/urls.py`.
- `activities/test_session_creation.py`: fixture dan persetujuan sintetis di tes.
- `config/settings/base.py`: default persetujuan kosong.
- `templates/activities/{activity_list,session_create}.html`, `templates/ui/base.html`.
- `static/ui/{app.css,session_form.js}`, README dan dokumen ini.

Seluruh management command memakai Python `.venv`, `-B -X utf8`, dotenv dan
bytecode nonaktif, serta settings test eksplisit `config.settings.test`.
Focused suite: `activities.test_session_creation activities.test_ui
activities.test_parameters config.tests.test_ui`. Tes histori membutuhkan file
temporer, sehingga dijalankan di luar sandbox read-only dengan izin. Database
tetap SQLite in-memory. Full regression hanya satu kali setelah implementasi
stabil, dengan timeout terbatas; tidak ada server persisten, commit atau push.

Hasil akhir aktual:

- **78 focused tests lulus**. Assertion pesan sukses disesuaikan agar redirect
  fetch milik test helper tidak mengonsumsi message sebelum assertion; kebutuhan
  filesystem temporer tes histori dipenuhi lewat izin eksekusi di luar sandbox.
- **774 full regression tests lulus**, satu kali, 131,293 detik (timeout 180 detik).
- Django system check tanpa masalah, tidak ada check yang disembunyikan.
- Migration drift: `No changes detected`.
- `git diff --check`, whitespace seluruh 14 file baru/berubah, sintaks Python dan
  `node --check` untuk script form: bersih.
- Tidak ada konfigurasi persetujuan operasional yang diaktifkan. Implementasi
  default-deny siap ditinjau/di-commit; aktivasi operasional menunggu keputusan
  persetujuan sah sebagaimana batas di atas.
