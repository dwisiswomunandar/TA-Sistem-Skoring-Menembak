# Protokol Pengujian Sistem, Deteksi AI, Kalibrasi, dan Scoring v0.1

**ID:** TEST-PLAN-001 / DEC-13 / CTRL-004  
**Disusun:** 13 November 2026  
**Branch acuan:** `feat/django-migration`  
**Baseline sebelum dokumen:** `dc427a8`  
**Status:** **DRAF METODOLOGI — BELUM DILAKSANAKAN / BELUM DISETUJUI LAPANGAN**  
**Sasaran:** aplikasi KOPASKA — Shooting Range, untuk penyiapan rancangan pengujian dan bukti yang dapat diaudit ketika aplikasi benar-benar siap.

> **Pemisahan fakta:** Documented/Implemented di bawah berarti kode/service atau pengujian lama tercatat, bukan fitur sudah siap operasional. Seluruh alur kewenangan, SOP, data target, standar penilaian, jumlah sampel, dan penggunaan lambang masih menunggu observasi/pengambilan data menurut klarifikasi pengguna. Tidak ada angka hasil, foto, ground truth, pengukuran akurasi, atau wawancara lapangan yang dibuat dalam dokumen ini.

## 1. Dasar internal dan keterbatasan baseline

- `docs/SRS_v0.1.md` adalah **dokumen historis awal** yang masih mengacu Laravel/YOLOv8. Jangan memperlakukannya sebagai SRS Django yang sudah direkonsiliasi.
- `docs/DATABASE_DESIGN_BASELINE_v1.1.md`: rancangan Django, YOLO26s sebagai **model tujuan**, scoring geometris authoritative di server, bukti privat, histori berversi.
- `docs/ITER_06_AUDITED_DETECTION_POINT_IMPORT.md`: konversi bbox → titik tengah analisis, bukan penentuan skor oleh AI.
- `docs/ITER_07_MEASURED_TARGET_CALIBRATION.md`: homografi dari 4 landmark terukur plus titik cek independen; profil fisik nyata **belum tersedia**. `TARGET_CALIBRATION_PROFILES` default kosong.
- `docs/MVP_01_SCORING.md`: implementasi aturan `inward-circle-v1`, sentuh batas inklusif menggunakan perhitungan eksak.
- `docs/ITER_08_AUDITED_ASSESSMENT_FINALIZATION.md` dan ITER-09A/B/C/D1: audit/akses dan backend/UI parsial. Pengujian otomatis terakhir yang dilaporkan: **802 tes Django lulus pada SQLite in-memory** (ITER-09D1); bukan tes full end-to-end atau pengujian PostgreSQL konkurensi.
- `src/ai-engine/README.md`: bobot YOLO26s nyata dan performa inferensi **belum terverifikasi**. Identitas checkpoint, weights SHA256, model runtime, parameter inferensi harus diverifikasi sebelum eksperimen.
- `docs/ITER_09D2_OPERATIONAL_AUTHORIZATION_PROPOSAL.md`: kewenangan Admin Lapbak atas perintah Perwira Lapbak bersifat **provisional**; policy operasional tetap default-deny.

**Tujuan pengujian dipisah menjadi:** (A) fungsi dan keamanan web, (B) deteksi lubang pada foto, (C) akurasi kalibrasi koordinat, (D) ketepatan skor per titik/total, (E) keberhasilan alur gabungan dan waktu proses.

## 2. Inventaris skenario Black Box dan traceability awal

Status pada tabel adalah **ketersediaan implementasi menurut dokumen repo**, bukan hasil tes baru. Kode TC dapat dikembangkan menjadi spreadsheet atau laporan saat pengujian.

