# ITER-07 - Kalibrasi Landmark Terukur dan Integrasi Scoring

Baseline: feat/django-migration pada 7b8806a. Implementasi teknis F03 memakai
kalibrasi berbantuan petugas, bukan kalibrasi otomatis berbasis citra.
Tidak ada model/migration, dependency, AI engine, UI atau endpoint baru.

## Data fisik belum tersedia

Repositori belum menyediakan profil landmark fisik terverifikasi yang cukup.
TARGET_CALIBRATION_PROFILES secara default kosong; command gagal tertutup jika
profil acuan belum disediakan. Tidak ada ukuran target, toleransi, pusat kertas,
landmark atau matriks identitas yang dijadikan default operasional.
TargetVersion merepresentasikan bidang scoring berpusat pada ring menurut baseline;
dimensi bidang ini tidak membuktikan bahwa pusat ring sama dengan pusat kertas.

Sebelum penggunaan lapangan, tentukan dan verifikasi:
1. Versi target published beserta dimensi/ring yang benar secara fisik.
2. Empat landmark terlihat pada bidang yang sama, koordinat mm-nya diukur relatif
   pusat ring, diurutkan sepanjang batas quadrilateral; bukan pusat kertas asumtif.
3. Sedikitnya satu landmark pemeriksaan independen yang tidak dipakai fitting.
   Beberapa titik uji yang tersebar diperlukan untuk evaluasi akurasi lapangan.
4. Referensi pengukuran/penanggung jawab/versi serta batas error mm yang disetujui.
5. Batas minimum rasio pemisahan geometri dan margin denominator projective.
   Angka kualitas dalam tes adalah kebijakan sintetis, bukan standar lapangan.

## Profil acuan yang dikonfigurasi server

Protected settings TARGET_CALIBRATION_PROFILES berisi list/tuple profil dengan
field persis berikut; tidak ada data fisik yang diterima melalui command klien:

- id: identifier profil berversi; jangan memakai ulang ID untuk acuan berbeda.
- target_version_id, width_mm, height_mm: cocok dengan versi target published.
- measurement_reference: sumber pengukuran yang tidak kosong.
- coordinate_contract: analysis-px-to-target-mm-center-origin-v1.
- fit_landmarks_mm: empat record {id, x_mm, y_mm}, berurutan pada convex hull.
- check_landmarks_mm: 1-16 record independen {id, x_mm, y_mm} di dalam hull.
- max_error_mm: batas residual fisik positif yang ditetapkan secara eksplisit.
- min_area_ratio, min_denominator_ratio: masing-masing berada dalam (0,1).

Landmark mm harus berada dalam bidang TargetVersion. Semua ID unik.
load_profile(profile_id) mengembalikan salinan canonical profil dan fingerprint.
Pengaturan deployment harus dilindungi dan profil diukur/review secara independen;
measurement_reference merupakan metadata penelusuran, bukan bukti kriptografis.
Tidak ada profil fisik nyata yang dibuat oleh iterasi ini.

## Metode deterministik

Metode measured-landmarks-projective-integer-v1 membentuk homografi planar dari
empat korespondensi (x_px,y_px) -> (x_mm,y_mm). Penyelesaian nullspace 8x9 memakai
rasional eksak dan tidak memaksakan koefisien kanan-bawah = 1. Matriks homogen
disimpan sebagai integer dengan faktor bersama dihilangkan; tidak ada pembulatan
koefisien floating point atau penambahan epsilon pada scoring. Ukuran integer
dibatasi 4096 bit sesuai kontrak scoring existing. Input numerik dibatasi 32 digit,
eksponen absolut 32 dan teks 80 karakter sebagai batas sumber daya teknis.

Validasi meliputi dimensi raster, finite/nonboolean, batas koordinat, landmark
berbeda, convex hull terurut, non-collinearity/rank, rasio luas minimum dan
invertibilitas. Denominator harus tetap satu tanda di seluruh frame foto dan
memenuhi margin minimum. Landmark pemeriksaan harus independen dan berada di hull.
Residual fitting DAN pemeriksaan dibatasi dalam mm memakai perbandingan kuadrat
rasional; residual kuadrat dan rasio kualitas disimpan tanpa kehilangan presisi.
Residual nol pada empat titik fit bukan bukti akurasi: pemeriksaan independen wajib.

Pusat ring ditentukan profil terukur; pusat foto/kertas tidak dipakai otomatis.
Input petugas harus berada pada raster analisis sesuai preprocessing_version foto,
bukan koordinat layar hasil resize/EXIF otomatis. File asli diverifikasi ulang
melalui inspect_original existing: SHA, byte size, MIME dan dimensi harus cocok.
Foto tidak diputar, diubah atau ditulis ulang. Homografi mengasumsikan target planar;
distorsi lensa, target melengkung, blur dan kesalahan penempatan landmark tidak
secara otomatis diperbaiki. Error di titik uji tidak menjamin akurasi seluruh bidang,
terutama di luar hull landmark. Pemetaan planar dijelaskan pada acuan OpenCV:
https://docs.opencv.org/4.12.0/d9/dab/tutorial_homography.html
Implementasi ini memakai standard library Python, tanpa menambah OpenCV/NumPy.

