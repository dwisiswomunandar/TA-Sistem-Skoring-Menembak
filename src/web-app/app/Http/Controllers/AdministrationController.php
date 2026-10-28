<?php

namespace App\Http\Controllers;

use App\Models\User;
use Barryvdh\DomPDF\Facade\Pdf;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Rules\Password;

class AdministrationController extends Controller
{
    private function staff(Request $request): void
    {
        abort_unless(in_array($request->user()->role, ['admin', 'scorer']), 403);
    }

    private function admin(Request $request): void
    {
        abort_unless($request->user()->role === 'admin', 403);
    }

    private function query(Request $request)
    {
        $query = DB::table('assessment_records as r')->join('assessment_sessions as s', 's.id', '=', 'r.session_id')
            ->join('kegiatan as k', 'k.id', '=', 's.kegiatan_id')->join('users as p', 'p.id', '=', 's.participant_id')
            ->join('users as a', 'a.id', '=', 's.assessor_id')
            ->select('r.*', 's.kegiatan_id', 's.participant_id', 's.assessor_id', 's.number', 'k.nama_kegiatan', 'p.name as participant', 'a.name as assessor');
        if ($request->user()->role === 'scorer') {
            $query->where('s.assessor_id', $request->user()->id);
        } elseif ($request->user()->role !== 'admin') {
            $query->where('s.participant_id', $request->user()->id)->where('r.status', 'locked');
        }

        return $query;
    }

    public function index(Request $request)
    {
        $staff = in_array($request->user()->role, ['admin', 'scorer']);

        return view('admin.index', ['records' => $this->query($request)->orderByDesc('r.id')->paginate(20),
            'activities' => $staff ? DB::table('kegiatan')->where('status_aktif', true)->orderByDesc('tanggal')->get() : collect(),
            'participants' => $staff ? User::where('role', 'peserta')->orderBy('name')->get() : collect(),
            'assessors' => $request->user()->role === 'admin' ? User::whereIn('role', ['scorer', 'admin'])->get() : collect(), 'staff' => $staff]);
    }

    public function activity(Request $request)
    {
        $this->admin($request);
        $validated = $request->validate(['nama_kegiatan' => 'required|string|max:255', 'tanggal' => 'required|date']);
        DB::table('kegiatan')->insert($validated + ['status_aktif' => true, 'created_at' => now(), 'updated_at' => now()]);

        return back()->with('success', 'Kegiatan dibuat.');
    }

    public function user(Request $request)
    {
        $this->admin($request);
        $validated = $request->validate(['name' => 'required|string|max:255', 'email' => 'required|email|max:255|unique:users', 'role' => ['required', Rule::in(['peserta', 'scorer', 'admin'])], 'password' => ['required', 'confirmed', Password::min(12)]]);
        $user = new User;
        $user->name = $validated['name'];
        $user->email = $validated['email'];
        $user->password = $validated['password'];
        $user->role = $validated['role'];
        $user->save();

        return back()->with('success', 'Akun dibuat.');
    }

    public function session(Request $request)
    {
        $this->staff($request);
        $validated = $request->validate(['kegiatan_id' => ['required', 'integer', Rule::exists('kegiatan', 'id')->where('status_aktif', true)],
            'participant_id' => ['required', 'integer', Rule::exists('users', 'id')->where('role', 'peserta')],
            'assessor_id' => ['required', 'integer', Rule::exists('users', 'id')->whereIn('role', ['scorer', 'admin'])],
            'number' => 'required|integer|min:1|max:100000']);
        abort_if($request->user()->role === 'scorer' && (int) $validated['assessor_id'] !== $request->user()->id, 403);
        $id = DB::transaction(function () use ($validated, $request) {
            // Serialize creation per activity, including concurrent duplicate requests.
            DB::table('kegiatan')->where('id', $validated['kegiatan_id'])->lockForUpdate()->first();
            abort_if(DB::table('assessment_sessions')->where('kegiatan_id', $validated['kegiatan_id'])->where('participant_id', $validated['participant_id'])->where('number', $validated['number'])->exists(), 409, 'Nomor sesi peserta sudah ada.');
            $sessionId = DB::table('assessment_sessions')->insertGetId($validated + ['created_at' => now(), 'updated_at' => now()]);
            $id = DB::table('assessment_records')->insertGetId(['session_id' => $sessionId, 'status' => 'draft', 'version' => 1, 'created_at' => now(), 'updated_at' => now()]);
            $this->audit($id, $request->user()->id, 'created', 'Pembuatan sesi', null, DB::table('assessment_records')->find($id));

            return $id;
        });

        return redirect()->route('records.show', $id);
    }

