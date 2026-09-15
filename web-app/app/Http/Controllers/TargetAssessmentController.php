<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\DB;

class TargetAssessmentController extends Controller
{
    // 1. Menerima gambar dan mengirimkannya ke CV Engine Python
    public function evaluateTarget(Request $request)
    {
        // Validasi unggahan file
        $request->validate([
            'target_image' => 'required|image|mimes:jpeg,png,jpg|max:5120',
        ]);

        $image = $request->file('target_image');
        $imageName = time() . '.' . $image->getClientOriginalExtension();
        
        // Simpan gambar secara lokal di folder public/uploads agar bisa ditampilkan di Canvas
        $image->move(public_path('uploads'), $imageName);
        $imageUrl = asset('uploads/' . $imageName);
        $imagePath = public_path('uploads/' . $imageName);

        try {
            // Tembak file gambar ke API Python yang menyala di Port 8001
            $response = Http::attach(
                'file', file_get_contents($imagePath), $imageName
            )->post('http://127.0.0.1:8001/api/evaluate');

            if ($response->successful()) {
                $hasilAi = $response->json()['data'];
                
                // Kembalikan view Canvas (Task 3) beserta data dari Python
                return view('target_assessment', [
                    'image_url' => $imageUrl,
                    'hasil_ai' => $hasilAi
                ]);
            } else {
                return back()->with('error', 'Gagal memproses gambar di CV Engine.');
            }
        } catch (\Exception $e) {
            return back()->with('error', 'Koneksi ke server Python terputus: ' . $e->getMessage());
        }
    }

    // 2. Menyimpan data final hasil koreksi Canvas ke Database secara aman (Task 3 Keamanan)
    public function saveAssessment(Request $request)
    {
        $payload = $request->all();

        try {
            DB::beginTransaction();

            // Simpan header menggunakan query builder Laravel (Otomatis menggunakan PDO ter-prepare)
            $idPenilaian = DB::table('hasil_penilaian')->insertGetId([
                'id_penembak' => 1, // Ubah sesuai Auth::id() jika sistem login sudah aktif
                'total_skor' => $payload['total_score'],
                'moa' => $payload['moa'],
                'created_at' => now(),
            ]);

            // Siapkan array batch insert untuk detail titik
            $detailTitik = [];
            foreach ($payload['shots'] as $shot) {
                $detailTitik[] = [
                    'id_penilaian' => $idPenilaian,
                    'pos_x' => $shot['x'],
                    'pos_y' => $shot['y'],
                    'skor' => $shot['score'] ?? 0
                ];
            }

            // Eksekusi insert masal
            DB::table('detail_titik')->insert($detailTitik);

            DB::commit();
            return response()->json(['status' => 'success', 'message' => 'Data penilaian berhasil diamankan di database.']);

        } catch (\Exception $e) {
            DB::rollBack();
            return response()->json(['status' => 'error', 'message' => 'Gagal menyimpan: ' . $e->getMessage()], 500);
        }
    }
}