<?php

use App\Http\Controllers\AccountController;
use App\Http\Controllers\AdministrationController;
use Illuminate\Support\Facades\Route;

Route::get('/', fn () => redirect('/administrasi'));
Route::middleware('guest')->group(function () {
    Route::view('/login', 'admin.login')->name('login');
    Route::post('/login', [AccountController::class, 'login'])->middleware('throttle:5,1');
    Route::view('/register', 'admin.register')->name('register');
    Route::post('/register', [AccountController::class, 'register'])->middleware('throttle:5,1');
});
Route::middleware('auth')->group(function () {
    Route::post('/logout', [AccountController::class, 'logout'])->name('logout');
    Route::get('/administrasi', [AdministrationController::class, 'index'])->name('admin.index');
    Route::post('/kegiatan', [AdministrationController::class, 'activity'])->name('activities.store');
    Route::post('/pengguna', [AdministrationController::class, 'user'])->name('users.store');
    Route::post('/sesi', [AdministrationController::class, 'session'])->name('sessions.store');
    Route::get('/hasil/{id}', [AdministrationController::class, 'show'])->whereNumber('id')->name('records.show');
    Route::patch('/hasil/{id}', [AdministrationController::class, 'update'])->whereNumber('id')->name('records.update');
    Route::get('/laporan', [AdministrationController::class, 'report'])->name('reports.index');
    Route::get('/hasil/{id}/foto', [AdministrationController::class, 'photo'])->whereNumber('id')->name('records.photo');
    Route::get('/hasil/{id}/pdf', [AdministrationController::class, 'pdf'])->whereNumber('id')->name('records.pdf');
    // Legacy data and source remain available; dummy-ID writes must not stay exposed.
    Route::get('/upload-target', fn () => redirect('/administrasi'))->name('upload.form');
    Route::get('/leaderboard', fn () => redirect('/laporan'))->name('leaderboard');
    Route::post('/evaluate-target', fn () => abort(410, 'Alur lama diarsipkan. Gunakan administrasi sesi.'))->name('evaluate.target');
    Route::post('/api/save-assessment', fn () => abort(410,'Penyimpanan lama dinonaktifkan. Gunakan hasil sesi.'))->name('save.assessment');
});
