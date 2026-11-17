# ITER-AI-04 — Pemeriksaan Integritas dan Struktur Arsip Dataset Historis

**ID:** ITER-AI-04 / CTRL-014 / lanjutan ITER-AI-02, ITER-AI-03 dan DEC-16  
**Tanggal pemeriksaan:** 17 November 2026  
**Branch dokumentasi:** `feat/django-migration`  
**Baseline branch sebelum commit:** `c9f49e2236aa5d972487977b743c28677870bb34`  
**Status:** **ARSIP SAAT INI TERVERIFIKASI / STRUKTUR LABEL LULUS PEMERIKSAAN STATIS / KESIAPAN TRAINING MASIH TERTUNDA**

## 1. Sumber dan integritas arsip

Pengguna mengunduh `dataset_yolo.zip` secara pribadi dari Google Drive ke direktori di luar repository, lalu melaporkan hash lokal `C7D1D07925D496396B151DE24844F16F69F555462F1B257ABABDB9E9A91189D7` dan ukuran 27,22 MiB (hasil pembulatan).

Asisten mengunduh salinan **langsung dari objek arsip Google Drive yang sama** pada konektor terautentikasi, lalu memeriksa berkas mentah di lingkungan pemeriksaan:

| Bukti | Nilai / status |
|---|---|
| SHA-256 output pengguna (lokal) | `c7d1d07925d496396b151de24844f16f69f555462f1b257ababdb9e9a91189d7` |
| SHA-256 dihitung langsung dari unduhan Google Drive saat ini | **identik**: `c7d1d07925d496396b151de24844f16f69f555462f1b257ababdb9e9a91189d7` |
| Ukuran byte arsip dari konektor / unduhan | **28.539.214 byte** |
| Hash yang direkam dalam `environment.json` eksperimen lama | `7f3bfb2337e860c70a425de46714739b8ddf4a009cd98a35164e05131b58a9e7` — **TIDAK COCOK** dengan objek Drive saat ini |
| `zipfile.testzip()` | **PASS**, mengembalikan `None` (tidak ada kegagalan CRC ZIP) |

**Interpretasi:** Mismatch terhadap `environment.json` lama bukan bukti korupsi unduhan pengguna. Salinan lokal pengguna dan objek Drive saat ini mempunyai hash yang sama persis; struktur ZIP juga lolos pengujian CRC. Namun **asal perbedaan nilai dalam environment.json tidak diketahui** (bisa merujuk arsip versi lain/catatan usang atau alasan lain), sehingga jangan mengganti atau menghapus bukti historis lama tanpa verifikasi. Hash terkini dianggap baseline integritas ZIP yang sedang diperiksa, bukan validasi akurasi dataset.

## 2. Pemeriksaan statis isi ZIP — dilakukan langsung dari arsip

Metode read-only: `zipfile` membuka central directory dan membaca isi entry; `json` membaca `manifest.json` dan `audit.json`; `data.yaml` dibaca sebagai teks; label `.txt` ditokenisasi, dikonversi menjadi angka dan divalidasi struktur polygon/normalisasi; `hashlib.sha256` memverifikasi byte setiap gambar terhadap manifest. **Tidak** mengekstrak seluruh arsip, menjalankan model, mengimpor notebook/libraries ML atau melatih model.

- ZIP: **72 entry** (63 file dan 9 entry direktori). File yang diperiksa: **30 JPG**, **30 TXT**, **2 JSON**, **1 YAML**.
- `data.yaml` mendeklarasikan satu kelas **`0: bullet_hole`** serta split `train/images`, `valid/images`, `test/images`. Path absolut Colab dalam YAML adalah path historis, **bukan** path training komputer pengguna.
- Semua 30 JPG memiliki pasangan `.txt` dengan stem sama pada split masing-masing. **0** label pasangannya hilang dalam cakupan ZIP.
- Semua baris anotasi yang dibaca memenuhi pemeriksaan awal: class ID numerik 0, ada pasangan koordinat berjumlah cukup untuk polygon, jumlah field ganjil (class + pasangan x/y), koordinat terhingga dalam rentang [0,1] dari parsing, dan tidak ditemukan baris malformed dalam pemeriksaan ini. **Ini hanya pemeriksaan struktur**, bukan mutu anotasi dan bukan pembuktian bahwa polygon mencakup lubang yang tepat.
- 30 gambar mempunyai SHA-256 **unik secara byte** dan **semua 30** digest gambar sesuai `manifest.json`; tidak terdapat mismatch hash pada path/split manifest yang diperiksa. Hash unik **tidak membuktikan** unit target fisik berbeda atau ketiadaan near-duplicate.

