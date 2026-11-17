# ITER-AI-04 - Audit Struktur dan Kualitas Dataset Historis

**ID:** ITER-AI-04 / CTRL-015 / lanjutan DEC-13, DEC-14, DEC-16
**Tanggal:** 17 November 2026 (Asia/Bangkok, UTC+7)
**Branch:** feat/django-migration
**HEAD awal:** c9f49e2236aa5d972487977b743c28677870bb34
**Status akhir:** **CONDITIONALLY REUSABLE**
**Makna status:** kandidat sumber anotasi setelah persyaratan di bawah dipenuhi; bukan dataset YOLO26s Detection yang sudah siap training atau hasil operasional yang tervalidasi.

## 1. Kesimpulan dan kategori bukti

Arsip privat dataset_yolo.zip yang disediakan pengguna memiliki struktur konsisten: **30 gambar, 30 file label, 176 polygon**, split **train 20/114, valid 5/28, test 5/34** (gambar/polygon). Seluruh gambar berhasil diverifikasi dan di-decode. Semua SHA-256 gambar cocok dengan manifest, dan manifest ZIP identik byte dengan manifest historis yang dibaca dari Google Drive.

Tidak ditemukan masalah format/rentang/degenerasi polygon pada pemeriksaan numerik. Class map menyatakan **0 = bullet_hole**, dan semua 176 baris memakai ID 0. Fakta ini belum membuktikan kebenaran semantik atau kelengkapan anotasi terhadap objek nyata.

Tiga syarat utama tetap terbuka:

1. **PROVENANCE UNRESOLVED:** SHA-256 ZIP lokal berbeda dari field zip_sha256 dalam environment.json historis. Penyebab dan artefak yang dimaksud field tersebut belum terbukti.
2. **REVIEW REQUIRED:** reviewer manusia perlu memastikan satu polygon merepresentasikan satu lubang yang dapat diidentifikasi, memeriksa objek terlewat, label salah dan kasus overlap.
3. **GROUP INDEPENDENCE UNKNOWN:** identitas lembar fisik/sesi tidak tersedia; indikator kemiripan lintas split memerlukan pemeriksaan. Tiga puluh gambar tidak diperlakukan sebagai tiga puluh target fisik independen.

**FAKTA TERVERIFIKASI** = hasil pembacaan langsung arsip/source metadata pada audit ini. **CATATAN HISTORIS** = dokumentasi run sebelumnya. **RISIKO** = dampak yang mungkin terjadi, bukan kejadian yang sudah terbukti. **ASUMSI/USULAN** = rencana yang belum diterapkan. **PENDING/REVIEW REQUIRED/UNKNOWN** = bukti belum cukup untuk menyimpulkan. Seluruh kebutuhan operasional Lapbak, sumber tembakan, geometri target, kaliber, aturan skor dan kewenangan tetap **PROVISIONAL** sampai observasi/review berwenang.

## 2. Ruang lingkup, sumber dan metode

### 2.1 Baseline dan dokumen

Git branch, HEAD, status --short, status --branch --porcelain=v1, diff dan diff --cached diperiksa. Branch sesuai permintaan; working tree dan index awal bersih. Tidak dilakukan fetch, sehingga tampilan relasi origin hanya menggunakan ref lokal.

Dokumen yang dibaca:

- [ITER-AI-02 - audit dataset lokal](ITER_AI_02_DATASET_READINESS_AUDIT.md).
- [ITER-AI-03 - review arsip Drive](ITER_AI_03_DRIVE_DATASET_ARCHIVE_REVIEW.md).
- [Register keputusan/perubahan](PROJECT_DECISION_AND_CHANGE_REGISTER.md), sampai CTRL-013.
- [TEST-PLAN-001](TEST_PLAN_SYSTEM_AI_SCORING_v0.1.md), termasuk unit independensi, split, ground truth dan batas klaim.
- Bagian dataset/ground truth pada [OBS-001](OBSERVATION_AND_DATA_COLLECTION_INSTRUMENT_v0.1.md) sebagai konteks.

### 2.2 Referensi bukti tanpa path privat

