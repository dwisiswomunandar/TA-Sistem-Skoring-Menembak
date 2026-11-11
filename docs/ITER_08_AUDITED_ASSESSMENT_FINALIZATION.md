# ITER-08 - Audited Assessment Verification and Locking

Baseline: feat/django-migration pada 0c11601. Menambahkan command bisnis
verify_assessment dan lock_assessment di assessments.finalization_services.
Tidak ada perubahan model/migration, dependency, AI/scoring engine, UI, endpoint,
atau fitur reopen. Semua guard publik existing tetap dipertahankan.

## Kontrak transisi

Kedua command menerima keyword-only actor, session_id, assessment_id,
expected_edit_version, expected_evidence_versions, operation_id, reason, optional
using. expected_evidence_versions memakai kontrak existing: calibration,
target_version, scoring_rule_version; masing-masing berisi id dan edit_version.
Actor adalah User hasil autentikasi backend tepercaya, bukan ID dari payload.
Tidak menerima status, skor, timestamp, verifier/locker ID atau flag bypass.

- verify_assessment: hanya draft terbaru -> verified, dengan skor server yang
  sudah dihitung, masih valid, serta evidence dan audit yang konsisten.
- lock_assessment: hanya verified terbaru -> locked, dengan audit verifikasi sah
  dan seluruh input/hasil yang masih cocok. Tidak ada transisi langsung draft
  ke locked, revisi baru, penurunan status, edit hasil locked, atau auto-reopen.
- Setiap transisi menaikkan edit_version sekali, mencatat aktor/timestamp server,
  dan menambahkan satu AuditEvent dalam transaksi yang sama.
- Hasil menggunakan DraftReceipt existing dengan point_id=None.

## Otorisasi provisional, default-deny

ASSESSMENT_FINALIZATION_POLICIES secara default () sehingga tidak ada akses baru
secara otomatis. Protected deployment settings dapat menyediakan satu policy per
activity_id, dengan field persis:

- activity_id: integer positif.
- verify_user_ids, lock_user_ids: daftar ID User yang disetujui untuk masing-masing
  aksi; tidak menyimpulkan kewenangan dari nama role atau created_by.
- allow_self_verification: bool eksplisit.
- allow_verifier_to_lock: bool eksplisit.
- allow_empty_results: bool eksplisit, tanpa mengarang jumlah tembakan/meleset.
- allow_manual_only_results: bool eksplisit untuk hasil yang hanya menyertakan
  titik manual; bukan izin memakai skor total dari klien.
- approval_reference: referensi persetujuan yang tidak kosong.

Tidak ada ID, approval, atau policy operasional nyata yang diisi oleh iterasi ini.
Policy kosong/tidak lengkap/duplikat atau tipe bool yang salah selalu ditolak.
Policy ini merupakan mekanisme persetujuan provisional, bukan aturan organisasi.
Referensi persetujuan adalah metadata penelusuran, bukan bukti kriptografis.

Policy menambah pembatasan pada otorisasi existing: User aktif, global
change_assessment, grant aktif pada kegiatan, dan assigned_assessor. Staff maupun
superuser tidak melewati scope, assignment atau policy eksplisit. Authorization
diperiksa sebelum kerja, di bawah lock, serta sebelum setiap receipt replay.

Kontrak existing hanya mendukung assigned_assessor. Konservatifnya petugas ini
dianggap self-verifier; allow_self_verification=False tidak membuka jalur verifier
independen. Karena assignment historis dibekukan, pola dua petugas independen
memerlukan keputusan/desain authorization berikutnya. Tidak ada role organisasi
atau perpindahan penugasan yang diciptakan untuk melewati pembatasan tersebut.
Lock oleh verifier yang sama juga memerlukan persetujuan eksplisit tersendiri.

## Validitas skor dan provenance

Command memakai urutan lock scoring existing: session -> participation -> assessment
-> points menurut PK -> photo -> optional run -> calibration -> target -> rings
menurut PK -> rule -> User -> grants. FK, status, snapshot sesi, versi request dan
seluruh kumpulan titik/ring dimuat ulang dari DB, bukan dipercayai dari klien.

Cache harus mempunyai satu audit assessment.draft_scored yang cocok. Input
snapshot/fingerprint dibentuk ulang dengan helper scoring existing dan hasil
dihitung ulang READ-ONLY memakai engine yang sama. Skor titik/total, engine version,
input hash, context lengkap dan audit asal harus identik. Ini bukan penulisan skor
baru. Metadata lifecycle/identity dan hash snapshot verifikasi dipakai untuk
mendeteksi drift sebelum lock. Audit ambigu atau state forged tanpa lineage ditolak.

