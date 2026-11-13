# Audit Kesiapan Implementasi ITER-10 — Integrasi UI Penilaian dan AI

**ID:** AUDIT-READINESS-001 / DEC-15 / CTRL-006  
**Tanggal penelaahan:** 13 November 2026  
**Branch:** `feat/django-migration`  
**HEAD yang diaudit:** `afd8dca`  
**Status:** **AUDIT STATIS TERBATAS — bukan hasil eksekusi aplikasi ataupun persetujuan operasional**

## 1. Tujuan dan metode

Menilai pekerjaan yang sudah ada sebelum mengintegrasikan kamera, AI detection, review titik, scoring, pengesahan nilai, dan laporan, agar implementasi mengikuti kode existing dan tidak membuat ulang service yang sudah tersedia.

Metode: inspeksi **tree repository GitHub pada commit di atas**, URL router, view, model/service terpilih, konfigurasi dan dokumen iterasi. Ini **bukan** code review menyeluruh, security penetration test, browser test, training model, inspeksi model `.pt`, pemeriksaan DB lokal, atau regresi ulang. Status “ada” berarti ditemukan source/kontrak yang relevan, **bukan** bukti fungsional end-to-end. Dokumen historis/test report terdahulu dipisahkan dari bukti pemeriksaan statis saat ini.

## 2. Inventaris antarmuka dan endpoint yang benar-benar terdaftar

`src/django-app/config/urls.py` mendaftarkan hanya:
- `/`: dashboard akun (`config/ui_views.py`).
- `/activities/`: rute `activities.urls` untuk daftar, detail kegiatan, sesi peserta dan form pembuatan sesi.
- `/login/`, `/logout/`, `/health/`, `/admin/`.

`src/django-app/activities/urls.py` mendaftarkan empat pola: `""`, `"sessions/new/"`, `"<int:activity_id>/"`, dan `"<int:activity_id>/participants/<int:participation_id>/sessions/"`. `activities/views.py` menegakkan login/GET-safe untuk worklist dan form POST khusus buat sesi; GET form ditentukan oleh `creation_contexts`.

**Tidak ditemukan router/view Django web untuk:** registrasi peserta mandiri, CRUD kegiatan dan aktivasi, verifikasi/approval peserta, pengambilan foto web, unggah foto ke private storage dengan otorisasi, menjalankan deteksi dari halaman penilaian, kanvas koreksi titik, score/review/verify/lock lewat UI, tampilan rekap global, ekspor PDF. **Ini temuan cakupan URL yang tercatat, bukan klaim bahwa semua fungsi tersebut tidak memiliki service backend.**

## 3. Matriks komponen dan batas kesiapan