| Split | Jumlah JPG | Label TXT | Instance polygon terbaca | Class ID |
|---|---:|---:|---:|---|
| train | 20 | 20 | 114 | 0 |
| valid | 5 | 5 | 28 | 0 |
| test | 5 | 5 | 34 | 0 |
| **Total** | **30** | **30** | **176** | **0 = bullet_hole menurut YAML** |

Hasil tersebut konsisten dengan angka `manifest.json` / `audit.json` yang sebelumnya hanya dicatat tanpa pemeriksaan ZIP. Kontrak dataset ini adalah **YOLO segmentation polygons**, belum label YOLO detection dengan 5 nilai per bbox.

## 3. Batas kepastian yang masih kritis

Hal-hal berikut **BELUM TERVERIFIKASI** walaupun struktur arsip lolos:
1. Apakah setiap polygon mengelilingi satu lubang peluru individual; kelengkapan objek, over/under-annotation, tumpuk/sobekan, dan label gangguan visual.
2. Kualitas bentuk polygon, luas area riil, penempatan pusat GT, serta konsistensi dengan aturan scoring inward-boundary yang masih provisional.
3. Identitas lembar target fisik, hubungan antar-foto, near-duplicate dan kebocoran tingkat group antar-split; `30 JPG` **tidak sama** dengan `30 lembar fisik independen`.
4. Kepastian izin/privasi, jenis simulasi vs live-fire, rantai asal anotasi, review oleh petugas dan kebenaran ground truth.
5. Full decode dan tampilan visual gambar per label, metadata orientasi EXIF, serta pengujian kompatibilitas pipeline training YOLO26s dan inferensi produksi.
6. File checkpoint detection lokal merupakan artefak berbeda; arsitektur YOLO26s yang benar dan performa training terbaru belum dibuktikan.

**Stop gate:** Jangan konversi permanen, membekukan final split, training YOLO26s atau mengklaim evaluasi akurasi sebelum audit visual, semantik kelas, independensi unit fisik dan izin penggunaan memadai.

## 4. Tindak lanjut ITER-AI-04B (untuk Codex, setelah file tersedia lokal)

1. Baca `ITER_AI_02_DATASET_READINESS_AUDIT.md`, `ITER_AI_03_DRIVE_DATASET_ARCHIVE_REVIEW.md`, dokumen ini dan register, serta inventaris ZIP di direktori privat **read-only**.
2. Cek gambar lengkap dan overlay semua polygon secara visual pada sampel representatif/lalu seluruh 30 bila mungkin; tentukan apakah label kelas `bullet_hole` mewakili setiap lubang. Temuan visual harus dicatat sebagai evidence, tidak boleh disimpulkan hanya dari `data.yaml`.
3. Kelompokkan foto menurut **physical_target_id** jika ada bukti, atau beri status UNKNOWN; uji near-duplicates/kemungkinan leak train-valid-test.
4. Buat **laporan** kelayakan konversi seg→det dan prosedur QA/label verification, plus checklist independensi/izin; tidak menulis label baru atau memulai training tanpa instruksi tahap lanjutan.
5. Pertahankan original ZIP privat dan hash, jangan menaruh path absolut yang sensitif/hasil foto/bobot/manifest pribadi pada Git, serta jangan mengubah `DETECTION_SERVICE` dan konfigurasi operasional.

**Batas pekerjaan asisten sekarang:** verifikasi SHA unduhan Google Drive dan pemeriksaan ZIP secara baca-saja; tidak ada perubahan model, code, dataset, permissions, database, pengujian regresi, inferensi atau training. Dokumen audit dan register saja yang berubah.
