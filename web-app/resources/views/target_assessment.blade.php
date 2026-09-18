<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Validasi Hasil AI - Sistem Penilaian Menembak</title>
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; }
        #targetCanvas { border: 2px solid #2c3e50; cursor: crosshair; max-width: 100%; height: auto; }
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
        <div class="col-md-8 mb-4">
            <div class="card shadow-sm border-0">
                <div class="card-header bg-dark text-white fw-bold">Visualisasi Lesan (Render AI)</div>
                <div class="card-body text-center bg-white" style="overflow-x: auto;">
                    <canvas id="targetCanvas" width="600" height="600"></canvas>
                </div>
            </div>
        </div>

        <div class="col-md-4">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-primary text-white fw-bold">Hasil Kalkulasi</div>
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center">
                        <h5 class="mb-0 text-secondary">Total Skor</h5>
                        <h1 class="mb-0 fw-bold text-success" id="uiTotalScore">0</h1>
                    </div>
                </div>
            </div>
            <button onclick="simpanKeDatabase()" class="btn btn-success btn-lg w-100 shadow-sm fw-bold">Kunci & Simpan Hasil</button>
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
        
        this.canvas.addEventListener('click', (e) => this.handleCanvasClick(e));
    }

    loadData(imageSrc, dataBalistik) {
        this.bgImage.src = imageSrc;
        this.shots = dataBalistik.shots || []; 
        this.updateScoreUI();
        
        this.bgImage.onload = () => {
            this.canvas.width = this.bgImage.width;
            this.canvas.height = this.bgImage.height;
            this.draw();
        };
    }

    draw() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.drawImage(this.bgImage, 0, 0, this.canvas.width, this.canvas.height);
        
        this.shots.forEach(shot => {
            this.ctx.beginPath();
            this.ctx.arc(shot.x, shot.y, this.HIT_RADIUS, 0, 2 * Math.PI);
            this.ctx.fillStyle = 'rgba(231, 76, 60, 0.85)';
            this.ctx.fill();
            this.ctx.lineWidth = 2;
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
            return Math.hypot(shot.x - mouseX, shot.y - mouseY) <= this.HIT_RADIUS * 3;
        });

        if (hitIndex !== -1) {
            this.shots.splice(hitIndex, 1);
        } else {
            const estimasiSkor = this.kalkulasiSkorFrontend(mouseX, mouseY);
            this.shots.push({x: mouseX, y: mouseY, score: estimasiSkor, sumber_deteksi: 'manual'}); 
        }
        
        this.updateScoreUI();
        this.draw(); 
    }

    updateScoreUI() {
        let total = 0;
        this.shots.forEach(shot => { total += (shot.score || 0); });
        document.getElementById('uiTotalScore').innerText = total;
    }

    kalkulasiSkorFrontend(x, y) {
        const centerX = this.canvas.width / 2;
        const centerY = this.canvas.height / 2;
        const distance = Math.hypot(centerX - x, centerY - y);
        if(distance < (this.canvas.width * 0.05)) return 10;
        if(distance < (this.canvas.width * 0.15)) return 9;
        if(distance < (this.canvas.width * 0.25)) return 8;
        return 5; 
    }
}

const evaluator = new CanvasEvaluator('targetCanvas');

// Injeksi variabel global dari Blade
const imagePathDb = "{!! $image_path_db ?? '' !!}";
const jenisSenjata = "{!! $jenis_senjata ?? '' !!}";
const sikap = "{!! $sikap ?? '' !!}";
const jarakM = "{!! $jarak_m ?? '' !!}";

document.addEventListener("DOMContentLoaded", () => {
    const imageUrl = "{!! $image_url ?? '' !!}";
    const aiData = @json($hasil_ai ?? []);
    
    if(imageUrl && aiData) {
        evaluator.loadData(imageUrl, aiData);
    }
});

function simpanKeDatabase() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    
    // Membawa metadata form sebelumnya ke Controller
    const payload = {
        total_score: document.getElementById('uiTotalScore').innerText,
        shots: evaluator.shots,
        image_path_db: imagePathDb,
        jenis_senjata: jenisSenjata,
        sikap: sikap,
        jarak_m: jarakM
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
            window.location.href = '/upload-target';
        } else {
            alert("Gagal: " + data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert("Kesalahan koneksi server saat menyimpan.");
    });
}
</script>
</body>
</html>