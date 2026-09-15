<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Validasi Hasil AI - Sistem Penilaian Menembak</title>
    
    <!-- Token CSRF untuk keamanan request POST Laravel -->
    <meta name="csrf-token" content="{{ csrf_token() }}">
    
    <!-- Memuat Bootstrap CSS agar tampilan rapi secara instan -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; }
    </style>
</head>
<body>

<div class="container py-5">
    <div class="row mb-3">
        <div class="col-12">
            <h2 class="fw-bold">Validasi Hasil Deteksi AI</h2>
            <p class="text-muted">Klik pada area kanvas untuk menambah (titik manual) atau menghapus (koreksi keliru) lubang peluru.</p>
        </div>
    </div>
    
    <div class="row">
        <!-- Kolom Kiri: Canvas Interaktif -->
        <div class="col-md-7 mb-4">
            <div class="card shadow-sm border-0">
                <div class="card-header bg-dark text-white fw-bold">
                    Visualisasi Lesan
                </div>
                <div class="card-body text-center bg-white" style="overflow-x: auto;">
                    <canvas id="targetCanvas" width="500" height="530" style="border: 2px solid #2c3e50; cursor: crosshair; max-width: 100%;"></canvas>
                </div>
            </div>
        </div>

        <!-- Kolom Kanan: Panel Statistik & Tombol Aksi -->
        <div class="col-md-5">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-primary text-white fw-bold">
                    Statistik Balistik
                </div>
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h4 class="mb-0 text-secondary">Total Skor</h4>
                        <h2 class="mb-0 fw-bold text-success" id="uiTotalScore">0</h2>
                    </div>
                    <hr>
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <span class="fs-5">Group Size</span>
                        <span class="fs-5 fw-bold"><span id="uiGroupSize">0.00</span> mm</span>
                    </div>
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="fs-5">MOA (Minute of Angle)</span>
                        <span class="fs-5 fw-bold"><span id="uiMoa">0.00</span></span>
                    </div>
                </div>
            </div>

            <!-- Tombol Simpan -->
            <button onclick="simpanKeDatabase()" class="btn btn-success btn-lg w-100 shadow-sm fw-bold">
                Simpan Hasil Final
            </button>
        </div>
    </div>
</div>

<script>
class CanvasEvaluator {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.shots = []; 
        this.bgImage = new Image();
        this.HIT_RADIUS = 6; 
        
        // Listener interaktif untuk fitur Click-to-Add/Remove
        this.canvas.addEventListener('click', (e) => this.handleCanvasClick(e));
    }

    loadData(imageSrc, dataBalistik) {
        this.bgImage.src = imageSrc;
        this.shots = dataBalistik.shots || []; 
        
        // Update Panel UI HTML
        document.getElementById('uiTotalScore').innerText = dataBalistik.total_score || 0;
        document.getElementById('uiGroupSize').innerText = dataBalistik.group_size_mm || 0;
        document.getElementById('uiMoa').innerText = dataBalistik.moa || 0;
        
        this.bgImage.onload = () => this.draw();
    }

    draw() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.drawImage(this.bgImage, 0, 0, this.canvas.width, this.canvas.height);
        
        // Render setiap titik perkenaan
        this.shots.forEach(shot => {
            this.ctx.beginPath();
            this.ctx.arc(shot.x, shot.y, this.HIT_RADIUS, 0, 2 * Math.PI);
            this.ctx.fillStyle = 'rgba(231, 76, 60, 0.85)'; 
            this.ctx.fill();
            this.ctx.lineWidth = 1.5;
            this.ctx.strokeStyle = '#FFFFFF';
            this.ctx.stroke();
        });
    }

    handleCanvasClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        const mouseX = (e.clientX - rect.left) * scaleX;
        const mouseY = (e.clientY - rect.top) * scaleY;
        
        const hitIndex = this.shots.findIndex(shot => {
            return Math.hypot(shot.x - mouseX, shot.y - mouseY) <= this.HIT_RADIUS * 2;
        });

        if (hitIndex !== -1) {
            this.shots.splice(hitIndex, 1);
        } else {
            this.shots.push({x: mouseX, y: mouseY, score: 0}); 
        }
        
        this.draw(); 
    }
}

const evaluator = new CanvasEvaluator('targetCanvas');

// === INJEKSI DATA DARI LARAVEL CONTROLLER ===
document.addEventListener("DOMContentLoaded", () => {
    const imageUrl = "{!! $image_url !!}";
    const aiData = @json($hasil_ai);
    
    if(aiData) {
        evaluator.loadData(imageUrl, aiData);
    }
});

function simpanKeDatabase() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    
    const payload = {
        total_score: document.getElementById('uiTotalScore').innerText,
        moa: document.getElementById('uiMoa').innerText,
        shots: evaluator.shots
    };

    fetch('/api/save-assessment', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRF-TOKEN': csrfToken,
            'Accept': 'application/json'
        },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        if(data.status === 'success'){
            alert("Berhasil: " + data.message);
        } else {
            alert("Gagal: " + data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert("Terjadi kesalahan koneksi server.");
    });
}
</script>
</body>
</html>