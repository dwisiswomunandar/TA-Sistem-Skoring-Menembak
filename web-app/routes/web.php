<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\TargetController;

// Rute Default / Landing
Route::get('/', function () {
    return view('welcome');
});

// Rute Menampilkan Form Upload Target
Route::get('/upload-target', function () {
    return view('upload_target'); 
})->name('upload.form');

// Rute Menangkap Submit Form & Mengeksekusi TargetController@evaluateTarget
Route::post('/evaluate-target', [TargetController::class, 'evaluateTarget'])->name('evaluate.target');

// Rute Menangkap Fetch POST dari Canvas (JSON Final) & Mengeksekusi TargetController@saveAssessment
Route::post('/api/save-assessment', [TargetController::class, 'saveAssessment'])->name('save.assessment');