| ID | Skenario masukan/tindakan | Hasil yang diharapkan | Status kesiapan |
|---|---|---|---|
| BB-01 | Akun petugas valid login | Berhasil, menuju dashboard sendiri | UI tersedia; pernah diperiksa browser |
| BB-02 | Akun tidak aktif / kredensial salah | Login ditolak tanpa membocorkan akun | Tes otomatis tercatat; retest nanti |
| BB-03 | Akses halaman tanpa login | Redirect login, tanpa data privat | Implementasi/tes tercatat |
| BB-04 | User tanpa permission/grant/assignment membuka kegiatan orang lain | Kosong/404 sesuai route; tidak bocor data | ITER-09B tersedia |
| BB-05 | Form pembuatan sesi tanpa explicit approval | Default-deny; tidak menulis sesi | ITER-09C, teramati di browser |
| BB-06 | Buat konteks tanpa protected plan | Ditolak; tidak membuat objek | ITER-09D1 tersedia, default-deny |
| BB-07 | Buat/aktifkan kegiatan atas otoritas yang telah disetujui | Audit actor, status, alasan benar | Aktivasi belum ada; policy menunggu observasi |
| BB-08 | Daftar, verifikasi identitas, dan approve peserta | Transisi sesuai kewenangan, dapat ditelusuri | Service keputusan belum ada |
| BB-09 | Buat sesi pistol/laras panjang dengan parameter valid/invalid | Konfigurasi valid diterima; konflik ditolak server | Resolver/session service tersedia; flow terblokir approval |
| BB-10 | Pilih foto target melalui jalur input yang disetujui | Validasi ukuran/MIME dan bukti privat | UI capture/upload belum terintegrasi; kamera vs galeri perlu keputusan |
| BB-11 | Inferensi sukses tetapi deteksi kosong | Status sukses + 0 deteksi, **bukan** otomatis 10 peluru meleset | Kontrak backend tersedia, AI nyata belum diuji |
| BB-12 | Inferensi gagal/timeout/file rusak | Gagal terklasifikasi, bukan skor nol palsu | Kontrak backend tersedia; uji integrasi belum |
| BB-13 | Koreksi titik AI oleh petugas | Revisi terotorisasi, nilai menjadi tidak valid sebelum hitung ulang | Service backend ada; UI kanvas belum |
| BB-14 | Kalibrasi sah vs landmark salah/perspektif buruk | Profil terukur diuji; kalibrasi ditolak bila kualitas gagal | Backend tersedia; profil nyata belum |
| BB-15 | Penilaian titik di dalam ring, tepat bersinggungan batas, di luar ring | Skor sesuai aturan server `inward` | Pure scoring + tes sintetis tersedia |
| BB-16 | Verifikasi dan locking oleh actor tanpa/sesuai kebijakan | Default-deny tanpa approval; transisi dengan audit setelah kebijakan sah | Backend tersedia; policy belum sah |
| BB-17 | Unduh rekap PDF dari hasil locked | Isi, versi, keterlacakan, akses sesuai scope | UI/report belum tersedia |
| BB-18 | Mengulang POST dengan UUID sama; UUID sama dengan payload berbeda | Idempotensi atau konflik yang aman | Layanan terkait tersedia; integrasi PostgreSQL belum |
| BB-19 | Pencabutan permission/grant saat proses/replay | Akses berubah sesuai policy; tidak membuka histori | Tes SQLite sebagian, concurrency PostgreSQL belum |
| BB-20 | Tampilan desktop dan emulasi mobile 393×852 | Tidak ada elemen terpotong, navigasi/form dapat digunakan | UI awal teramati; flow penuh belum |

**Format bukti tiap kasus:** ID, build/commit, timestamp, settings/env **tanpa rahasia**, role/scope penguji, input/fixture ID non-rahasia, langkah, expected, actual, PASS/FAIL/BLOCKED/NOT RUN, screenshot/log yang disanitasi, bug ID, reviewer, tanggal retest. Jangan mengisi kolom actual sampai diuji.

## 3. Dataset dan pengambilan citra

### 3.1 Unit independensi dan inventaris

- Rencana awal pengguna: sekitar **30 lembar target** yang mungkin bertambah; **bukan** klaim 30 lembar telah diambil. Jumlah peluru **10 per sesi** dilaporkan dalam rencana kebutuhan dan tetap perlu konfirmasi observasi.
- Satu identitas `physical_target_id` untuk satu lembar fisik; banyak foto `image_id` boleh diambil dari lembar yang sama, tetapi tidak dihitung sebagai lembar independen.
- Bedakan `source_type = simulated_manual_holes / live_fire` (atau kategori terdokumentasi lain). Lubang buatan manual **tidak otomatis mewakili bentuk, sobekan, diameter, atau tumpukan tembakan nyata**.
- Satu foto harus menunjukkan satu target; kondisi cahaya, sudut, jarak, perangkat, resolusi, dan tingkat overlap dicatat. Pengambilan target yang sudah dilepas dari papan dan penggunaan panduan framing masih mengikuti kebutuhan sementara.
- Semua foto asli disimpan privat. Jangan commit identitas prajurit, nomor pribadi, atau foto target lapangan sensitif ke repository. Gunakan ID anonim, hash file dan lokasi bukti terlindungi.

