# Fondasi Django ? TA Sistem Skoring Menembak

Implementasi baru berjalan terpisah dari `../web-app` (Laravel) dan
`../ai-engine` (FastAPI). Python yang diverifikasi: **3.13.5**, Django **5.2.18 LTS**.
Semua dependensi dipasang ke `.venv`, dengan versi terpin di `requirements.txt`.

```text
django-app/
??? manage.py
??? requirements.txt
??? .env.example
??? config/
?   ??? settings/{base,development,production,test}.py
?   ??? urls.py, views.py, asgi.py, wsgi.py
?   ??? tests/
??? accounts/          # User berbasis email, admin, forms, migrasi awal, tes
??? activities/        # App kosong; model domain ditunda
??? assessments/       # App kosong; model domain ditunda
```

## Persiapan dan pemeriksaan tanpa PostgreSQL

PowerShell, dari root repository:

```powershell
Set-Location src/django-app
py -3.13 -m venv .venv
& .venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
& .venv/Scripts/python.exe manage.py check
& .venv/Scripts/python.exe manage.py test --settings=config.settings.test
& .venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.settings.test
```

Salin `.env.example` hanya ketika `.env` belum ada. Aplikasi hanya membaca `.env`
di direktori ini, bukan `.env` root atau aplikasi lama. Environment proses lebih
diutamakan daripada isi `.env`. Jangan masukkan `.env` atau kredensial ke Git.

## Pemilihan settings

Pilih modul melalui environment proses `DJANGO_SETTINGS_MODULE` atau opsi
`--settings` pada management command. Jangan menaruh pemilih modul itu dalam
`.env`: berkas tersebut baru dimuat setelah Django memilih settings.

Urutan prioritas management command adalah `--settings`, environment proses,
lalu default development. WSGI/ASGI menggunakan environment proses atau default
production; opsi `--settings` hanya berlaku untuk management command.

