# ITER-AI-02 — Audit Kesiapan Dataset YOLO26s Detection

**ID:** ITER-AI-02 / CTRL-012 / lanjutan DEC-13, DEC-14, DEC-16
**Tanggal:** 16 November 2026 (zona waktu proyek: Asia/Bangkok / UTC+7)
**Branch:** feat/django-migration
**HEAD awal yang diaudit:** 0110690e1e601390d6c0d9be788fb53c9b44cef9
**Status:** AUDIT SELESAI; DATASET DETECTION LOKAL BELUM SIAP UNTUK TRAINING.
**Lingkup perubahan:** dokumentasi saja, atas instruksi pengguna ITER-AI-02.

## 1. Kesimpulan dan batas kepastian

Dalam cakupan proyek yang diperiksa, tidak ditemukan paket dataset detection, anotasi objek, class map, manifest dataset, konfigurasi training, atau split train/validation/test. Ditemukan 12 berkas gambar; delapan JPEG pada folder legacy Laravel hanya mempunyai dua isi unik berdasarkan SHA-256. Keberadaan gambar dan checkpoint tidak membuktikan tersedianya dataset berlabel maupun keberhasilan training YOLO26s.

Jumlah gambar dataset beranotasi yang berhasil ditemukan = **0**; jumlah instance anotasi yang berhasil ditemukan = **0**. Ini adalah hasil pencarian artefak lokal, bukan klaim bahwa dataset di Drive atau di luar folder proyek kosong. Jumlah kelas, jumlah lubang sebenarnya, jumlah target fisik, izin data dan kualitas label = **UNKNOWN / BELUM TERVERIFIKASI**.

**Fakta terverifikasi** berarti diperiksa langsung pada filesystem/source lokal dalam audit ini. **Catatan historis** berarti terbaca pada dokumen proyek dan tidak diuji ulang. **Asumsi/usulan** berarti rancangan untuk keputusan selanjutnya. **Risiko** berarti kemungkinan dampak yang belum tentu sudah terjadi. **Pekerjaan tertunda** berarti belum dilaksanakan. Seluruh kebutuhan operasional Lapbak, geometri target, kaliber, jumlah tembakan, kewenangan dan aturan penilaian tetap **PROVISIONAL** sampai observasi lapangan.

## 2. Metode, cakupan dan baseline Git

Pemeriksaan awal menjalankan git status --short, git status --branch --porcelain=v1, git branch --show-current, git rev-parse HEAD, git diff --stat dan git diff --cached --stat. Branch sesuai permintaan, working tree dan index bersih, tanpa perubahan lokal yang perlu direkonsiliasi. Relasi branch terhadap origin dibaca dari ref lokal; tidak dilakukan fetch dan tidak diklaim sinkron dengan server pada saat audit.

Dokumen yang dibaca:

- [Register keputusan/perubahan](PROJECT_DECISION_AND_CHANGE_REGISTER.md), sampai CTRL-011.
- [Audit model YOLO26s](AUDIT_AI_YOLO26S_MODEL_READINESS.md), termasuk bagian 7–10.
- [Protokol pengujian](TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md), khususnya bagian 3–6.
- [Instrumen observasi](OBSERVATION_AND_DATA_COLLECTION_INSTRUMENT_v0.1.md), khususnya bagian D, E dan G.
- README AI, requirements, kontrak/runtime/API AI serta source validator/import deteksi Django.

Pencarian memakai rg --files -uu, rg -n, inventaris os.walk dari root proyek, pengelompokan SHA-256, dan pembacaan header PNG/JPEG dengan modul standar Python. Proses inventaris menggunakan src/ai-engine/venv/Scripts/python.exe dengan opsi -I -S -B -X utf8; tidak mengimpor aplikasi, Ultralytics atau Torch. Salah kutip pada percobaan awal skrip inventaris dikoreksi dengan input melalui stdin; hanya hasil perintah sukses yang dipakai. Pencarian rg awal dengan glob path Windows dikoreksi memakai opsi -g.