### 3.2 Rencana sampling dan split (contoh, bukan angka final)

Jika benar tersedia 30 lembar fisik independen, **contoh rancangan awal** adalah 18 lembar train, 6 validation, 6 final test (60/20/20 *berdasarkan lembar*). Ini hanya ilustrasi alokasi: sangat kecil untuk kesimpulan kuat; kelas/kaliber/kondisi mungkin tidak seimbang. Perlu revisi setelah inventaris nyata.

**Aturan wajib anti-leakage:** seluruh foto dari satu lembar, sesi, dan hasil augmentasi dari sumber itu harus berada pada **split yang sama**. Jangan memecah gambar, crop, atau variasi pencahayaan dari satu target fisik antara training dan testing. Tetapkan seed dan manifest ID split; kumpulan final test tidak boleh dipakai untuk memilih hyperparameter/threshold/epoch. Catat jika bukti fisik target yang sama dipakai dalam beberapa sesi (pengelompokan diperketat). Alat split berbasis groups dapat digunakan; bukan wajib menambah dependency proyek.

Susun variasi yang *direncanakan*: dua kategori kaliber yang diperbolehkan, titik pada berbagai ring, batas ring, potensi lubang berdekatan/overlap, target tanpa lubang, latar berbeda, foto agak miring, glare/bayangan, dan kualitas gambar buruk. Pertahankan subset **challenge** terpisah untuk kasus yang berada di luar batas MVP; jangan menganggap semua kasus berhasil didukung.

### 3.3 Contoh skema manifest (bukan data hasil)

`physical_target_id, image_id, source_type, intended_caliber_mm, capture_device, width_px, height_px, illumination_category, camera_angle_category, overlap_category, split, file_sha256, annotation_version, ground_truth_reviewer, review_status`.

Jangan menyimpan kredensial, lokasi sensitif, atau identitas asli di manifest publik. Simpan hanya metadata yang perlu untuk metode dan repeatability.

## 4. Protokol ground truth dan persetujuan label

1. Tetapkan **pedoman anotasi berversi** sebelum labeling: satu lubang = satu anotasi dengan bounding box dan pusat, flag terlihat/tidak pasti, jenis overlap/retak, serta ID gambar/lembar.
2. Pisahkan anotasi lokasi deteksi AI dari ground truth skor. Penentuan **nilai rujukan** harus mengikuti aturan target fisik yang ditetapkan dan diverifikasi petugas berwenang. Jangan menjadikan output AI sebagai ground truth.
3. Idealnya dua penilai anotasi independen bekerja tanpa melihat hasil prediksi; ketidaksepakatan diberi status `needs_adjudication`, ditinjau penilai yang ditunjuk, dan dicatat alasan/versinya. Bila personel terbatas, laporkan keterbatasan daripada mengaku ada dua validator.
4. Untuk batas ring, dokumentasikan lokasi tepi lubang dan garis fisik; **titik pusat anotasi saja mungkin tidak cukup** bila kertas rusak/robek. Kaliber dalam scoring diambil dari konteks sesi dan aturan resmi, bukan ditebak dari bbox.
5. Kasus ambigu/overlap tidak boleh diam-diam dihapus dari evaluasi. Kelompokkan menjadi `unambiguous` dan `ambiguous/challenge`, lalu laporkan keduanya. Hasil tidak pasti diberi `indeterminate`, bukan skor yang dikarang.
6. Simpan versi guideline, pemeriksa, tanggal, dan adjudication log, dengan akses terbatas. Untuk data simulasi gunakan penanda eksplisit; catatan reviewer bukan persetujuan organisasi.

## 5. Metrik, definisi, dan pemisahan sumber kesalahan

### 5.1 Deteksi objek (bbox)

Gunakan evaluasi standar model pada **holdout berbasis target**: `precision`, `recall`, `F1`, `mAP@0.5`, `mAP@0.5:0.95`, dan jumlah TP/FP/FN. Matching box ke ground truth mesti **satu-ke-satu**, berdasarkan IoU (dan kelas yang berlaku). Laporkan threshold confidence, NMS IoU, bobot SHA, runtime, resolusi input, serta kurva bila tersedia. Definisi metric box Ultralytics: https://docs.ultralytics.com/guides/yolo-performance-metrics ; https://docs.ultralytics.com/reference/utils/metrics

Contoh formula hitungan pada satu cutoff yang ditetapkan sebelum final test:
- `Precision = TP / (TP + FP)`
- `Recall = TP / (TP + FN)`
- `F1 = 2 × Precision × Recall / (Precision + Recall)`

