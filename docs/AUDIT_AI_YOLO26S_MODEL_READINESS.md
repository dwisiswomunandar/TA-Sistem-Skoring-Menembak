# Audit Kesiapan dan Pembuktian Identitas Model YOLO26s

**ID:** AUDIT-AI-001 / DEC-16 / CTRL-007  
**Tanggal:** 13 November 2026  
**Branch:** `feat/django-migration`  
**HEAD yang ditelaah:** `4efd217`  
**Status:** **AUDIT STATIS; CHECKPOINT YOLO26s BELUM TERVERIFIKASI; INFERENSI NYATA BELUM DIUJI**

## 1. Metode dan ruang lingkup

Audit ini memeriksa langsung versi source di GitHub: `src/ai-engine/detector_runtime.py`, `main_api.py`, `detection_contract.py`, `requirements.txt`, `README.md`, tes sintetis AI, `src/django-app/assessments/detection_client.py`, `detection_services.py`, `detection_import_services.py`, `src/django-app/config/settings/base.py` dan `.gitignore`. Dokumen iterasi dan dokumentasi resmi Ultralytics digunakan sebagai konteks.

**Tidak dapat diakses dari GitHub:** checkpoint `.pt` lokal pengguna, identitas asli model terlatih, riwayat training, bobot yang dimaksud untuk produksi, file dataset privat dan hasil pengukuran. `.gitignore` mengecualikan `src/ai-engine/*.pt`. Nama file `best.pt`, teks `yolo26s`, atau SHA-256 **sendirian** tidak mengesahkan arsitektur dan ketepatan model. Pemeriksaan ini tidak mengeksekusi kode, model, server, test suite, GPU atau DB.

## 2. Fakta source dan temuan

| ID | Aspek | Bukti spesifik di source | Status / risiko |
|---|---|---|---|
| AI-01 | Pemilihan bobot | `Settings.weights_path`, variabel `AI_WEIGHTS_PATH`; `load_model(path)` memanggil `YOLO(str(path))` | BISA MEMUAT .pt yang dikonfigurasi; tidak membatasi YOLO26s |
| AI-02 | Validasi awal checkpoint | `DetectorRuntime.load`: perlu file `.pt` lokal nonkosong, batas ukuran, SHA256 sebelum/sesudah load, `model.task == "detect"` | TERSEDIA, tetapi `detect` bukan bukti generasi atau ukuran model |
| AI-03 | Metadata identitas | `model_name = ultralytics.{type(model.model).__name__}`; `weights_sha256`, `runtime_version`, `adapter_version`, `checkpoint_runtime_version` | Provenance tersimpan; nama kelas seperti `DetectionModel` tidak membuktikan YOLO26**s** |
| AI-04 | Versi pustaka | `src/ai-engine/requirements.txt` mem-pin `ultralytics==8.4.141`, `torch==2.14.0`, paket lainnya | PIN TERSEDIA; **versi terpasang di komputer & kompatibilitas checkpoint aktual belum diperiksa** |
| AI-05 | Format deteksi | `detection_contract.detection_result`: satu gambar, orig_shape cocok, bbox `xyxy`, class ID bilangan bulat tidak negatif, confidence 0–1 | VALIDASI STRUKTURAL TERSEDIA; bukan validasi bahwa kelas adalah lubang peluru |
| AI-06 | Penggunaan kelas saat impor | `assessments/detection_import_services.py` melakukan `for index, row in enumerate(detections, 1)` lalu membuat `ShotPoint` untuk **setiap** bbox; `class_id` tidak dipakai untuk memilih/menolak kelas | **RISIKO AKURASI UTAMA**: model dengan kelas selain lubang dapat menghasilkan titik palsu; penanganan harus didesain dan diuji |
| AI-07 | Koordinat titik | `_midpoint(x1,x2)` dan `_midpoint(y1,y2)` menghasilkan titik tengah bbox; kalibrasi dan scoring dilakukan service lain | MIDPOINT BBOX bukan bukti pusat geometris lubang nyata, terutama sobekan/overlap |
| AI-08 | Batas gambar | JPEG/PNG, MIME terverifikasi, 8 MiB upload dan 16 juta piksel/8192 sisi sebagai default, tanpa EXIF transpose; BGR raster | KONTRAK ADA; orientasi, kamera dan toleransi lapangan masih memerlukan evaluasi |
| AI-09 | Status gagal | `main_api.py`: bobot tidak tersedia → `MODEL_UNAVAILABLE` HTTP 503, inferensi gagal → status gagal; sukses kosong `detections=[]` | FAIL-CLOSED pada ketidaktersediaan model; nol deteksi **bukan** otomatis sepuluh skor nol |
| AI-10 | Integrasi Django | `detection_client.Configuration.current` mengharuskan pinned provenance; `validate_response` mencocokkan hash, dimensi, preprocessing, versi adapter dan parameter; `detection_services.detect_photo` mempunyai audit | SERVICE TERSEDIA, tetapi `DETECTION_SERVICE=None` pada base; tidak diaktifkan via UI |
| AI-11 | Tes yang ada | `src/ai-engine/test_main_api.py` memakai `SyntheticNetwork`/model factory; kontrak memakai tensor sintetis | TES KONTRAK ADA; **tidak membuktikan inferensi model YOLO26s/akurasi foto nyata** |
| AI-12 | Keamanan runtime | Endpoint FastAPI tidak menambahkan autentikasi klien di `main_api.py`; service Django mengatur trust dan URL internal melalui konfigurasi | Jangan mempublikasikan FastAPI secara langsung; uji isolasi jaringan, batas resource, TLS/proxy serta ancaman upload pada deployment |

