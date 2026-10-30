# Integritas parent historis — Iterasi 04B.1

Dasar: [baseline v1.1](DATABASE_DESIGN_BASELINE_v1.1.md) §3.2, §4, §6 dan
[batas Iterasi 04A](ASSESSMENT_RESULTS_ITERATION_4A.md). Cakupan hanya proteksi
parent domain dan penyelarasan penguncian referensi. Tidak ada scoring engine,
otorisasi, endpoint, UI, inferensi atau workflow hasil verified/locked baru.

## Konteks yang dibekukan

| Parent | Referensi yang membekukan | Field yang dilindungi |
|---|---|---|
| TrainingSession | TargetPhoto.session, Assessment.session (termasuk draft), AuditEvent.session | participation, session_number, weapon_type, shooting_position, distance_m, caliber_mm |
| ActivityParticipation | TrainingSession.participation, AuditEvent.participation, AuditEvent.actor_participation | activity, participant |

Pemeriksaan menggunakan data tersimpan, bukan related object yang dicache atau
hasil prefetch lama. ID FK dibandingkan dengan parent asal di database, sehingga
pemindahan konteks ke parent baru tidak dapat menghindari histori sumber.

assigned_assessor dan created_by pada sesi yang sudah direferensikan juga
dibatasi: koreksi atribusi/penugasan historis memerlukan audit bisnis yang belum
tersedia. edit_version sesi yang dirujuk tidak boleh diganti pemanggil, karena
snapshot historical_context 04A menyimpan versi sesi tersebut. created_at kedua
parent tidak boleh ditulis ulang. updated_at boleh berubah pada save tanpa
perubahan payload; ini tidak mengganti konteks historis.

Sesi tanpa referensi tetap dapat dikoreksi/dipindahkan ke partisipasi yang sah.
Pemindahan harus mengulang syarat destination active/approved/verified, bukan
hanya memeriksa keberadaan FK. Partisipasi tanpa sesi/audit tetap boleh mengganti
pasangan activity/participant, tunduk pada validasi dan unique constraint lama.

## Administrasi dan stale object

Validasi administratif Iterasi 01 tetap berlaku: perubahan status/alasan,
metadata keputusan dan participant_identifier tidak mengganti pasangan FK
kanonis. Withdrawal tetap mempertahankan sesi lama dan menolak sesi baru.
Penutupan kegiatan/withdrawal tidak membuat save tanpa perubahan pada sesi lama
menjadi gagal. Ini tidak memberi otorisasi atau klaim audit atomik pada perubahan
administratif; pemanggil tetap kode tepercaya, bukan payload endpoint publik.

edit_version parent sekarang dikelola server. Record baru mulai 1; perubahan
payload yang diizinkan menaikkan versi sekali; save tanpa perubahan tidak
menaikkan versi. Versi pada instance harus cocok dengan versi tersimpan yang
dikunci. Objek stale ditolak, termasuk upaya memulihkan status approved dari
objek yang dimuat sebelum withdrawal. Token request dan audit bisnis atomik
menyeluruh tetap menjadi bagian layanan berikutnya.

Penyimpanan parsial/update_fields dan instance deferred ditolak agar instance
tidak mengambil versi terbaru secara diam-diam sambil membawa payload lama.
Manager publik/default/base/related menolak update, queryset delete, bulk_create,
bulk_update dan partial update_or_create. Jalur async mendelegasikan ke guard
yang sama. Instance baru dengan PK tersimpan ditolak; record baru selalu INSERT;
objek yang dihapus tidak dapat dihidupkan lagi melalui instance stale.

Delete instance tanpa referensi masih tersedia, dengan pemeriksaan versi dan
lock row sendiri. Referensi historis tetap menggunakan collector FK PROTECT;
tidak ada cascade atau perubahan/penghapusan histori. Delete partisipasi hanya
mengunci partisipasi, kemudian membaca referensi melalui collector: tidak
mengunci sesi dalam urutan terbalik.

## Audit urutan locking

Urutan global untuk row yang sudah ada, melewati jenis yang tidak diperlukan:

TrainingSession → ActivityParticipation → Activity → Participant → Assessment →
ShotPoint → TargetPhoto → DetectionRun → Calibration → TargetVersion → TargetRing
→ ScoringRuleVersion. Beberapa row pada jenis yang sama dikunci menurut PK
menaik. AuditEvent hanya INSERT; tidak mengambil lock untuk memutasi event lama.

