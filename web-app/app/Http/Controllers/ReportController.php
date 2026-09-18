<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class ReportController extends Controller
{
    /**
     * Mengambil data skor tertinggi dari database untuk ditampilkan di Leaderboard (FR-09)
     */
    public function leaderboard()
    {
        // Menggabungkan tabel header dengan tabel users (peserta) dan kegiatan
        $leaderboard = DB::table('penilaian_header')
            ->join('users as peserta', 'penilaian_header.peserta_id', '=', 'peserta.id')
            ->join('kegiatan', 'penilaian_header.kegiatan_id', '=', 'kegiatan.id')
            ->select(
                'penilaian_header.id',
                'peserta.name as nama_peserta',
                'kegiatan.nama_kegiatan',
                'penilaian_header.jenis_senjata',
                'penilaian_header.total_skor',
                'penilaian_header.created_at'
            )
            ->where('penilaian_header.status', 'terkunci') // Hanya tampilkan data yang sudah divalidasi
            ->orderByDesc('penilaian_header.total_skor') // Urutkan dari skor tertinggi
            ->get();

        return view('reports.leaderboard', compact('leaderboard'));
    }
}