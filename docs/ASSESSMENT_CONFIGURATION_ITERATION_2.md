# Konfigurasi assessments - iterasi 2

Sumber utama: [Database Design Baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md)
bagian 3.3 dan acceptance criteria tentang konfigurasi, publikasi, serta histori.
Instruksi pengguna pada iterasi ini memperjelas bahwa skor ring berdekatan
boleh sama: skor harus **tidak meningkat** ke arah luar, bukan turun secara
ketat pada setiap ring. Dokumen baseline asli tidak diubah.

## Cakupan dan representasi

Tiga model konkret ditambahkan pada assessments:

| Model | Atribut dan aturan utama |
|---|---|
| TargetVersion | code, version, name, width_mm, height_mm, status, created_by, published_at, created_at, updated_at |
| TargetRing | target_version FK PROTECT, ring_number, outer_radius_mm, score, timestamps |
| ScoringRuleVersion | code, version, status, method inward, algorithm_version, boundary_policy, numeric_precision, created_by, published_at, timestamps |

TargetVersion dan ScoringRuleVersion berbagi abstract VersionedConfiguration;
abstract base ini tidak membuat tabel/model domain tambahan. FK pembuat memakai
AUTH_USER_MODEL dan PROTECT. Unique (code, version) berlaku per jenis konfigurasi;
TargetRing mempunyai unique (target_version, ring_number). FK ring tidak diberi
indeks duplikat karena unique komposit sudah mencakup prefix target_version.

Status, versi minimal satu, kode/nama tidak kosong, metadata publikasi,
dimensi/radius positif, serta nomor/skor ring minimal satu ditegakkan oleh
constraint DB yang relevan. Skor 0 untuk titik di luar ring bukan TargetRing
bernilai 0 dan belum diimplementasikan pada iterasi ini.

Dimensi dan radius memakai DecimalField (12,4) dalam milimeter sebagai batas
penyimpanan teknis, bukan penetapan ukuran target, radius, toleransi, atau
standar lapangan. Tidak ada default dimensi/radius, seed target, atau ukuran
fisik resmi. Geometri yang direpresentasikan adalah lingkaran konsentris di
pusat bidang persegi panjang; offset pusat, target nonlingkaran, ketebalan garis,
dan toleransi harus dirancang/diamati sebelum diperlukan oleh engine.

numeric_precision dan boundary_policy adalah metadata deskriptif eksplisit;
algorithm_version merupakan identitas kontrak algoritma, bukan import path
yang dijalankan. Metadata boleh belum lengkap pada draft, wajib nonblank saat
publikasi. Tidak ada evaluator formula, registry algoritma, kalkulasi skor,
atau validasi bahwa versi engine tersebut sudah terpasang. Makna matematis
kebijakan batas/rounding/toleransi masih memerlukan pengesahan dan kontrak
terstruktur sebelum engine digunakan; aplikasi tidak menciptakan nilai default
operasional untuk keputusan yang belum tersedia.

## Publikasi dan histori

1. Buat/simpan draft menggunakan create() atau save() penuh.
2. Untuk target, tambahkan ring lewat instance save()/create(). Draft boleh
   sementara mempunyai urutan/geometri ring yang belum siap.
3. publish() memvalidasi konfigurasi dalam transaksi dan menetapkan published_at
   dari waktu server. Pengubahan status melalui save() tetap menjalankan validasi.
4. Publikasi target memerlukan minimal satu ring; urut ring dari pusat menurut
   ring_number, radius bertambah ketat, dan skor tidak meningkat ke arah luar.
   Nomor ring tidak dipaksa berurutan tanpa celah. Diameter setiap lingkaran
   harus muat pada kedua dimensi bidang; tidak ada margin fisik tersembunyi.
5. Payload published/retired tidak dapat diedit atau dihapus melalui API model.
   Perubahan payload memerlukan record baru dengan versi berbeda.
6. retire() hanya menerima published, mengambil data tersimpan yang dikunci,
   dan mengubah status/updated_at; payload dan waktu publikasi tetap sama.
   Retirement merupakan satu transisi lifecycle terkontrol, bukan izin mengedit
   payload frozen. Retired tidak dapat diterbitkan ulang.

Target dan ring memakai penguncian parent yang sama. Pemindahan ring mengunci
kedua parent menurut PK dan menolak parent sumber maupun tujuan yang frozen.
Objek draft yang stale tidak dapat menimpa record yang sudah dipublikasikan.
Semua klaim efektivitas row locking/konkurensi tetap menunggu pengujian PostgreSQL.

## Batas perlindungan ORM

Manager publik/default/base dan related manager memakai ConfigurationQuerySet.
update(), delete() pada queryset, bulk_create(), dan bulk_update() ditolak untuk
ketiga model, termasuk draft. Gunakan operasi instance yang tervalidasi.
Partial save(update_fields=...) juga ditolak agar payload yang divalidasi sama
dengan payload yang disimpan. update_or_create() yang melakukan partial update
bukan jalur perubahan konfigurasi yang didukung. Jalur async yang mendelegasikan
ke operasi tersebut tetap memakai guard yang sama.

Perlindungan ini adalah kebijakan aplikasi, **bukan jaminan immutability DB**.
SQL langsung, raw QuerySet yang sengaja dibangun tanpa manager, API internal
ORM/save_base(), pemanggilan langsung Model.save(), atau historical models
dalam data migration dapat melewati guard. Bulk API aman karena ditolak,
bukan karena Django otomatis memanggil save() untuk setiap record.

Constraint database tetap menolak bentuk data yang tidak valid, tetapi tidak
membandingkan state lama/baru atau memeriksa seluruh urutan ring antarbaris.
Tidak ada trigger DB atau pembatasan privilege khusus dalam iterasi ini.
Akses DB dan API internal harus dibatasi pada kode tepercaya; migrasi data
dan maintenance wajib ditinjau. Test raw QuerySet secara eksplisit menunjukkan
batas tersebut agar dokumentasi tidak mengklaim proteksi absolut.

## Verifikasi

Seluruh tes dijalankan dengan config.settings.test, SQLite dalam memori,
PYTHON_DOTENV_DISABLED=1 dan PYTHONDONTWRITEBYTECODE=1. Tes baru mencakup
publikasi, skor ring sama, geometri, freeze/retirement, ring reparenting,
stale objects, larangan bulk/partial writes, async guards, FK PROTECT,
dan constraint SQL melalui raw QuerySet test yang sengaja melewati manager.

SQLite tidak membuktikan row locking, perlombaan publikasi/perubahan ring,
presisi numeric, batas varchar, dan collation PostgreSQL. DBMS nyata tidak
diakses. Migrasi lama, Laravel/FastAPI, User dan model iterasi 1 dipertahankan.
Tidak ada AI, scoring engine, Assessment, ShotPoint, foto, kalibrasi, audit,
UI, commit, atau push pada iterasi ini.

Rujukan teknis:
[Django bulk operations](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#bulk-create),
[row locking](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update),
[constraints](https://docs.djangoproject.com/en/5.2/ref/models/constraints/).
