<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <title>Upload Gambar Lesan Target</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #1a1a1a; color: #fff; text-align: center; padding: 40px; }
        .upload-card { background-color: #2a2a2a; border-radius: 8px; padding: 30px; display: inline-block; border: 1px solid #444; }
        input[type="file"] { margin: 15px 0; font-size: 14px; }
        .btn-upload { background-color: #28a745; color: white; padding: 10px 24px; border: none; font-size: 16px; border-radius: 5px; cursor: pointer; }
        .btn-upload:hover { background-color: #218838; }
    </style>
</head>
<body>
    <div class="upload-card">
        <h2>Sistem Penilaian Menembak - Input Lesan</h2>
        
        <!-- PERUBAHAN: action diubah menggunakan fungsi route() Laravel agar dinamis dan tepat sasaran -->
        <form action="{{ route('evaluate.target') }}" method="POST" enctype="multipart/form-data">
            @csrf
            <label>Pilih File Foto Lesan (.jpg / .png):</label><br>
            <input type="file" name="target_image" accept="image/*" required><br>
            <button type="submit" class="btn-upload">Proses & Nilai Lesan</button>
        </form>
    </div>
</body>
</html>