### Penilaian utama

1. **Belum ada pemeriksaan arsitektur YOLO26s khusus** pada runtime. `model.task == detect` hanya memverifikasi tugas. `model_name` mengambil nama kelas Python umum, bukan bukti skala model. Hal ini merupakan **kesenjangan verifikasi identitas**, bukan bukti bahwa bobot pengguna salah.
2. **Service impor semua kelas secara default**, sehingga akurasi scoring bisa terganggu jika bobot berkelas ganda atau bobot generic COCO terpakai. Pelabelan, kelas yang diizinkan, mekanisme menolak kelas tak sesuai, serta hasil uji harus dirancang sebelum inferensi dipakai untuk skor.
3. **Laporan README AI sebelumnya** mencatat inspeksi statis suatu aset lokal historis mengarah ke `yolov8n.pt`; itu bukan kepastian keadaan checkpoint lokal **saat ini**. Perlakukan bobot lama sebagai historis sampai ada hasil audit baru.
4. Dokumen resmi Ultralytics mendukung varian `yolo26s.pt` untuk object detection, tetapi checkpoint pretrained itu **bukan** otomatis model peluru yang telah fine-tuned dan teruji pada target lapangan.
5. Format bbox + midpoint adalah kontrak yang sudah digunakan. Jangan mengganti ke segmentation, centroid/contour, atau perubahan threshold secara ad hoc; perubahan representasi membutuhkan versi baru, tes dan evaluasi.

## 3. Gerbang pembuktian model (untuk diperiksa pada lingkungan tepercaya)

| Gerbang | Bukti minimum | Status |
|---|---|---|
| M1 — File nyata | Lokasi lokal yang disetujui, ukuran bytes, SHA-256 tanpa mengunggah bobot | **PARTIAL — output inventaris lokal dilaporkan pengguna; belum diperiksa langsung** |
| M2 — Provenance training | Arsitektur dan skala model, task, kelas/names, jumlah kelas, dataset dan versi anotasi, training run, epoch, versi tool | NOT VERIFIED |
| M3 — Verifikasi isi checkpoint | Muat bobot **hanya dari sumber tepercaya** di lingkungan terisolasi; baca `model.task`, metadata model/config/yaml, training args jika ada, class names, head dan checkpoint origin | NOT RUN |
| M4 — Uji kompatibilitas | Jalankan uji pada satu citra sintetis/terizin, cek `orig_shape`, finite bbox, versi adapter & envelope, error/missing weights | NOT RUN |
| M5 — Kebenaran kelas | Tetapkan nama dan ID kelas yang benar-benar mewakili lubang peluru; validasi reject/filter yang aman sebelum impor bila diperlukan | DECISION/TEST PENDING |
| M6 — Uji akurasi | Ground truth independen; split berdasarkan lembar target fisik; precision/recall/mAP, FP/FN, error pusat dalam piksel/mm dan dampaknya ke skor | DATASET/GT PENDING |
| M7 — Deployment | Protected path/hash/provenance, internal-only FastAPI, konfigurasi Django trust, backup/retensi, load/performance test | NOT RUN |

