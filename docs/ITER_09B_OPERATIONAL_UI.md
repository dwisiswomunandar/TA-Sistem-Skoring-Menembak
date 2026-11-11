# ITER-09B — Operational UI: Activities & Participants

Baseline: `feat/django-migration` pada `5bf40e5`.

## Halaman dan perilaku

- `/activities/`: daftar kegiatan yang mempunyai sesi dalam penugasan pengguna
  dan memenuhi kebijakan baca di bawah. Nama, tanggal latihan dan status dari DB.
- `/activities/<activity_id>/`: detail kegiatan dan daftar partisipasi yang
  mempunyai sesi dalam scope yang sama. Nama peserta, nomor peserta **pada
  kegiatan tersebut**, status pendaftaran. Bukan direktori seluruh peserta.
- `/activities/<activity_id>/participants/<participation_id>/sessions/`: parameter
  sesi yang ditugaskan kepada pengguna untuk partisipasi/kegiatan tersebut:
  nomor sesi, senjata, posisi, jarak dan kaliber. Tidak menampilkan assessment,
  status hasil, skor, foto, audit atau evidence.
- Layout Tactical Command Center existing, navigasi aktif, breadcrumb, tabel
  dengan scroll horizontal untuk layar kecil, empty state dan pagination 20 baris.
  Semua link mempertahankan konteks kegiatan/partisipasi dari server.
- Semua halaman wajib login, GET/HEAD saja, `never_cache`; login/logout/CSRF dan
  pembatasan Django Admin existing tidak berubah. Dashboard akun tetap tanpa
  query/statistik operasional; sidebar menghubungkan halaman Kegiatan.

## Kebijakan baca minimal

Iterasi ini secara eksplisit menetapkan proyeksi baca terbatas atas konteks kerja
yang sudah dilindungi otorisasi draft existing. Bukan penggunaan
`draft_sessions` sebagai izin baca umum. Tidak membuat role organisasi atau
permission/grant baru dan tidak mengubah otorisasi command assessment.

Sesi terlihat hanya bila **seluruh** syarat berikut terpenuhi pada data DB saat
request diproses:

1. Actor adalah User hasil autentikasi server yang tersimpan dan masih aktif.
   Helper `authenticated_user` existing memvalidasi dan memuat ulang actor.
2. User memiliki permission `assessments.change_assessment` yang benar-benar
   diberikan secara langsung atau lewat group. Resolusi memakai content type
   `assessments.assessment`, bukan codename saja, dan tidak memakai permission
   cache pada actor. `is_superuser` tidak menggantikan permission eksplisit ini.
3. Ada `ActivityPermissionGrant` aktif untuk user, permission yang sama dan
   kegiatan dari partisipasi sesi tersebut.
4. `TrainingSession.assigned_assessor_id` sama dengan ID user.

`is_staff`, `is_superuser`, `created_by`, permission tanpa grant, grant tanpa
permission, maupun assignment saja tidak memberikan akses. Grant/global/group
permission/assignment yang dicabut berlaku pada request berikutnya. Tidak ada
API pemberian izin, perubahan grant atau auto-provisioning dalam UI ini.

Daftar kegiatan adalah proyeksi sesi yang lolos filter tersebut. Daftar
partisipasi juga diturunkan dari sesi itu, **bukan** seluruh partisipasi kegiatan
yang terlihat. Detail selalu dicari pada queryset tersaring. Route sesi memakai
PK `ActivityParticipation`, bukan PK identitas global `Participant`, dan kembali
memeriksa relasi activity + participation + assignment. Satu orang dalam dua
kegiatan tidak menggabungkan keanggotaan/sesinya. ID tidak dikenal maupun di luar
scope menghasilkan 404 yang sama; querystring tidak dapat mengganti actor/scope.

Query memilih hanya field tampilan yang diperlukan. Tidak membaca identitas
global/akun peserta, nomor identitas, kode masuk kegiatan, informasi petugas lain,
tabel assessment atau agregat lintas kegiatan. Pagination/count juga berada
dalam scope yang sama; joins/subquery menghindari duplikasi dan N+1 peserta.
Teks database tetap memakai autoescape Django.

## Batas dan keputusan lanjutan

Tidak ada blocker untuk worklist terbatas ini. Akses baca yang lebih luas tetap
**default-deny**, termasuk seluruh peserta dalam kegiatan, peserta tanpa sesi,
kegiatan tanpa penugasan, statistik dashboard, hasil penilaian dan file evidence.
Kebutuhan pengelola untuk melihat data tersebut memerlukan kebijakan tersendiri;
status staff/superuser/creator tidak digunakan untuk mengasumsikan kewenangan itu.

Tidak ada CRUD kegiatan/peserta/sesi, kamera, AI, scoring, verify/lock atau PDF.
Model/schema, migration, dependency, backend assessment dan Django Admin tidak
diubah. Tidak ada mock/seed data operasional; fixture sintetis hanya ada di tes.

## File implementasi

- `src/django-app/activities/{read_access,views,urls,test_ui}.py`
- `src/django-app/config/urls.py`
- `src/django-app/templates/activities/{activity_list,activity_detail,participant_sessions}.html`
- `src/django-app/templates/ui/{layout,pagination}.html`
- `src/django-app/static/ui/app.css`
- `src/django-app/README.md` dan dokumen ini.

## Verifikasi

Semua management command memakai `.venv/Scripts/python.exe -B -X utf8`,
`PYTHON_DOTENV_DISABLED=1`, `PYTHONDONTWRITEBYTECODE=1`, dan
`--settings=config.settings.test`. Database adalah SQLite in-memory, bukan
database operasional. Perintah tes dijalankan foreground dengan timeout terbatas;
tidak menjalankan server. Smoke test semua route memakai Django test client.

Focused suite: `activities.test_ui config.tests.test_ui activities.test_authorization`.
Mencakup navigasi sah, akses anonim/inaktif, seluruh kombinasi permission/grant/
assignment, flag staff/superuser, pencabutan akses, IDOR lintas kegiatan dan
partisipasi, identitas peserta lintas kegiatan, empty state, pagination, escape
teks, metode HTTP/cache, field sensitif, tanpa query assessment/penulisan serta
batas jumlah query untuk peserta.

Tes otorisasi existing memerlukan direktori temporer untuk evidence; sandbox
read-only tidak menyediakannya, sehingga tes tersebut dijalankan di luar sandbox
dengan izin. Tidak mengubah test settings atau melemahkan pengujian untuk melewati
pembatasan filesystem. Verifikasi visual browser dan PostgreSQL tidak termasuk
klaim hasil Django test client.

Hasil akhir:

- **56 focused tests lulus** setelah kebutuhan direktori temporer dipenuhi.
- **746 tests full regression lulus**, satu kali, 130,987 detik (timeout 180 detik).
  Termasuk tes UI terbaru, autentikasi, otorisasi dan batas Django Admin existing.
- Django system check: tidak ada masalah, tidak ada check yang disembunyikan.
- Migration drift: `No changes detected`.
- `git diff --check`, sintaks Python serta whitespace file baru/berubah: bersih.
- Tidak menggunakan database operasional, server persisten, background terminal,
  commit atau push. Siap ditinjau dan di-commit untuk scope baca terbatas ITER-09B.