| ID | Komponen | Bukti source/referensi | Status berdasarkan audit | Konsekuensi |
|---|---|---|---|---|
| R-01 | Login, logout, dashboard akun dan branding KOPASKA | `config/urls.py`, `config/ui_views.py`, `templates/ui/*`, `static/ui/app.css` | **UI ROUTED**; branding terverifikasi manual oleh pengguna pada iterasi sebelumnya | Pertahankan UI yang sudah ada |
| R-02 | Worklist kegiatan, partisipasi, daftar sesi | `activities/{urls,views,read_access}.py` | **UI ROUTED, SCOPED/DEFAULT-DENY**; data tampak hanya sesuai assignment/grant/permission | Tidak membuka semua kegiatan untuk kemudahan demo |
| R-03 | Buat sesi penilaian | `activities/{urls,views,session_creation,session_creation_policy}.py` | **UI + SERVICE, DEFAULT-DENY** tanpa approval/eligibility | Tidak dapat menyiapkan sesi pertama otomatis |
| R-04 | Penyiapan data awal draft/pending | `activities/context_provisioning.py`, management command | **SERVICE + CLI, DEFAULT DISABLED** | Belum aktivasi/verifikasi/approval/pemberian hak |
| R-05 | Aktivasi kegiatan, verifikasi peserta, approval partisipasi | Dokumen ITER-09D2; route dan service terpilih | **KEBIJAKAN BELUM DISETUJUI / FLOW BELUM TERSEDIA** | Milestone terpisah setelah otorisasi yang sah |
| R-06 | Model domain kegiatan, sesi, target, point, foto, audit | `activities/models.py`, `assessments/models.py`, `assessments/result_models.py` | **MODEL TERSEDIA**; schema dan status nyata harus diverifikasi saat eksekusi | Reuse tabel/model; jangan membuat ulang tanpa temuan |
| R-07 | Simpan foto original privat / metadata | `assessments/storage.py`, `assessments/photo_metadata.py`, `assessments/models.py` | **STORAGE/MODEL BOUNDARY TERSEDIA, FOTO WEB INGRESS BELUM** | Butuh alur capture, validasi file, penyimpanan dan aksi service terotorisasi |
| R-08 | FastAPI inference /api/evaluate | `src/ai-engine/{main_api,detector_runtime,detection_contract}.py` | **ENDPOINT CODE TERSEDIA**; bobot YOLO26s nyata dan inferensi lapangan **BELUM TERVERIFIKASI** | Uji checkpoint/artifact terpisah; pembatasan jaringan internal |
| R-09 | Django → inference dan audit DetectionRun | `assessments/{detection_client,detection_services}.py` | **SERVICE ADA, NON-UI**; `DETECTION_SERVICE = None` | Provenance dan endpoint di protected settings masih perlu izin/review |
| R-10 | Import bbox ke ShotPoint | `assessments/detection_import_services.py` | **SERVICE ADA, NON-UI**; bbox midpoint, bukan deteksi batas fisik lubang | Ground truth geometri/center perlu diuji |
| R-11 | Kalibrasi 4 landmark + cek independen | `assessments/{calibration_math,calibration_services}.py` | **SERVICE ADA, NON-UI**; `TARGET_CALIBRATION_PROFILES = ()` | Profil ukuran fisik sah, landmark/UX belum tersedia |
| R-12 | Inward scoring per-shot dan audited draft | `assessments/{scoring,scoring_services}.py` | **ENGINE/SERVICE ADA, NON-UI** | Skoring otomatis final perlu penilaian lapangan/GT dan layar review |
| R-13 | Koreksi titik/draft dan verify/lock | `assessments/{services,finalization_services}.py` | **SERVICE ADA, NON-UI**; finalization policy default `()` | Perlu UI, audit editor, pemisahan tugas dan approval |
| R-14 | Laporan PDF, rekap antarpeserta | Pencarian tree/URL dan template Django pada HEAD | **TIDAK DITEMUKAN IMPLEMENTASI UI/ROUTE** | Implementasi setelah data locked & izin baca ditetapkan |
| R-15 | Deployment online | `config/settings/production.py`, `src/django-app/README.md` | **KONFIGURASI PRODUKSI DASAR ADA; DEPLOYMENT END-TO-END BELUM TERVERIFIKASI** | HTTPS, serving static/private, internal FastAPI, PostgreSQL, backup perlu uji |

**Konfigurasi default-closed dari source `config/settings/base.py`:**
`DETECTION_SERVICE=None`; `TARGET_CALIBRATION_PROFILES=()`; `ASSESSMENT_FINALIZATION_POLICIES=()`; `TRAINING_SESSION_CREATION_APPROVALS=()`; `OPERATIONAL_CONTEXT_PROVISIONING_ENABLED=False` dan `OPERATIONAL_CONTEXT_PROVISIONING_PLANS=()`. Semuanya bukan error yang boleh “diperbaiki” dengan bypass; ada untuk menjaga otorisasi.

**Catatan bukti temporal:** `docs/ITER_09D1_SECURE_CONTEXT_PROVISIONING.md` melaporkan 802 regresi Django lulus menggunakan SQLite in-memory. Pengguna telah memeriksa branding pada browser lokal dan mengirimkan bukti sinkronisasi Git sampai commit OBS-001 sebelumnya. **Tidak ada full test, PostgreSQL concurrency, inferensi nyata, foto capture atau UI penilaian yang diuji pada audit ini**.

## 4. Temuan utama dan akar hambatan teknis

