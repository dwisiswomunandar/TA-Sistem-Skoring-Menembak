# Bukti foto, deteksi, dan kalibrasi — Iterasi 03

Sumber: [Database Design Baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md),
khususnya §3.4, §4, §6, serta
[batas ORM Iterasi 02](ASSESSMENT_CONFIGURATION_ITERATION_2.md).
Implementasi hanya menambahkan TargetPhoto, DetectionRun, Calibration dan abstract
EvidenceRecord (tanpa tabel tambahan). Seluruh FK menggunakan PROTECT.

## Foto asli dan ruang koordinat

TargetPhoto menyimpan session, storage_key unik, sha256, byte_size, width_px,
height_px, verified_mime_type, uploaded_by, preprocessing_version,
analysis_width_px, analysis_height_px dan created_at. Tidak ada FileField,
ImageField atau properti URL pada model.

Saat insert, save() membaca file yang **sudah ada** melalui alias storage
target_photos. SHA-256 dan jumlah byte dihitung dari seluruh byte asli;
Pillow memeriksa struktur serta mendekode file untuk menentukan MIME dan dimensi.
Metadata yang diberikan pemanggil harus cocok dengan hasil pemeriksaan.
Ekstensi nama dan Content-Type pemanggil tidak dipercaya. Byte asli tidak
dirotasi, diubah ukurannya, ditranscode atau ditimpa.

