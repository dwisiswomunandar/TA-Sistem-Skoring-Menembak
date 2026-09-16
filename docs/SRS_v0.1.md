# Software Requirements Specification (SRS) v0.1

**Proyek:** Sistem Penilaian Hasil Latihan Menembak Berbasis Web
**Status:** Draf Awal (Iterasi 1)

## 1. Pendahuluan

* **Tujuan:** Dokumen ini bertujuan untuk mendefinisikan spesifikasi kebutuhan perangkat lunak untuk Sistem Penilaian Hasil Latihan Menembak. Dokumen ini menjadi acuan utama bagi pengembang dalam proses desain, implementasi, dan pengujian.
* **Ruang Lingkup:** Sistem menggunakan *Computer Vision* (YOLOv8) untuk mendeteksi lubang peluru pada citra lesan tembak, menghitung total skor (*Inward Scoring*), dan menyediakan antarmuka Kanvas Interaktif untuk verifikasi dan koreksi manual oleh Tim Penilai.
* **Definisi:**
* **Inward Scoring:** Sistem perhitungan skor di mana lubang peluru yang memotong garis batas ring akan mendapatkan nilai ring yang lebih tinggi (menguntungkan penembak).
* **YOLOv8:** Model kecerdasan buatan untuk deteksi objek.


* **Referensi:** Updated Blueprint Sistem Penilaian Latihan Menembak (Internal).

## 2. Deskripsi Umum

* **Gambaran Produk:** Sebuah aplikasi web hibrida yang mengintegrasikan *backend* PHP (Laravel) untuk manajemen data operasional dan *engine* Python (FastAPI + OpenCV + YOLOv8) untuk pemrosesan citra.
* **Pengguna (Stakeholder):**
1. **Admin Lapbak:** Mengelola jadwal kegiatan, menyediakan akses registrasi, dan merekap laporan akhir.
2. **Peserta (Prajurit):** Mendaftar ke kegiatan aktif dan melihat *leaderboard* skor.
3. **Scorer (Tim Penilai):** Mengambil citra dari lapangan, mengeksekusi deteksi AI, memverifikasi hasil pada kanvas, dan mengunci skor.
4. **Perwira Penanggung Jawab:** Memantau hasil rekapitulasi latihan.


* **Batasan (Constraints):**
* Hanya mengakomodasi kaliber amunisi 5.56mm (laras panjang) dan 9mm (pistol) **[KEPUTUSAN]**.
* Tidak mencakup kalkulasi analitik balistik lanjutan (MOA, MPI, Group Size) **[KEPUTUSAN]**.
* Pengambilan foto menggunakan fitur *HTML5 Camera API* langsung melalui *browser* perangkat *mobile* **[REKOMENDASI]**.



## 3. Kebutuhan Fungsional (FR)

Format dan penentuan prioritas menggunakan metode MoSCoW sesuai acuan materi pembelajaran.

| ID | Aktor | Kebutuhan Fungsional | Prioritas | Sumber |
| --- | --- | --- | --- | --- |
| **FR-01** | Admin | Sistem dapat membuat jadwal Kegiatan Latihan Menembak baru dan mem- *generate* URL/QR Code registrasi. | Must | SOP Lapbak |
| **FR-02** | Peserta | Sistem menyediakan fitur registrasi peserta baru atau *login* peserta lama ke kegiatan yang sedang aktif. | Must | SOP Lapbak |
| **FR-03** | Scorer | Sistem dapat mengakses kamera *smartphone* untuk mengambil foto lesan peserta secara langsung. | Must | Updated Blueprint |
| **FR-04** | Sistem | *AI Engine* mengekstrak koordinat titik lubang tembakan dari foto menggunakan YOLOv8. | Must | Updated Blueprint |
| **FR-05** | Scorer | Sistem menampilkan kanvas interaktif untuk menambah/menghapus titik deteksi yang meleset (*Human-in-the-loop*). | Must | Updated Blueprint |
| **FR-06** | Sistem | Sistem mengkalkulasi *Total Skor (Inward Scoring)* otomatis setiap ada perubahan titik pada kanvas. | Must | Updated Blueprint |
| **FR-07** | Scorer | Sistem memungkinkan *Scorer* untuk mengunci (*lock*) hasil penilaian dan menyimpannya ke *database*. | Must | SOP Lapbak |
| **FR-08** | Admin | Sistem dapat mengekspor rekapitulasi nilai seluruh peserta menjadi dokumen PDF. | Should | SOP Lapbak |
| **FR-09** | Peserta | Sistem menampilkan *leaderboard* (daftar nilai seluruh peserta) pada kegiatan tersebut. | Could | SOP Lapbak |

## 4. Kebutuhan Nonfungsional (NFR)

| ID | Kategori | Kebutuhan Nonfungsional |
| --- | --- | --- |
| **NFR-01** | *Performance* | *API* (FastAPI) harus mengembalikan *response* JSON hasil ekstraksi titik maksimal dalam waktu 5 detik. |
| **NFR-02** | *Security* | Autentikasi dan *session* dibatasi berdasarkan *Role-Based Access Control* (Admin, Scorer, Peserta, Perwira). |
| **NFR-03** | *Security* | Semua eksekusi ke *database* wajib menggunakan *PDO Prepared Statements*. |
| **NFR-04** | *Integrity* | Proses penyimpanan data penilaian dibungkus dalam `DB::transaction` agar data tidak korup jika terjadi kegagalan sistem terputus. |

## 5. Model Sistem

**5.1. Struktur Data (Database)**
Struktur relasional minimum untuk mendukung operasional sistem:

1. `users`: id, nama, email, password, role.
2. `kegiatan`: id, nama_kegiatan, tanggal, status_aktif.
3. `master_lesan`: id, jenis_amunisi (5.56mm / 9mm), konstanta_kalkulasi.
4. `penilaian_header`: id, kegiatan_id, peserta_id, scorer_id, lesan_id, foto_path, total_skor, status.
5. `penilaian_detail_titik`: id, penilaian_id, koordinat_x, koordinat_y, skor_ring, sumber_deteksi (ai/manual).

**5.2. Alur Proses Utama (User Story Inti)**
Sebagai **Scorer**, saya ingin **memilih peserta, memfoto lesannya, dan memverifikasi deteksi AI di atas kanvas**, sehingga **nilai tembakan dapat dihitung otomatis dan bebas dari subjektivitas**.

## 6. Lampiran

* *Requirement Traceability Matrix* (RTM) akan disusun bersamaan dengan fase implementasi dan pembuatan *Test Case*.