    private function audit($id, $actor, $action, $reason, $before, $after): void
    {
        DB::table('assessment_audits')->insert(['record_id' => $id, 'actor_id' => $actor, 'action' => $action, 'reason' => $reason, 'before' => $before ? json_encode($before) : null, 'after' => json_encode($after), 'created_at' => now()]);
    }

    public function show(Request $request, int $id)
    {
        $record = $this->query($request)->where('r.id', $id)->first();
        abort_unless($record, 404);
        $audits = in_array($request->user()->role, ['admin', 'scorer']) ? DB::table('assessment_audits as h')->join('users as u', 'u.id', '=', 'h.actor_id')->where('h.record_id', $id)->select('h.*', 'u.name')->orderBy('h.id')->get() : collect();

        return view('admin.record', compact('record', 'audits'));
    }

    public function update(Request $request, int $id)
    {
        $this->staff($request);
        $validated = $request->validate(['version' => 'required|integer|min:1', 'action' => ['required', Rule::in(['save', 'verify', 'lock', 'reopen'])],
            'reason' => 'required|string|min:5|max:2000', 'total' => 'nullable|integer|min:0|max:1000000', 'notes' => 'nullable|string|max:5000', 'photo' => 'nullable|image|mimes:jpg,jpeg,png|max:5120']);
        $uploaded = null;
        try {
            DB::transaction(function () use ($request, $id, $validated, &$uploaded) {
                $before = DB::table('assessment_records')->where('id', $id)->lockForUpdate()->first();
                abort_unless($before, 404);
                $session = DB::table('assessment_sessions')->find($before->session_id);
                abort_unless($request->user()->role === 'admin' || $session->assessor_id === $request->user()->id, 403);
                abort_if((int) $before->version !== (int) $validated['version'], 409, 'Data berubah. Muat ulang halaman sebelum melanjutkan.');
                $change = ['version' => $before->version + 1, 'updated_at' => now()];
                switch ($validated['action']) {
                    case 'save':
                        abort_unless($before->status === 'draft', 409, 'Hanya draf dapat diubah.');
                        $change += ['total' => $validated['total'] ?? null, 'notes' => $validated['notes'] ?? null];
                        if ($request->hasFile('photo')) {
                            $uploaded = $request->file('photo')->store('assessment-photos', 'local');
                            abort_unless($uploaded, 500, 'Gagal menyimpan lampiran.');
                            $change['photo_path'] = $uploaded;
                        }
                        break;
                    case 'verify':
                        abort_unless($before->status === 'draft' && $before->total !== null, 409, 'Simpan nilai pada draf sebelum verifikasi.');
                        $change += ['status' => 'verified', 'verified_by' => $request->user()->id, 'verified_at' => now()];
                        break;
                    case 'lock':
                        abort_unless($before->status === 'verified', 409, 'Verifikasi hasil terlebih dahulu.');
                        $change += ['status' => 'locked', 'locked_at' => now()];
                        break;
                    case 'reopen':
                        $this->admin($request);
                        abort_unless(in_array($before->status, ['verified', 'locked']), 409);
                        $change += ['status' => 'draft', 'verified_by' => null, 'verified_at' => null, 'locked_at' => null];
                        break;
                }
                DB::table('assessment_records')->where('id', $id)->update($change);
                $this->audit($id, $request->user()->id, $validated['action'], $validated['reason'], $before, DB::table('assessment_records')->find($id));
            });
        } catch (\Throwable $e) {
            if ($uploaded) {
                Storage::disk('local')->delete($uploaded);
            } throw $e;
        }

        return back()->with('success', 'Perubahan tersimpan dan tercatat.');
    }

    public function report(Request $request)
    {
        $query = $this->query($request)->where('r.status', 'locked');
        $validated = $request->validate(['kegiatan_id' => 'nullable|integer|exists:kegiatan,id']);
        if (! empty($validated['kegiatan_id'])) {
            $query->where('s.kegiatan_id', $validated['kegiatan_id']);
        }

        return view('admin.report', ['records' => $query->orderByDesc('r.id')->paginate(50)->withQueryString()]);
    }

    public function photo(Request $request, int $id)
    {
        $record = $this->query($request)->where('r.id', $id)->first();
        abort_unless($record && $record->photo_path, 404);
        abort_unless(Storage::disk('local')->exists($record->photo_path), 404);

        return Storage::disk('local')->response($record->photo_path, null, ['Cache-Control' => 'private, no-store', 'X-Content-Type-Options' => 'nosniff']);
    }

    public function pdf(Request $request,int $id)
    {
        $record = $this->query($request)->where('r.id',$id)->where('r.status','locked')->first();
        abort_unless($record,404);

        return Pdf::loadView('admin.pdf',compact('record'))->download('hasil-'.$id.'.pdf');
    }
}