Jika penyebut nol, nyatakan `N/A` beserta jumlah kejadian; jangan menyamakan dengan akurasi sempurna atau 0 begitu saja. mAP bukan ketepatan skor akhir. Catat `FP/image`, `FN/image`, lubang per lembar, dan perbedaan count prediksi vs ground truth.

### 5.2 Kalibrasi geometris (satuan mm)

Empat landmark fitting **tidak cukup** untuk klaim akurasi, karena dapat memiliki error fitting nol. Gunakan landmark pemeriksaan independen dan beberapa titik uji tersebar; catat `error_mm = jarak(Euclidean prediksi-mm, titik-rujukan-mm)`. Sajikan rerata/median, P95, maksimum, jumlah titik, dan kondisi foto. Bedakan error kalibrasi dari error bbox/center dan dari error label manusia. Jangan menetapkan toleransi mm operasional tanpa pengukuran dan persetujuan.

### 5.3 Penilaian geometris per lubang dan per sesi

Scoring engine saat ini memakai `inward-circle-v1` dan `touch-inclusive-v1`. Untuk ring berurutan dari dalam ke luar, deteksi sentuhan dihitung jika `d² ≤ (R_outer + caliber/2)²`; ring pertama yang memenuhi menjadi skor, sedangkan miss yang *included* bisa bernilai 0. Ini **kontrak implementasi**, bukan konfirmasi aturan lapangan. Lihat `docs/MVP_01_SCORING.md`.

Laporkan:
- `Exact-score accuracy` = proporsi lubang dengan skor server yang sama dengan **skor ground truth yang telah diadjudikasi**.
- `Per-shot MAE` = rerata `|skor_server − skor_referensi|` pada pasangan lubang valid.
- `Per-session total MAE` = rerata `|total_server − total_referensi|` untuk sesi yang lengkap dan status ground truth-nya dapat ditentukan.
- `Exact total match` = proporsi sesi lengkap yang totalnya tepat sama.
- Confusion matrix untuk perpindahan zona, khususnya zona yang berbatasan.
- Breakdown: deteksi keliru, titik tengah keliru, homografi, aturan garis/kaliber, dan koreksi manual. **Jangan mengisi nilai hilang dengan skor 0.**

Pisahkan setidaknya 3 kondisi eksperimen: (I) **oracle** posisi titik+kalibrasi benar untuk uji scoring murni; (II) anotasi titik benar + kalibrasi terukur untuk uji transformasi; (III) pipeline AI → transformasi → skor. Perbedaan antar kondisi tidak otomatis membuktikan satu penyebab tanpa kontrol yang layak.

### 5.4 End-to-end, performa, dan review petugas

- Ukur waktu dari `capture/submit` sampai hasil draft tampil, serta waktu `inferensi` terpisah dari upload/kalibrasi/review.
- Catat p50/median, p95, maksimum, perangkat CPU/GPU, koneksi, ukuran file, retry/timeout dan jumlah sampel.
- Ukur perubahan skor **sebelum** dan **sesudah** koreksi manual petugas; jangan mengklaim peningkatan akurasi tanpa ground truth independen.
- Uji akses tidak sah, pencabutan izin, audit/provenance, kondisi foto gagal, reset draft, verifikasi/lock sesuai policy dan hasil laporan terpisah. Tidak ada izin pengujian operasional tersirat dari protokol ini.

## 6. Kriteria penerimaan dan pelaporan

**Belum ada batas lulus angka resmi.** Jangan menetapkan `mAP ≥ x`, `akurasi ≥ y%`, `MAE ≤ z`, atau `latency ≤ n detik` sebagai keputusan final dari chat. Setelah pilot kecil dan konfirmasi pihak terkait, tetapkan kriteria penerimaan secara **prospektif sebelum melihat hasil final test**. Hindari mengubah cutoff hanya untuk meluluskan model.

Satu ringkasan eksperimen harus mencakup:
- Tanggal, revisi kode/commit, model_name, bobot SHA256, runtime/adapter, parameter inferensi.
- ID target dan gambar teranonim, jumlah foto, target fisik unik, lubang GT, kelas/kaliber, split dan kondisi.
- Prosedur ground truth/reviewer, jumlah `indeterminate`, pengukuran target/kalibrasi dan ketidakpastian.
- TP/FP/FN, P/R/F1/mAP, error kalibrasi mm, ketepatan skor dan error total; hasil untuk subset ambigu.
- Keamanan/black-box: ID test, expected/actual, PASS/FAIL/BLOCKED/NOT RUN, reproduksi bug, bukti anonim.
- Keterbatasan dan potensi bias: sampel kecil, lubang manual ≠ tembakan nyata, satu jenis target/perangkat, distribusi data berbeda.