Foto asli diperiksa ulang dengan inspect_original existing (hash, ukuran byte,
dimensi dan MIME). Selected run, bila ada, harus succeeded dan envelope-nya cocok.
Titik AI harus mempunyai audit impor yang cocok dengan identitas asal tiap titik,
hash output/parameters, model/bobot/runtime/adapter dan identitas foto. Koreksi
koordinat manusia tetap diperbolehkan saat draft tanpa menghapus asal AI. Titik AI
legacy tanpa audit impor yang memadai tidak otomatis disahkan oleh command ini.
Manual fallback tetap geometris dan memerlukan policy eksplisit. Deteksi kosong
bukan sepuluh tembakan nol; hasil kosong memerlukan policy eksplisit tersendiri.

## Token versi dan konservasi histori

Constraint existing mengharuskan scored_edit_version == edit_version. Saat transisi
kedua token maju bersama sebagai carry-forward validitas cache. Ini BUKAN klaim
bahwa scoring baru disimpan: scored_at, point scores, total, scoring_input_sha256,
scoring_context dan historical_context tidak ditulis ulang. Input context tetap
mencatat versi pre-scoring asli; audit transisi menyimpan source_scored_version dan
source_scoring_event_id yang asli. Revalidasi memakai versi input dari audit asal,
bukan mengganti snapshot agar cocok dengan token lifecycle terbaru.

Audit verify caused_by_event -> audit scoring; audit lock caused_by_event -> audit
verify. Masing-masing menyimpan state sebelum/sesudah dan snapshot policy aktif.
Source audit dan event lama tidak diperbarui. Penulisan status dibatasi pada internal
Model.save untuk field lifecycle/token saja, setelah seluruh validasi. Tidak ada
trusted=True, flag bypass publik, atau pelemahan guard model/queryset.

UUID/replay, receipt dan collision rollback memakai helper existing. Replay identik
mengembalikan receipt historis tanpa mengubah state, termasuk verify replay setelah
locked. Current authorization/policy tetap wajib; replay tidak memulihkan skor,
menulis timestamp baru atau memeriksa ulang bytes evidence untuk mengulang operasi.
UUID dengan payload/actor/scope/action berbeda ditolak. Kegagalan validasi,
persistence, engine read-only atau audit me-rollback seluruh perubahan transisi.

## Keputusan dan batasan yang belum final

Siapa yang boleh verify/lock, self-verification, pemisahan petugas, manual-only,
hasil kosong dan approval organisasi masih PROVISIONAL. Deployment harus tetap
menolak secara default sampai keputusan eksplisit tersedia. Policy historis dicatat
pada saat keputusan; aksi lock baru harus memenuhi policy lock saat ini.

Jika target/rule/evidence berubah token atau input setelah verified, lock ditolak.
Tidak ada automatic refresh, rescore verified atau reopen untuk memperbaikinya.
Perlu koordinasi lifecycle konfigurasi selama proses finalisasi atau prosedur
pemulihan auditable pada iterasi terpisah; jangan memperbaiki histori melalui SQL.
Model/SQL/filesystem administrator tetap berada di luar jaminan guard aplikasi.
Verifikasi byte terakhir juga bukan continuous filesystem immutability.

## Verifikasi aktual - 11 November 2026

- Focused: seluruh 34 tes lulus, tanpa kegagalan.
- Full regression dijalankan satu kali setelah stabil: 708 tes lulus dalam
  117.321 detik (674 baseline + 34 baru).
- Django system check: tanpa masalah.
- Migration drift: No changes detected.
- git diff --check serta syntax/whitespace termasuk file baru: lulus.
- Seluruh tes memakai config.settings.test, SQLite dalam memori, dotenv/bytecode
  dinonaktifkan, serta foto, deteksi, metrologi dan approval sintetis.

Cakupan mencakup alur penuh import -> measured calibration -> scoring -> verified
-> locked, default-deny, privilege/scope, cache/provenance forgery, input stale,
replay/revocation, UUID collision, timestamp, rollback dan immutable history.
Simulasi collision dan pencatatan urutan lock bukan uji race/locking PostgreSQL.
Tidak ada database operasional atau inferensi nyata yang diakses. Status verified
atau locked pada tes bukan validasi akurasi/aturan lapangan. Uji PostgreSQL khusus
serta keputusan policy dan metrologi nyata diperlukan sebelum penggunaan operasional.
