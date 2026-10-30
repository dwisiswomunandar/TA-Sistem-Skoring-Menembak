# Database Iterasi 04A — Assessment, ShotPoint, AuditEvent

Dasar: [baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md), khususnya §3.5,
§4.1, §6, serta review desain Iterasi 04 dalam percakapan. Penambahan hanya
tiga model CORE terakhir; kelas berada di assessments/result_models.py dan
diekspos melalui assessments/models.py. Migrasi tambahan adalah
assessments/0003_assessment_shotpoint_auditevent.py. Model dan migrasi
Iterasi 01–03 dipertahankan.

## Batas 04A

Ini merupakan kontrak penyimpanan dan proteksi ORM, **bukan layanan penilaian
produksi**. Tidak ada scoring engine, inferensi, endpoint, UI, otorisasi,
adapter autentikasi peserta, layanan audit bisnis atomik, atau workflow
verify/lock/reopen. Tidak ada parameter fisik/skor/hasil AI yang diisi otomatis.

Penyimpanan publik Assessment hanya menerima draft awal (revisi 1), tanpa
skor/provenance perhitungan. Pengubahan menjadi verified/locked, penulisan skor
(termasuk nol), maupun pembukaan revisi baru ditolak. Data verified/locked dalam
tes adalah fixture sintetis yang dimasukkan melalui bypass ORM tepercaya,
ditandai eksplisit; bukan hasil resmi atau bukti validitas algoritma.

## Assessment

- FK session; revision_number; previous_assessment self-OneToOne; status
  draft/verified/locked; edit_version; FK photo/detection_run/calibration/
  scoring_rule_version; notes dan revision_reason.
- total_score nullable; historical_context; scoring_context;
  scoring_engine_version; scoring_input_sha256; scored_at; scored_edit_version.
- created_by, verified_by, locked_by dan timestamps. Semua FK PROTECT.
- Unique (session, revision_number), maksimal satu draft/verified per sesi,
  predecessor tunggal, nomor/versi positif, status terbatas, total nonnegatif,
  metadata lifecycle/provenance lengkap atau kosong, serta urutan waktu.
- Indeks (status, locked_at); indeks FK session tidak diduplikasi karena
  unique komposit telah menyediakan prefix session.

historical_context schema_version 1 diambil dari sesi tersimpan saat insert:
ID sesi/partisipasi/kegiatan/peserta, nomor/versi sesi, senjata, sikap, jarak,
dan kaliber. Besaran decimal disimpan sebagai string agar tidak kehilangan
presisi. Snapshot yang dikirim pemanggil harus identik dengan sumber server;
snapshot tidak boleh diganti setelah insert.

Foto harus berasal dari sesi yang sama. Run yang dipilih harus succeeded pada
foto yang sama, termasuk jika berhasil dengan nol deteksi. Kalibrasi harus
accepted pada foto yang sama. Rule harus pernah published; retired tetap
dapat direferensikan sebagai histori. Kebijakan pemilihan konfigurasi retired
untuk pekerjaan baru harus ditetapkan layanan 04B.

Skema memvalidasi predecessor locked, sesi sama, nomor tepat satu sebelumnya,
dan alasan revisi. Public save tetap menolak revisi > 1 karena proses cloning,
otorisasi, audit dan idempotensi belum tersedia. Validasi struktur ini tidak
membuka transisi produksi. Assessment yang tersimpan verified/locked tidak
dapat diubah/dihapus, termasuk dengan mengubah status instance menjadi draft.
Penghapusan Assessment melalui model juga ditutup sampai kebijakan retensi ada.

Marker scoring hanya disiapkan sebagai schema. SHA-256 input tidak dihitung,
engine tidak dipanggil, dan metadata bukan bukti bahwa perhitungan benar.
Validasi total terhadap jumlah skor titik hanya memeriksa konsistensi data
tersimpan, bukan kebenaran geometris atau otoritas skor.

Query helper latest_working(session_id=...) mengambil revisi terbesar;
latest_official(session_id=...) mengambil revisi locked terbesar. Keduanya
memerlukan lingkup sesi eksplisit, tetapi belum melakukan otorisasi. Locked lama
tetap tersimpan ketika draft baru ada; label sedang ditinjau/telah digantikan
dan kebijakan leaderboard masih menjadi pekerjaan layanan/laporan.

