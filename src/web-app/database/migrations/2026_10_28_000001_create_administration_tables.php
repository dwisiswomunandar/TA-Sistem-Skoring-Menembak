<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('assessment_sessions', function (Blueprint $t) {
            $t->id();
            $t->foreignId('kegiatan_id')->constrained('kegiatan')->restrictOnDelete();
            $t->foreignId('participant_id')->constrained('users')->restrictOnDelete();
            $t->foreignId('assessor_id')->constrained('users')->restrictOnDelete();
            $t->unsignedInteger('number');
            $t->timestamps();
            $t->unique(['kegiatan_id', 'participant_id', 'number']);
        });
        Schema::create('assessment_records', function (Blueprint $t) {
            $t->id();
            $t->foreignId('session_id')->unique()->constrained('assessment_sessions')->restrictOnDelete();
            $t->string('photo_path')->nullable();
            $t->unsignedInteger('total')->nullable();
            $t->text('notes')->nullable();
            $t->string('status')->default('draft');
            $t->unsignedInteger('version')->default(1);
            $t->foreignId('verified_by')->nullable()->constrained('users')->restrictOnDelete();
            $t->timestamp('verified_at')->nullable();
            $t->timestamp('locked_at')->nullable();
            $t->timestamps();
        });
        Schema::create('assessment_audits', function (Blueprint $t) {
            $t->id();
            $t->foreignId('record_id')->constrained('assessment_records')->restrictOnDelete();
            $t->foreignId('actor_id')->constrained('users')->restrictOnDelete();
            $t->string('action');
            $t->text('reason');
            $t->json('before')->nullable();
            $t->json('after');
            $t->timestamp('created_at');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('assessment_audits');
        Schema::dropIfExists('assessment_records');
        Schema::dropIfExists('assessment_sessions');
    }
};
