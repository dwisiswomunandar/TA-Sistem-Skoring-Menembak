<?php

namespace App\Services;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Exception;

class ShotDetailService
{
    /**
     * Menyimpan atau memperbarui detail titik tembak dengan DB Transaction & Prepared Statements
     *
     * @param int $targetId
     * @param array $shots
     * @return bool
     */
    public function saveShotDetails(int $targetId, array $shots): bool
    {
        DB::beginTransaction();
        try {
            // Hapus data perkenaan lama untuk re-indexing bersih
            DB::table('target_shots')
                ->where('id_target', '=', $targetId)
                ->delete();

            $insertData = [];
            foreach ($shots as $shot) {
                $insertData[] = [
                    'id_target'   => $targetId,
                    'pos_x'       => (float)($shot['x'] ?? $shot['pos_x']),
                    'pos_y'       => (float)($shot['y'] ?? $shot['pos_y']),
                    'score_point' => (int)($shot['score'] ?? $shot['score_point']),
                    'action_type' => $shot['action_type'] ?? 'auto_detected',
                    'created_at'  => now(),
                    'updated_at'  => now()
                ];
            }

            if (!empty($insertData)) {
                DB::table('target_shots')->insert($insertData);
            }

            DB::commit();
            return true;
        } catch (Exception $e) {
            DB::rollBack();
            Log::error("Error saveShotDetails on target #{$targetId}: " . $e->getMessage());
            return false;
        }
    }

    /**
     * Mengambil rekapitulasi leaderboard petembak (Prepared Statement & Aggregation Safe)
     *
     * @return array
     */
    public function getShootingLeaderboard(): array
    {
        return DB::table('targets')
            ->join('shooters', 'targets.id_shooter', '=', 'shooters.id')
            ->select(
                'shooters.name as shooter_name',
                'shooters.rank as shooter_rank',
                'shooters.unit as shooter_unit',
                DB::raw('SUM(target_shots.score_point) as total_score'),
                DB::raw('ROUND(AVG(target_shots.score_point), 2) as avg_score'),
                'targets.group_size_mm',
                'targets.moa'
            )
            ->leftJoin('target_shots', 'targets.id', '=', 'target_shots.id_target')
            ->groupBy(
                'targets.id', 
                'shooters.id', 
                'shooters.name', 
                'shooters.rank', 
                'shooters.unit', 
                'targets.group_size_mm', 
                'targets.moa'
            )
            ->orderByDesc('total_score')
            ->orderBy('targets.group_size_mm', 'asc')
            ->get()
            ->toArray();
    }
}