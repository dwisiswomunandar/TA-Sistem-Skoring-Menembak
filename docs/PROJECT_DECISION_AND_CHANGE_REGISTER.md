# Register Keputusan, Perubahan, dan Audit Proyek — KOPASKA Shooting Range

**ID:** CTRL-001  
**Dicatat:** 13 November 2026  
**Branch acuan:** `feat/django-migration`  
**Baseline terakhir sebelum dokumen ini:** `c1953b7`  
**Tujuan:** indeks kontrol perubahan dan keputusan agar pekerjaan berikutnya—termasuk oleh Codex—dapat diaudit tanpa bergantung pada ingatan percakapan.

> **Batas kepastian:** Dokumen ini mencatat laporan pengguna, dokumentasi iterasi, riwayat commit, dan observasi browser yang telah diberikan. Status **PROPOSAL/PROVISIONAL** bukan persetujuan organisasi atau bukti observasi lapangan. Status **IMPLEMENTED** bukan bukti kesiapan operasional/akurasi AI. Jangan merekayasa persetujuan, hasil tes, atau verifikasi yang belum berlangsung.

## 1. Aturan pencatatan wajib sejak CTRL-001

Setiap keputusan penting, perubahan kode, konfigurasi, aset, akses, desain database, logika scoring, hasil pengujian, dan perubahan scope harus mempunyai jejak tercatat **sebelum dianggap selesai**. Untuk perubahan yang dapat memengaruhi kewenangan, keamanan, penilaian, atau histori, catat keputusan dan alasannya **sebelum implementasi**; catat hasil aktual setelahnya.

Kolom minimum setiap entri baru:
- **ID/tanggal** (waktu Indonesia Barat bila diketahui) dan sumber permintaan/keputusan.
- **Status:** `PROPOSAL`, `PROVISIONAL`, `APPROVED_FOR_IMPLEMENTATION`, `IMPLEMENTED`, `VERIFIED`, atau `BLOCKED`. Persetujuan desain oleh pemilik proyek **berbeda dari** persetujuan operasional unit.
- **Apa/kenapa:** keputusan, alasan, ruang lingkup, alternatif/risiko, batas default-deny.
- **Artefak:** path file, nama modul, dan commit/PR; jika hanya lokal atau belum ada commit, sebutkan jelas.
- **Verifikasi:** perintah/suite, jumlah dan hasil aktual, DB/environment, browser/manual vs automated; kosong jika belum diuji.
- **Konsekuensi:** dampak ke SRS, ERD/schema, keamanan, UI, API, tes, dan naskah; keputusan yang belum diambil serta pihak yang harus memutuskan.
- **Riwayat revisi:** jangan hapus klaim/keputusan lama diam-diam; tambahkan catatan koreksi/supersedes, sumber dan alasan.

Satu commit sebaiknya satu scope yang dapat direview. Jangan commit kredensial, `.env`, file persetujuan nyata sensitif, identitas peserta, evidence target operasional, atau konfigurasi protected-plan. Perubahan repository oleh AI harus menjelaskan apakah tes dijalankan atau hanya pemeriksaan statis. AuditEvent di database adalah jejak tindakan aplikasi, **bukan pengganti** register keputusan proyek ini.

## 2. Register keputusan yang relevan