**Jangan klaim** 30 × 10 = 300 tembakan nyata, hasil 100% tepat, atau performa lapangan berdasarkan unit test mock dan data simulasi. Status aplikasi sebagai prototype D3 tidak menghapus kewajiban membatasi klaim sesuai data.

## 7. Gerbang kerja dan checklist yang menunggu

| Gerbang | Status saat dokumen dibuat | Pemilik keputusan/aksi berikut |
|---|---|---|
| G1 — SOP/role/izin kegiatan disahkan | BLOCKED — observasi belum dilakukan | Pihak Lapbak sesuai otoritas |
| G2 — Spesifikasi target + contoh pengukuran/cara memberi GT | BLOCKED — pengukuran lapangan terverifikasi belum ada di repository | Penanggung jawab pengukuran/penilai lapangan |
| G3 — Komitmen sumber dataset, keamanan dan pemisahan train/test | PROPOSAL | Pengelola riset; tinjau izin penggunaan data |
| G4 — Checkpoint YOLO26s + hash + konfigurasi runtime | NOT VERIFIED | Developer/model reviewer |
| G5 — UI kamera/galeri, anotasi/review, laporan | NOT IMPLEMENTED FULLY | Iterasi development berikutnya |
| G6 — Pengujian kasus positif/negatif PostgreSQL | NOT RUN | Developer/tester dengan DB development terisolasi |
| G7 — Pelaksanaan foto, ground truth, metrik dan tes akhir | NOT RUN | Penguji dan reviewer yang ditetapkan |
| G8 — Dokumentasi keputusan, bukti, laporan dan deviasi | TRACKED pada `PROJECT_DECISION_AND_CHANGE_REGISTER.md` | Setiap penanggung jawab perubahan |

Konflik fitur capture: `SRS_v0.1.md` menyebut kamera langsung; catatan kebutuhan 2 November menyebut kamera **atau galeri**. **Jangan implementasi salah satunya atas dasar asumsi**; harus ada klarifikasi kebutuhan sebelum ITER-10.

## 8. Referensi metodologi yang dapat diverifikasi

1. Ultralytics, *YOLO Performance Metrics*: https://docs.ultralytics.com/guides/yolo-performance-metrics — definisi box precision/recall/mAP dan kecepatan.
2. Ultralytics, *Metrics API*: https://docs.ultralytics.com/reference/utils/metrics — definisi AP50, AP50:95 dan ringkasan per gambar.
3. Scikit-learn, *GroupShuffleSplit*: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html — split berdasarkan group, bukan gambar yang saling terkait.
4. OpenCV, *Homography tutorial*: https://docs.opencv.org/4.12.0/d9/dab/tutorial_homography.html — relasi geometri planar, bukan bukti profil target proyek.

Semua referensi ini adalah sumber **metodologi/teknis**, bukan bukti bahwa hasil eksperimen telah dilakukan atau SOP Lapbak sudah disahkan.

## 9. Handoff untuk Codex atau penguji berikutnya

1. Verifikasi HEAD dan `docs/PROJECT_DECISION_AND_CHANGE_REGISTER.md`; baca protokol ini dan docs ITER-06/07/08/09 terkait.
2. Audit kesesuaian kontrak code YOLO runtime, persetujuan/permission, data target, ring, dan jalur data serta kemungkinan perubahan sejak dokumen dibuat.
3. Rekonsiliasi SRS Laravel/YOLOv8 historis dengan Django/YOLO26s baru **tanpa menghapus histori**.
4. Implementasi/tes dilakukan bertahap; catat versi, perubahan, hasil dan kegagalan di register. **Jangan menjalankan tes, migrasi, atau seed dari dokumen ini secara otomatis**.
5. Pastikan keputusan lapangan dan privasi data telah disetujui sebelum mengambil data nyata atau mengaktifkan akses.

**Catatan hasil saat penyusunan:** dokumen metodologi ini dibuat dari penelaahan sumber internal dan dokumentasi metrik. **Tidak ada foto target diuji, ground truth dibuat, inferensi dijalankan, pengukuran lapangan, black-box manual baru, atau automated test yang dijalankan dalam pekerjaan ini.**
