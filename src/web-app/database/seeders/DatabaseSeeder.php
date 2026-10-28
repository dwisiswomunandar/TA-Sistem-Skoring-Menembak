<?php

namespace Database\Seeders;

use Illuminate\Database\Seeder;

class DatabaseSeeder extends Seeder
{
    public function run(): void
    {
        // Tidak menghapus data atau membuat akun dengan kata sandi bawaan.
        $this->command?->info('Gunakan php artisan admin:create untuk membuat administrator.');
    }
}