| ID | Keputusan / batas | Status per 13 Nov 2026 | Dasar dan tindak lanjut |
|---|---|---|---|
| DEC-01 | Django menjadi stack web aktif pada branch migrasi, dengan komponen AI terpisah; jangan anggap Laravel sebagai implementasi aktif branch ini | IMPLEMENTED untuk fondasi Django | Periksa `src/django-app/`, commit iterasi, dan dokumen domain; spesifikasi/deployment AI belum final |
| DEC-02 | Parameter jenis menembak: laras panjang 5,56 mm / 100 m, pilih sikap; pistol 9 mm / berdiri, pilih 15/20/25 m | PROVISIONAL; validator ITER-05 terimplementasi | [Keputusan 2 Nov](DECISION_2026-11-02_PARAMETER_LATIHAN_MENEMBAK_PROVISIONAL.md), [ITER-05](ITER_05_PROVISIONAL_SHOOTING_PARAMETERS.md); **wajib konfirmasi lapangan** |
| DEC-03 | AI memberi titik deteksi; kalibrasi terukur dan skor geometris membutuhkan profil target/ground truth serta evaluasi nyata | IMPLEMENTED sebagai service; VALIDASI LAPANGAN BELUM | [ITER-06](ITER_06_AUDITED_DETECTION_POINT_IMPORT.md), [ITER-07](ITER_07_MEASURED_TARGET_CALIBRATION.md); jangan klaim akurasi terukur |
| DEC-04 | Verifikasi/lock hasil penilaian dan pembuatan sesi memerlukan approval/permission/grant eksplisit; tidak ada bypass implisit staff/superuser/creator | IMPLEMENTED, default-deny; kebijakan organisasi PROVISIONAL | [ITER-08](ITER_08_AUDITED_ASSESSMENT_FINALIZATION.md), [ITER-09C](ITER_09C_AUTHORIZED_SESSION_CREATION.md) |
| DEC-05 | Worklist hanya memperlihatkan kegiatan/peserta dengan sesi yang ditugaskan dan scope grant; kegiatan tanpa sesi tidak otomatis tampak | IMPLEMENTED; keterbatasan bootstrap terbuka | [ITER-09B](ITER_09B_OPERATIONAL_UI.md); tidak boleh dibuka dengan data lintas kegiatan |
| DEC-06 | Penyiapan konteks hanya menghasilkan Activity draft, Participant pending, ActivityParticipation pending; perubahan status dan pemberian izin terpisah | IMPLEMENTED tetapi default-deny | [ITER-09D1](ITER_09D1_SECURE_CONTEXT_PROVISIONING.md); protected plan awal masih kosong |
| DEC-07 | Identitas **UI aplikasi** adalah **KOPASKA / Shooting Range**, dengan logo KOPASKA dan tema Tactical Command Center; jangan membawa identitas STTAL ke UI. Identitas institusi pendidikan pada dokumen akademik tidak ikut dihapus | IMPLEMENTED dan terlihat pada browser untuk branding; izin publikasi lambang perlu dipastikan | Commit `ef963fd`, `1b7f6c7`, `c1953b7`; aset `src/django-app/static/ui/img/logo-kopaska.png` |
| DEC-08 | Pengujian lokal memakai PostgreSQL 17.11 dan DB development `ta_menembak_dev`, bukan DB production; suite otomatis iterasi yang dilaporkan sebelumnya memakai SQLite in-memory | ENVIRONMENT VERIFIED SEBAGIAN | Koneksi Django, migrasi, login, navigasi, tampilan desktop dan emulasi mobile diperiksa manual; **belum** ada tes konkurensi PostgreSQL |
| DEC-09 | Tunda Codex ketika pengguna melaporkan sisa kuota weekly 3%; lakukan review, dokumentasi, perancangan, dan perubahan kecil langsung yang dapat diaudit | KEPUTUSAN KERJA SEMENTARA | User 13 Nov 2026; tidak berarti code review/performance otomatis sudah dilakukan |
| DEC-10 | Rancangan alur tiga aktor (Perwira/Pengelola, Petugas Lapbak, Peserta) dipakai sebagai bahan diskusi hak akses | **PROPOSAL, BUKAN PENGESAHAN ROLE OPERASIONAL** | Lihat Bagian 4; harus direkonsiliasi dengan hasil observasi dan kewenangan unit |
| DEC-11 | Urutan penyiapan kegiatan dan keputusan sensitif untuk ITER-09D2 dibatasi per konteks, dipisah dari UI dan dari aktivasi akses otomatis | **PROPOSAL; TIDAK ADA PERSETUJUAN OPERASIONAL** | [Rancangan kebijakan ITER-09D2](ITER_09D2_OPERATIONAL_AUTHORIZATION_PROPOSAL.md), dicatat 13 Nov 2026. Belum mengubah permission, data atau protected plans |
| DEC-12 | Klarifikasi pengguna: **Admin Lapbak melaksanakan pembuatan dan aktivasi kegiatan latihan atas perintah Perwira Lapbak**; tidak boleh menyamakan Admin Lapbak dengan superuser atau petugas penilai | **PROVISIONAL — BELUM OBSERVASI LAPANGAN** | Disampaikan pengguna 13 Nov 2026; memperjelas/menimpa hanya asumsi pelaksana kegiatan pada DEC-10/DEC-11, bukan kebijakan organisasi yang disahkan. [Rancangan ITER-09D2](ITER_09D2_OPERATIONAL_AUTHORIZATION_PROPOSAL.md) |
| DEC-13 | Protokol pengujian dipisah menjadi fungsi UI/domain, deteksi AI, kalibrasi, scoring, dan integrasi; satuan dan holdout disusun untuk menghindari kebocoran data antarfoto satu target | **PROPOSAL — METODE PENGUJIAN, BUKAN HASIL** | [Protokol pengujian](TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md), 13 Nov 2026. Metode, data, hasil dan ambang lulus harus dikonfirmasi; belum ada tes atau dataset aktual yang dijalankan |