## ShotPoint

- FK assessment PROTECT; point_number positif, unik dalam assessment;
  x_px/y_px Decimal(16,6); source ai/manual; origin_detection_id;
  is_included; exclusion_reason; score nullable; timestamps.
- Unique bersyarat (assessment, origin_detection_id) untuk AI; source dan
  provenance harus cocok; skor nonnegatif; titik dikeluarkan memerlukan alasan
  dan score NULL. Batas teknis koordinat juga ditegakkan di DB.
- Koordinat wajib finite, bukan bool, 0 <= x < analysis_width_px dan
  0 <= y < analysis_height_px. Ukuran foto asli tidak digunakan sebagai batas
  bila dimensi analisis berbeda. Presisi ini bukan toleransi scoring lapangan.
- AI memerlukan run succeeded yang dipilih parent dan ID deteksi yang benar
  dalam raw_result. Manual tidak dapat mengaku mempunyai asal deteksi AI.
  Pemindahan titik AI oleh manusia tetap mempertahankan provenance aslinya.
- Parent, point_number, asal AI/manual, ID deteksi dan waktu pembuatan tidak
  dapat diganti. Parent verified/locked menolak tambah/edit/hapus, termasuk
  percobaan memindahkan titik ke draft lain.

Public save menolak score non-NULL. Titik draft dapat diedit/dihapus dengan
versi parent yang sesuai. Setelah ada titik, Assessment menolak penggantian
foto/run/kalibrasi/rule melalui save biasa. Reset draft yang ter-audit belum ada.

## Versi dan transaksi model

Update draft Assessment serta create/update/delete ShotPoint memerlukan
expected_edit_version integer yang cocok dengan versi Assessment tersimpan.
Versi dikelola server; perubahan berhasil menaikkannya sekali. Token hilang,
stale, bool, atau versi model yang diganti pemanggil ditolak.

Urutan model: lock sesi, lock Assessment, lalu lock ShotPoint. Penulisan titik
dan kenaikan versi/invalidation marker hitung parent berada dalam satu atomic;
kegagalan salah satunya me-rollback keduanya. Snapshot sesi diperiksa kembali
sebelum menulis. Tidak ada klaim transaksi perubahan-plus-audit bisnis: saat ini
penulisan draft dan pembuatan AuditEvent masih dua operasi model terpisah.

Manager publik/default/base dan related manager menolak update, queryset
delete, bulk_create, bulk_update, serta partial save. Async tetap menggunakan
guard yang sama; asave/adelete titik mendukung expected_edit_version.

## AuditEvent

- actor_kind user/participant/system; actor_user atau actor_participation atau
  system_component/system_version dengan constraint XOR kondisional.
- Lingkup utama tepat satu FK activity/participation/session. FK assessment
  opsional hanya pada lingkup sesi yang sama. Semua FK, termasuk caused_by_event,
  PROTECT. Identitas aktor dan lingkup kejadian merupakan dua hal berbeda.
- operation_id UUID unik; action nonblank; reason; before_state/after_state;
  version_before/version_after; created_at server. Tidak ada updated_at.
- Indeks (scope, created_at, id) untuk activity/participation/session/assessment
  tanpa indeks FK prefix duplikat. Versi mutasi harus naik tepat satu; event
  penciptaan dapat memakai before NULL dan after 1; event tanpa versi keduanya NULL.

AuditEvent hanya dapat di-insert; perubahan dan penghapusan ditolak. Event
user/system yang ditulis kode tepercaya divalidasi secara struktural, **bukan
diverifikasi identitas/kewenangannya oleh model**. Jangan menghubungkan model
ini langsung dengan payload klien. Action masih berupa kode nonblank; registry
action/permission dan alasan wajib per aksi menjadi pekerjaan 04B.

Principal peserta ditautkan ke ActivityParticipation, tanpa akun dummy.
authentication_context mempunyai schema_version, participation_id, method,
authenticated_at berzona waktu, authentication_id UUID nonsecret, dan
credential_generation opsional. Scope harus sesuai principal.