Cakupan termasuk file tersembunyi/ignored milik proyek, termasuk folder upload/storage legacy. Direktori .git, venv, .venv, env, vendor, node_modules, __pycache__, staticfiles, .pytest_cache dan htmlcov dikecualikan sebagai dependency/cache/VCS. Tidak mengikuti symlink/junction; tidak ditemukan direktori tertaut dalam cakupan. Folder assets dan tests di root kosong. Inventaris sukses mencatat **347 file sebelum penambahan laporan ini**, tanpa error akses. Batas ini bukan pencarian seluruh komputer; contoh dataset bawaan dependency bukan dataset penelitian proyek.

Ekstensi dan nama kandidat diperiksa untuk YAML, JSON/JSONL/NDJSON, TXT, CSV, XML, notebook, manifest, label/annotation, arsip dan konfigurasi training. Kandidat terstruktur yang ditemukan hanya tiga requirements TXT, robots.txt, composer.json, package.json dan phpunit.xml; tidak ada dataset YAML, CSV, notebook, manifest atau arsip dataset lokal. File database, log dan .env tidak dibuka untuk mencari data pribadi atau konfigurasi rahasia.

## 3. Inventaris gambar dan dataset lokal — FAKTA TERVERIFIKASI

| Lokasi | Berkas gambar | Isi unik SHA-256 di lokasi | Interpretasi terbatas |
|---|---:|---:|---|
| docs/diagram_arsitektur.png | 1 | 1 | Aset dokumentasi, bukan pasangan image-label |
| src/ai-engine/lesan_master.png | 1 | 1 | Aset master, tanpa anotasi ditemukan |
| src/django-app/static/ui/img/logo-kopaska.png | 1 | 1 | Aset UI |
| src/web-app/public/assets/images/lesan_master.png | 1 | 1 | Aset master legacy, berbeda bytes dari master AI |
| src/web-app/public/uploads/ | 4 | 1 | JPEG legacy; izin/sumber dan target fisik belum diketahui |
| src/web-app/storage/app/public/targets/ | 4 | 2 | JPEG legacy; satu isi juga ada pada public/uploads |
| **Total seluruh lokasi** | **12** | **6 secara global** | Bukan jumlah sampel dataset independen |
| **Subset JPEG legacy, gabungan kedua folder** | **8** | **2 secara global** | Tidak memiliki label dataset yang ditemukan |

**Duplikasi byte-identik terverifikasi:**

- Kelompok A (5 file): public/uploads/1788636859.jpeg, 1788636969.jpeg, 1788637562.jpeg, 1788637793.jpeg, serta storage/app/public/targets/WR8S3uoYBvChLsxUHSyRZfDUiCCIo5UTxmglppNZ.jpg. Semua path relatif terhadap src/web-app/. Masing-masing 4.310.214 bytes; header raster 4032 × 3024.
- Kelompok B (3 file): dbVrgM49se08j1fxJpu2MWOdGhKvaQtleG3oTxZG.jpg, U4655CcEgEcbtSrqCALzcUmz7hAKFxUOJ09zaX4f.jpg dan xCqfyAJ8R0345rErOs9dNFtDb7pyycrYQlnvnoCT.jpg, di src/web-app/storage/app/public/targets/. Masing-masing 3.754.691 bytes; header raster 3024 × 4032.

Enam isi gambar unik bukan enam target fisik. Dua JPEG unik juga belum membuktikan dua lembar fisik berbeda. Hash identik membuktikan bytes identik; hash berbeda tidak membuktikan target berbeda. Tidak dilakukan pemeriksaan visual, hitung lubang, validasi full decode, verifikasi EXIF atau pemeriksaan near-duplicate.

| Aspek dataset | Hasil lokal | Batas makna |
|---|---|---|
| Gambar berpasangan dengan anotasi objek | 0 ditemukan | Tidak ada label dataset YOLO/COCO/XML yang ditemukan |
| File/instance anotasi objek | 0 / 0 ditemukan | Bukan klaim nol objek pada JPEG |
| Class map dan jumlah kelas | Tidak ditemukan / UNKNOWN | ID pada fixture tes bukan class map dataset |
| Manifest dan physical_target_id | Tidak ditemukan | Independensi sampel tidak dapat ditetapkan |
| Train / validation / final test | Tidak ditemukan untuk ketiganya | Jumlah anggota masing-masing UNKNOWN |
| data.yaml / args.yaml / results.csv lokal | Tidak ditemukan | Tidak boleh menyimpulkan konfigurasi dari nama best.pt |
| Izin, source_type, reviewer dan ground truth | Tidak ditemukan sebagai bukti dataset | Upload aplikasi bukan bukti izin penelitian |

