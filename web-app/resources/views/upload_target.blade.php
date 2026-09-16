<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Upload Gambar Lesan Target</title>
    <!-- Memuat Bootstrap untuk keseragaman UI dengan halaman Scorer -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #1a1a1a; color: #fff; }
        .upload-card { background-color: #2a2a2a; border-radius: 8px; padding: 30px; border: 1px solid #444; }
    </style>
</head>
<body class="d-flex align-items-center justify-content-center vh-100">

    <div class="upload-card shadow-lg col-md-5">
        <h3 class="mb-4 text-center">Sistem Penilaian - Input Lesan</h3>
        
        <form action="{{ route('evaluate.target') }}" method="POST" enctype="multipart/form-data">
            @csrf
            
            <!-- Pilihan Jenis Senjata -->
            <div class="mb-3">
                <label for="jenis_senjata" class="form-label">Jenis Senjata:</label>
                <select name="jenis_senjata" id="jenis_senjata" class="form-select" onchange="handleFormDinamis()" required>
                    <option value="" selected disabled>-- Pilih Senjata --</option>
                    <option value="laras_panjang">Laras Panjang (SS2/dll)</option>
                    <option value="pistol">Pistol (G2/dll)</option>
                </select>
            </div>

            <!-- Container Sikap (Muncul jika Laras Panjang) -->
            <div class="mb-3" id="sikap_container" style="display: none;">
                <label for="sikap" class="form-label">Sikap Menembak:</label>
                <select name="sikap" id="sikap" class="form-select">
                    <option value="berdiri">Berdiri</option>
                    <option value="duduk">Duduk</option>
                    <option value="tiarap" selected>Tiarap</option>
                </select>
            </div>

            <!-- Container Jarak (Muncul jika Pistol) -->
            <div class="mb-3" id="jarak_container" style="display: none;">
                <label for="jarak_m" class="form-label">Jarak Target:</label>
                <select name="jarak_m" id="jarak_m" class="form-select">
                    <option value="15">15 Meter</option>
                    <option value="20">20 Meter</option>
                    <option value="25" selected>25 Meter</option>
                </select>
            </div>
            
            <!-- Hidden Input untuk menampung nilai default/tersembunyi -->
            <input type="hidden" name="hidden_sikap" id="hidden_sikap" value="">
            <input type="hidden" name="hidden_jarak" id="hidden_jarak" value="">

            <!-- Input Kamera/File -->
            <div class="mb-4">
                <label class="form-label">Ambil Foto Lesan (Otomatis buka kamera di HP):</label>
                <!-- PERUBAHAN: Menambahkan attribute capture="camera" -->
                <input type="file" name="target_image" class="form-control" accept="image/*" capture="camera" required>
            </div>
            
            <button type="submit" class="btn btn-success w-100 fw-bold">Proses & Nilai Lesan</button>
        </form>
    </div>

    <script>
        // Logika Vanilla JS untuk Form Dinamis
        function handleFormDinamis() {
            const senjata = document.getElementById('jenis_senjata').value;
            const sikapContainer = document.getElementById('sikap_container');
            const jarakContainer = document.getElementById('jarak_container');
            
            const hiddenSikap = document.getElementById('hidden_sikap');
            const hiddenJarak = document.getElementById('hidden_jarak');

            if (senjata === 'laras_panjang') {
                // Tampilkan Sikap, Sembunyikan Jarak (Default 100m)
                sikapContainer.style.display = 'block';
                jarakContainer.style.display = 'none';
                
                hiddenJarak.value = '100'; // Jarak fix 100m
                hiddenSikap.value = '';    // Dikosongkan karena pakai select visible
            } 
            else if (senjata === 'pistol') {
                // Tampilkan Jarak, Sembunyikan Sikap (Default Berdiri)
                sikapContainer.style.display = 'none';
                jarakContainer.style.display = 'block';
                
                hiddenSikap.value = 'berdiri'; // Sikap fix berdiri
                hiddenJarak.value = '';        // Dikosongkan karena pakai select visible
            }
        }
    </script>
</body>
</html>