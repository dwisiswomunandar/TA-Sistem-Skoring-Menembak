# ITER-09D1 — Secure Operational Context Provisioning

Baseline: `feat/django-migration` pada `9b40560`. Menindaklanjuti audit ITER-09D.

## Hasil dan batas operasi

Service `activities.context_provisioning.provision_operational_context` dan command
interaktif `provision_operational_context` membuat tepat satu Activity baru **draft**,
satu Participant baru **pending**, dan satu ActivityParticipation baru **pending**.
Ketiganya tersimpan bersama satu audit `training_context.provisioned` dalam transaksi.
Tidak memakai ulang atau mengubah objek existing berdasarkan nama/nomor identitas.
UUID baru adalah intent konteks baru; UUID sama adalah retry, bukan update objek.

Tidak ada aktivasi, verifikasi identitas, approval pendaftaran, assignment, sesi,
permission global/group/grant, maupun konfigurasi approval ITER-09C yang diberikan.
Verifier/decision maker dan waktunya tetap null, account peserta tetap null.
Created_by dan registered_by ditentukan dari operator hasil autentikasi server.
Worklist ITER-09B dan pembuatan sesi ITER-09C tetap mengikuti default-deny existing.

Tidak ada perubahan UI/Django Admin, model, migration, dependency, AI/scoring,
kamera, upload atau PDF. Tidak ada data maupun persetujuan operasional nyata
yang diisi pada implementasi ini.

## Otorisasi

Settings bawaan tetap:

```python
OPERATIONAL_CONTEXT_PROVISIONING_ENABLED = False
OPERATIONAL_CONTEXT_PROVISIONING_PLANS = ()
```

Flag saja, request, referensi otorisasi, staff/superuser/group/creator atau password
saja tidak memberikan kewenangan. Diperlukan autentikasi Django berhasil **dan**
kecocokan persis dengan protected deployment plan yang disetujui secara sah.
Service menurunkan actor_id dari autentikasi, bukan menerima caller User/actor ID.
User wajib tersimpan, aktif, berada pada write DB yang sama, dan hash password
hasil autentikasinya masih cocok dengan row yang dimuat ulang di bawah lock.
Protected plan diperiksa kembali sebelum perubahan dan setiap receipt replay.

Setiap plan memiliki field request di bawah ditambah actor_id integer positif.
Flag non-bool, enabled dengan plan kosong, struktur/tipe salah, field berlebih,
ID bool dan operation UUID duplikat ditolak fail-closed. Tidak membaca enable atau
plan dari environment flag atau membentuk approval otomatis dari request.
Referensi hanya metadata penelusuran, bukan bukti kriptografis persetujuan organisasi.

Otorisasi pembuatan data nyata masih harus ditetapkan oleh pihak berwenang:
operator, payload, purpose, UUID, alasan dan referensinya. Sampai tersedia,
service/CLI menolak operasi. Kebijakan terpisah untuk aktivasi/verifikasi/approval
dan provisioning permission belum ditambahkan. Tidak ada field status/bypass
yang bisa mengaktifkan keputusan tersebut melalui service ini.

## Kontrak data dan receipt

Keyword-only: actor_email, actor_password, data, optional using. Data dict harus
memiliki field persis berikut; nested dict juga memakai field persis:

| Field | Kontrak |
|---|---|
| operation_id | UUID eksplisit; dipertahankan untuk retry |
| purpose | development, simulation, atau operational |
| activity | name dan training_date (YYYY-MM-DD / Python date) |
| participant | full_name dan identity_number |
| participation | participant_identifier |
| reason | nonblank, maksimal 2000 karakter |
| authorization_reference | nonblank, maksimal 255 karakter |

Nama wajib nonblank dan maksimal 255. Nomor identitas/nomor peserta opsional,
maksimal 64; null/blank dinormalisasi mengikuti model, tanpa nomor pengganti.
Whitespace luar, UUID dan date dikanonisasi sebelum plan comparison/fingerprint.
Tipe, Unicode, NUL, tanggal dan batas field divalidasi sebelum penulisan. FK, PK,
status, timestamp dan actor tidak menerima mass assignment. Field validation,
full_clean/save serta seluruh constraint model existing tetap dijalankan.

Purpose disimpan pada receipt/audit dan terikat fingerprint. Development/simulation
tidak dikonversi menjadi persetujuan operasional. Mengganti purpose pada UUID sama
ditolak. Label operational sendiri juga bukan otorisasi keputusan sensitif:
hasilnya tetap draft/pending/pending untuk seluruh purpose.

Receipt immutable memuat operation_id, audit_event_id, actor_id, activity_id,
participant_id, participation_id, purpose, status=prepared_pending_decisions,
access_decision=authorized_for_context_data_only, dan not_granted_by_operation.
`receipt.as_dict()` siap JSON. Receipt menyatakan status/keputusan **saat operasi**,
bukan keadaan live atau seluruh kewenangan user saat ini. Password, nama dan nomor
identitas tidak otomatis dimasukkan pada receipt maupun snapshot. Alasan/referensi
dari operator tetap harus bebas kredensial.

## Transaksi, audit, histori dan race

Lock User menyerialkan request patuh dari operator yang sama. Seluruh row domain
setelah lock adalah row baru milik transaksi ini; tidak mengambil lock domain
existing sesudah security lock. UUID audit unik melindungi collision lintas command.
Audit memakai scope participation saja (scope XOR), version null -> 1, sehingga
historical guard relasi activity/participant dan PROTECT existing ikut berlaku.
Audit append-only mencatat ID/status awal, actor, purpose, keputusan terbatas,
referensi, fingerprint dan receipt. Before menyatakan ketiga objek belum ada.

