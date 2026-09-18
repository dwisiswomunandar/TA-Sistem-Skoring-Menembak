<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\DB; // WAJIB untuk Transaksi Database

class TargetController extends Controller
{
    public function evaluateTarget(Request $request)
    {
        $request->validate([
            'target_image' => 'required|image|mimes:jpeg,png,jpg|max:5120',
            'jenis_senjata' => 'required|in:laras_panjang,pistol',
        ]);

        $file = $request->file('target_image');
        $imageData = file_get_contents($file->getRealPath());
        $imagePath = $file->store('targets', 'public');
        $base64Image = 'data:image/' . $file->getClientOriginalExtension() . ';base64,' . base64_encode($imageData);

        $pythonApiUrl = 'http://localhost:8001/api/evaluate';
        
        try {
            $response = Http::attach('file', $imageData, $file->getClientOriginalName())->post($pythonApiUrl);
            $hasil_ai = $response->successful() ? $response->json() : ['status' => 'error', 'shots' => []];
        } catch (\Exception $e) {
            $hasil_ai = ['status' => 'error', 'shots' => []];
        }

        // Return Data ditambah variabel senjata, sikap, dan jarak untuk dikirim kembali saat menyimpan
        return view('target_assessment', [
            'image_url' => $base64Image, 
            'hasil_ai' => $hasil_ai,
            'image_path_db' => $imagePath,
            'jenis_senjata' => $request->jenis_senjata,
            'sikap' => $request->hidden_sikap,
            'jarak_m' => $request->hidden_jarak
        ]);
    }

    public function saveAssessment(Request $request)
    {
        $request->validate([
            'total_score' => 'required|numeric',
            'shots' => 'array',
            'image_path_db' => 'required|string',
            'jenis_senjata' => 'required|string'
        ]);

        try {
            // Membuka Transaksi Database (NFR-04)
            DB::beginTransaction();

            // Insert ke tabel Penilaian Header
            $headerId = DB::table('penilaian_header')->insertGetId([
                'kegiatan_id' => 1, // Menggunakan dummy ID 1 dari Seeder
                'peserta_id' => 1,  // Menggunakan dummy ID 1 dari Seeder
                'scorer_id' => 2,   // Menggunakan dummy ID 2 dari Seeder
                'jenis_senjata' => $request->jenis_senjata,
                'sikap' => $request->sikap ?? null,
                'jarak_m' => $request->jarak_m ?? null,
                'foto_path' => $request->image_path_db,
                'total_skor' => $request->total_score,
                'status' => 'terkunci',
                'created_at' => now(),
                'updated_at' => now(),
            ]);

            // Mapping array detail titik dari Kanvas
            $detailData = [];
            foreach ($request->shots as $shot) {
                $detailData[] = [
                    'penilaian_header_id' => $headerId,
                    'koordinat_x' => $shot['x'],
                    'koordinat_y' => $shot['y'],
                    'skor_ring' => $shot['score'] ?? 0,
                    'sumber_deteksi' => $shot['sumber_deteksi'] ?? 'ai',
                    'created_at' => now(),
                    'updated_at' => now(),
                ];
            }

            // Insert ke tabel Penilaian Detail Titik secara massal
            if (count($detailData) > 0) {
                DB::table('penilaian_detail_titik')->insert($detailData);
            }

            // Kunci permanen ke Database
            DB::commit();

            return response()->json(['status' => 'success', 'message' => 'Laporan tembakan berhasil dikunci.']);

        } catch (\Exception $e) {
            DB::rollBack(); // Batalkan semua insert jika terjadi error
            return response()->json(['status' => 'error', 'message' => 'Gagal menyimpan ke database: ' . $e->getMessage()], 500);
        }
    }
}