**F-01 [Blocker operasional] — Deadlock bootstrap sesi:** Worklist bergantung pada sesi yang sudah ditugaskan, sedangkan sesi pertama membutuhkan aktivitas aktif, partisipasi approved, peserta verified, scoped grant dan explicit approval yang belum diotorisasi. ITER-09D1 hanya membuat draft/pending. Bukti: `activities/read_access.py`, `activities/session_creation_policy.py`, `activities/context_provisioning.py`; dokumen ITER-09C/D1/D2. **Solusi bukan** memberi semua hak pada superuser.

**F-02 [Blocker fitur] — Tidak ada flow UI assessment/foto:** `config/urls.py` tidak memasukkan `assessments.urls`; tree Django tidak menampilkan `assessments/urls.py` atau `assessments/views.py`, maupun template layar penilaian. Service yang tersedia tidak bisa dioperasikan oleh petugas melalui web saat ini.

**F-03 [Blocker validitas AI] — Checkpoint tujuan belum tervalidasi:** `src/ai-engine/README.md` menyatakan bobot lokal historis terindikasi YOLOv8n dan checkpoint YOLO26s belum diverifikasi. `detector_runtime.py` menerima bobot yang dipilih dari environment, bukan membuktikan ia YOLO26s. Jangan menyebut model final sudah terlatih dan siap evaluasi.

**F-04 [Blocker validitas scoring lapangan] — Profil target terukur/GT belum disahkan:** `TARGET_CALIBRATION_PROFILES` kosong dan aturan parameter/target tetap provisional. Jangan memasukkan angka ukur asumtif ke konfigurasi published atau hasil resmi.

**F-05 [Integration risk] — Kamera/foto original:** model metadata, private storage dan pemeriksaan bytes tersedia, tetapi endpoint web untuk capture/upload tidak tercatat. Tentukan kontrak koordinat raster, orientasi EXIF, batas file, privasi, dan aksi penyimpanan terotorisasi sebelum menghubungkan detector. Perbedaan kebutuhan **kamera saja vs kamera/galeri** belum direkonsiliasi.

**F-06 [Security/reliability risk] — Workflow dan PostgreSQL nyata:** sebagian kontrak async/HTTP, race, replay UUID, permission/grant revocation dan isolasi objek pernah diuji dengan mock/SQLite; belum dibuktikan aman secara end-to-end pada PostgreSQL test database dengan koneksi independen.

**F-07 [Document drift] — Referensi historis:** README root masih menyebut Laravel/YOLOv8; `docs/SRS_v0.1.md` masih mengikuti arsitektur awal, dan `src/ai-engine/README.md` pada bagiannya masih menyebut bridge Django sebagai pekerjaan future walaupun `detection_services.py` sekarang sudah ada. Jangan menimpa baseline historis diam-diam: buat SRS Django baru/README yang menjelaskan migrasi dan `supersedes` setelah audit.

## 5. Prioritas kerja yang direkomendasikan (untuk Codex ketika digunakan lagi)

| Prioritas | Milestone dan keluaran yang dapat diperiksa | Dependensi | Mode pengerjaan |
|---|---|---|---|
| **P0-A** | Cocokkan audit statis dengan test commands/coverage, `git status`, branch dan docs; buat checklist readiness terbaru | Tidak memerlukan observasi | Review + focused tests; tidak ada rewrite |
| **P0-B** | Selesaikan kebijakan kewenangan ITER-09D2: Admin Lapbak bertindak atas perintah Perwira Lapbak, pencabutan, self-verification, approval. Pisahkan mock test dari operasi | **Observasi/otorisasi sah untuk operasional** | Perancangan dan test sintetis boleh; aktivasi/permission nyata tetap default-deny |
| **P0-C** | Audit model checkpoint YOLO26s, provenance, bobot hash, versi runtime dan pemisahan train/val/test | Bobot nyata & dataset yang diizinkan | Bisa dikerjakan mandiri, tidak membutuhkan membuka policy kegiatan |
| **P1** | Rancang dan implementasikan foto target terotorisasi + UI capture, termasuk kualitas foto/preprocessing dan contract private storage | Keputusan capture vs galeri, hak akses, format image | Mulai dari tes kontrak dan komponen minimal |
| **P2** | UI penilaian draft: pilih evidence, panggil inference, import points, kalibrasi, koreksi manusia, score_draft; tampilkan status dan error yang dapat ditindaklanjuti | P1; profil target terukur; backend service existing; approval yang sah | Reuse service; jangan menghitung skor di browser |
| **P3** | UI verify/lock dan tabel rekap per kegiatan; izin baca dan audit/provenance | P2, kebijakan verifier/locker | Tidak menganggap superuser boleh semua |
| **P4** | PDF hasil `locked` dengan versi, status, dan filter scope yang valid | P3 | Periksa keamanan akses dan asal skor |
| **P5** | Uji pipeline dengan foto dan ground truth nyata, kinerja, regression, PostgreSQL concurrency, perangkat mobile | Izin data, observasi, model dan pipeline selesai | Laporkan error dan keterbatasan, bukan angka sintetis |
| **P6** | Deployment online aman untuk demonstrasi sidang dan backup/restore | P1–P5 minimum yang dapat didemokan | Secure env/proxy/static, jangan buka private media |

