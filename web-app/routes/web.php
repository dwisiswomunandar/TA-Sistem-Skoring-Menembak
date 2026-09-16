<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\TargetAssessmentController;

/*
|--------------------------------------------------------------------------
| Web Routes
|--------------------------------------------------------------------------
|
| Here is where you can register web routes for your application. These
| routes are loaded by the RouteServiceProvider and all of them will
| be assigned to the "web" middleware group. Make something great!
|
*/

// ==========================================
// 1. ROUTE HALAMAN UTAMA & UPLOAD GAMBAR
// ==========================================
// Menampilkan halaman depan atau form unggah lesan tembak
Route::get('/', function () {
    return view('welcome'); // Sesuaikan jika ada landing page khusus
})->name('home');

Route::get('/upload-target', function () {
    // Memanggil file resources/views/upload_target.blade.php secara langsung
    return view('upload_target'); 
})->name('upload.form');


// ==========================================
// 2. ROUTE INTEGRASI AI & COMPUTER VISION (TASK 1 & 2)
// ==========================================
// Menerima file gambar dari form, mengirimnya ke API Python (OpenCV/YOLOv8), 
// lalu me-return view 'target_assessment.blade.php' yang berisi Canvas Interaktif
Route::post('/evaluate-target', [TargetAssessmentController::class, 'evaluateTarget'])->name('evaluate.target');


// ==========================================
// 3. ROUTE PENYIMPANAN DATABASE (TASK 3)
// ==========================================
// Menerima data JSON (koordinat final) dari frontend Canvas via Fetch API 
// dan menyimpannya ke MySQL menggunakan skema yang aman dari SQL Injection
Route::post('/api/save-assessment', [TargetAssessmentController::class, 'saveAssessment'])->name('save.assessment');