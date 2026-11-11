# ITER-09A — Django UI Foundation

Baseline: `feat/django-migration` pada `5b29811`.

## Cakupan

- `/login/`: Django `LoginView` dan subclass presentasi `AuthenticationForm`.
  Identitas email, normalisasi dan backend autentikasi existing dipertahankan.
  Error kredensial generik, password tidak dipantulkan kembali, CSRF wajib,
  serta validasi `next` same-host dan HTTPS dari Django tetap digunakan.
- `/`: dashboard wajib login, GET/HEAD saja, respons `never_cache`.
  Menampilkan nama/email/status akun dari `request.user` saja.
- `/logout/`: Django `LogoutView`, POST dengan CSRF; kembali ke login.
- Template bersama, navigasi desktop/mobile tanpa JavaScript, dark mode Modern
  Tactical Command Center, CSS lokal tanpa framework/dependency/font/CDN baru.
  Skip link, label form, error terasosiasi, focus keyboard dan reduced motion.
- `/health/` dan `/admin/` existing dipertahankan.

## Batas akses dan blocker

Belum ada kebijakan baca operasional yang dapat dipakai dashboard.
`activities.authorization.draft_sessions` secara eksplisit merupakan selector
otorisasi aksi draft, bukan kebijakan baca umum/download. Permission global,
grant kegiatan, assignment, creator, staff maupun superuser tidak digunakan
untuk menciptakan izin baca baru.

Dashboard default-deny terhadap **semua** data operasional: tidak membaca tabel
kegiatan, peserta, sesi, evidence, assessment atau audit; tidak menghitung total,
menampilkan skor/nama operasional, maupun membuat tautan ke fitur yang belum ada.
Keadaan tidak tersedia ditampilkan secara eksplisit, tanpa angka nol atau dummy.
Ringkasan operasional terblokir sampai kebijakan baca tersendiri disepakati dan
diimplementasikan dengan scope kegiatan yang tepat.

Tidak ada perubahan model/schema/migration, kebijakan otorisasi, alur bisnis,
Laravel, FastAPI, kamera, AI, scoring, dependency, provisioning akun atau database
operasional. Tidak ada seed/demo data dalam halaman operasional.

## Verifikasi terbatas

Dari `src/django-app`, gunakan Python `.venv/Scripts/python.exe`, dotenv nonaktif,
bytecode nonaktif, dan settings test eksplisit. Tes hanya memakai SQLite in-memory.

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& .venv/Scripts/python.exe -B -X utf8 manage.py test config.tests.test_ui config.tests.test_foundation accounts.tests.test_users --settings=config.settings.test --noinput
& .venv/Scripts/python.exe -B -X utf8 manage.py check --settings=config.settings.test
& .venv/Scripts/python.exe -B -X utf8 manage.py makemigrations --check --dry-run --settings=config.settings.test
git diff --check
```

Tes UI mencakup normalisasi login, gagal/inaktif, redirect aman, CSRF login/logout,
logout GET ditolak, logout berhasil, akses anonim/inaktif, escape output, respons
tanpa cache, dan larangan query operasional pada pengguna biasa/staff/superuser
meski memiliki permission global dan grant aktif. Fixture sintetis hanya di tes.

Smoke test visual manual pada database development khusus yang sudah tersedia:
login, dashboard, logout; periksa desktop/mobile 360px, zoom, dan navigasi keyboard.
Serving static production tetap memerlukan collectstatic dan konfigurasi web
server existing. Tidak perlu migrasi baru untuk iterasi ini.

Hasil implementasi: **37 focused tests lulus**, system check tanpa masalah,
migration drift `No changes detected`, dan diff check bersih. Tidak menjalankan
server/background terminal, migrasi database operasional, commit atau push.
Tampilan browser dan integrasi PostgreSQL belum diuji pada iterasi ini; verifikasi
template/form dan penemuan static asset dilakukan lewat Django test client.