Gagal participant/participation/audit me-rollback seluruh objek baru. Replay identik
mengembalikan receipt historis tanpa mengganti status, timestamp atau audit.
UUID yang sama tidak dapat direbind ke actor/payload lain. Collision setelah lookup
awal memicu rollback penuh lalu maksimum satu retry, selalu memeriksa kewenangan
saat ini sebelum mengembalikan receipt. Tidak memakai update/bulk/raw SQL bypass.

Konkurensi PostgreSQL nyata belum diuji. Tes SQLite membuktikan alur, constraint,
rollback dan collision tersimulasi, bukan sertifikasi konkurensi PostgreSQL.

## Penggunaan development setelah diotorisasi

Langkah berikut **tidak dijalankan selama implementasi**. Pengelola terlebih dahulu
memastikan DB development yang diizinkan, akun operator aktif yang sudah ada, serta
otorisasi nyata khusus pembuatan data. Penyiapan keputusan sensitif tetap terpisah.

1. Tetapkan protected settings module di luar version control. Setelah review,
   isi plan berikut dengan nilai yang disetujui. Simbol berikut bukan seed/approval:

   ```python
   OPERATIONAL_CONTEXT_PROVISIONING_ENABLED = True
   OPERATIONAL_CONTEXT_PROVISIONING_PLANS = ({
       "actor_id": REVIEWED_ACTOR_ID,
       "operation_id": REVIEWED_OPERATION_UUID,
       "purpose": "development",
       "activity": {"name": REVIEWED_ACTIVITY_NAME, "training_date": REVIEWED_DATE_ISO},
       "participant": {"full_name": REVIEWED_PERSON_NAME, "identity_number": REVIEWED_IDENTITY_OR_EMPTY},
       "participation": {"participant_identifier": REVIEWED_IDENTIFIER_OR_EMPTY},
       "reason": REVIEWED_DATA_CREATION_REASON,
       "authorization_reference": REAL_DATA_PROVISIONING_AUTHORIZATION_REFERENCE,
   },)
   ```

2. Buat JSON request lokal terlindungi dengan field/nilai yang sama **tanpa actor_id**,
   password atau field tambahan. Plan ditetapkan independen setelah review;
   file request bukan sumber approval otomatis.
3. Dari src/django-app pada terminal interaktif, jalankan:

   ```powershell
   & .venv/Scripts/python.exe -B -X utf8 manage.py provision_operational_context --settings=config.settings.authorized_context --actor-email '<email-operator-yang-disetujui>' --request-file 'C:\Protected\context-request.json'
   ```

   `config.settings.authorized_context` adalah contoh modul lokal terproteksi yang
   harus disiapkan pengelola; modul enabled tersebut tidak dibuat oleh iterasi ini.
   Password diminta lewat getpass, tanpa argumen password, environment atau pipe.
4. Simpan JSON receipt. UUID/request sama dapat diulang selama plan masih berlaku;
   nonaktifkan plan setelah kebutuhan retry selesai. Objek tetap draft/pending/pending.

Pemanggilan dari adapter deployment tepercaya menggunakan service yang sama:

```python
from getpass import getpass
from activities.context_provisioning import provision_operational_context

receipt = provision_operational_context(
    actor_email=REVIEWED_ACTOR_EMAIL,
    actor_password=getpass("Provisioning actor password: "),
    data=REVIEWED_REQUEST_WITHOUT_ACTOR_ID,
)
```

CLI hanya menambahkan --actor-email dan --request-file pada opsi management Django.
UTF-8 JSON dibatasi 64 KiB; duplicate keys, NaN/Infinity dan malformed/deep input
ditolak. Error input/config/auth generik tanpa isi plan/password; output sukses
hanya receipt. Tidak ada server/background process atau mode simulasi bypass.

## Pengujian

Semua command memakai Python .venv, -B -X utf8, PYTHON_DOTENV_DISABLED=1,
PYTHONDONTWRITEBYTECODE=1, --settings=config.settings.test --noinput.
Database hanya SQLite in-memory. Focused suite: activities.test_context_provisioning,
activities.test_bootstrap, activities.test_session_creation. Fixture/persetujuan
sintetis hanya di tes, tidak diterapkan pada development/production.

Tes mencakup validasi/mass assignment, unauthorized/revoked access, purpose tanpa
keputusan sensitif, isolasi konteks/security state, audit/receipt, rollback parsial,
read-only UUID replay, collision tersimulasi, constraint SQLite nyata (CHECK/UNIQUE),
historical guard/PROTECT, CLI interaktif, bounded JSON dan output minim data.

Hasil aktual: **89 focused tests lulus** dan **802 full regression tests lulus**
(satu kali, 121,412 detik, timeout 180 detik). System check tanpa masalah; migration
drift `No changes detected`; diff check, sintaks Python dan whitespace keenam file
baru/berubah bersih. Audit strict JSON existing tetap dipertahankan; status enum
pada snapshot memakai nilai string primitif.

Full regression memakai izin file temporer tes existing di luar sandbox read-only,
tetap dengan SQLite in-memory dan dotenv nonaktif. Tidak mengakses DB development/
production, menjalankan server, membuat plan enabled nyata, commit atau push.
