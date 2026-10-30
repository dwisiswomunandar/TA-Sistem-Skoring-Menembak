# Layanan draft dan audit atomik — Iterasi 04B.2

Dasar: [baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md),
[04A](ASSESSMENT_RESULTS_ITERATION_4A.md), dan
[04B.1](HISTORICAL_PARENT_INTEGRITY_ITERATION_4B_1.md). Model, constraint dan
seluruh migrasi sebelumnya dipertahankan. Tidak diperlukan migrasi tambahan.

## API domain

assessments/services.py menyediakan lima fungsi sinkron, semuanya keyword-only:

| Fungsi | Operasi |
|---|---|
| create_initial_draft | Draft pertama, revisi 1, expected_edit_version=0 |
| update_draft | Catatan dan referensi bukti pada draft terbaru |
| add_shot_point | Titik manual baru; koordinat eksplisit dan nomor dari server |
| update_shot_point | Koordinat dan status penyertaan; asal AI/manual tetap |
| delete_shot_point | Menghapus titik draft, menyimpan payload sebelumnya dalam audit |

Semua fungsi menerima actor, session_id, operation_id UUID, expected_edit_version,
reason nonblank, serta using opsional untuk satu alias DB. Fungsi selain delete
menerima data dict. Mutasi existing membutuhkan assessment_id; update/delete
titik membutuhkan point_id. Argumen data hanya menerima allowlist per fungsi.

Assessment: notes, photo_id, detection_run_id, calibration_id,
scoring_rule_version_id. ShotPoint: x_px, y_px, is_included, exclusion_reason.
Payload status, skor, actor, pembuat, nomor revisi/titik, predecessor, sumber AI,
ID deteksi dan provenance scoring ditolak. Penambahan AI dari inferensi tidak
disediakan; titik AI tersimpan dapat dikoreksi tanpa mengubah sumber/ID asal.

Koordinat dikonversi ke Decimal lalu divalidasi presisinya sebelum normalisasi
fingerprint. Tidak ada pembulatan diam-diam. Scope foto/run/kalibrasi harus sama;
rule baru harus published, sedangkan referensi retired yang sudah dipilih dapat
dipertahankan. Perubahan referensi ketika titik masih ada tetap ditolak guard
04A; tidak ada reset draft atau pembukaan revisi dalam layanan ini.

## Identitas dan otorisasi

actor wajib User Django tersimpan yang berasal dari konteks autentikasi server
tepercaya, misalnya request.user yang sudah diverifikasi backend. Tidak ada
actor_id/actor_participation/actor_kind dari payload. AnonymousUser, ID/dict,
User belum tersimpan, alias DB berbeda, User hilang atau inactive ditolak.
Record User dibaca ulang; flag dan permission cache pada object caller tidak
dipercaya. User.is_authenticated pada model bukan bukti asal autentikasi; caller
tetap wajib mengambil principal dari boundary autentikasi, bukan me-resolve ID
yang dikirim klien lalu menyebutnya actor.

| Operasi | Permission wajib |
|---|---|
| Draft awal | assessments.add_assessment |
| Edit draft | assessments.change_assessment |
| Tambah titik | assessments.change_assessment + assessments.add_shotpoint |
| Edit titik | assessments.change_assessment + assessments.change_shotpoint |
| Hapus titik | assessments.change_assessment + assessments.delete_shotpoint |

Audit merupakan efek wajib operasi yang sah, sehingga tidak memerlukan grant
add_auditevent tersendiri. is_staff tidak memberi izin. Django has_perms dan
backend yang dikonfigurasi tetap menjadi sumber keputusan, termasuk perilaku
superuser bawaan; tidak ada shortcut role organisasi buatan aplikasi.

Dengan ModelBackend bawaan, permission tersebut **berskala model/global**, bukan
assignment atau keanggotaan kegiatan. assigned_assessor tidak dijadikan kebijakan
otorisasi baru yang belum disahkan. ID Assessment/ShotPoint/evidence tetap harus
cocok dengan scope sesi/parent perintah. Kebijakan object-level/lintas kegiatan
untuk organisasi nyata harus disahkan sebelum endpoint dibuka; layanan ini tidak
mengklaim telah menyelesaikan RBAC atau isolasi tenant.

## Transaksi dan urutan lock

Setiap perintah memakai satu atomic luar untuk perubahan domain, invalidasi
cache, versi parent, dan INSERT AuditEvent. Lock pertama: TrainingSession →
ActivityParticipation → Assessment → seluruh ShotPoint menurut PK → User aktor.
User dikunci terakhir dan permission diperiksa ulang sebelum mutasi; writer User
saat ini tidak mengambil lock domain. Lock model/audit berikutnya pada sesi,
partisipasi dan hasil yang sama bersifat reentrant dan telah dipegang transaksi.

Pembuatan audit memakai savepoint di dalam atomic luar agar collision UUID dapat
diterjemahkan dengan aman. Semua kegagalan tetap dipropagasikan ke atomic luar.
Tidak memakai on_commit untuk menunda audit. Kegagalan audit membatalkan
Assessment/titik, cache, timestamps dan kenaikan versi bersama-sama.

Setiap operasi baru yang berhasil menaikkan edit_version tepat sekali; create
menghasilkan versi 1. Perintah edit tanpa perubahan nilai juga merupakan perintah
baru dan menghasilkan satu audit/kenaikan versi. Request stale dengan UUID baru
ditolak tanpa perubahan. Model tetap memvalidasi status draft, snapshot konteks
historis dan referensi silang. verified/locked tidak dapat dimutasi.