Pencocokan file TXT dengan stem gambar menghasilkan 0. Selain itu, inventaris seluruh file tidak menemukan label di direktori labels terpisah maupun anotasi dalam format terstruktur. Tidak adanya label tidak boleh diartikan sebagai semua gambar merupakan sampel negatif.

## 4. Struktur AI, kontrak dan konfigurasi — FAKTA SOURCE

| Artefak | Bukti source pada HEAD audit | Implikasi |
|---|---|---|
| [main_api.py](../src/ai-engine/main_api.py) | create_app; POST /api/evaluate; runtime terpisah | Jalur inferensi, bukan pipeline training |
| [detector_runtime.py](../src/ai-engine/detector_runtime.py) | Settings; load_model; DetectorRuntime.load/evaluate | Memuat checkpoint lokal yang dikonfigurasi; task harus detect |
| [detection_contract.py](../src/ai-engine/detection_contract.py) | detection_result, ADAPTER_VERSION ultralytics-boxes-v1 | Output bbox axis-aligned dalam piksel analisis |
| [requirements.txt](../src/ai-engine/requirements.txt) | Pin ultralytics==8.4.141, torch==2.14.0 | Pin source; instalasi/kompatibilitas training tidak diuji ulang |
| [test_main_api.py](../src/ai-engine/test_main_api.py) | SyntheticNetwork dan model factory sintetis | Fixture kontrak, bukan dataset atau bukti akurasi |
| [run_test.py](../src/ai-engine/run_test.py) dan [shooting_engine.py](../src/ai-engine/shooting_engine.py) | Script mengimpor ShootingTargetEngine, tetapi modul mendefinisikan TargetScoringEngine; lesan_test.png tidak ditemukan | Contoh legacy tidak konsisten secara statis; tidak dijalankan atau diperbaiki |

Tidak ditemukan model.train, notebook, training YAML atau pipeline pembentukan split pada source proyek yang diperiksa. Confidence 0.2, IoU 0.7, imgsz 640, max_det 300 dan device cpu pada Settings adalah **parameter inferensi**, bukan konfigurasi training yang telah tervalidasi.

Kontrak deteksi v1 memerlukan satu result dengan orig_shape cocok, class_id integer tidak negatif, confidence finite dalam [0,1], dan bbox_xyxy positif serta berada dalam raster. Hasil kosong merupakan sukses dengan detections kosong; kegagalan bukan skor nol. Format training YOLO normalized xywh berbeda dari format keluaran API xyxy piksel.

[Import Django](../src/django-app/assessments/detection_import_services.py), khususnya loop baris 117 dan _midpoint, membuat ShotPoint dari setiap bbox tervalidasi tanpa pemilihan kelas lubang. Validator hanya membatasi class_id secara struktural. **Risiko P0:** kelas target, ring atau objek generik dapat ikut menjadi titik tembakan jika model salah kelas. Usulan satu kelas tidak menggantikan kebutuhan verifikasi names/nc pada checkpoint dan penanganan kelas tak dikenal sebelum pemakaian operasional. Tidak ada perubahan perilaku aplikasi dalam audit ini.

Preprocessing saat ini adalah decoded-rgb-to-bgr-no-exif-v1: raster tidak diputar dengan EXIF dan tidak di-homografi/crop. **Risiko orientasi:** gambar dan label harus merujuk raster yang sama; rancangan persiapan harus mengatasi perbedaan orientasi viewer/training terhadap runtime. Metadata EXIF gambar lokal belum diperiksa. Pilihan yang diusulkan adalah membuat salinan kanonis berversi dengan orientasi dibakukan dan label sesuai raster hasil, sambil mempertahankan asli secara privat; belum dilakukan dan memerlukan keputusan teknis sebelum implementasi.

Batas default runtime: JPEG/PNG, 8 MiB, 16.000.000 piksel dan sisi maksimal 8192. Header master AI 5905 × 6259 = 36.959.395 piksel; master legacy 6260 × 6260 = 39.187.600 piksel. Keduanya melebihi batas piksel default bila diajukan utuh; ini kesimpulan statis, bukan hasil request API. Keduanya tidak dihitung sebagai foto training berlabel. Resolusi header delapan JPEG legacy berada di bawah batas piksel/sisi, tetapi kelayakan decode, isi dan izin tetap belum diverifikasi.