## Command, histori dan audit

calibration_services.calibrate_draft menerima keyword-only actor, session_id,
assessment_id, photo_id, target_version_id, expected_edit_version,
expected_target_edit_version, profile_id, expected_profile_sha256, observations,
operation_id, reason, optional using. observations hanya mapping ID landmark ke
pasangan piksel; matriks, koordinat fisik dan batas kualitas berasal dari server.

Gunakan global change_assessment, grant kegiatan aktif dan assigned_assessor
existing; authorization diulang di bawah lock serta mendahului replay. Hanya draft
terbaru dengan snapshot sesi, foto dan evidence yang cocok dapat dikalibrasi.
Request stale untuk assessment, target atau fingerprint profil ditolak.

Urutan lock: session -> participation -> assessment -> points menurut PK -> photo
-> optional run -> optional previous Calibration -> target -> rings menurut PK
-> User -> grants. Lock record kalibrasi baru dan parent yang sudah dipegang
bersifat milik transaksi/reentrant; tidak menambah joined-lock query.

Satu transaksi membuat Calibration draft lalu accepted melalui lifecycle model
existing (versi 1 -> 2), memasang FK pada draft, menghapus seluruh cache skor,
menaikkan versi assessment sekali dan menyimpan satu audit assessment.calibrated.
Accepted Calibration lama, target/ring/rule, identitas/koordinat titik dan snapshot
histori tidak ditulis ulang. Penggantian FK kalibrasi dalam frame piksel yang sama
memakai bounded internal Model.save setelah validasi penuh; guard publik terhadap
pergantian evidence saat titik sudah ada tetap tertutup. Verified/locked tetap
immutable. Audit menyimpan profil terukur, fingerprint, observasi, matriks, kualitas,
versi dan identitas foto/target tanpa private storage path.

Replay UUID identik mengembalikan DraftReceipt historis tanpa memulihkan kalibrasi
atau skor lama, termasuk setelah profil dinonaktifkan. Authorization tetap wajib.
Konflik UUID dan kegagalan acceptance/persistence/audit me-rollback seluruh mutasi.

## Integrasi scoring

Urutan backend yang diuji: pilih foto/run/rule pada draft -> import_detection_points
-> calibrate_draft -> score_draft existing dengan expected evidence versions terkini.
Kalibrasi dapat memasang transformasi setelah titik AI tersedia tanpa mengubah
koordinat/provenance piksel. Rule published harus sudah dipilih sesuai workflow
existing; iterasi ini tidak membuka reset/pergantian rule atau foto dengan titik.

Kalibrasi dan scoring merupakan dua command atomik terpisah. Kegagalan scoring
mempertahankan kalibrasi accepted dan draft unscored, tanpa skor parsial. Hasil tetap
draft; acceptance kalibrasi tidak berarti verifikasi/pengesahan nilai resmi.

## Verifikasi aktual - 11 November 2026

- Focused awal: 31 tes lulus; setelah penguatan integritas foto: 33 tes lulus.
- Full regression dijalankan SATU kali setelah stabil: 674 tes lulus dalam
  118.621 detik (641 baseline + 33 baru).
- Django system check: tanpa masalah; migration drift: No changes detected.
- git diff --check, syntax dan whitespace termasuk file baru: lulus.
- Tes menggunakan SQLite dalam memori, foto/profil pengukuran sintetis, dotenv dan
  bytecode dinonaktifkan. Tidak ada database operasional atau layanan AI diakses.

Tes membuktikan origin ring yang berbeda dari pusat foto, perspektif, batas ring,
degenerasi/near-collinearity/horizon, residual independen, integritas file/konteks,
authorization, versi stale, replay, immutable history, rollback dan integrasi engine.
Ini bukan validasi akurasi lapangan atau perilaku row locking PostgreSQL. Uji
independent connections pada PostgreSQL tes diperlukan sebelum operasional,
termasuk kalibrasi vs scoring/edit/retirement/revocation dan collision UUID.
Storage/ORM/audit guards tetap bukan perlindungan absolut terhadap admin filesystem
atau SQL; termasuk potensi perubahan file setelah verifikasi byte terakhir.

Status: siap review implementasi teknis. Kalibrasi lapangan belum siap sampai profil
terukur, batas penerimaan, protokol landmark/raster dan evaluasi foto nyata tersedia.
