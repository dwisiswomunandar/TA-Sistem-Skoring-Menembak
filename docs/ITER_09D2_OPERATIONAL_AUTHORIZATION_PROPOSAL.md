# ITER-09D2 — Proposal Kebijakan Otorisasi dan Alur Operasional Awal

**ID keputusan:** DEC-11 / CTRL-002  
**Tanggal:** 13 November 2026  
**Branch acuan:** `feat/django-migration`  
**Baseline sebelum dokumen:** `e1a0b9b`  
**Status:** **PROPOSAL — BELUM DISETUJUI UNTUK OPERASI ATAU IMPLEMENTASI**  
**Metode:** analisis dokumen/kontrak service dari ITER-09B, 09C, 09D1 serta register. **Tidak** ada wawancara Lapbak, validasi kewenangan, eksekusi CLI, pengubahan data/permission/settings, uji PostgreSQL, atau tes baru dalam penulisan ini.

## 1. Masalah dan tujuan terbatas

Pengujian browser membuktikan halaman login, dashboard, kegiatan dan Buat Sesi dapat ditampilkan. Tetapi kegiatan di worklist baru tampak apabila ada `TrainingSession` yang sudah di-assigned dan memenuhi permission + grant. Sementara pembuatan sesi pertama mensyaratkan aktivitas `active`, identitas peserta `verified`, pendaftaran `approved`, dua permission global, grant scoped kegiatan dan entry approval yang cocok. `provision_operational_context` dari ITER-09D1 hanya membuat `Activity(draft)`, `Participant(pending)`, `ActivityParticipation(pending)`.

Tujuan usulan ini: membuat **satu** alur bootstrap yang dapat diaudit, dengan pemisahan tiap keputusan sensitif. Ini **bukan** otorisasi untuk menaikkan status, memberi izin, atau memasukkan data nyata.

## 2. Aktor konseptual dan matriks tanggung jawab (BELUM DISAHKAN)

| Tindakan | Perwira/Pengelola (usulan) | Petugas Lapbak (usulan) | Peserta (usulan) | Dasar implementasi kini |
|---|---|---|---|---|
| Menyiapkan draft kegiatan & partisipasi | Mengajukan/menugaskan operator yang sah | Bantu input bila didelegasikan | Menyediakan data diri sesuai mekanisme | D1: service+CLI data-only, default disabled |
| Mengaktifkan kegiatan | Memberi keputusan otorisasi aktivasi | Melaksanakan bila didelegasikan | Tidak | Service aktivasi belum tersedia |
| Verifikasi identitas peserta | Menetapkan mekanisme dan verifikator | Melakukan pemeriksaan bila diberi kewenangan | Melengkapi data | Service keputusan belum tersedia |
| Menyetujui partisipasi | Menetapkan approver yang sah | Dapat mengusulkan atau memutus jika diberi kewenangan | Tidak menyetujui diri sendiri | Service belum tersedia |
| Menunjuk petugas/izin | Mengotorisasi ruang lingkup | Menggunakan hanya scope yang ditugaskan | Tidak | Grant/bootstrap existing terbatas |
| Membuat sesi pertama | Tidak otomatis hanya karena status perwira | Membuat hanya setelah explicit approval | Tidak | C: service+UI, default-deny |
| Verifikasi/locking skor | Menyetujui pemisahan peran & kondisi | Sesuai policy hasil terpisah | Tidak | ITER-08: policy default-deny |

**Jangan mengimplementasikan kolom aktor sebagai group Django, superuser bypass, atau role final sebelum pihak Lapbak mengonfirmasi.** Unit perlu menetapkan siapa berwenang, cara delegasi/revokasi, dasar verifikasi identitas, self-assignment, serta self-verification dan siapa boleh lock skor.

## 3. Alur proses minimum yang diusulkan (state machine)

Setiap transisi harus memakai service bisnis terotorisasi, actor hasil autentikasi server, alasan, operation UUID, referensi keputusan yang telah ditinjau, audit append-only, transaksi, verifikasi versi dan rollback pada kegagalan. Tidak melakukan `QuerySet.update`, raw SQL bypass, atau mengubah histori.