| Jalur saat ini | Lock eksplisit / keputusan |
|---|---|
| TrainingSession.save, sesi tersimpan | Sesi dahulu; partisipasi sumber/tujuan berdasarkan PK; activity lalu participant bila membuat/mengganti konteks |
| TrainingSession.save, sesi baru | Belum ada row sesi: mulai dari partisipasi, lalu activity dan participant, baru INSERT; tidak mengambil lock sesi lama dari bawah participation |
| ActivityParticipation.save/delete | Row partisipasi sendiri; pemeriksaan keberadaan sesi/audit hanya SELECT biasa, tanpa lock sesi |
| TargetPhoto.save | Sesi sebelum row foto/insert; referensi pertama diserialkan dengan perubahan konteks sesi |
| AuditEvent.save, lingkup sesi | Sesi dahulu, lalu partisipasi lingkup/aktor yang diperlukan menurut PK, validasi ulang dan INSERT |
| AuditEvent.save, lingkup partisipasi | Partisipasi sebelum validasi dan INSERT; melindungi batas referensi audit pertama |
| Assessment.save | Sesi → Assessment, urutan 04A dipertahankan melalui helper lock sesi bersama |
| ShotPoint.save/delete | Sesi → Assessment → ShotPoint, urutan 04A dipertahankan |
| DetectionRun.save/delete | Row run saja; foto immutable sudah membekukan konteks sesi |
| Calibration.save/accept | Row kalibrasi → TargetVersion saat acceptance; tidak ada jalur TargetVersion yang mengunci kembali kalibrasi |
| TargetVersion/ScoringRuleVersion save/publish/retire/delete | Konfigurasi itu sendiri; tidak ada lock parent domain atau kalibrasi setelahnya |
| TargetRing.save/delete | TargetVersion sumber/tujuan menurut PK → row ring; pemeriksaan ring saat publikasi hanya membaca |
| User, Participant, Activity | Tidak mempunyai select_for_update eksplisit; tidak ditambahkan operasi locking lintas domain dalam iterasi ini |

Join select_related(...).select_for_update() lama pada TrainingSession dihapus.
Parent eligibility dikunci eksplisit per jenis untuk menghindari penguncian
beberapa tabel melalui urutan eksekusi join yang tidak dinyatakan aplikasi.
Helper mengunci FK yang dipersistensikan; objek terkait yang sudah dicache tidak
dianggap otoritatif. Writer baru wajib mengikuti urutan ini, termasuk transaksi
luar yang memanggil model; jangan memegang lock participation lalu meminta lock
sesi lama.

Perubahan kecil pada penyimpanan foto/audit diperlukan agar pemeriksaan
"belum ada referensi" di parent tidak berlomba dengan pembuatan referensi
pertama. Tidak ada perubahan snapshot audit, nilai locked, foto atau hasil lama.
Pintu participant audit, scoring, verify/lock/reopen pada 04A tetap tertutup.

## Migrasi dan kompatibilitas

activities/0002_historical_parent_managers hanya dua AlterModelOptions untuk
base_manager_name/default_manager_name. Migrasi state ini diperlukan agar
_base_manager juga memakai guard dan state migrasi konsisten dengan model.
Tidak ada kolom, tabel, indeks, constraint SQL baru atau operasi migrasi data.
Seluruh migrasi lama dipertahankan.

Tes SQL constraint Iterasi 01 kini memakai QuerySet mentah yang sengaja dibuat
tanpa manager; ini menjaga tujuan tes DB ketika update manager publik ditutup.
Tes drift 04A diperkuat: save normal parent sekarang ditolak, sementara bypass
SQL tetap menunjukkan bahwa pemeriksaan snapshot hasil mendeteksi drift.

## Verifikasi dan batas PostgreSQL

Seluruh tes menggunakan config.settings.test, SQLite dalam memori, dotenv
dimatikan dan Python -B. Tes mencakup tiap jenis referensi, seluruh field konteks,
stale object/prefetch, koreksi tanpa histori, administrasi valid, versi gagal/
save tanpa perubahan, PROTECT, base/related/async manager, dan data tersimpan
yang tidak berubah ketika penulisan ditolak.

Hasil 30 Oktober 2026: **292 tes lulus (255 lama + 37 baru)**; system check
tanpa masalah; makemigrations --check --dry-run menghasilkan No changes detected.
sqlmigrate activities 0002 pada SQLite menegaskan kedua operasi sebagai no-op
SQL (hanya state manager). git diff dan pemeriksaan whitespace mencakup file
terlacak serta file baru. Foto sintetis temporary memerlukan izin filesystem
Windows di luar sandbox; database tetap SQLite dalam memori.

Tes urutan merekam permintaan ORM select_for_update; **SQLite tidak menerapkan
row lock PostgreSQL**. Urutan aplikasi sudah diaudit, tetapi ini bukan bukti
bebas deadlock pada DBMS nyata atau terhadap transaksi eksternal sembarang.
Pada PostgreSQL khusus masih wajib diuji: create reference vs parent edit,
session creation/reparent vs participation edit/withdrawal, dua writer stale,
urutan PK, rollback, FK/unique lock implisit, isolation level, dan deadlock retry.

SQL langsung, QuerySet tanpa manager, save_base/Model.save langsung, migrasi
historis, serta API internal ORM tetap merupakan bypass tepercaya. Tidak ada
trigger, privilege khusus atau klaim immutability absolut. Pengujian PostgreSQL
nyata belum dilakukan. Data yang sudah drift akibat SQL/maintenance sebelumnya
tidak diperbaiki otomatis atau ditulis ulang oleh iterasi ini.

Lanjutan 04B: audit bisnis atomik, principal/otorisasi, idempotensi/request-version,
prosedur koreksi konteks historis dan penugasan assessor, serta kebijakan retensi.
Tidak ada perubahan Laravel/FastAPI, commit atau push.
