<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        // 1. Tabel users (Base table)
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('email')->unique();
            $table->timestamp('email_verified_at')->nullable();
            $table->string('password');
            $table->enum('role', ['admin', 'scorer', 'peserta', 'perwira'])->default('peserta');
            $table->rememberToken();
            $table->timestamps();
        });

        // (Tabel bawaan Laravel untuk autentikasi)
        Schema::create('password_reset_tokens', function (Blueprint $table) {
            $table->string('email')->primary();
            $table->string('token');
            $table->timestamp('created_at')->nullable();
        });

        Schema::create('sessions', function (Blueprint $table) {
            $table->string('id')->primary();
            $table->foreignId('user_id')->nullable()->index();
            $table->string('ip_address', 45)->nullable();
            $table->text('user_agent')->nullable();
            $table->longText('payload');
            $table->integer('last_activity')->index();
        });

        // 2. Tabel kegiatan (Base table)
        Schema::create('kegiatan', function (Blueprint $table) {
            $table->id();
            $table->string('nama_kegiatan');
            $table->date('tanggal');
            $table->boolean('status_aktif')->default(true);
            $table->timestamps();
        });

        // 3. Tabel master_lesan (Base table)
        Schema::create('master_lesan', function (Blueprint $table) {
            $table->id();
            $table->enum('jenis_amunisi', ['5.56mm', '9mm']);
            $table->json('poin_per_ring'); 
            $table->timestamps();
        });

        // 4. Tabel penilaian_header (Transaction table - depends on users & kegiatan)
        Schema::create('penilaian_header', function (Blueprint $table) {
            $table->id();
            $table->foreignId('kegiatan_id')->constrained('kegiatan')->cascadeOnDelete();
            $table->foreignId('peserta_id')->constrained('users')->cascadeOnDelete();
            $table->foreignId('scorer_id')->constrained('users')->cascadeOnDelete();
            $table->enum('jenis_senjata', ['laras_panjang', 'pistol']);
            $table->enum('sikap', ['berdiri', 'duduk', 'tiarap'])->nullable();
            $table->integer('jarak_m')->nullable();
            $table->string('foto_path')->nullable();
            $table->integer('total_skor')->default(0);
            $table->enum('status', ['draft', 'terkunci'])->default('draft');
            $table->timestamps();
        });

        // 5. Tabel penilaian_detail_titik (Transaction table - depends on penilaian_header)
        Schema::create('penilaian_detail_titik', function (Blueprint $table) {
            $table->id();
            $table->foreignId('penilaian_header_id')->constrained('penilaian_header')->cascadeOnDelete();
            $table->float('koordinat_x');
            $table->float('koordinat_y');
            $table->integer('skor_ring');
            $table->enum('sumber_deteksi', ['ai', 'manual']);
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        // Drop tabel dengan urutan terbalik untuk mencegah error constraint Foreign Key
        Schema::dropIfExists('penilaian_detail_titik');
        Schema::dropIfExists('penilaian_header');
        Schema::dropIfExists('master_lesan');
        Schema::dropIfExists('kegiatan');
        
        Schema::dropIfExists('sessions');
        Schema::dropIfExists('password_reset_tokens');
        Schema::dropIfExists('users');
    }
};