| ID bukti | Sumber yang diperiksa | Batas |
|---|---|---|
| ZIP-LOCAL-01 | ZIP privat dataset_yolo.zip yang ditunjuk pengguna, di luar repository | Dibaca langsung; path absolut tidak disalin ke dokumentasi |
| ZIP-META-01 | data.yaml, manifest.json, audit.json di dalam ZIP | Dibaca di memori; tidak diekstrak ke disk |
| DRIVE-META-01 | environment.json pada arsip run historis 26 Oktober 2026 | Dibaca sebagai metadata melalui konektor Google Drive |
| DRIVE-META-02 | manifest.json dari run historis yang sama | Dibandingkan byte dan record dengan manifest ZIP |

Folder Drive ditelusuri dari folder proyek dan run yang ditemukan, bukan ID tebakan. Pencarian nama environment.json awal tidak menemukan hasil; listing hierarkis menemukan metadata. Tidak dilakukan unduh ZIP/bobot dari Drive, akses arsip COCO original, atau publikasi URL/ID Drive privat. environment.json tidak terdapat di ZIP maupun folder privat lokal yang diperiksa; perbandingan memakai metadata historis yang benar-benar dibaca dari konektor.

### 2.3 Pelaksanaan read-only

Skrip audit dijalankan melalui stdin, memakai Python environment AI yang sudah ada, opsi -I -B -X utf8. Modul utama: zipfile, hashlib, json, pathlib, math, statistics, Pillow **12.3.0**, NumPy **2.5.2**, PyYAML **6.0.3**; tidak memasang dependency. YAML dibaca dengan safe_load. Ultralytics, Torch, model, notebook dan aplikasi tidak dijalankan.

Isi ZIP dibaca ke memori dengan ZipFile; tidak memakai extract/extractall atau menulis salinan gambar/label. Hash ZIP dihitung sebelum dan sesudah pemeriksaan dan tetap sama. Audit gambar meliputi Image.verify, pembukaan ulang dan load penuh, dimensi, EXIF Orientation, SHA encoded bytes, SHA raster RGB dan indikator dHash. Tidak dilakukan inspeksi visual manusia maupun klaim hasil inspeksi visual oleh asisten.

Akses baca privat berhasil pada tool yang digunakan. Tidak ada penolakan akses yang dilewati atau penggantian jalur/tool untuk menghindari sandbox. Penulisan keluaran dibatasi dokumentasi repository melalui mekanisme izin sandbox.

## 3. Struktur dan keamanan ZIP - FAKTA TERVERIFIKASI

| Pemeriksaan | Hasil |
|---|---|
| Ukuran ZIP lokal | **28.539.214 bytes** |
| Entri total | **72** = **9 direktori + 63 file** |
| File | **30 JPG + 30 TXT + 2 JSON + 1 YAML** |
| Jumlah ukuran uncompressed | **29.115.870 bytes** |
| Jumlah compressed size anggota | **28.525.690 bytes**; berbeda dari ukuran berkas ZIP karena container/header |
| Anggota terbesar uncompressed | **1.014.261 bytes** |
| Rasio ekspansi maksimum per anggota | **2,853949 kali** (uncompressed / compressed) |
| Metode kompresi | 9 entri direktori stored; 63 file DEFLATE |
| CRC seluruh anggota | Lulus; testzip tidak menemukan anggota rusak |
| Enkripsi / nested archive | 0 / 0 ditemukan |
| Path absolut, drive/ADS colon, parent traversal | 0 ditemukan |
| Symlink atau anggota special-file | 0 ditemukan |
| Nama anggota identik / tabrakan casefold+NFC | 0 / 0 ditemukan |
| Nama reserved Windows atau komponen berakhir spasi/titik | 0 ditemukan |

Pemeriksaan path menormalkan pemisah untuk mendeteksi traversal dan memeriksa metadata mode file. Tidak ada ekstraksi yang diotorisasi oleh hasil ini. Sebelum full read, skrip membatasi maksimum 10.000 entri, total 512 MiB, 64 MiB per anggota, dan rasio ekspansi 200 kali. Ini batas pengamanan teknis audit, bukan ambang penerimaan dataset penelitian. Arsip aktual berada jauh di bawah batas tersebut.