| Tahap | Kondisi sebelum → sesudah | Gerbang dan kewajiban |
|---|---|---|
| P0 — Persiapan | Tidak ada konteks → `Activity draft`, `Participant pending`, `Participation pending` | D1 hanya dapat dipanggil jika protected plan data-only sah cocok persis; tidak membuat approval |
| P1 — Aktivasi | `Activity draft → active` | Operator/pemberi keputusan tertentu disetujui, kegiatan ditinjau, audit dan alasan; **service belum tersedia** |
| P2 — Identitas | `Participant pending → verified` | Verifikator sah dan dasar verifikasi dicatat secara minimal/privat; **service belum tersedia** |
| P3 — Partisipasi | `Participation pending → approved` | Approver sah, parent P1+P2 valid menurut guard existing, audit; **service belum tersedia** |
| P4 — Permission & grant | Petugas yang disetujui memperoleh dua permission existing dan grant scoped | Explicit approval untuk user dan activity; *preflight* dampak permission global ke kegiatan lain; **tidak otomatis** |
| P5 — Approval sesi | Entry persetujuan `user_id + activity_id + participation_id` pada protected settings | Persetujuan sah, review perubahan konfigurasi, tidak dikirim dari payload pengguna; **default kosong** |
| P6 — Sesi pertama | Belum ada sesi → `TrainingSession` bernomor 1 (bila belum ada sesi lain) | Service ITER-09C, pilihan parameter ITER-05, actor sebagai assigned assessor, audit & UUID; tanpa hak memilih assignee lain |
| P7 — Worklist | Sesi terlihat pada kegiatan & partisipasi | Sesuai ITER-09B: assignment actor + global `change_assessment` + grant aktif; **dashboard ringkasan tetap default-deny** |

**Urutan P1/P2** dapat disesuaikan setelah validasi parent guards dan kebijakan lapangan, tetapi P3/P6 tidak boleh melompati syarat eligibility.

## 4. Persyaratan kebijakan untuk implementasi D2

1. **Keputusan organisasi terlebih dahulu.** Definisikan operator, approver/verifikator, otoritas aktivasi, dampak revokasi, evidence persetujuan dan aturan pemisahan tugas. Draf ini hanya menyarankan posisi; tidak memberi hak.
2. **Domain service minimal.** Tambahkan service untuk P1/P2/P3 dan provisioning P4 hanya setelah policy. Reuse model, permission, audit dan constraint existing. Tidak perlu CRUD web lengkap untuk milestone pertama.
3. **Jangan pakai akun superuser sebagai bypass.** Semua permission yang diperlukan harus benar-benar ada dan berlaku dalam scope actor/objek. Bootstrap existing tidak menambahkan `activities.add_trainingsession`. Pemberian permission global membutuhkan pemeriksaan grant/approval di seluruh konteks yang mungkin terdampak, atau desain delegasi yang lebih sempit.
4. **Protected settings tidak diaktifkan dengan flag kosong.** Persetujuan P5 harus disetujui secara independen, dipasang melalui jalur deployment terlindungi dan dapat dicabut. Referensi teks bukan bukti kriptografis persetujuan.
5. **Data development terpisah.** Contoh untuk tes memakai identitas sintetis, ditandai sebagai `development/simulation`; jangan menyisipkan nomor identitas personel nyata, foto target nyata, atau approval organisasi ke repo atau fixture produksi. Tidak ada mode bypass `is_demo` pada security path produksi.
6. **Tindakan sensitif tidak dipaketkan menjadi auto-approve.** Satu request pembuatan draft tidak otomatis mengaktifkan kegiatan, memverifikasi peserta, menyetujui partisipasi, menugaskan assessor atau mengaktifkan policy.
7. **Koreksi dan pencabutan.** Definisikan state transitions/expected version, ownership, revocation dan apakah aksi yang sudah diaudit menjadi immutable; jangan mengubah referensi historis untuk “merapikan” data.

## 5. Skenario uji penerimaan yang wajib disiapkan (BELUM DIJALANKAN)