**Gerbang minimum untuk mulai eksperimen AI nyata adalah M1–M4**, ditambah gambar yang berizin. **M5–M7 wajib sebelum klaim akurasi lapangan atau penggunaan operasional**. Proyek boleh memulai integration tests dengan **mock/sintetis** tanpa melewati gerbang data operasional, tetapi keluaran harus berlabel non-operasional.

### Pemeriksaan lokal yang aman dan tidak memuat model

Dari **root repository** pada PowerShell VS Code, kedua perintah di bawah bersifat read-only dan tidak menjalankan bobot Python:

```powershell
Get-ChildItem -Path .\src\ai-engine -Filter *.pt -File |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem -Path .\src\ai-engine -Filter *.pt -File |
    Get-FileHash -Algorithm SHA256 |
    Select-Object Path, Hash
```

Jika file tidak berada di direktori tersebut, gunakan path checkpoint terpercaya yang diketahui; **jangan mencari file pribadi secara massal atau menyalin path sensitif ke repository**. Jika tidak ada output, keberadaan checkpoint lokal belum dapat ditetapkan. Perintah ini hanya memverifikasi inventaris/hash, **tidak** mengidentifikasi arsitektur.

**Langkah berikutnya** adalah audit metadata menggunakan lingkungan Python terisolasi setelah asal checkpoint diperiksa. Jangan memuat file `.pt` yang tidak dipercaya: berkas PyTorch checkpoint dapat menggunakan mekanisme deserialisasi yang berisiko. Hasil yang layak dicatat adalah struktur arsitektur, scale/nc/names, versi library dan log training/validation yang sah; hindari menyertakan bobot asli, informasi sensitif atau file `.env` di repo.

### Validasi bukti model dan dataset

- SHA-256 memastikan identitas *byte-for-byte* antar-runs, bukan arsitektur model atau nilai metrik.
- `model.task == detect` perlu dipasangkan dengan pengujian kelas dan provenance training.
- `Ultralytics 8.4.141` pada file requirements adalah baseline dependency saat commit, bukan hasil `pip show` lokal.
- Format output yang diharapkan: `bbox_xyxy`, `class_id`, `confidence`; kaliber dan aturan skor tetap berasal dari konteks sesi/target, bukan dari prediksi bbox.
- Jika ada beberapa bobot `best.pt`, jangan menimpa yang lama atau menganggap semuanya merupakan model yang sama. Beri manifest berversi pada penyimpanan privat, catat SHA-256 sebelum eksperimen, dan bedakan **pretrained COCO** vs **fine-tuned bullet-hole**.

## 4. Prioritas perbaikan berikutnya

| Urut | Pekerjaan | Ketergantungan | Kriteria selesai |
|---|---|---|---|
| P0 | Inventaris file .pt, SHA-256 dan asal training lokal (M1–M2) | Akses komputer pengguna; tanpa Codex | Bukti metadata tanpa bobot terunggah |
| P1 | Review keandalan identifikasi generasi/skala checkpoint dan kelas `hole` aktual (M3/M5) | Model terpercaya, class map | Keputusan desain dan tes adversarial multi-kelas, bukan percaya nama file |
| P2 | Contract/smoke test FastAPI dengan checkpoint aktual (M4) | P1, gambar tes berizin/sintetis | Status, bbox/class, hashing & error tervalidasi; hasil tidak diklaim akurasi |
| P3 | Validasi dataset holdout target fisik dan ground truth lapangan (M6) | Instrumen OBS-001, izin data, protokol TEST-PLAN-001 | Metrik terukur dengan sampel dan ketidakpastian |
| P4 | Integrasi UI Django + otorisasi + foto terproteksi dengan mock sebelum model nyata, lalu tes PostgreSQL & mobile (M7) | Kebijakan otorisasi provisional dan keputusan input kamera | Tidak melonggarkan default-deny; audit lengkap |

**Catatan keamanan:** tidak ada pengubahan bobot, dependency, code, model/migration, deployment plans atau akses database yang disarankan secara otomatis oleh audit ini. Secara khusus, `DETECTION_SERVICE` **tetap** `None` sampai provenance dan jaringan telah direview.