Allowlist teknis awal: gambar diam JPEG dan PNG; gambar rusak, file hilang,
format lain, APNG multiframe, serta decompression bomb ditolak. Allowlist ini
bukan pengesahan kebijakan upload lapangan. Pillow dipin ke 13.0.0.
Rujukan: [pemeriksaan dan decoding Pillow](https://pillow.readthedocs.io/en/stable/reference/Image.html).

PrivateTargetPhotoStorage memakai PRIVATE_TARGET_PHOTO_ROOT, secara default
src/django-app/private-media/, terpisah dari MEDIA_ROOT dan diabaikan Git.
Key harus relatif di bawah originals/, tanpa URL, traversal, backslash,
query string atau percent encoding. Backend menolak URL, penimpaan, pemakaian
ulang key, pemendekan nama, dan penghapusan rutin; collision tidak menghasilkan
nama pengganti. Tidak ada endpoint upload/download atau route penyajian file.
Web server deployment wajib menjaga direktori ini tetap privat.

Foto dan seluruh metadatanya immutable sejak insert. File dengan byte sama
boleh tercatat pada key berbeda; digest tidak dipakai sebagai identitas peserta
atau sebagai unique global. File tidak dihapus bila validasi/insert DB gagal.
Pembersihan file yatim dan retensi harus memakai proses terpisah yang memeriksa
referensi histori; transaksi filesystem dan database bukan satu transaksi atomik.

width_px/height_px berasal dari file asli. analysis_width_px/analysis_height_px
wajib diberikan eksplisit bersama preprocessing_version, tanpa default resize,
rotasi atau orientasi EXIF. Ruang koordinat analisis inilah yang dirujuk run
dan kalibrasi melalui FK photo yang sama. Iterasi ini **tidak menjalankan atau
membuktikan kebenaran pipeline preprocessing**; produsen aset analisis nantinya
harus menyediakan kontrak berversi yang cocok dengan dimensi tersebut.

## DetectionRun

Metadata: photo, triggered_by, status, model_name, weights_sha256,
runtime_version, adapter_version, parameters, raw_result, started_at,
finished_at, failure_code, created_at, updated_at. SHA bobot hanya divalidasi
formatnya; tidak ada bobot yang dibaca/dijalankan. Identitas model tidak diisi
default ke YOLO atau bobot historis.

Transisi melalui instance save() penuh:

| State tersimpan | State tujuan | Syarat |
|---|---|---|
| baru | pending | Identitas model/bobot/runtime/adapter dan parameters eksplisit |
| pending | running | started_at ditetapkan server |
| pending | failed | Gagal sebelum eksekusi; finished_at server, started_at tetap NULL |
| running | succeeded | Hasil terstruktur wajib; finished_at server |
| running | failed | failure_code nonblank; hasil NULL; finished_at server |

pending/running tidak boleh memiliki hasil atau failure_code. Waktu selesai
tidak mendahului mulai. Tidak ada retry pada record selesai: buat run baru.
Photo, pemicu, identitas model, parameters dan waktu mulai tidak dapat diganti
setelah insert, sehingga hasil terlambat tidak bisa dipindahkan ke foto lain.
Run succeeded/failed tidak dapat diedit atau dihapus; pending/running boleh
dihapus melalui instance, tanpa menghapus file/foto. Indeks (photo, created_at)
tersedia tanpa indeks FK prefix duplikat.

parameters harus objek JSON eksplisit, boleh {} bila memang demikian;
tidak ada confidence threshold, ukuran input atau parameter inferensi default.
Semua JSON menolak NaN/Infinity, key nonstring dan nilai Python non-JSON.

Kontrak penyimpanan adapter **v1** (bukan klaim bentuk native output YOLO):

- raw_result adalah objek dengan schema_version integer 1 dan detections array.
- Setiap deteksi memuat id string nonblank yang unik dalam run, class_id integer
  nonnegatif, confidence finite dalam [0,1], dan bbox_xyxy berisi empat angka.
- Bounding box memakai piksel analisis, urutan x1,y1,x2,y2, luas positif,
  0 ≤ x1 < x2 ≤ analysis_width_px dan 0 ≤ y1 < y2 ≤ analysis_height_px.
  Batas kanan/bawah merepresentasikan tepi bidang, bukan indeks piksel pusat.
- Metadata tambahan pada objek hasil/deteksi dipertahankan bila JSON valid.
- succeeded dengan detections: [] adalah sukses nol deteksi. failed memakai
  raw_result NULL dan failure_code, sehingga keduanya tidak tertukar.

Adapter produksi harus memetakan keluaran asli ke kontrak ini secara eksplisit.
Tidak ada hasil AI, confidence atau bounding box yang dibuat aplikasi;
semua angka/hasil di tes hanya fixture sintetis.

## Calibration

Metadata: photo, target_version, status, transform_matrix, method_version,
created_by, accepted_by, accepted_at, created_at dan updated_at. Draft boleh
belum mempunyai matriks; setiap matriks yang diberikan harus valid. method_version
wajib eksplisit. Tidak ada matriks identitas, titik acuan atau skala default.

Representasi teknis yang dipilih adalah array JSON 3×3 homogen yang memetakan
piksel gambar analisis ke bidang target dalam mm, pusat target sebagai nol.
Semua elemen harus angka finite, bukan bool/string, dan determinan bukan nol.
Pemeriksaan singularitas menggunakan rasional atas angka tersimpan tanpa
mengarang epsilon/toleransi. Matriks projective diperbolehkan; tidak dipaksa
menjadi affine atau mempunyai elemen kanan bawah 1.

Record dimulai draft. Draft dapat diedit/dihapus, termasuk memilih foto/target,
tetapi pembuat dan waktu pembuatan tetap. draft → accepted memerlukan matriks,
accepted_by valid dan TargetVersion yang **masih published di database**;
target dikunci ketika menerima, accepted_at ditetapkan server. Retired tidak
dapat dipilih untuk penerimaan baru. Retirement setelah acceptance tidak
mengubah histori kalibrasi yang sudah diterima.

accepted immutable dan tidak dapat dihapus. Kebijakan teknis iterasi ini juga
menjadikan rejected sebagai keputusan terminal, tanpa metadata acceptance;
koreksi membuat draft baru. Ini memperjelas lifecycle yang belum dirinci baseline,
bukan keputusan operasional tentang siapa yang boleh menerima/menolak.

Validitas bentuk/invertibilitas **tidak membuktikan kualitas geometri**.
Algoritma fitting, titik acuan, arah sumbu fisik, denominator projective pada
bidang foto, conditioning, residual/error, toleransi dan kriteria penerimaan
lapangan masih harus ditetapkan dan diuji sebelum kalibrasi/scoring produksi.
Tidak ada algoritma kalibrasi atau scoring yang dijalankan di iterasi ini.

## Constraint dan batas proteksi

Constraint DB mencakup unique key, digest SHA-256, ukuran positif, MIME/status
terbatas, metadata lifecycle, urutan waktu run, serta metadata acceptance.
Validasi struktur JSON/matriks dan lintas record dijalankan di aplikasi.
Instance save() menggunakan atomic dan select_for_update pada record tersimpan;
objek stale tidak dapat menimpa/menghapus state terminal atau menghidupkan lagi
record mutable yang sudah dihapus. Tidak ada transaksi inferensi panjang.

Manager publik/default/base dan related manager memakai EvidenceQuerySet:
update(), queryset delete(), bulk_create(), bulk_update(), partial save dan
update_or_create() untuk record yang sudah ada ditolak, termasuk state mutable.
Jalur async memakai guard yang sama. Gunakan create() dan save()/delete() instance
yang memenuhi lifecycle. Tidak ada perubahan pada guard konfigurasi Iterasi 02.

Seperti Iterasi 02, ini **bukan immutability absolut pada database/storage**:
SQL mentah, QuerySet tanpa manager, save_base(), Model.save() langsung,
historical model pada data migration, dan administrator filesystem dapat
melewati guard aplikasi. Tes secara eksplisit menunjukkan batas ini.
Trigger DB, privilege khusus, WORM/object lock belum disediakan.

## Migrasi dan verifikasi

Migrasi tambahan: assessments/0002_photo_detection_calibration.py, bergantung
pada assessments/0001_target_configuration, activities/0001_initial_domains
dan AUTH_USER_MODEL. Tidak ada perubahan atau penghapusan migrasi lama.
Tes migrasi mundur/maju pada SQLite terisolasi memastikan target, ring dan rule
Iterasi 02 tetap tersimpan saat migrasi tambahan diterapkan.

Verifikasi memakai config.settings.test (SQLite dalam memori), dotenv dimatikan,
dan folder foto temporary per tes. Tes mencakup pembacaan byte, MIME spoof/rusak,
key privat, immutability, lifecycle, output kosong/gagal, JSON invalid, matriks,
PROTECT, stale object, bulk/partial/async guard, SQL constraint dan preservasi
data migrasi. PostgreSQL nyata tidak diakses; efektivitas locking, concurrency,
regex/JSON dan perilaku constraint pada PostgreSQL tetap belum terbukti.

Hasil verifikasi pada 30 Oktober 2026: **193 tes lulus** (135 lama + 58 baru),
system check tanpa masalah, makemigrations --check --dry-run melaporkan
No changes detected, migrate --plan valid, pip check tanpa dependensi rusak,
serta git diff --check bersih untuk file terlacak maupun file baru. Seluruh
command Django menggunakan --settings=config.settings.test dan
PYTHON_DOTENV_DISABLED=1. Tes file dijalankan dengan izin akses temporary Windows
karena sandbox menolak subdirektori temporary; database tetap SQLite dalam memori.

Constraint digest memakai anchor akhir string ketat agar newline tambahan
ditolak, merujuk [constraint escape PostgreSQL](https://www.postgresql.org/docs/current/functions-matching.html#POSIX-CONSTRAINT-ESCAPES-TABLE).

Tidak ada perubahan Laravel/FastAPI, endpoint upload, eksekusi YOLO26s,
scoring engine, UI, Assessment, ShotPoint, AuditEvent, commit atau push.

Keputusan berikutnya: kontrak preprocessing/adapter produksi dan asal aset
analisis; metode serta standar mutu kalibrasi; otorisasi aktor (termasuk peserta
tanpa User); batas ukuran upload; retensi/cleanup file yatim; backend privat
deployment dan pengujian PostgreSQL khusus. Registrasi mandiri/audit aktor
non-User yang sudah terbuka pada baseline belum diselesaikan pada iterasi ini.