**Prinsip urutan:** jangan menunggu observasi untuk semua tugas. Audit/model-bobot dan test service dengan fixture sintetis dapat disiapkan lebih awal, tetapi fitur yang bergantung pada aturan Lapbak tidak boleh diaktifkan memakai approval palsu.

## 6. Acceptance criteria minimum sebelum memulai ITER-10 integrasi nyata

- [ ] HEAD, perubahan lokal, test baseline dan risiko diverifikasi kembali oleh implementer.
- [ ] Ada izin pemanfaatan dataset dan checkpoint yang diketahui identitas/hashnya.
- [ ] Ada kontrak capture/foto original, security upload dan ruang koordinat yang disepakati.
- [ ] Ada profil target fisik terukur dan pemeriksa independen; atau ITER-10 dinyatakan **uji sintetik non-operasional saja**.
- [ ] Pengujian UI/endpoint tidak melemahkan gerbang `default-deny`.
- [ ] Disiapkan tes unit/contract/integration untuk kasus gagal AI, kosong deteksi, overlap, boundary ring dan token stale.
- [ ] Bukti hasil tersimpan pada register audit; status `NOT RUN` tetap jelas untuk tes yang belum dilakukan.

## 7. Ringkasan audit untuk penyerahan Codex

**Yang sudah ada**: autentikasi dan shell UI, worklist scoped, form create session default-deny, command context provisioning default-disabled, model domain, detector FastAPI, Django detection bridge dan import, kalibrasi, scoring, draft editing serta finalization service. **Yang belum selesai di browser**: kegiatan/partisipasi operasional yang terotorisasi, capture/foto, pipeline assessment, review, verify/lock dan laporan. **Yang belum sah secara data**: parameter lapangan, profil target fisik, bobot YOLO26s final, ground truth dan uji kompetensi model.

Codex harus mulai dengan `git status` dan HEAD, membaca register + audit ini, memeriksa source aktual (bukan menganggap dokumen selalu mutakhir), mengusulkan scope kecil dengan tes dan tanpa bypass security, lalu mengembalikan ringkasan file berubah, commit, tes aktual, blocker & instruksi sinkronisasi. Jangan membuka fitur operasional semata-mata untuk menampilkan demo.

**Referensi internal:** `docs/PROJECT_DECISION_AND_CHANGE_REGISTER.md`; `docs/ITER_09B_OPERATIONAL_UI.md`; `docs/ITER_09C_AUTHORIZED_SESSION_CREATION.md`; `docs/ITER_09D1_SECURE_CONTEXT_PROVISIONING.md`; `docs/ITER_09D2_OPERATIONAL_AUTHORIZATION_PROPOSAL.md`; `docs/ITER_07_MEASURED_TARGET_CALIBRATION.md`; `docs/TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md`; `docs/OBSERVATION_AND_DATA_COLLECTION_INSTRUMENT_v0.1.md`; `src/ai-engine/README.md`.

**Batas pekerjaan audit ini:** tidak ada perubahan source/runtime settings/model/migration/database/izin, tidak ada perintah tes, server, inference, training, browser, atau PostgreSQL yang dijalankan. Hanya inspeksi file GitHub dan dokumentasi.