Namun JSON/ID tersebut bukan bukti autentikasi. Karena adapter tepercaya belum
ada, **public save AuditEvent participant selalu ditolak**. full_clean dapat
memvalidasi bentuk calon record untuk tes/desain. Tidak ada parameter
trusted=True, bypass publik, atau akun staff fiktif. Pengisian jalur participant
harus dilakukan setelah kontrak autentikasi dan audit atomik 04B tersedia.

Snapshot wajib objek JSON schema_version integer 1, finite, key string, tanpa
tuple/nilai Python non-JSON. Nama field secret umum ditolak rekursif. Filter
nama ini bukan jaminan redaksi semua isi secret; 04B wajib memakai allowlist
snapshot per aksi dan tidak menyimpan token/PIN/session key/Authorization.

## Keterbatasan dan pekerjaan wajib 04B

1. Implementasikan layanan atomik perubahan, audit, optimistic concurrency,
   idempotensi dan validasi versi terbaru. Model 04A tidak menjamin setiap
   perubahan draft sudah mempunyai event audit.
2. Bangun otorisasi nyata dan adapter principal peserta. FK User saja tidak
   membuktikan wewenang. Bedakan aksi pengguna dari job sistem; jaga kausalitas
   dan jangan melabeli registrasi anonim sebagai principal terautentikasi.
3. Bekukan TrainingSession.participation/nomor dan metadata fisik yang sudah
   menjadi dasar histori. Model Iterasi 01 masih mengizinkan sebagian perubahan.
   Snapshot 04A mempertahankan nilai asal dan menolak penulisan berikutnya ketika
   terjadi drift; tidak mencegah perubahan parent atau seluruh query tampilan.
4. Bekukan ActivityParticipation.activity/participant setelah dirujuk audit,
   termasuk sebagai aktor tanpa sesi. Perlindungan saat ini baru terkait
   keberadaan TrainingSession. Sesuaikan urutan locking model lama agar tidak
   berlawanan dengan urutan session-first pada hasil.
5. Setelah engine dan aturan lapangan disahkan, sediakan kalkulasi server,
   provenance/fingerprint yang benar, invalidasi, verifikasi, locking,
   pembukaan revisi, cloning titik, reset draft dan alasan wajib. Jangan
   membuka gate status/score 04A hanya dengan mengisi metadata.
6. Sahkan aturan kalibrasi, rounding/batas ring, kaliber, jumlah/meleset/
   tumpang tindih tembakan, target retired, nol titik, self-verification,
   leaderboard, akses withdrawn dan retensi. Belum ada keputusan operasional
   atau hasil AI yang diciptakan 04A.
7. Uji PostgreSQL khusus: locking/race, partial unique, numeric/nonfinite,
   JSON/regex, indeks, privilege dan trigger untuk proteksi histori/audit.

SQL langsung, QuerySet yang sengaja dibuat tanpa manager, save_base/raw fixture,
pemanggilan Model.save langsung, historical model, serta administrator DB masih
dapat melewati guard aplikasi, sama seperti Iterasi 02–03. Tes menyatakan batas
ini secara eksplisit. Tidak ada trigger atau klaim immutability DB absolut.

## Verifikasi

System check dan seluruh tes memakai config.settings.test (SQLite dalam memori),
PYTHON_DOTENV_DISABLED=1 dan Python -B. Foto tes sintetis berada di temporary
directory. Tes migrasi maju/mundur memeriksa bukti Iterasi 03 tetap utuh; tes
lama juga tetap memeriksa preservasi konfigurasi Iterasi 02.

Hasil akhir 30 Oktober 2026: **255 tes lulus (193 lama + 62 baru)**. System check
tanpa masalah; makemigrations --check --dry-run melaporkan No changes detected;
git diff --check bersih, termasuk pemeriksaan file baru. Tes filesystem dijalankan
dengan izin akses temporary Windows; database tetap SQLite dalam memori.
Tes mencakup schema, lifecycle tertutup, skor nullable/tidak dipercaya, FK lintas
konteks, constraint SQL, histori locked, guard bulk/base/related/async, versi stale,
rollback model, INSERT audit tanpa UPDATE dan preservasi migrasi lama.

Tidak ada akses PostgreSQL nyata, perubahan Laravel/FastAPI, commit atau push.