**Penilaian ZIP bomb:** tidak ditemukan indikasi ekspansi berlebihan, arsip bersarang atau duplikasi/overlap yang menyebabkan pembacaan ZIP gagal. Pembacaan bounded dan CRC berhasil. Ini bukan sertifikasi bebas malware atau bukti autentisitas/provenance arsip.

Struktur seluruh direktori dan jenis file:

~~~text
dataset_yolo.zip
  data.yaml
  manifest.json
  audit.json
  train/                 # 1 direktori
    images/              # 20 JPG
    labels/              # 20 TXT
  valid/                 # 1 direktori
    images/              # 5 JPG
    labels/              # 5 TXT
  test/                  # 1 direktori
    images/              # 5 JPG
    labels/              # 5 TXT
~~~

Tiga direktori split dan masing-masing dua subdirektori berjumlah sembilan entri direktori. Tidak ada file tambahan selain tiga konfigurasi/metadata dan 60 gambar/label tersebut.

## 4. Konfigurasi, pasangan file dan gambar - FAKTA TERVERIFIKASI

Isi data.yaml, dengan nilai path privat/historis disamarkan:

~~~yaml
path: <HISTORICAL_COLAB_ROOT>
train: train/images
val: valid/images
test: test/images
names:
  0: bullet_hole
~~~

Nilai path asli merupakan root absolut Colab historis; tidak dapat dianggap sebagai path dataset aktif pada komputer saat audit. Field val menunjuk valid/images, sesuai nama direktori ZIP. Mapping names memiliki tepat satu kelas; nc tidak dinyatakan eksplisit. Semua ID label tercakup oleh mapping tersebut. YAML tidak menetapkan sendiri bahwa isi objek semantis sudah benar.

Setiap nama gambar memiliki file TXT dengan stem sama pada labels di split yang sama. Hasil: **30 pasangan lengkap, 0 label hilang, 0 label orphan**. Tidak ada record manifest duplikat atau record tambahan tanpa gambar; split/nama dan jumlah instance cocok untuk seluruh 30 record.

| Pemeriksaan gambar | Hasil |
|---|---|
| Verify dan full decode | **30/30 lulus** |
| Dimensi width x height | **11 gambar 3024 x 4032; 19 gambar 4032 x 3024** |
| Piksel per gambar | **12.192.768** |
| EXIF Orientation | Tidak ditemukan pada seluruh 30 gambar |
| Keutuhan ZIP sesudah audit | Hash sebelum/sesudah sama |

Ketiadaan tag Orientation mengurangi ketidakjelasan transformasi berbasis tag pada salinan ini; tidak membuktikan orientasi, perspektif atau pasangan label benar secara visual. Tidak dilakukan penilaian blur, glare, eksposur, representasi kaliber atau domain live-fire melalui inspeksi manusia.

## 5. Jumlah gambar/instance dan validitas polygon

### 5.1 Perbandingan terhadap manifest historis

| Split | Gambar aktual | Label aktual | Polygon aktual | Manifest/audit historis | Status |
|---|---:|---:|---:|---|---|
| train | 20 | 20 | 114 | 20 gambar / 114 polygon | MATCH |
| valid | 5 | 5 | 28 | 5 / 28 | MATCH |
| test | 5 | 5 | 34 | 5 / 34 | MATCH |
| **Total** | **30** | **30** | **176** | **30 / 176** | **MATCH** |

Jumlah 176 adalah **instance polygon**, belum jumlah lubang terverifikasi. Tidak ada ground truth skor atau bukti setiap foto satu target fisik independen.

### 5.2 Instance per gambar

ID A001-A030 ditetapkan berdasarkan urutan leksikografis nama entry JPG lengkap di ZIP, mulai indeks 1. Dengan hash ZIP yang dicatat, pemilik dapat mereproduksi pemetaan tanpa memasukkan nama file gambar privat ke repository.