## 5. Pemisahan checkpoint dan arsip — STATUS BUKTI BERBEDA

| Artefak | Bukti langsung audit ini | Bukti historis / UNKNOWN | Keputusan audit |
|---|---|---|---|
| Lokal src/ai-engine/best.pt | Satu file .pt ditemukan; **6.249.258 bytes**; SHA-256 **ab19f53f1befa6d415406f808f423186b1b2384c6f932f269b723ba4af29e14d** | CTRL-009/010 mencatat indikasi Detect, C2f, DFL, yolov8n.pt dan yolov8_lesan_model3; training, kelas, scale dan mAP belum terbukti | Kandidat **YOLOv8 Detection legacy**; bukan YOLO26s terverifikasi |
| Arsip Drive eksperimen 26 Oktober 2026 | Tidak diakses ulang dan tidak diunduh dalam ITER-AI-02 | AUDIT-AI-001 bagian 10 / CTRL-011 mencatat arsip **YOLOv8 Segmentation**, weights/best.pt **7.144.948 bytes**, dataset_yolo.zip, dataset_coco_original.zip, manifest.json, audit.json dan environment/log | Eksperimen historis terpisah; isi dataset/kelas/split belum diaudit lokal |
| Model tujuan YOLO26s Detection | Tidak ditemukan checkpoint lokal dengan provenance YOLO26s terverifikasi | Tidak ada bukti training YOLO26s yang dapat divalidasi pada cakupan ini | Masih target persiapan, belum lulus kesiapan model |

Hash lokal cocok dengan nilai user-reported CTRL-008. Ini menambahkan **verifikasi langsung inventaris file/hash** pada bukti lama, tanpa menaikkan status arsitektur/kelas/training menjadi VERIFIED. Tidak dilakukan torch.load, unpickle, YOLO(path), atau pembacaan metadata internal checkpoint dalam audit ini.

Perbedaan ukuran menunjukkan dua best.pt tersebut bukan artefak byte-identik menurut metadata yang tercatat; hubungan asal training tetap UNKNOWN. Nama file sama tidak menunjukkan run yang sama. Metrik mask historis tidak dipakai sebagai metrik bbox detection, dataset YOLO26s atau ketepatan skor. Status dan hasil Drive tetap **catatan historis**, bukan verifikasi ulang konektor pada iterasi ini.

## 6. Evaluasi kesiapan dan risiko

| ID / prioritas | Fakta atau gap | Risiko dan penilaian |
|---|---|---|
| DS-01 / P0 | Dataset berlabel, class map dan izin belum ditemukan | **NOT READY**; tidak ada dasar training atau klaim statistik dataset |
| DS-02 / P0 | Delapan JPEG legacy = dua isi unik; ID target/sesi dan split tidak ada | **LEAKAGE UNKNOWN**, bukan kebocoran terbukti. Random split per file berisiko menempatkan salinan identik lintas split |
| DS-03 / P0 | Class ID diterima struktural; semua bbox diimpor | Semantik bullet_hole wajib dibuktikan; jangan melatih kelas ring/target lalu mengimpor semua hasil sebagai lubang |
| DS-04 / P1 | Tidak ada anotasi/review log | Kualitas bbox, objek terlewat, overlap dan konsistensi pusat **NOT ASSESSABLE** |
| DS-05 / P1 | Arsip segmentation hanya tercatat di Drive | Kebutuhan konversi **CONDITIONAL**, belum ada dasar menyatakan semua label dapat dipakai |
| DS-06 / P1 | Orientasi dan skala objek pada raster training belum ditetapkan | EXIF/crop/resize dapat menggeser label; lubang kecil dapat hilang detail setelah resize |
| DS-07 / P1 | Training configuration dan provenance YOLO26s belum tersedia | Eksperimen belum dapat direproduksi; pin dependency bukan bukti kompatibilitas |
| DS-08 / P2 | Dokumen/skrip historis belum seluruhnya mengikuti source aktif | Risiko membaca contoh lama sebagai prosedur aktif |