**Peringatan global ketidakpastian:** Pengguna telah menegaskan bahwa **seluruh alur kegiatan dan kewenangan yang disampaikan sejak awal proyek masih bersifat sementara** sampai observasi langsung/pengambilan data. Tidak satu pun keterangan peran/kebijakan operasional boleh ditingkatkan menjadi hasil observasi atau persetujuan resmi tanpa bukti baru. Implementasi/proteksi teknis yang sudah ada tetap fakta kode, namun **tidak otomatis memvalidasi aturan lapangan**.

**Perhatian terhadap perbedaan kebutuhan:** dokumen keputusan parameter 2 November mencatat pilihan **kamera atau galeri**. Jika ada dokumen/pernyataan lain yang membatasi **kamera saja**, tandai konflik dan minta keputusan eksplisit sebelum ITER-10. Jangan memilih sepihak.

## 3. Indeks perubahan dan bukti verifikasi

| Tahap/perubahan | Commit | Bukti yang tercatat dan batas |
|---|---|---|
| ITER-05 — validator parameter sesi | `7697839` | Laporan 613 tes Django lulus; parameter masih provisional |
| ITER-06 — import AI detection ke ShotPoint | `7b8806a` | Laporan 641 tes lulus; bukan validasi akurasi model nyata |
| ITER-07 — kalibrasi terukur | `0c11601` | Laporan 674 tes lulus; profil referensi/pengukuran lapangan belum disahkan |
| ITER-08 — verify/lock teraudit | `5b29811` | Laporan 708 tes lulus; keputusan kebijakan masih kosong/default-deny |
| ITER-09A — login, dashboard, layout | `5bf40e5` | Laporan 723 tes full regression lulus; kemudian login/dashboard teramati di browser lokal |
| ITER-09B — UI kegiatan/partisipasi/sesi | `bbd8391` | Laporan 746 tes lulus; browser lokal menampilkan default-deny kegiatan |
| ITER-09C — pembuatan sesi terotorisasi | `9b40560` | Laporan 774 tes lulus; form default-deny teramati, sesi operasional belum dibuat |
| ITER-09D1 — context provisioning | `e658464` | Laporan 802 tes lulus dengan SQLite in-memory; DB development/production tidak disentuh oleh implementasi |
| Branding UI menjadi KOPASKA / Shooting Range | `ef963fd` | Template/CSS diubah via GitHub; belum menjalankan suite sesudah perubahan |
| Perbaikan bingkai logo lama | `1b7f6c7` | Class CSS lama dihapus; pengguna mengonfirmasi perbaikan tampilan |
| Logo PNG ditambahkan ke Git | `c1953b7` | Pengguna mengonfirmasi commit/push, working tree bersih; izin penggunaan/publikasi lambang perlu dipastikan |