Semua skor titik dibersihkan menjadi NULL, total dan marker scoring dibersihkan
pada mutasi draft. Invalidation cache memakai write internal terbatas terhadap
row draft yang sudah dikunci/tervalidasi, agar tidak menaikkan versi untuk setiap
row. Tidak ada jalur menulis skor non-NULL atau mengklaim hasil geometris benar.

## Idempotensi

operation_id wajib UUID. AuditEvent.operation_id yang sudah unique menjadi
ledger transaksi; tidak ada tabel operation baru. SHA-256 perintah kanonis
disimpan di after_state bersama command_result. Fingerprint mengikat versi
kontrak, action, User, sesi, Assessment/titik target, expected_edit_version,
payload ternormalisasi dan alasan. Hash ini bukan tanda tangan antimanipulasi DB.

Retry dengan UUID dan perintah kanonis identik mengembalikan DraftReceipt yang
sama: operation_id, audit_event_id, assessment_id, edit_version dan point_id.
Tidak ada mutasi, kenaikan versi atau audit duplikat. Pemeriksaan replay dilakukan
sebelum pemeriksaan versi stale/keberadaan titik yang mungkin sudah dihapus.
Permission dan keaktifan aktor tetap diperiksa pada setiap retry.

UUID yang sama untuk aktor/action/scope/payload/versi/alasan berbeda ditolak dengan
OperationConflict bila ledger ditemukan. Constraint unique juga melindungi
collision antartransaksi; kegagalan INSERT audit me-rollback perubahan domain.
UUID terikat hanya setelah audit berhasil commit. Perintah gagal tidak mereservasi
UUID. Koreksi payload setelah kegagalan dapat memakai UUID yang belum terikat.

Receipt adalah hasil **perintah historis**, bukan state agregat saat ini. Retry
lama setelah edit lain tidak mengembalikan state ke versi lama. Caller harus
membaca ulang agregat untuk state terkini. Perintah create kedua dengan UUID baru
ditolak jika sudah ada Assessment; ini tidak membuat revisi resmi.

## Snapshot dan batas keamanan

Snapshot schema_version 1 dibentuk dari atribut persistensi melalui allowlist:
identitas/revisi/versi/status, ID bukti, catatan, marker cache hitung terbatas,
dan titik dengan PK/nomor/koordinat/provenance/penyertaan/skor. Snapshot sesudah
dibaca dari DB agar enum dan Decimal memakai representasi tersimpan. Audit
memakai lingkup session dan FK assessment, actor_kind=user dari principal server.

Tidak menyalin request, header, cookie, password/hash, token/PIN, session key,
profile User, storage key, URL foto, parameter inferensi atau raw_result AI.
Opaque scoring_context tidak disalin; hanya boolean keberadaannya. Catatan,
alasan dan alasan pengecualian adalah teks bisnis yang sengaja diaudit; jangan
memasukkan kredensial ke teks tersebut. Ini bukan redaksi otomatis semua secret
yang mungkin disisipkan ke free text oleh caller.

Guard model 01–04B.1 tetap utuh. Direct Assessment.save/ShotPoint.save/delete
masih API ORM level rendah untuk kode tepercaya dan **belum otomatis membuat
audit bisnis**. Direct AuditEvent.save juga hanya validasi struktur, bukan
layanan otorisasi. Model admin, import, worker dan endpoint mendatang wajib memakai
layanan sesuai tujuan, bukan meneruskan payload ke model. SQL mentah, Model.save,
save_base, historical model dan private helper juga tetap bypass tepercaya.
Tidak ada parameter trusted=True atau bypass status/score publik baru.

Audit participant masih tertutup sampai adapter autentikasi tersedia. Tidak ada
scoring engine, verified/locked/reopen, endpoint, UI, YOLO26s, akun dummy atau
perubahan Laravel/FastAPI. Tidak ada commit/push atau akses PostgreSQL nyata.

## Verifikasi dan pekerjaan lanjutan

Tes memakai config.settings.test, SQLite dalam memori, dotenv dimatikan dan
Python -B. Meliputi lima operasi sukses, permission/aktor ditolak, payload
forbidden, referensi lintas konteks, request stale, normalisasi/idempotensi,
collision UUID, invalidasi cache, rollback audit dan versi konsisten. Tes rollback
memastikan pembuatan audit benar-benar dicoba sebelum kegagalan disimulasikan.

Hasil 31 Oktober 2026: **336 tes lulus (292 lama + 44 baru)**. Setelah assertion
rollback diperketat, seluruh 44 tes layanan juga diverifikasi ulang dan lulus.
System check tanpa masalah, makemigrations --check --dry-run menghasilkan
No changes detected, serta pemeriksaan git diff/whitespace mencakup file baru.
Tidak ada perubahan file terlacak/model/migrasi lama. Tes foto memakai temporary
directory Windows dengan izin filesystem di luar sandbox; DB tetap SQLite
dalam memori.

SQLite tidak membuktikan row lock atau race nyata. PostgreSQL khusus masih
memerlukan tes retry identik bersamaan, collision lintas sesi, penghapusan vs
update, FK/unique lock implisit dan deadlock retry. Pengubahan permission M2M oleh
administrator tidak otomatis mengikuti lock User layanan; semantik pencabutan
izin yang bersamaan memerlukan kontrak dan pengujian tersendiri.

Lanjutan: kebijakan object-level organisasi, registry action/retensi/redaksi,
kontrak autentikasi participant, endpoint dengan boundary autentikasi dan input
yang benar, idempotency retention, penomoran titik jika harus tidak pernah dipakai
ulang, lalu scoring/verifikasi/revisi resmi pada iterasi yang disahkan terpisah.
