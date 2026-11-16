# ITER-AI-03 — Review Penemuan Arsip Dataset Segmentasi di Google Drive

**ID:** ITER-AI-03 / CTRL-013 / kelanjutan DEC-13 dan DEC-16  
**Tanggal:** 16 November 2026  
**Branch:** `feat/django-migration`  
**Baseline:** `91c671cd4332b43a012dfb55345fe18cb0f21f45`  
**Status:** **REVIEW ARSIP DAN MANIFEST — KELAYAKAN DATASET YOLO26s BELUM TERVERIFIKASI**  
**Lingkup perubahan:** hanya dokumentasi, tidak mengubah laporan ITER-AI-02 yang benar untuk scope lokal.

## 1. Konteks dan pemisahan cakupan

Codex pada ITER-AI-02 membuktikan bahwa **tidak ditemukan dataset berlabel di direktori repository lokal**. Kesimpulan itu tetap berlaku. Pada penelusuran tambahan melalui konektor Google Drive yang terhubung, ditemukan arsip percobaan terpisah `TA_Segmentasi_Lesan/20261026_152246` (26 Oktober 2026). Konektor menampilkan folder `train`, `weights`, ZIP dataset original/YOLO, serta `manifest.json`, `audit.json`, `environment.json`, `args.yaml` dan `results.csv`.

**Batas privasi:** URL/ID Google Drive privat, file ZIP, foto, konten dataset, path lokal absolut dan bobot tidak disalin ke GitHub. Pemeriksaan ini membaca manifest/audit/log konfigurasi; **tidak membuktikan** bahwa isi ZIP terkini, image-label, geometri anotasi atau integritasnya sesuai manifest. Jangan mengasumsikan adanya izin data lapangan dari keberadaan arsip.

## 2. Bukti metadata dan statistik manifest yang dibaca

File `manifest.json` berisi 30 record dengan field `split`, `file`, `sha256`, `instances`. Perhitungan agregat langsung dari record:

| Split | Gambar tercatat | Jumlah `instances` tercatat | SHA-256 unik pada split |
|---|---:|---:|---:|
| train | 20 | 114 | 20 |
| valid | 5 | 28 | 5 |
| test | 5 | 34 | 5 |
| **Total** | **30** | **176** | **30** |

Tidak ada SHA identik antara split menurut field manifest, tetapi ini **tidak menyingkirkan near-duplicate, foto satu lembar yang sama, kebocoran target fisik, atau hash/manifest yang tidak cocok dengan isi ZIP**. Field `physical_target_id`, `source_type`, izin pengumpulan, class map, reviewer, dan ground truth skor **tidak ada** dalam manifest yang dibaca. Jumlah instance di sini adalah angka pada arsip segmentation/polygon; **bukan bukti 176 lubang peluru individual**.

`audit.json` secara independen pada tingkat ringkasan metadata arsip menyebut train = 20 images / 114 polygons, valid = 5 / 28, test = 5 / 34; cocok dengan jumlah record. Ini adalah konsistensi dua file metadata dari satu proses, **bukan verifikasi eksternal anotasi**.

## 3. Konfigurasi training historis yang ditemukan

`args.yaml` dibaca sebagai teks. Field relevan:
- `task: segment`, `mode: train`, `model: yolov8n-seg.pt`.
- `imgsz: 1280`, `batch: 2`, `seed: 42`.
- `epochs: 100` (anggaran training), `patience: 25`, `save_period: 10`, `pretrained: true`.
- Run dan data berada di lingkungan Colab/Drive; path absolut tidak disalin ke repository.

`results.csv` mencatat 54 epoch; nilai tertinggi kolom `metrics/mAP50-95(M)` adalah **0.02632 pada epoch 29**. Ini metrik **segmentasi mask** dari log eksperimen lama, bukan evaluasi final YOLO26s, deteksi lubang peluru atau hasil scoring. Bobot arsip segmentation dan file lokal detection `src/ai-engine/best.pt` tetap **artefak berbeda dengan provenance yang belum direkonsiliasi**.

## 4. Keputusan metode selanjutnya — PROPOSAL

1. **Jangan membuat dataset dari nol sebelum audit ZIP.** Arsip yang ditemukan merupakan kandidat sumber anotasi, bukan dataset YOLO26s yang sudah siap.
2. Dengan izin pemilik data, ambil **salinan pribadi** `dataset_yolo.zip` dan berkas relevan ke direktori privat di luar repository. Pastikan hash, ukuran dan keamanan arsip sebelum ekstraksi; jangan unzip secara otomatis ke source proyek atau menjalankan notebook lama tanpa review.
3. Lakukan audit isi ZIP: `data.yaml` (class names), image/label matching, format segmentation polygon, koordinat valid, jumlah instance, object semantics (apakah individual bullet hole, ring atau entire target), EXIF/orientasi, full decode, coverage, overlap, near-duplicate dan manifest-vs-ZIP.
4. Bila kelas benar-benar `bullet_hole` per instance: buat **rancangan** konversi mask polygon → bbox dengan review visual manusia dan pelacakan versi, bukan menjalankan training langsung. Bila label bukan lubang individual, gunakan gambar hanya jika diizinkan dan lakukan relabeling sesuai pedoman baru.
5. Bangun `physical_target_id` atau grup identitas yang dibuktikan dari bahan asli, lalu **re-split berdasarkan lembar fisik**, jangan mempertahankan split 20/5/5 sebagai final hanya karena hash gambar unik.
6. Tetapkan bahwa angka 30 gambar arsip **bukan** keputusan anggaran 30 lembar fisik dari TEST-PLAN-001; data independen, izin, klasifikasi GT dan kualitas anotasi harus ditentukan sebelum training YOLO26s.

**Stop condition:** jika data/izin, kelas sumber, identitas target fisik, atau integritas pasangan gambar-label tidak dapat dibuktikan, tandai `BLOCKED / NOT VERIFIED`. Jangan mengarang kualitas label, mAP detection atau kecocokan model.

## 5. Batas verifikasi dan prioritas

**Diperiksa langsung:** metadata Google Drive, struktur folder arsip, berkas manifest/audit/config yang dapat dibaca, agregasi split dan jumlah instances, beberapa field args.yaml.  
**Belum diperiksa:** isi ZIP dan checksum berkas di dalamnya, full decode visual, label class/map, kebenaran polygon, sampel target independen, class `bullet_hole`, izin pengambilan data, checkpoint model dan inferensi.  
**Tidak dikerjakan:** perubahan source, dataset conversion, download ZIP ke repo, training, inferensi, test suite, database, deploy, perubahan settings/approval, atau observasi lapangan.

**Tugas teknis berikut:** audit artefak ZIP secara read-only (ITER-AI-04) setelah pemilik menyiapkan salinan yang diizinkan, dan dokumentasikan cross-check dataset + kelayakan konversi. Ini menggantikan asumsi “tidak ada dataset sehingga harus mulai anotasi dari nol”; hasil ITER-AI-02 tetap sah **khusus inventaris lokal**.
