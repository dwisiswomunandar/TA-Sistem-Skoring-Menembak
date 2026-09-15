<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <title>Laporan Hasil Penilaian Menembak</title>
    <style>
        body { font-family: sans-serif; font-size: 12px; color: #333; }
        .header { text-align: center; margin-bottom: 20px; border-bottom: 2px solid #000; padding-bottom: 10px; }
        .header h2 { margin: 0; text-transform: uppercase; }
        .header p { margin: 2px 0; font-size: 10px; color: #555; }
        .meta-table, .data-table { width: 100%; border-collapse: collapse; margin-bottom: 15px; }
        .meta-table td { padding: 4px; vertical-align: top; }
        .data-table th, .data-table td { border: 1px solid #999; padding: 6px; text-align: center; }
        .data-table th { background-color: #f2f2f2; font-weight: bold; }
        .summary-box { background: #f8f9fa; border: 1px solid #ddd; padding: 10px; margin-top: 10px; }
        .footer { margin-top: 30px; text-align: right; font-size: 10px; }
    </style>
</head>
<body>

    <div class="header">
        <h2>Laporan Hasil Penilaian Menembak Presisi</h2>
        <p>Sistem Penilaian Menembak Otomatis (Computer Vision & Balistik)</p>
    </div>

    <!-- Informasi Petembak & Target -->
    <table class="meta-table">
        <tr>
            <td width="18%"><strong>ID Target</strong></td>
            <td width="2%">:</td>
            <td width="30%">#{{ $target->id }}</td>
            <td width="18%"><strong>Tanggal Uji</strong></td>
            <td width="2%">:</td>
            <td width="30%">{{ \Carbon\Carbon::parse($target->created_at)->format('d-m-Y H:i') }}</td>
        </tr>
        <tr>
            <td><strong>Jarak Tembak</strong></td>
            <td>:</td>
            <td>25 Meter</td>
            <td><strong>Jenis Lesan</strong></td>
            <td>:</td>
            <td>Lesan Standar 50cm</td>
        </tr>
    </table>

    <!-- Ringkasan Balistik -->
    <div class="summary-box">
        <h4 style="margin: 0 0 8px 0;">Ringkasan Kalkulasi Balistik</h4>
        <table width="100%">
            <tr>
                <td><strong>Total Perolehan Skor:</strong> {{ $ballistics['total_score'] }}</td>
                <td><strong>Group Size:</strong> {{ $ballistics['group_size_mm'] }} mm</td>
            </tr>
            <tr>
                <td><strong>Rata-rata Skor:</strong> {{ $ballistics['mean_score'] }}</td>
                <td><strong>Akurasi MOA:</strong> {{ $ballistics['moa'] }} MOA</td>
            </tr>
            <tr>
                <td><strong>Total Tembakan:</strong> {{ $ballistics['count'] }} Titik</td>
                <td></td>
            </tr>
        </table>
    </div>

    <h4 style="margin-top: 15px; margin-bottom: 5px;">Rincian Titik Perkenaan Peluru</h4>
    <table class="data-table">
        <thead>
            <tr>
                <th>No</th>
                <th>Koordinat (X, Y)</th>
                <th>Skor Ring</th>
                <th>Tipe Deteksi</th>
            </tr>
        </thead>
        <tbody>
            @forelse($shots as $index => $shot)
            <tr>
                <td>{{ $index + 1 }}</td>
                <td>({{ $shot->pos_x }}, {{ $shot->pos_y }})</td>
                <td><strong>{{ $shot->score_point }}</strong></td>
                <td>{{ $shot->action_type == 'manual_added' ? 'Koreksi Juri' : 'Auto Detect' }}</td>
            </tr>
            @empty
            <tr>
                <td colspan="4">Tidak ada data titik perkenaan.</td>
            </tr>
            @endforelse
        </tbody>
    </table>

    <div class="footer">
        <p>Dicetak Otomatis oleh Sistem Penilaian Menembak | {{ date('d-m-Y H:i:s') }}</p>
    </div>

</body>
</html>