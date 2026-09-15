<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('shot_details', function (Blueprint $table) {
            $table->id('id_detail');
            $table->integer('id_target');
            $table->integer('pos_x');
            $table->integer('pos_y');
            $table->integer('score_point');
            $table->string('action_type', 30)->default('auto_detected');
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('shot_details');
    }
};