**Tidak boleh disimpulkan:** angka suite dari iterasi berbeda bukan satu suite identik pada seluruh commit. Tidak ada klaim bahwa perubahan branding telah melewati full regression, bahwa kamera/AI UI sudah terintegrasi, atau bahwa database operasional sudah diotorisasi.

## 4. ITER-09D2 — rancangan awal untuk keputusan, BUKAN instruksi eksekusi

**Target:** menghilangkan deadlock administrasi awal secara terkontrol sehingga pengujian `kegiatan → peserta → partisipasi → petugas berwenang → sesi pertama → worklist` dapat dilakukan tanpa bypass. Jangan langsung aktifkan protected plans/settings atau membuat approval/referensi fiktif.

| Aksi | Aktor yang *diusulkan* | Syarat minimum yang harus dirinci | Status |
|---|---|---|---|
| Menyiapkan draft kegiatan dan data peserta | **Admin Lapbak atas perintah Perwira Lapbak** (klarifikasi pengguna; belum observasi) | Identitas operator, tujuan, scope satu konteks, cara membuktikan/menautkan perintah yang sah belum dikonfirmasi | PROVISIONAL; service data-only ITER-09D1 tersedia, default-deny |
| Aktivasi kegiatan | **Admin Lapbak atas perintah Perwira Lapbak** (klarifikasi pengguna; belum observasi) | Keputusan status, bukti perintah, audit actor/timestamp/alasan; tanpa aktivasi otomatis | PROVISIONAL; service belum ada |
| Verifikasi identitas peserta | Petugas verifikator yang ditetapkan | Dasar verifikasi & bukti yang boleh dicatat, pemisahan tugas bila dibutuhkan | POLICY PENDING; service belum ada |
| Approval pendaftaran/partisipasi | Pengelola/petugas yang disetujui | Scope activity+participation, alasan, otoritas, audit dan revocation | POLICY PENDING; service belum ada |
| Grant/permission dan penugasan petugas | Admin kegiatan yang secara eksplisit ditetapkan | Dua permission existing `activities.add_trainingsession` dan `assessments.change_assessment` + grant `change_assessment` per kegiatan; analisis dampak permission global ke kegiatan lain | POLICY PENDING; bootstrap existing tidak memberi `add_trainingsession` |
| Membuat sesi pertama | Petugas yang disetujui untuk konteks spesifik | Persetujuan user+activity+participation, eligibility, dua permission, grant aktif, self-assignment eksplisit | Service/UI ITER-09C tersedia, settings masih default-deny |

**Pertanyaan wajib sebelum mengizinkan implementasi operasional:**
1. Siapa pejabat/operator yang berwenang membuat, mengaktifkan, memverifikasi, menyetujui, serta mencabut tiap keputusan, dan bagaimana evidensinya?
2. Apakah satu petugas boleh merangkap verifikator, approver, assessor, serta verifier/locker nilai? Terutama self-verification dan self-assignment harus diputuskan secara terpisah.
3. Bagaimana membentuk akun/permission global minimum tanpa meningkatkan akses ke kegiatan lain? Siapa memegang kewenangan perubahan protected settings?
4. Bagaimana menandai serta mengisolasi data **development/simulasi** dari operasi Lapbak yang sebenarnya?

**Rencana D2 yang paling kecil setelah ada keputusan:** (a) keputusan kebijakan tertulis/terverifikasi; (b) service aktivasinya, identitas/partisipasi, provisioning permission/grant sesuai scope; (c) tes gagal aman dan audit rollback/UUID, isolation, revoke; (d) tes PostgreSQL development khusus; (e) uji alur browser. Tidak ada implementasi atau aktivasi otomatis yang diotorisasi oleh dokumen ini.

## 5. Backlog verifikasi & ketergantungan