| ID | Skenario | Ekspektasi |
|---|---|---|
| D2-T01 | Tanpa persetujuan/plan, panggil setiap aksi sensitif | Default-deny; tanpa perubahan row maupun audit sukses |
| D2-T02 | Actor tidak aktif, salah user, atau permission/grant dicabut | Ditolak sebelum dan dalam transaksi |
| D2-T03 | Actor superuser/staff tanpa approval yang tepat | Tetap ditolak |
| D2-T04 | Activity/participant/participation berbeda dari scope persetujuan | Ditolak tanpa kebocoran data/IDOR |
| D2-T05 | Gagal di tengah transisi atau saat audit | Rollback atomik, histori lama tidak berubah |
| D2-T06 | UUID sama + request sama; UUID sama + payload/actor berbeda | Replay receipt tanpa efek baru; konflik ditolak |
| D2-T07 | Memberikan permission global ketika user memiliki grant konteks lain | Preflight melaporkan dampak dan menolak perluasan akses tanpa persetujuan |
| D2-T08 | Skenario authorized pada DB test, dari draft hingga sesi pertama | Setiap transisi punya audit; nomor sesi unik, assignment actor, worklist hanya memuat konteks itu |
| D2-T09 | Dua request sesi simultan PostgreSQL | Tidak ada nomor ganda, audit/receipt valid, tanpa deadlock permanen |
| D2-T10 | Revokasi sebelum replay/GET halaman | Recheck otorisasi; akses yang dicabut tidak muncul |
| D2-T11 | Render mobile dan desktop setelah alur authorized | Form, navigasi, detail serta error akses berfungsi; tidak hanya tes HTML |

Tes sebelumnya (802 SQLite in-memory per ITER-09D1) **tidak** membuktikan T08–T11 di PostgreSQL.

## 6. Keputusan yang harus diperoleh sebelum pekerjaan kode D2

- **AUTH-01:** Pihak/role yang sah untuk membuat draft, mengaktifkan kegiatan, verifikasi identitas, approval partisipasi, memberi permission/grant dan revokasi; dokumen/sumber penetapan.
- **AUTH-02:** Apakah user yang sama boleh menjadi operator, verifikator, approver dan petugas penilai; hubungan dengan `allow_self_verification`, `allow_verifier_to_lock` ITER-08.
- **AUTH-03:** Siapa boleh mengubah protected settings/approval dan bagaimana persetujuan user+kegiatan+partisipasi diberikan/dicabut.
- **AUTH-04:** Kontrak pemilihan kegiatan dan pendaftaran peserta; apakah input awal oleh peserta sendiri, petugas, atau kombinasi.
- **AUTH-05:** Pengujian sintetis vs penggunaan nyata: data apa yang boleh dicatat dan siapa menyetujui penggunaan logo/identitas satuan.

**Status setiap AUTH-xx saat dokumen dibuat: BELUM DIPUTUSKAN.** Tidak ada implementasi atau perubahan policy yang diizinkan oleh penulisan proposal ini.

## 7. Handoff ketika Codex dipakai lagi

1. Mulai dari HEAD terkini dan `docs/PROJECT_DECISION_AND_CHANGE_REGISTER.md`. Baca dokumen ini, ITER-09D1, ITER-09C, ITER-09B serta audit source aktual.
2. Cocokkan keputusan `AUTH-01` sampai `AUTH-05` dengan bukti yang telah disetujui; jangan menafsirkan dokumen PROPOSAL sebagai izin.
3. Pecah implementasi ke service P1/P2/P3 lalu provisioning hak P4/P5, dan integrasikan melalui UI existing. Utamakan satu scope per commit; audit permission global dan state/constraint sebelum perubahan model.
4. Tambahkan tes fail-closed, rollback, replay, revocation dan PostgreSQL concurrency; laporkan yang benar-benar dijalankan.
5. Update register dan dokumentasi iterasi dengan hash commit, bukti uji, dan perubahan/supersedes. Tidak menjalankan Codex, server, migration atau perubahan aplikasi selama penulisan proposal ini.

**Referensi internal:** `docs/ITER_09D1_SECURE_CONTEXT_PROVISIONING.md`, `docs/ITER_09C_AUTHORIZED_SESSION_CREATION.md`, `docs/ITER_09B_OPERATIONAL_UI.md`, `docs/ITER_08_AUDITED_ASSESSMENT_FINALIZATION.md`, `docs/PROJECT_DECISION_AND_CHANGE_REGISTER.md`.