Contoh PowerShell menggunakan environment proses:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'config.settings.test'
& .venv/Scripts/python.exe manage.py check
Remove-Item Env:DJANGO_SETTINGS_MODULE
```

Untuk memilih konfigurasi pada satu command, termasuk menimpa pilihan environment:

```powershell
& .venv/Scripts/python.exe manage.py check --settings=config.settings.test
```

Ketika menjalankan tes tanpa membaca `.env`, set
`PYTHON_DOTENV_DISABLED=1` pada environment proses. Pemilihan settings test tetap
harus eksplisit; menonaktifkan dotenv tidak memilih settings secara otomatis.

`manage.py` menggunakan `config.settings.development` secara default. Tidak ada
koneksi PostgreSQL yang diperlukan untuk `check`. Tes menggunakan SQLite dalam
memori melalui **settings test eksplisit**, termasuk penerapan migrasi hanya di
database tes sementara. Tidak ada fallback SQLite pada development/production.
Hasher MD5 hanya dipakai oleh settings test; development/production memakai
hasher aman bawaan Django. Tes tidak membuktikan kompatibilitas nyata PostgreSQL.

## Menjalankan dengan PostgreSQL baru

Server PostgreSQL **tidak disediakan atau diasumsikan sudah tersedia**. Siapkan
database baru beserta user khusus Django, lalu isi `POSTGRES_*` pada `.env`.
Gunakan PostgreSQL 14+ sesuai dukungan Django 5.2. Jangan gunakan database Laravel,
akun database lama, atau salinan produksi untuk langkah berikut.

Setelah tujuan koneksi database baru dikonfirmasi, jalankan secara manual:

```powershell
& .venv/Scripts/python.exe manage.py migrate --plan
& .venv/Scripts/python.exe manage.py migrate
& .venv/Scripts/python.exe manage.py createsuperuser
& .venv/Scripts/python.exe manage.py runserver 127.0.0.1:8002
```

Port 8002 memisahkan layanan dari port engine lama. `/health/` adalah liveness
JSON tanpa query database, **bukan pemeriksaan kesiapan database**. `/admin/`
menggunakan login email dan membutuhkan database baru yang telah dimigrasikan.
Root `/` belum memiliki UI. Jangan menjalankan server dengan settings test.

`AUTH_USER_MODEL = "accounts.User"` sudah ditetapkan sebelum migrasi awal.
`accounts/migrations/0001_initial.py` hanya membuat identitas autentikasi;
email dinormalisasi ke huruf kecil, unik tanpa membedakan kapital, dan password
diproses melalui Django. `is_staff`/`is_superuser` dan permissions Django tersedia;
role operasional dan 14 model domain belum diimplementasikan.

Autentikasi sinkron dan async menggunakan normalisasi email yang sama: kapitalisasi
serta whitespace di awal/akhir tidak mengubah identitas akun. Validasi format
email dan kebijakan password diterapkan oleh form akun. Manager tetap merupakan
API ORM yang tidak otomatis menjalankan seluruh validasi form.

Tes regresi mencakup kedua jalur autentikasi, form pembuatan/perubahan akun,
pemilihan settings lewat entry point management/WSGI/ASGI, serta penolakan
production ketika variabel wajib tidak tersedia. Subprocess tes bootstrap
menonaktifkan dotenv dan menolak pembukaan koneksi database.

## Konfigurasi production

Gunakan `DJANGO_SETTINGS_MODULE=config.settings.production` pada environment
deployment atau `--settings=config.settings.production` untuk management command.
WSGI/ASGI memilih production secara default; jangan set development pada deployment.

Production memerlukan `DJANGO_SECRET_KEY` acak yang kuat (minimal 50 karakter,
bukan nilai contoh), `DJANGO_ALLOWED_HOSTS` eksplisit, serta `POSTGRES_DB`,
`POSTGRES_USER`, dan `POSTGRES_HOST`. Sediakan password melalui environment/secret
store sesuai autentikasi PostgreSQL. Buat secret lokal dengan:

```powershell
& .venv/Scripts/python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
& .venv/Scripts/python.exe manage.py check --deploy --settings=config.settings.production
& .venv/Scripts/python.exe manage.py collectstatic --noinput --settings=config.settings.production
```

Simpan secret hasil generator ke secret store, jangan ke repository. Untuk TLS
database remote, atur `POSTGRES_SSLMODE=verify-full` dan sertifikat PostgreSQL
yang sesuai. Nilai `prefer` pada contoh ditujukan untuk development.

HTTPS redirect dan secure cookies aktif pada production, DEBUG selalu mati,
HSTS mulai dari 3600 detik. `includeSubDomains` dan preload sengaja belum aktif:
verifikasi seluruh domain/subdomain sebelum mengaktifkannya. `check --deploy`
akan melaporkan rekomendasi keduanya (W005/W021), tanpa disembunyikan.
Jika memakai reverse proxy untuk terminasi TLS, konfigurasi kepercayaan header
HTTPS hanya setelah memastikan proxy menghapus header klien yang dipalsukan;
konfigurasi proxy/deployment belum termasuk fondasi ini.

Static files terkumpul di `staticfiles/`; web server production harus menyajikan
direktori itu. `media/` disiapkan sebagai lokasi runtime tanpa endpoint upload
atau penyajian publik otomatis. Logging diarahkan ke console melalui
`DJANGO_LOG_LEVEL`, tanpa menyimpan password atau isi environment. Deployment
WSGI/ASGI, reverse proxy, dan serving static/media belum diintegrasikan;
`runserver` hanya untuk development.

## Batas implementasi dan pekerjaan lanjutan

Tidak ada perubahan kode Laravel/FastAPI, integrasi YOLO26s, dashboard, import data
lama, atau migrasi terhadap database lama. Dark Mode / Modern Command Center
ditunda sampai tahap UI. Audit terpisah dan Database Design Baseline v1.1 belum
ditemukan dalam repository pada implementasi ini; SRS v0.1 tidak diperlakukan
sebagai penggantinya. Kebutuhan lapangan belum dibakukan menjadi enum/constraint.

Pekerjaan berikutnya: verifikasi dan versioning Database Design Baseline v1.1,
kemudian petakan relasi identitas pengguna dan `activities` sebelum membuat
migrasi domain pertama. Uji integrasi PostgreSQL harus dilakukan pada database
khusus yang baru setelah server tersedia.

Rujukan: [Django 5.2 LTS](https://www.djangoproject.com/download/),
[custom User](https://docs.djangoproject.com/en/5.2/topics/auth/customizing/),
[PostgreSQL](https://docs.djangoproject.com/en/5.2/ref/databases/#postgresql-notes),
[deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).