Belum ada train/val/test yang dapat dibandingkan. Karena itu tidak dilakukan penghitungan overlap antar-split, group leakage, distribusi kelas atau near-duplicate lintas split. Bukti duplikasi yang tersedia adalah lintas direktori aplikasi. Tidak dilakukan penghapusan atau pemindahan salinan.

## 7. Format yang dituju dan konversi bersyarat — USULAN

YOLO26s tersedia untuk tugas detection; varian -seg adalah tugas berbeda. Model pretrained generik belum merupakan detector lubang peluru proyek. Rujukan: [Ultralytics YOLO26](https://docs.ultralytics.com/models/yolo26/).

Usulan awal adalah **satu kelas, ID 0 = bullet_hole**, belum class map aktual. Nilai ring, kaliber dan skor tetap metadata/domain, bukan kelas objek. Pedoman perlu membedakan lubang, cetakan ring, angka, patch, noda dan sobekan. Jangan mengasumsikan bentuk sobekan sebagai beberapa tembakan.

Struktur rencana di penyimpanan dataset privat (belum dibuat):

~~~text
dataset-detection-v1/
  data.yaml
  images/train/  images/val/  images/test/
  labels/train/  labels/val/  labels/test/
  manifest.csv
  annotation_guideline.md
  review_log.csv
  split_manifest.csv
~~~

Contoh data.yaml berikut **template**, bukan konfigurasi yang sudah tersedia atau instruksi menjalankan training:

~~~yaml
path: /PRIVATE_DATASET_ROOT/dataset-detection-v1
train: images/train
val: images/val
test: images/test
names:
  0: bullet_hole
~~~

Format label yang dituju: satu baris per instance, class x_center y_center width height dalam koordinat ternormalisasi terhadap raster pasangan; class zero-based, lebar/tinggi positif, semua batas box valid. Framework tidak mewajibkan test untuk training, tetapi final holdout tetap dibutuhkan oleh protokol TA. Gambar negatif tanpa objek boleh tidak mempunyai label menurut format framework; untuk audit proyek usulkan label kosong eksplisit dan status reviewed_negative agar berbeda dari missing annotation. Rujukan: [Format dataset detection Ultralytics](https://docs.ultralytics.com/datasets/detect/).

**Jika arsip segmentation kelak tersedia dengan izin:** periksa apakah polygon/mask benar-benar merepresentasikan satu lubang per instance, bukan seluruh lesan/ring atau gabungan lubang. Label YOLO segmentation memakai class diikuti pasangan titik polygon, bukan lima nilai detection; lihat [Format instance segmentation Ultralytics](https://docs.ultralytics.com/datasets/segment/).

Rancangan konversi matematis untuk satu instance polygon yang valid: xmin/min(x), xmax/max(x), ymin/min(y), ymax/max(y); xc=(xmin+xmax)/2, yc=(ymin+ymax)/2, w=xmax-xmin, h=ymax-ymin. Untuk titik normalized, hasil tetap normalized; untuk koordinat piksel, bagi komponen x/w dengan lebar raster dan y/h dengan tinggi raster. Polygon multi-bagian satu instance tetap satu bbox yang mencakup bagiannya. COCO bbox berbentuk x,y,w,h piksel perlu konversi pusat dan normalisasi, serta pemetaan category_id ke class ID; jangan menyalin ID COCO secara buta.

Validasi hasil dengan overlay manusia, jumlah instance sebelum/sesudah dan batas raster. Mask crowd/merged holes, polygon degenerat atau lubang yang tak terpisah perlu adjudication/relabel; konversi tidak dapat memulihkan jumlah lubang atau pusat yang tidak ada pada sumber. Midpoint bbox turunan tidak otomatis sama dengan pusat fisik lubang. Pertahankan sumber dan ID, hash, versi alat/aturan konversi, class map dan lineage pada salinan versi baru. **Konversi belum dilaksanakan.**

## 8. Dataset minimum dan persiapan realistis TA D3 — PROPOSAL

Tidak ada jumlah minimum universal yang menjamin akurasi. Angka berikut adalah **rencana anggaran awal**, mengikuti contoh TEST-PLAN-001, bukan data tersedia atau syarat lulus resmi.

1. **P0 — Tetapkan sumber dan izin.** Konfirmasi OBS-001: akses foto, penyimpanan/retensi, source_type, target dan otoritas ground truth. Jika hanya simulasi yang tersedia, batasi klaim ke simulasi; jangan menyebut validasi live-fire.
2. **P0 — Pedoman dan pilot anotasi.** Gunakan sejumlah kecil target berizin dari calon train untuk menyamakan definisi bullet_hole, bbox rapat, pusat rujukan, ambiguous/overlap dan hard negatives. Review seluruh label pilot; dua reviewer independen diutamakan, keterbatasan satu reviewer wajib dicatat. Jangan menggunakan final test untuk menyusun aturan berdasarkan hasil model.
3. **P1 — Anggaran awal 30 lembar fisik independen**, bila benar dapat dikumpulkan. Contoh split **18 train / 6 val / 6 test**. Dua atau tiga foto tiap lembar memberi rencana **60–90 foto**, tetap **30 unit fisik**, bukan 60–90 unit independen. Jumlah lubang/anotasi ditetapkan dari label nyata; jangan mengasumsikan 30 × 10 tembakan. Jika kapasitas memungkinkan, tambah lembar independen sebelum menambah banyak sudut dari lembar yang sama.
4. **P1 — Representasi dan pengecualian.** Rekam sumber simulated_manual_holes/live_fire, kondisi cahaya/sudut/perangkat, target kosong dan pengganggu, lubang pada zona hitam/putih/batas, dan kondisi challenge. Rencanakan target negatif berizin tersendiri dalam anggaran; hitung ulang alokasi bila menambahnya. Jika dua kaliber termasuk scope, sebar unit fisik keduanya pada tiap split bila tersedia; bila tidak memadai, persempit klaim secara eksplisit. Tidak ada persentase distribusi atau jumlah negatif aktual yang diasumsikan.
5. **P1 — Anti-leakage sebelum augmentasi.** Gunakan group_id yang mengikat physical_target_id, sesi/capture terkait dan seluruh turunan/crop/augmentasi. Jika target dipakai ulang lintas sesi, gabungkan semua kelompok terkait. Tetapkan split sekali dengan seed tercatat, stratifikasi kondisi sejauh mungkin, cek exact hash dan near-duplicate/manual review. Tidak ada group maupun salinan identik lintas split. Augmentasi hanya train; final test dikunci untuk evaluasi akhir.
6. **P1 — QA dataset.** Periksa semua pasangan image-label, full decode dan orientasi, finite/range box, integer class, ID yang terdaftar, label ganda, objek terlewat, negatif yang benar, konsistensi raster dan review ambiguity. Untuk dataset kecil TA, review visual seluruh pasangan lebih realistis daripada sampel kecil. Laporkan statistik gambar/target/objek per split setelah benar dihitung.
7. **P1 — Rancangan eksperimen terbatas.** Setelah dataset lolos QA dan ada instruksi training terpisah, provision checkpoint YOLO26s detect dari sumber tepercaya, catat hash/arsitektur/task/names, environment dan resolved dependencies. Mulai satu baseline pada ukuran input yang selaras dengan runtime 640; putuskan epoch budget, patience, batch, device dan seed berdasarkan resource pilot. Satu pembanding resolusi lebih tinggi hanya jika objek terlalu kecil dan resource cukup; pilih memakai validation serta ukur kebutuhan menyelaraskan runtime. Tidak menetapkan parameter training final dalam audit ini.
8. **P2 — Evaluasi dan arsip.** Pilih threshold/checkpoint pada validation, kemudian satu evaluasi final holdout beku. Laporkan precision/recall/F1, TP/FP/FN, mAP50 dan mAP50-95 box, count error dan error midpoint terhadap pusat GT independen; skor akhir dievaluasi terpisah menurut TEST-PLAN-001. Simpan manifest, split/label version, data.yaml, args.yaml, log/kurva, environment, commit, seed, bobot SHA dan keterbatasan. Tidak membuat ambang lulus atau hasil metrik sebelum ada bukti.

Manifest privat minimum yang diusulkan: dataset_version, image_id, physical_target_id, session_group_id anonim, group_id, source_type, permission_evidence_id, file_sha256, width_px, height_px, orientation_policy, source_image_id/transform_version, split, class_map_version, annotation_version, guideline_version, reviewer_id anonim, review_status dan ambiguity_flags. Hash/checksum label dan split manifest disimpan untuk reproduksi. Ground truth center/score disimpan terpisah dari label detection bila perlu; bukan berasal dari prediksi AI. Ringkasan repository cukup metadata anonim dan agregat yang diizinkan.

## 9. Pekerjaan tertunda dan gerbang lanjut

| Gerbang | Status ITER-AI-02 | Bukti agar dapat dilanjutkan |
|---|---|---|
| Izin dan inventaris sumber | PENDING | Bukti izin dan daftar target/foto yang boleh diproses |
| Dataset/class map/pedoman | NOT FOUND LOCALLY | Pasangan image-label valid, kelas bullet_hole disepakati, review tercatat |
| Split independen | NOT FOUND LOCALLY | Manifest group, cek hash/near-duplicate dan final holdout beku |
| Kualitas/konversi | NOT ASSESSABLE / CONDITIONAL | Review overlay; konversi berversi hanya jika sumber segmentation layak |
| Model YOLO26s dan training config | NOT VERIFIED / NOT FOUND | Provenance checkpoint terpercaya serta konfigurasi eksperimen yang dapat direproduksi |
| Uji akurasi dan scoring | NOT RUN | GT independen, metrik terukur serta keterbatasan; aturan operasional tetap provisional |

Keputusan lanjut: persiapan pedoman/manifest dapat dirancang sekarang; training belum memiliki dasar dataset yang terverifikasi dalam proyek. Tidak mengaktifkan DETECTION_SERVICE; source saat audit tetap None, TARGET_CALIBRATION_PROFILES tetap kosong. Implementasi proteksi kelas atau perubahan preprocessing merupakan pekerjaan kode terpisah, bukan keluaran audit ini.

## 10. Konsistensi dokumentasi dan pemeriksaan aktual

- DEC-13/TEST-PLAN-001 tetap proposal metode: 30 lembar dan 18/6/6 tidak dinaikkan menjadi fakta; unit independensi tetap target fisik/kelompok terkait.
- DEC-14/OBS-001 tetap instrumen belum dilaksanakan: tidak ada izin, observasi, pengukuran atau review petugas yang direkayasa.
- DEC-16/AUDIT-AI-001 tetap membedakan model tujuan, kandidat detection lokal dan arsip segmentation. Tambahan CTRL-012 hanya memperkuat bukti inventaris lokal/hash; tidak menghapus klaim historis user-reported dan tidak menyertifikasi arsitektur.
- README root masih menyebut Laravel/YOLOv8. README AI masih menyebut bridge Django sebagai future/no bridge, sedangkan detection_client.py dan detection_services.py sudah ada. Keduanya dicatat sebagai dokumentasi historis yang tertinggal; source aktif menjadi acuan audit. Tidak menimpa README atau dokumen lama dalam scope ini.
- Semua angka inventaris ditelusurkan ke filesystem, header atau SHA; seluruh angka usulan diberi label proposal. Perhitungan 12 file/6 isi unik dan subset 8 file/2 isi unik konsisten; tidak disamakan dengan jumlah target/lubang.
- Pemeriksaan aktual: status/branch/HEAD/diff Git, penelusuran file termasuk ignored, pencarian source, hash checkpoint/gambar, pembacaan header PNG/JPEG, dan pembacaan tiga referensi resmi Ultralytics. Tidak ada dataset/bobot diunduh atau diunggah.
- Pemeriksaan akhir dokumentasi mencakup target tautan relatif, nomor CTRL, konsistensi statistik/status, diff/whitespace serta cakupan dokumentasi sebelum commit. Hasil penyimpanan/commit dilaporkan sesudah dijalankan; hash commit baru bukan bagian dari HEAD yang diaudit.
- Unit/regression test, training, inferensi, migrasi, database, server, full image decode, observasi lapangan dan torch.load/unpickle **tidak dijalankan**. Tidak ada perubahan kode aplikasi, dependency, dataset, bobot, permissions atau settings; tidak ada penghapusan/pemindahan berkas.

**Batas rekomendasi:** dataset kecil dapat mendukung eksperimen TA D3 dengan klaim terbatas; kecukupannya harus dinilai dari variasi, jumlah unit independen dan hasil holdout, bukan dari penambahan foto duplikat atau augmentasi.
