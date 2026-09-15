<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up()
    {
        // Tabel Header: Rekap Hasil Penilaian AI
        Schema::create('hasil_penilaian', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('id_penembak')->nullable(); // ID User
            $table->integer('total_skor');
            $table->decimal('moa', 8, 2); // Nilai MOA bisa desimal
            $table->timestamps();
        });

        // Tabel Detail: Titik Koordinat Kanvas
        Schema::create('detail_titik', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('id_penilaian');
            $table->float('pos_x');
            $table->float('pos_y');
            $table->integer('skor')->default(0);
            
            // Relasi ke tabel utama
            $table->foreign('id_penilaian')->references('id')->on('hasil_penilaian')->onDelete('cascade');
        });
    }

    public function down()
    {
        Schema::dropIfExists('detail_titik');
        Schema::dropIfExists('hasil_penilaian');
    }
};