<?php

namespace Database\Seeders;

use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;

class DatabaseSeeder extends Seeder
{
    public function run(): void
    {
        // Kosongkan tabel untuk mencegah duplikasi saat testing ulang
        DB::statement('SET FOREIGN_KEY_CHECKS=0;');
        DB::table('penilaian_detail_titik')->truncate();
        DB::table('penilaian_header')->truncate();
        DB::table('master_lesan')->truncate();
        DB::table('kegiatan')->truncate();
        DB::table('users')->truncate();
        DB::statement('SET FOREIGN_KEY_CHECKS=1;');

        // 1. Data Dummy Pengguna
        DB::table('users')->insert([
            ['id' => 1, 'name' => 'Peserta Siswo', 'email' => 'peserta@sttal.ac.id', 'password' => Hash::make('password'), 'role' => 'peserta', 'created_at' => now()],
            ['id' => 2, 'name' => 'Scorer Lapbak', 'email' => 'scorer@sttal.ac.id', 'password' => Hash::make('password'), 'role' => 'scorer', 'created_at' => now()],
        ]);

        // 2. Data Dummy Kegiatan
        DB::table('kegiatan')->insert([
            ['id' => 1, 'nama_kegiatan' => 'Latihan Menembak TW 1', 'tanggal' => now(), 'status_aktif' => 1, 'created_at' => now()]
        ]);

        // 3. Data Master Lesan
        DB::table('master_lesan')->insert([
            ['id' => 1, 'jenis_amunisi' => '5.56mm', 'poin_per_ring' => json_encode(['ring_10'=>20]), 'created_at' => now()],
            ['id' => 2, 'jenis_amunisi' => '9mm', 'poin_per_ring' => json_encode(['ring_10'=>25]), 'created_at' => now()]
        ]);
    }
}