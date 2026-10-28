<?php

use App\Models\User;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Validator;
use Illuminate\Validation\Rules\Password;

Artisan::command('admin:create', function () {
    $data = ['name' => $this->ask('Nama administrator'), 'email' => $this->ask('Email'), 'password' => $this->secret('Kata sandi (minimal 12 karakter)')];
    $v = Validator::make($data, ['name' => 'required|string|max:255', 'email' => 'required|email|max:255|unique:users', 'password' => ['required', Password::min(12)]]);
    if ($v->fails()) {
        foreach ($v->errors()->all() as $e) {
            $this->error($e);
        }

return 1;
    }
    if ($data['password'] !== $this->secret('Ulangi kata sandi')) {
        $this->error('Kata sandi tidak sama.');

        return 1;
    }
    $user = new User($data);
    $user->role = 'admin';
    $user->save();
    $this->info('Administrator dibuat.');

    return 0;
})->purpose('Membuat administrator tanpa kata sandi bawaan atau menghapus data');
