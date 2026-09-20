<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Leaderboard - Sistem Penilaian Menembak</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #1a1a1a; color: #fff; }
        .card-dark { background-color: #2a2a2a; border: 1px solid #444; border-radius: 8px; }
        .table-dark-custom { color: #fff; }
        .table-dark-custom th { background-color: #333; border-color: #555; }
        .table-dark-custom td { border-color: #555; vertical-align: middle; }
    </style>
</head>
<body class="py-5">

<div class="container">
    <div class="row mb-4">
        <div class="col-12 d-flex justify-content-between align-items-center">
            <div>
                <h2 class="fw-bold text-success mb-0">Leaderboard Latihan Menembak</h2>
                <p class="text-secondary mb-0">Klasemen Skor Tertinggi (Inward Scoring)</p>
            </div>
            <a href="{{ route('upload.form') }}" class="btn btn-outline-light">Kembali ke Upload Target</a>
        </div>
    </div>

    <div class="card card-dark shadow-lg">
        <div class="card-body p-0">
            <div class="table-responsive">
                <table class="table table-hover table-dark-custom mb-0 text-center">
                    <thead>
                        <tr>
                            <th scope="col" width="5%">Peringkat</th>
                            <th scope="col" class="text-start">Nama Prajurit</th>
                            <th scope="col">Kegiatan</th>
                            <th scope="col">Senjata</th>
                            <th scope="col">Waktu Penilaian</th>
                            <th scope="col" width="15%" class="text-success fs-5">Total Skor</th>
                        </tr>
                    </thead>
                    <tbody>
                        @forelse($leaderboard as $index => $row)
                        <tr>
                            <td class="fw-bold fs-5">{{ $index + 1 }}</td>
                            <td class="text-start fw-bold">{{ $row->nama_peserta }}</td>
                            <td>{{ $row->nama_kegiatan }}</td>
                            <td class="text-uppercase">{{ str_replace('_', ' ', $row->jenis_senjata) }}</td>
                            <td class="text-secondary">{{ \Carbon\Carbon::parse($row->created_at)->format('d M Y, H:i') }}</td>
                            <td class="text-success fw-bold fs-4">{{ $row->total_skor }}</td>
                        </tr>
                        @empty
                        <tr>
                            <td colspan="6" class="py-4 text-secondary">Belum ada data penilaian yang terkunci.</td>
                        </tr>
                        @endforelse
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

</body>
</html>