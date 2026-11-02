# Keputusan Sementara — Taksonomi Latihan dan Parameter Menembak

**ID:** DEC-2026-11-02-01  
**Tanggal pencatatan:** 2 November 2026  
**Status:** **PROVISIONAL — keputusan kebutuhan pengguna sementara, MENUNGGU OBSERVASI LAPANGAN**  
**Sumber keputusan:** klarifikasi langsung pengguna pada diskusi Tugas Akhir, 2 November 2026.  
**Ruang lingkup:** terminologi kegiatan latihan, jenis menembak, pilihan parameter petugas, alur pengambilan foto dan penilaian.  
**Sifat dokumen:** dokumentasi kebutuhan; **bukan** pengesahan aturan organisasi, bukan hasil observasi, dan bukan instruksi untuk langsung mengubah implementasi.

## 1. Koreksi terminologi utama

**Jenis latihan** mengacu pada nama/kategori kegiatan latihan, **bukan** kombinasi jenis menembak, sikap, jarak, dan kaliber.

Contoh yang diberikan pengguna: **Latihan Menembak Triwulan I (Pertama)**. Triwulan II/III/IV dapat digunakan sebagai contoh penamaan sejenis, tetapi keberadaannya sebagai jadwal/kategori resmi **belum diverifikasi**.

**Jenis menembak** adalah kategori teknis yang dipilih petugas pada saat menilai peserta:
1. **Laras panjang**.
2. **Laras pendek / pistol**.

Jangan menggunakan istilah `jenis_latihan` untuk menyimpan pilihan `laras_panjang`/`pistol` tanpa membedakannya secara eksplisit dari kegiatan latihan.

## 2. Matriks parameter sementara

| Parameter | Laras panjang | Laras pendek / pistol |
|---|---|---|
| Jenis menembak | Laras panjang | Pistol |
| Sikap yang berlaku | **Berdiri, duduk, tiarap** | **Berdiri saja** |
| Jarak yang berlaku | **100 meter saja** | **15, 20, atau 25 meter** |
| Kaliber | **5,56 mm** | **9 mm** |
| Input lanjutan yang dipilih petugas | **Sikap** (3 opsi) | **Jarak** (3 opsi) |
| Nilai yang ditetapkan otomatis | Jarak 100 m dan kaliber 5,56 mm | Sikap berdiri dan kaliber 9 mm |

**Aturan kombinasi untuk kebutuhan aplikasi (sementara):**
- Laras panjang: (`berdiri` | `duduk` | `tiarap`, 100 m, 5,56 mm).
- Pistol: (`berdiri`, 15 m | 20 m | 25 m, 9 mm).
- Pilihan yang tidak sesuai matriks harus ditolak melalui validasi server, bukan hanya disembunyikan pada UI.
- Kaliber bukan input bebas petugas dalam alur ini; nilainya diturunkan dari jenis menembak.
- Ketentuan ini hanya berlaku untuk **cakupan aplikasi yang sedang dirumuskan**, bukan klaim standar umum semua kegiatan menembak.

## 3. Alur petugas/penilai yang disepakati sementara

### A. Laras panjang
1. Pilih peserta.
2. Pilih jenis menembak: **laras panjang**.
3. Pilih sikap: **berdiri / duduk / tiarap**.
4. Sistem menetapkan **jarak 100 m** dan **kaliber 5,56 mm**.
5. **Buka kamera atau unggah foto dari galeri**.
6. Sistem melakukan deteksi dan perhitungan nilai otomatis.
7. Petugas memeriksa serta memverifikasi hasil penilaian.
8. Selesai.

### B. Pistol
1. Pilih peserta.
2. Pilih jenis menembak: **pistol**.
3. Pilih jarak: **15 m / 20 m / 25 m**.
4. Sistem menetapkan **sikap berdiri** dan **kaliber 9 mm**.
5. **Buka kamera atau unggah foto dari galeri**.
6. Sistem melakukan deteksi dan perhitungan nilai otomatis.
7. Petugas memeriksa serta memverifikasi hasil penilaian.
8. Selesai.

**Keterangan:** Kegiatan latihan (misalnya Triwulan I) tetap menjadi konteks yang mengelompokkan hasil, tetapi **kapan dan bagaimana kegiatan dipilih pada UI belum dijelaskan pengguna**. Jangan menyisipkan tahapan pemilihan kegiatan baru ke dalam dua alur di atas seolah-olah sudah diputuskan.

## 4. Implikasi teknis yang perlu ditinjau saat pengembangan dilanjutkan

- `Activity` secara konseptual mewakili kegiatan/nama latihan; `TrainingSession` mewakili konteks penilaian peserta.
- Field kandidat yang sudah ada pada baseline `TrainingSession`: `weapon_type`, `shooting_position`, `distance_m`, `caliber_mm`. **Belum diputuskan perlu/tidaknya tabel baru atau migrasi skema.**
- Form petugas harus menampilkan pilihan kontekstual: laras panjang → pilih sikap; pistol → pilih jarak.
- Backend harus konsisten menurunkan sikap/jarak/kaliber otomatis dan memvalidasi kombinasi sebelum penilaian.
- Data hasil dan laporan harus dapat ditelusuri ke kegiatan, peserta, jenis menembak, sikap, jarak, dan kaliber.
- Pengambilan gambar mendukung **kamera dan galeri** menurut kebutuhan terbaru; ini memperluas asumsi sebelumnya yang lebih menekankan pengambilan langsung.
- Perubahan parameter dapat memengaruhi API/form/services, validasi domain, tes regresi, dan dokumentasi SRS. **Tidak ada perubahan kode, migration, atau pengujian yang dinyatakan telah dilakukan oleh dokumen ini.**

## 5. Batas kepastian dan hal yang menunggu observasi

Semua parameter pada Bagian 1–3 telah **dinyatakan oleh pengguna sebagai acuan kerja sementara**, tetapi **belum divalidasi melalui observasi lapangan**.

Yang belum diputuskan melalui klarifikasi ini antara lain:
- Penamaan resmi, jadwal, dan pengelompokan kegiatan latihan per triwulan.
- Apakah peserta mengikuti semua sikap/jarak atau hanya sebagian; jumlah sesi dan pengulangan percobaan.
- Alur pemilihan kegiatan, penugasan petugas, dan relasi peserta–kegiatan pada UI.
- Ketentuan target, jumlah tembakan, perlakuan lubang tumpang tindih, dan tata cara skor resmi. **Jangan menyimpulkan bahwa bagian-bagian ini berubah** karena klarifikasi parameter jenis menembak.
- Apakah hasil observasi lapangan nantinya memerlukan pengecualian pada matriks sementara.

## 6. Ketentuan pengelolaan keputusan

1. Pertahankan dokumen ini sebagai rekam keputusan awal dengan label **PROVISIONAL**.
2. Setelah observasi, verifikasi satu per satu definisi, opsi, dan aturan pada matriks.
3. Jika ada perbedaan, buat revisi tercatat dengan sumber dan tanggal; jangan diam-diam mengganti keputusan lama.
4. Sinkronkan revisi tervalidasi dengan SRS, desain database, UI, logika backend, tes, dan naskah TA.
5. Pada percakapan baru, **baca dokumen ini terlebih dahulu** sebelum mendefinisikan parameter latihan; jangan menganggapnya final.

**Referensi internal:** `docs/SRS_v0.1.md`, `docs/DATABASE_DESIGN_BASELINE_v1.1.md`. Keduanya merupakan baseline sebelumnya dan perlu direkonsiliasi setelah observasi/keputusan lanjutan.