| ID audit | Split | Instance polygon |
|---|---|---:|
| A001 | test | 6 |
| A002 | test | 10 |
| A003 | test | 5 |
| A004 | test | 7 |
| A005 | test | 6 |
| A006 | train | 5 |
| A007 | train | 6 |
| A008 | train | 5 |
| A009 | train | 5 |
| A010 | train | 4 |
| A011 | train | 4 |
| A012 | train | 6 |
| A013 | train | 5 |
| A014 | train | 4 |
| A015 | train | 6 |
| A016 | train | 8 |
| A017 | train | 6 |
| A018 | train | 5 |
| A019 | train | 10 |
| A020 | train | 5 |
| A021 | train | 6 |
| A022 | train | 6 |
| A023 | train | 7 |
| A024 | train | 6 |
| A025 | train | 5 |
| A026 | valid | 4 |
| A027 | valid | 5 |
| A028 | valid | 6 |
| A029 | valid | 5 |
| A030 | valid | 8 |

### 5.3 Pemeriksaan numerik seluruh 176 baris

Format diperiksa sebagai integer class, diikuti pasangan x,y ternormalisasi, minimal tiga titik. Ini mengikuti [format YOLO Instance Segmentation Ultralytics](https://docs.ultralytics.com/datasets/segment/). Validasi source-format tidak melibatkan loader/model YOLO.

| Pemeriksaan | Hasil |
|---|---|
| Baris dengan format/token/jumlah pasangan tidak valid | **0** |
| Nilai nonnumeric, nonfinite, di luar [0,1] | **0** |
| Kelas di luar mapping | **0**; seluruh **176** memakai class 0 |
| File label kosong | **0** |
| Rentang koordinat global teramati | **0.0170714286 sampai 0.8773207672** |
| Polygon dengan penutup mengulang titik pertama | **176** |
| Jumlah vertex setelah penutup berulang dilepas | **6-14** |
| Polygon degenerat | **0** |
| Self-intersection / nonadjacent edge contact | **0** |
| Zero-length edge setelah penutup dilepas | **0** |

Definisi degenerat: kurang dari tiga titik unik, luas shoelace absolut <= 1e-12 pada koordinat normalized, atau lebar/tinggi envelope <= 0. Pemeriksaan perpotongan memakai pasangan sisi nonadjacent serta toleransi cross-product/on-segment 1e-12; penutup berulang dilepas terlebih dahulu. Hasil bergantung pada aritmetika float dan definisi ini, bukan validasi geometri menggunakan semua library/format ekspor.

Luas polygon normalized min/median/max = **0.0001296508 / 0.0001645645 / 0.0002881650**. Kecilnya envelope/luas relatif konsisten dengan objek berukuran kecil secara geometris, tetapi bukan pembuktian bahwa objeknya lubang peluru. Tidak ada ambang ukuran lubang fisik yang divalidasi.

## 6. Checksum dan diskrepansi provenance

### 6.1 Checksum gambar dan manifest

**30/30 SHA-256 gambar aktual cocok** dengan record manifest; jumlah instance per file juga cocok. Tidak ada mismatch nama/split/hash/count atau record ekstra. Manifest hanya memuat file, split, sha256 dan instances; tidak ada checksum label, physical_target_id, source_type, izin, reviewer atau skor rujukan.

Manifest di dalam ZIP memiliki SHA-256:

~~~text
8111d897985ea3b5ce375cc704f1a102bde975b3f16e10fa25406bcec6325cd9
~~~

Manifest Drive yang dibaca berukuran **5.999 bytes**, mempunyai hash yang sama, dan **identik byte serta record** dengan manifest ZIP. Ini pemeriksaan lintas lokasi metadata; keduanya tetap berasal dari alur arsip historis yang sama. Manifest gambar tidak memberikan checksum historis per label, sehingga kesamaan label byte-for-byte terhadap anotasi asli belum dibuktikan.

### 6.2 SHA-256 ZIP berbeda dari environment.json - TETAP TERBUKA

| Sumber | Nilai SHA-256 |
|---|---|
| ZIP lokal aktual, dihitung langsung | **c7d1d07925d496396b151de24844f16f69f555462f1b257ababdb9e9a91189d7** |
| Field zip_sha256 pada environment.json historis | **7f3bfb2337e860c70a425de46714739b8ddf4a009cd98a35164e05131b58a9e7** |

Kedua nilai **berbeda**. environment.json juga mencatat ultralytics 8.4.189, torch 2.11.0+cu130, dan GPU Tesla T4; ini metadata eksperimen lama, bukan versi/perangkat yang dipakai untuk training baru. Field hash tersebut tidak menyertakan nama ZIP atau inventaris anggota yang mengikatnya secara eksplisit ke ZIP lokal ini.

**Kesimpulan terbatas:** ZIP lokal tidak dapat disahkan sebagai byte-identik dengan artefak yang dimaksud field historis tersebut. Audit ini tidak membuktikan penyebab selisih dan tidak menyimpulkan repackaging, penambahan metadata, korupsi, modifikasi anotasi atau salah file. Kesamaan ukuran metadata Drive dataset_yolo.zip dengan ukuran lokal pun tidak membuktikan byte-identik; ZIP Drive tidak diunduh ulang.

**PENDING:** pemilik/producer run perlu mengidentifikasi artefak tepat yang di-hash saat environment.json dibuat dan menyediakan bukti checksum/inventaris asal yang dapat ditelusuri. Pertahankan kedua hash beserta batasnya; jangan memperbarui hash historis agar sekadar cocok. CRC dan checksum gambar yang lulus tidak menyelesaikan diskrepansi provenance keseluruhan arsip.

## 7. Duplikasi dan indikasi kemiripan lintas split

### 7.1 Exact duplicate

- **30 SHA-256 encoded JPG unik**; tidak ada duplicate bytes intra/antar-split.
- **30 SHA-256 raster RGB hasil decode unik**; tidak ada duplicate raster RGB pada pemeriksaan ini.
- Nama dasar sumber setelah suffix ekspor .rf. dilepas juga tidak berulang. Nama berbeda tidak membuktikan identitas lembar fisik berbeda.

### 7.2 Near-duplicate sebagai indikator, bukan putusan

Metode deterministik: convert grayscale, resize **9 x 8** dengan Pillow LANCZOS, bandingkan intensitas pixel kanan > kiri menghasilkan **64-bit dHash**, lalu hitung Hamming distance. Tidak dilakukan alignment, perspective correction, EXIF transpose, atau pemeriksaan tahan rotasi. Cutoff **distance <= 5** dipilih sebagai filter review audit, bukan ambang validasi dataset yang teruji.

Seluruh **435 pasangan** diperiksa; **83** memenuhi filter <= 5. Dari **225 pasangan lintas split**, **43** memenuhi filter; **1** pasangan lintas split memiliki dHash distance 0. Terdapat dua pasangan distance 0 pada seluruh dataset. dHash identik tidak berarti file atau target fisik identik.

Contoh pasangan lintas split untuk review:

| ID audit | Split | dHash distance | Korelasi grayscale 32 x 32 |
|---|---|---:|---:|
| A003 - A030 | test - valid | 0 | 0.985677 |
| A005 - A029 | test - valid | 1 | 0.993301 |
| A022 - A029 | train - valid | 1 | 0.980651 |
| A005 - A018 | test - train | 1 | 0.979367 |
| A024 - A029 | train - valid | 1 | 0.977680 |

Korelasi adalah Pearson pada grayscale 32 x 32 hasil resize LANCZOS, tanpa alignment; hanya indikator tambahan. Pola cetakan target/latar yang mirip dapat mendominasi kedua metrik dan menimbulkan false positive, sementara perubahan sudut/crop dapat menyembunyikan foto berulang.

**REVIEW REQUIRED:** bandingkan pasangan kandidat pada sumber asli dan bukti target/sesi, kemudian perluas pemeriksaan ke seluruh 30 gambar untuk grouping. **LEAKAGE BELUM TERBUKTI; INDEPENDENSI SPLIT BELUM TERVERIFIKASI.** Jangan otomatis menghapus gambar, menggabungkan target, atau mempertahankan split 20/5/5 sebagai holdout akhir hanya berdasarkan metrik ini.

## 8. Semantik satu lubang per instance - REVIEW REQUIRED

Yang terbukti adalah label satu kelas bernama bullet_hole dan polygon yang valid secara numerik. Belum terbukti:

- Tiap instance mencakup tepat satu lubang, bukan noda, angka/ring, patch, robekan atau beberapa lubang yang menyatu.
- Semua lubang yang seharusnya dilabeli sudah tercakup dan tidak ada duplikasi instance.
- Polygon mengelilingi tepi lubang dengan konsisten dan label sesuai orientasi raster.
- Variasi sumber simulated/live_fire, kaliber, target, cahaya, blur dan overlap memenuhi scope penelitian.
- Jumlah lembar fisik independen dan skor ground truth.

Tidak ada inspeksi visual manusia, overlay atau adjudication yang dilaksanakan/diakui pada iterasi ini. Istilah **REVIEW REQUIRED** mempertahankan ketidakpastian tersebut; hasil audit format tidak ditingkatkan menjadi sertifikasi 176 lubang.

Usulan review: seluruh 30 gambar/176 polygon diperiksa di lingkungan privat dengan overlay, tanpa prediksi AI sebagai acuan; tandai accepted, needs_adjudication atau indeterminate per instance dan catat versi guideline, reviewer serta alasan. Review independen kedua diutamakan; bila tidak tersedia, laporkan keterbatasan. Reviewer juga perlu mencari missing annotations dan sampel negatif; **0 label kosong** tidak membuktikan bahwa dataset telah memiliki negatif terverifikasi atau bahwa negatif harus dipaksakan dari foto ini.

## 9. Kelayakan konversi polygon ke YOLO26s Detection - ANALISIS

**Secara struktural/matematis memungkinkan:** seluruh polygon memiliki envelope positif di dalam raster dan class ID yang terdaftar. Konversi belum dilakukan dan tidak ada file label detection yang dibuat.

Rancangan setelah syarat provenance/review terpenuhi: untuk tiap instance polygon normalized, ambil xmin, xmax, ymin, ymax; hitung xc=(xmin+xmax)/2, yc=(ymin+ymax)/2, w=xmax-xmin, h=ymax-ymin. Class dipertahankan lewat class map berversi. Keluaran detection yang dituju adalah class xc yc w h normalized, berbeda dari polygon sumber; lihat [format dataset Detection Ultralytics](https://docs.ultralytics.com/datasets/detect/).

Risiko yang harus ditangani:

1. **Pusat envelope bukan pusat fisik.** Polygon asimetris/robekan dapat menggeser midpoint bbox. Centroid polygon pun bukan otomatis pusat fisik lubang. Evaluasi pusat membutuhkan GT independen; jangan menurunkan pusat GT atau skor rujukan dari hasil konversi sendiri.
2. **Instance menyatu tidak pulih dengan konversi.** Satu polygon yang mencakup beberapa lubang memerlukan adjudication/relabel; jangan otomatis memecah atau menganggap satu polygon = satu tembakan.
3. **Informasi kontur hilang.** Bbox tidak menyimpan bentuk tepi untuk kasus sentuh batas; scoring tetap memerlukan kalibrasi/geometri/aturan yang divalidasi terpisah.
4. **Objek kecil saat resize.** Analisis envelope di memori, dengan skala hipotetis sisi panjang ke 640 tanpa crop, memberi sisi bbox terpendek min/median/max **5.7984 / 7.9060 / 11.3202 pixel**. Ini perhitungan geometri, bukan preprocessing yang diterapkan atau hasil inferensi. Detail kecil dan pilihan resolusi perlu eksperimen validation setelah ada instruksi training tersendiri.
5. **Split dan lineage harus ditata ulang bila diperlukan.** Semua foto/crop/augmentasi dari target/sesi terkait tetap satu group; jangan mewarisi split historis sebelum grouping terverifikasi. Pertahankan ZIP asli, hash, anotasi sumber, ID, pedoman, reviewer dan versi konversi pada salinan baru yang nantinya diotorisasi.

Validasi pascakonversi yang diusulkan: class/count konsisten per file, normalisasi/bounds valid, overlay bbox terhadap sumber yang sudah disetujui, pusat GT independen dan evaluasi terpisah untuk ambiguous/challenge. Tidak ada klaim perubahan mAP atau ketepatan skor dari analisis ini.

## 10. Keputusan, batas reuse dan tindak lanjut

**Keputusan audit: CONDITIONALLY REUSABLE sebagai kandidat sumber anotasi.** Bukti cukup untuk mempertahankan kandidat dan merencanakan review, sehingga tidak perlu langsung memulai ulang anotasi dari nol. Status ini bukan izin konversi/training dan bukan jaminan kesesuaian operasional. Semantik objek, provenance ZIP dan independensi target secara individual masih **INSUFFICIENT EVIDENCE**.

| Prioritas / gerbang | Aksi berikut | Bukti penyelesaian | Status saat audit |
|---|---|---|---|
| P0 / provenance | Klarifikasi field zip_sha256 historis terhadap artefak asal | Identitas artefak/checksum/inventaris yang dapat ditelusuri; diskrepansi tidak ditutupi | UNRESOLVED |
| P0 / izin dan semantik | Konfirmasi batas izin, guideline; review 30 gambar/176 polygon | Review log privat, adjudication, source_type dan limitations | REVIEW REQUIRED |
| P0 / independensi | Tetapkan physical_target_id/group_id dari bukti sumber dan review kandidat | Grouping terverifikasi; split berbasis target/sesi; holdout final tidak bocor | UNKNOWN |
| P1 / rencana detection | Tetapkan versi class map/konversi dan pusat GT independen | Rancangan disepakati serta instruksi implementasi terpisah | PROPOSAL |
| P1 / kelayakan eksperimen | Nilai variasi dan jumlah unit independen; lengkapi data bila perlu | Statistik sesudah review/grouping, termasuk negatif/challenge | PENDING |
| P2 / evaluasi | Susun pilot YOLO26s setelah gerbang sebelumnya dan instruksi baru | Validasi box/center/scoring terpisah sesuai TEST-PLAN-001 | NOT RUN |

Jika mismatch provenance tidak dapat direkonsiliasi, semantik belum direview atau target fisik tidak dapat diidentifikasi, pemakaian untuk klaim training/evaluasi final tetap ditahan. Laporkan keterbatasan secara eksplisit; jangan menaikkan status hanya karena jumlah gambar/instance sesuai.

## 11. Konsistensi dokumentasi dan batas pekerjaan

- ITER-AI-02 tetap benar untuk scope repository lokal pada saat audit tersebut. ZIP kini dibaca dari lokasi privat di luar repository; tidak berarti dataset berlabel sudah ada di source proyek.
- ITER-AI-03 menyatakan 30 gambar/176 polygon berdasarkan manifest dan kelas UNKNOWN saat itu. ITER-AI-04 menambahkan pemeriksaan langsung isi ZIP, class map 0=bullet_hole, pasangan, geometry, CRC dan checksum; tidak menulis ulang bukti lama atau menganggap metadata sebagai bukti semantik.
- Split aktual 20/5/5 adalah split historis gambar. Usulan 18/6/6 untuk 30 lembar fisik pada TEST-PLAN-001/ITER-AI-02 tidak disamakan atau diwujudkan oleh 30 gambar arsip ini.
- Arsip ini terkait dataset eksperimen YOLOv8 Segmentation historis. Checkpoint detection lokal dan bobot segmentation Drive tetap artefak terpisah; tidak ada checkpoint dibaca/dimuat dalam iterasi ini.
- Seluruh statistik di atas dihitung dari arsip/metadata yang dibaca; tidak ada metrik training, hasil scoring, observasi lapangan atau inspeksi manusia yang direkayasa.
- Pemeriksaan akhir dokumentasi mencakup identitas CTRL berikutnya, tautan relatif, penjumlahan split/instance, konsistensi kategori bukti, encoding, whitespace, konflik Git dan scope dua dokumen. Hasil aktual penyimpanan/commit dilaporkan setelah dijalankan.
- Tidak ada training, inferensi, konversi dataset, ekstraksi permanen, pengubahan/penghapusan ZIP, instalasi dependency, migrasi, akses DB, test suite aplikasi, atau aktivasi konfigurasi AI.
- Gambar, label asli, ZIP, bobot, path privat lengkap, URL/ID Drive dan raw manifest tidak ditambahkan ke repository. Keluaran hanya laporan agregat/ID audit dan tambahan CTRL-015 pada register; artefak privat tetap pada sumbernya.
