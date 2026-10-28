<?php

namespace Tests\Feature;

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

class AdministrationTest extends TestCase
{
    use RefreshDatabase;

    private function user(string $role): User
    {
        $u = User::factory()->create();
        $u->role = $role;
        $u->save();

        return $u;
    }

    private function fixture(): array
    {
        $admin = $this->user('admin');
        $scorer = $this->user('scorer');
        $participant = $this->user('peserta');
        $activity = DB::table('kegiatan')->insertGetId(['nama_kegiatan' => 'Uji administrasi', 'tanggal' => '2026-10-28', 'status_aktif' => true]);
        $this->actingAs($admin)->post('/sesi', ['kegiatan_id' => $activity, 'participant_id' => $participant->id, 'assessor_id' => $scorer->id, 'number' => 1])->assertRedirect();

        return [$admin, $scorer, $participant, DB::table('assessment_records')->first()->id, $activity];
    }

    private function change($user, $id, $action, $version, $extra = [])
    {
        return $this->actingAs($user)->patch('/hasil/'.$id, array_merge(['action' => $action, 'version' => $version, 'reason' => 'Pengujian perubahan data'], $extra));
    }

    public function test_guests_cannot_read_or_write(): void
    {
        $this->get('/administrasi')->assertRedirect('/login');
        $this->post('/sesi', [])->assertRedirect('/login');
        $this->get('/laporan')->assertRedirect('/login');
    }

    public function test_registration_cannot_elevate_role(): void
    {
        $this->post('/register', ['name' => 'Peserta Baru', 'email' => 'new@example.test', 'password' => 'very-long-password', 'password_confirmation' => 'very-long-password', 'role' => 'admin'])->assertRedirect('/administrasi');
        $this->assertDatabaseHas('users', ['email' => 'new@example.test', 'role' => 'peserta']);
    }

    public function test_login_logout_and_invalid_password(): void
    {
        $u = $this->user('peserta');
        $this->post('/login', ['email' => $u->email, 'password' => 'incorrect'])->assertSessionHasErrors('email');
        $this->assertGuest();
        $this->post('/login', ['email' => $u->email, 'password' => 'password'])->assertRedirect('/administrasi');
        $this->assertAuthenticatedAs($u);
        $this->post('/logout')->assertRedirect('/login');
        $this->assertGuest();
    }

    public function test_only_admin_can_create_accounts_or_activities(): void
    {
        $this->actingAs($this->user('scorer'))->post('/kegiatan', [])->assertForbidden();
        $this->post('/pengguna', [])->assertForbidden();
    }

    public function test_participant_cannot_create_session(): void
    {
        $this->actingAs($this->user('peserta'))->post('/sesi', [])->assertForbidden();
    }

    public function test_duplicate_session_and_invalid_participant_are_rejected(): void
    {
        [$a,$s,$p,$id,$k] = $this->fixture();
        $data = ['kegiatan_id' => $k, 'participant_id' => $p->id, 'assessor_id' => $s->id, 'number' => 1];
        $this->actingAs($a)->post('/sesi', $data)->assertStatus(409);
        $data['participant_id'] = $s->id;
        $this->post('/sesi', $data)->assertSessionHasErrors('participant_id');
        $this->assertDatabaseCount('assessment_sessions', 1);
    }

    public function test_scorer_cannot_assign_other_scorer(): void
    {
        [$a,$s,$p,$id,$k] = $this->fixture();
        $this->actingAs($s)->post('/sesi', ['kegiatan_id' => $k, 'participant_id' => $p->id, 'assessor_id' => $a->id, 'number' => 2])->assertForbidden();
    }

    public function test_full_workflow_audit_and_pdf(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $this->change($s, $id, 'save', 1, ['total' => 72, 'notes' => 'Nilai masukan petugas'])->assertRedirect();
        $this->change($s, $id, 'verify', 2)->assertRedirect();
        $this->change($s, $id, 'lock', 3)->assertRedirect();
        $this->assertDatabaseHas('assessment_records', ['id' => $id, 'total' => 72, 'status' => 'locked', 'version' => 4]);
        $this->assertDatabaseCount('assessment_audits', 4);
        $last = DB::table('assessment_audits')->orderByDesc('id')->first();
        $this->assertSame('verified', json_decode($last->before, true)['status']);
        $this->assertSame('locked', json_decode($last->after, true)['status']);
        $this->actingAs($p)->get('/hasil/'.$id)->assertOk()->assertSee('72');
        $this->get('/hasil/'.$id.'/pdf')->assertOk()->assertHeader('content-type', 'application/pdf');
    }