## 5. Instruksi handoff untuk Codex

1. Verifikasi branch/HEAD dan daftar perubahan lokal. Baca register, audit ini, `ITER_10_IMPLEMENTATION_READINESS_AUDIT.md` dan TEST-PLAN-001.
2. Verifikasi secara independen semantik kelas dan model. Jangan hanya menyetujui karena file bernama `yolo26s.pt` atau checkpoint model.task `detect`.
3. Audit penggunaan `class_id` dalam import, kebijakan whitelist/konversi, serta perilaku gagal aman untuk kelas tidak dikenali. Jangan mengubah data historis diam-diam.
4. Audit kontrol provenance dan gating ketika konfigurasi diaktifkan. Uji happy/fail/malformed, zero detection, replay UUID, akses scope, dan kebocoran informasi.
5. Jika model nyata sudah tersedia, bandingkan metadata terhadap manifest training dan SHA sebelum smoke test; tidak menyimpan raw weights di Git. Catat semua hasil tes aktual beserta perangkat/versi.
6. Update register `DEC-16` saat audit baru menemukan bukti, `supersedes` dengan alasan jika keputusan berubah. Semua aturan lapangan tetap provisional sampai observasi.

## 6. Referensi

Internal:
- `docs/PROJECT_DECISION_AND_CHANGE_REGISTER.md`
- `docs/ITER_10_IMPLEMENTATION_READINESS_AUDIT.md`
- `docs/TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md`
- `docs/ITER_06_AUDITED_DETECTION_POINT_IMPORT.md`
- `docs/ITER_07_MEASURED_TARGET_CALIBRATION.md`
- `src/ai-engine/README.md`

Eksternal:
- Dokumentasi Ultralytics YOLO26: https://docs.ultralytics.com/models/yolo26/
- Dokumentasi Ultralytics deteksi objek: https://docs.ultralytics.com/tasks/detect/
- Referensi DetectionModel Ultralytics: https://docs.ultralytics.com/reference/nn/tasks/

**Bukti pengujian dalam pekerjaan ini:** tidak ada tes atau inferensi nyata yang dijalankan. Audit source statis dan dokumentasi eksternal diperiksa pada 13 November 2026. Keputusan penggunaan checkpoint tertentu dan ambang akurasi **belum dibuat**.

## 7. Bukti lokal lanjutan — 13 November 2026 (CTRL-008)

Pengguna menjalankan PowerShell `Get-ChildItem -Recurse -File -Filter "*.pt"` pada direktori `src/ai-engine`, menghitung ukuran MB (dibulatkan dua desimal) serta `Get-FileHash -Algorithm SHA256`, dan memberikan output berikut:

| Informasi | Nilai yang dilaporkan |
|---|---|
| Jumlah file `.pt` yang tampil pada cakupan pencarian | **1** |
| Nama | `src/ai-engine/best.pt` |
| Ukuran | **5,96 MB** (angka pembulatan; nilai bytes belum dicatat) |
| SHA-256 | `AB19F53F1BEFA6D415406F808F423186B1B2384C6F932F269B723BA4AF29E14D` |
| Asal/riwayat training, arsitektur, skala, class names | **BELUM TERIDENTIFIKASI** |

**Status bukti:** `USER-REPORTED LOCAL CHECKPOINT INVENTORY`. Asisten belum mengakses berkas bobot pengguna secara langsung dan tidak memverifikasi hasil hash di lingkungan terpisah. Temuan ini mengisi inventaris awal gerbang M1, **tidak** menaikkan M2/M3 ke verified. SHA-256 hanya mengidentifikasi bytes, bukan membuktikan YOLO26s, jenis tugas, atau kelas lubang peluru. Tidak ada file `.pt` yang dimasukkan ke Git, tidak ada checkpoint yang diload, inferensi, pengujian, atau perubahan kode.

**Langkah verifikasi aman berikutnya:** baca metadata statis arsip PyTorch (jika format ZIP) memakai modul standar Python `zipfile` dan `pickletools.genops`, **tanpa** `torch.load`, `YOLO(path)`, atau eksekusi deserialisasi; hasilnya indikatif. Jika hasil metadata tidak cukup untuk identitas model, validasi lanjutan memerlukan asal bobot tepercaya dan lingkungan terisolasi.