- [ ] Otorisasi organisasi Lapbak untuk role/aktivasi/approval, pemisahan tugas, identitas, penggunaan lambang.
- [ ] Rekonsiliasi kontradiksi kamera-only vs kamera/galeri dan pembaruan SRS.
- [ ] Konfigurasi dan pengukuran target fisik, kalibrasi/ground truth, model YOLO dan evaluasi scoring di data nyata.
- [ ] Pengujian transaksi/race PostgreSQL, keamanan runtime, PostgreSQL deployment.
- [ ] Uji form operasional dengan data development terpisah setelah policy yang tepat; bukan membuka akses superuser otomatis.
- [ ] Uji gambar logo pada deployment `collectstatic`, verifikasi responsive mobile/UX dan aksesibilitas.
- [ ] Perbarui README root yang masih merujuk baseline Laravel/YOLOv8 sebelum milestone dokumentasi publik; jangan mengubah klaim historis tanpa jejak.

## 6. Instruksi handoff untuk Codex/reviewer berikutnya

1. Pastikan branch dan HEAD terbaru; baca **dokumen ini**, lalu dokumen iterasi terkait, model/service dan tes yang benar-benar ada. Jangan menganggap tabel ini menggantikan verifikasi source.
2. Bandingkan commit terakhir dengan `git diff`/log dan cari perubahan lokal yang belum tercatat; **jangan overwrite** perubahan pengguna.
3. Tinjau klaim **VERIFIED** terhadap environment dan evidence aktual. Pisahkan hasil unit test SQLite, browser lokal dan PostgreSQL nyata.
4. Semua perubahan baru harus menambah/merujuk entry bernomor pada register ini, memperbarui dokumen iterasi & tes yang relevan, menyebut commit/path/approval/blocker. Jika ada keputusan baru yang menyanggah keputusan lama, tambahkan `supersedes` dan alasan.
5. Pertahankan default-deny tanpa verifikasi kewenangan. Jangan mengaktifkan protected plan, mengarang approval, mengabaikan audit, atau membuat seed operasional.
6. Lakukan code review dan regresi terfokus sesuai risiko sebelum commit; catat pengujian yang **tidak** dijalankan. Pisahkan commit dokumentasi dari perubahan perilaku kode bila memungkinkan.

**Riwayat register:** CTRL-001 = pembentukan indeks audit dan proposal D2, bersumber dari dokumen repo, log commit, laporan tes, dan observasi browser yang dibagikan hingga 13 November 2026; tidak mengubah kode aplikasi atau database.

**Riwayat tambahan — 13 November 2026, CTRL-002:** rancangan kebijakan dan urutan transisi ITER-09D2 ditulis sebagai PROPOSAL, mengikuti audit D1/C/B dan diskusi. Catatan ini tidak berarti persetujuan organisasi, perubahan backend, pengujian baru, atau aktivasi akses. Dokumen: `docs/ITER_09D2_OPERATIONAL_AUTHORIZATION_PROPOSAL.md`.

**Riwayat tambahan — 13 November 2026, CTRL-003:** klarifikasi kebutuhan pengguna dicatat sebagai DEC-12 (Admin Lapbak adalah pelaksana pembuatan/aktivasi kegiatan atas perintah Perwira Lapbak). Pengguna mengingatkan bahwa semua alur dan kewenangan yang telah disampaikan sejak awal masih sementara sampai observasi/pengambilan data. Penyesuaian ini hanya dokumentasi, tidak mengaktifkan approval, tidak memberi hak akses, tidak menjalankan migrasi/tes, dan tidak mengubah program. Rancangan sebelumnya yang menempatkan Perwira/Pengelola sebagai pelaksana langsung **digantikan pada dua aksi tersebut saja**, bukan dihapus dari jejak historis.

**Riwayat tambahan — 13 November 2026, CTRL-004:** dibuat proposal protokol pengujian sistem, deteksi AI, kalibrasi, dan scoring (`docs/TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md`). Termasuk checklist traceability, rencana ground truth, evaluasi per-target dan batas data, tanpa eksekusi tes, tanpa perubahan kode/migration/DB, tanpa izin penggunaan data nyata dan tanpa klaim hasil. Seluruh aturan lapangan tetap provisional sampai observasi.
