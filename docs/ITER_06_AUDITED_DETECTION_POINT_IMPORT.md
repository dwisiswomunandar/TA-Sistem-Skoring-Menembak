# ITER-06 - Audited DetectionRun to ShotPoint Import

Baseline: `feat/django-migration` pada `7697839`. Menyelesaikan AUDIT-003 F02
pada lapisan service Django. Tidak ada perubahan model, migration, dependency,
UI, endpoint, detection engine, atau scoring engine.

## Kontrak command

`assessments.detection_import_services.import_detection_points` menerima
keyword-only actor, session_id, assessment_id, detection_run_id,
expected_edit_version, operation_id, reason, dan optional using. Actor berasal
dari autentikasi Django tepercaya; ID aktor dari payload bukan autentikasi.
Koordinat, skor, source, serta provenance tidak diterima dari pemanggil.
Hasil memakai DraftReceipt existing; point_id=None karena operasi berupa batch.
Jumlah dan ID titik tersedia pada metadata audit detection_import.

Gunakan otorisasi shotpoint.added existing: global change_assessment DAN
add_shotpoint, grant aktif keduanya pada kegiatan, serta assigned_assessor.
Pemeriksaan diulang di dalam transaksi dan selalu mendahului replay receipt.
Assessment harus draft terbaru, versinya cocok, snapshot sesi tidak drift,
dan sudah memilih foto serta run succeeded yang sama. Pemilihan evidence tetap
melalui command existing; impor tidak memilih, mengganti, atau mereset evidence.

## Konversi dan provenance

Setiap bbox tervalidasi menghasilkan satu titik tengah dalam piksel analisis.
Versi konversi: analysis-bbox-midpoint-half-even-6dp-v1. Angka ditafsirkan melalui
representasi desimal, midpoint dihitung sebagai pecahan eksak, lalu dibulatkan
half-even ke enam desimal sesuai kolom ShotPoint, independen dari Decimal context.
Titik hasil pembulatan di luar raster ditolak tanpa clipping. Tidak ada resize,
rotasi EXIF, transformasi perspektif, threshold baru, filter kelas, atau deduplikasi
spasial. Kalibrasi dan scoring tetap merupakan proses terpisah.

Titik menyimpan source=ai, origin_detection_id asli, included=True, score=NULL.
Titik lama dipertahankan; nomor baru mengikuti maksimum nomor yang tersimpan.
Schema-1 dasar tetap didukung. Bila metadata envelope FastAPI tersedia, status,
dimensi, preprocessing, hash input, identitas model/bobot/runtime/adapter dan
parameters harus cocok dengan foto/run tersimpan.

## Transaksi, audit dan idempotensi

Urutan lock: session -> participation -> assessment -> points menurut PK -> photo
-> run -> User -> grants. Seluruh input/run dan titik divalidasi sebelum penulisan.
Satu transaksi memasukkan batch, menghapus cache skor seluruh titik/assessment,
menaikkan versi assessment sekali, dan menambahkan satu audit
assessment.detections_imported. Kegagalan insert, persistence, atau audit
me-rollback seluruh perubahan, termasuk skor dan versi sebelumnya.

INSERT titik memakai bounded internal Model.save setelah validasi penuh dan
penguncian aggregate. Public ShotPoint.save menaikkan versi per titik; batch ini
menaikkan versi sekali. Tidak ditambahkan bypass publik atau pelemahan guard.
UUID/replay, receipt, snapshot, invalidasi dan audit memakai helper existing.
Replay identik mengembalikan receipt historis tanpa memulihkan titik atau
membatalkan skor terbaru. UUID berbeda dengan run yang sudah diimpor ditolak,
termasuk setelah titik dihapus. Marker selesai memakai audit existing, tanpa tabel
baru. Konflik UUID lintas scope ditangani unique constraint dan rollback existing.

Deteksi kosong yang succeeded mengimpor nol titik dan mencatat satu audit/langkah
versi. Tidak dianggap inferensi gagal, sepuluh tembakan meleset, atau skor nol.
Audit mencatat versi konversi, count/ID, provenance run/foto dan hash hasil mentah
serta parameters; tidak menyalin raw parameters, secret, atau storage path.

## Verifikasi aktual - 11 November 2026

- Focused run awal: 26 tes lulus; setelah penguatan envelope/replay: 28 tes lulus.
- Full regression Django dijalankan satu kali setelah stabil: 641 tes lulus
  dalam 105.368 detik (613 baseline + 28 baru).
- Django system check: tanpa masalah.
- Migration drift check: No changes detected.
- Pemeriksaan whitespace/syntax file baru: lulus; git diff --check: lulus.
- Seluruh tes memakai config.settings.test, SQLite dalam memori, dotenv dan
  bytecode dinonaktifkan, serta foto/output deteksi sintetis.

## Batas verifikasi

Tidak ada layanan AI, bobot nyata, atau database operasional yang diakses.
Provenance struktural tidak membuktikan identitas/akurasi YOLO26s maupun proses
pembuatan run historis sudah diaudit. Semua deteksi diimpor; makna kelas, kualitas
titik tengah dan aturan lapangan masih membutuhkan verifikasi serta review petugas.
SQL/internal ORM tetap dapat melewati guard aplikasi. Tes SQLite dan simulasi
collision/urutan lock bukan bukti konkurensi PostgreSQL. Sebelum operasional,
uji independent connections untuk impor bersamaan, edit/scoring vs impor,
pencabutan hak dan collision UUID lintas sesi pada PostgreSQL tes khusus.