    public function test_locked_record_cannot_be_edited_and_only_admin_can_reopen(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $this->change($s, $id, 'save', 1, ['total' => 0]);
        $this->change($s, $id, 'verify', 2);
        $this->change($s, $id, 'lock', 3);
        $this->change($s, $id, 'save', 4, ['total' => 99])->assertStatus(409);
        $this->change($s, $id, 'reopen', 4)->assertForbidden();
        $this->change($a, $id, 'reopen', 4)->assertRedirect();
        $this->assertDatabaseHas('assessment_records', ['id' => $id, 'status' => 'draft', 'total' => 0, 'version' => 5, 'verified_by' => null, 'locked_at' => null]);
    }

    public function test_invalid_order_and_stale_updates_do_not_write_audit(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $this->change($s, $id, 'verify', 1)->assertStatus(409);
        $this->change($s, $id, 'lock', 1)->assertStatus(409);
        $this->change($s, $id, 'save', 1, ['total' => 12])->assertRedirect();
        $this->change($s, $id, 'save', 1, ['total' => 13])->assertStatus(409);
        $this->assertDatabaseCount('assessment_audits', 2);
    }

    public function test_record_access_is_scoped(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $other = $this->user('scorer');
        $this->actingAs($other)->get('/hasil/'.$id)->assertNotFound();
        $this->change($other, $id, 'save', 1, ['total' => 15])->assertForbidden();
        $this->actingAs($p)->get('/hasil/'.$id)->assertNotFound();
        $this->change($s, $id, 'save', 1, ['total' => 15]);
        $this->change($s, $id, 'verify', 2);
        $this->change($s, $id, 'lock', 3);
        $this->actingAs($this->user('peserta'))->get('/hasil/'.$id)->assertNotFound();
        $this->get('/hasil/'.$id.'/pdf')->assertNotFound();
    }

    public function test_bad_values_and_missing_reason_are_rejected(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $this->change($s, $id, 'save', 1, ['total' => -1])->assertSessionHasErrors('total');
        $this->change($s, $id, 'save', 1, ['total' => 1.5])->assertSessionHasErrors('total');
        $this->change($s, $id, 'save', 1, ['reason' => ''])->assertSessionHasErrors('reason');
        $this->assertDatabaseCount('assessment_audits', 1);
    }

    public function test_pages_render_and_drafts_are_not_in_reports(): void
    {
        [$a,$s,$p,$id] = $this->fixture();
        $this->actingAs($a)->get('/administrasi')->assertOk()->assertSee('Buat akun');
        $this->get('/hasil/'.$id)->assertOk();
        $this->get('/laporan')->assertOk()->assertDontSee('Uji administrasi');
        $this->get('/hasil/'.$id.'/pdf')->assertNotFound();
    }

    public function test_seeder_does_not_delete_existing_data(): void
    {
        $this->fixture();
        $this->seed();
        $this->assertDatabaseCount('users', 3);
        $this->assertDatabaseCount('assessment_records', 1);
    }

    public function test_photo_is_private_and_requires_record_access(): void
    {
        Storage::fake('local');
        [$a,$s,$p,$id] = $this->fixture();
        $this->change($s, $id, 'save', 1, ['total' => 10, 'photo' => UploadedFile::fake()->image('test.jpg')])->assertRedirect();
        $record = DB::table('assessment_records')->find($id);
        Storage::disk('local')->assertExists($record->photo_path);
        $this->actingAs($s)->get('/hasil/'.$id.'/foto')->assertOk();
        $this->actingAs($p)->get('/hasil/'.$id.'/foto')->assertNotFound();
        $this->change($s, $id, 'verify', 2);
        $this->change($s,$id,'lock',3);
        $this->actingAs($p)->get('/hasil/'.$id.'/foto')->assertOk();
        $this->actingAs($this->user('peserta'))->get('/hasil/'.$id.'/foto')->assertNotFound();
    }

    public function test_legacy_write_endpoint_is_closed(): void
    {
        $this->actingAs($this->user('admin'))->post('/api/save-assessment',[])->assertStatus(410);
    }
}
