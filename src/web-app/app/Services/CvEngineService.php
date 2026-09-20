<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Exception;

class CvEngineService
{
    protected string $apiUrl;

    public function __construct()
    {
        // URL Endpoint FastAPI Engine
        $this->apiUrl = config('services.cv_engine.url', 'http://127.0.0.1:8001/api/v1/process-target');
    }

    /**
     * Mengirimkan file gambar lesan ke FastAPI Engine via HTTP Multipart POST.
     *
     * @param string $filePath Path absolut file gambar di server
     * @param float $distanceM Jarak tembak dalam meter (default 25.0)
     * @return array|null
     */
    public function processTargetImage(string $filePath, float $distanceM = 25.0): ?array
    {
        try {
            if (!file_exists($filePath)) {
                throw new Exception("File gambar tidak ditemukan pada path: {$filePath}");
            }

            // Mengirim request Multipart Form Data ke FastAPI
            $response = Http::timeout(30)
                ->attach('file', file_get_contents($filePath), basename($filePath))
                ->post($this->apiUrl, [
                    'distance_m' => $distanceM
                ]);

            if ($response->successful()) {
                return $response->json();
            }

            logger()->error('FastAPI Engine Error: ' . $response->body());
            return null;

        } catch (Exception $e) {
            logger()->error('Gagal menghubungkan ke FastAPI Engine: ' . $e->getMessage());
            return null;
        }
    }
}