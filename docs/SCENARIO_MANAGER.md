# Scenario Manager (SCRUM-22)

Komponen back-end yang membuat, mengonfigurasi, menyimpan, dan menduplikasi skenario *what-if* (Tabel 4.5). Setiap skenario berisi satu dari delapan situasi keputusan (Tabel 4.2), parameter situasi itu (Tabel 4.4), dan baseline snapshot yang menjadi dasarnya ([BASELINE_SNAPSHOT.md](BASELINE_SNAPSHOT.md)).

| Bagian | Berkas |
| --- | --- |
| Tabel `twinner.scenario` | `backend/app/modules/simulasi/scenario_models.py`, migrasi `0022` |
| Skema dan aturan parameter per situasi (tanpa basis data) | `backend/app/modules/simulasi/scenario_params.py` |
| Layanan | `backend/app/modules/simulasi/scenario_service.py` |
| Endpoint | `backend/app/modules/simulasi/scenario_router.py` |
| Tes | `backend/tests/test_scenario_params.py` (tanpa basis data), `backend/tests/test_scenario_api.py` (marker `db`) |

## Alur dan status

```
POST /scenarios ──► draft ──PATCH (parameter)──► draft ──POST /save──► saved
                                                   ▲                    │
                                                   └──PATCH parameter───┘
```

| Status | Arti |
| --- | --- |
| `draft` | Baru dibuat, atau parameternya diubah setelah disimpan. Parameter boleh belum lengkap atau belum valid; kesalahannya dilaporkan di `parameter_errors`, bukan ditolak, supaya isian formulir UC-02 yang belum selesai tidak hilang. |
| `saved` | Lolos validasi penuh lewat `POST /save` dan siap disimulasikan. |

Skenario `saved` selalu berisi parameter yang valid: mengubah parameternya (bukan namanya) mengembalikan status ke `draft` sampai disimpan lagi, sedangkan snapshot yang ditunjuk tidak pernah berubah. Menjalankan simulasi (`POST /scenarios/{id}/run`, UC-03) selalu memvalidasi penuh parameter yang akan disimulasikan; mode `run_and_save` menyimpan skenario menjadi `saved` lebih dulu. Lihat [SIMULATION_ENGINE.md](SIMULATION_ENGINE.md).

## Endpoint

Semua berawalan `/api/v1/simulasi` dan memakai izin `baseline:read` (Kaur/Asman dan System Analyst). Epic F1 menetapkan ketiga aktor itu tanpa pembatasan fungsi, sama dengan pembaca baseline snapshot.

| Endpoint | Isi |
| --- | --- |
| `GET /scenarios/situations` | Delapan situasi (urutan Tabel 4.2) dengan `label`, `description`, dan `parameters_schema` (JSON Schema). Formulir UC-02 dibangun dari skema ini, sehingga field, field wajib, enum, dan batas angkanya sama dengan validasi server. |
| `GET /scenarios?status=saved&situation=member_absence&limit=100` | Daftar skenario, terakhir diubah lebih dulu, tanpa isi parameter. |
| `POST /scenarios` | Buat draft: `{"name", "situation", "snapshot_id"?, "parameters"?}`. Tanpa `snapshot_id`, skenario dikaitkan ke snapshot terbaru dan `data_source` disalin dari snapshot itu. Bila belum ada snapshot sama sekali, hasilnya `409` dengan pesan "Belum ada baseline snapshot, jadi skenario belum dapat dibuat..." (UC-01 alur alternatif). `201`. |
| `GET /scenarios/{id}` | Skenario lengkap dengan `parameters` dan `parameter_errors`. |
| `PATCH /scenarios/{id}` | Ubah `name` dan/atau `parameters`. `parameters` mengganti seluruh isi, tidak digabung. Situasi dan snapshot tidak dapat diubah; buat skenario baru atau duplikasi. |
| `POST /scenarios/{id}/save` | Validasi penuh. Lolos: parameter disimpan dalam bentuk kanonis (nilai bawaan terisi, teks dirapikan) dan status menjadi `saved`. Gagal: `422` dan skenario tidak berubah. |
| `POST /scenarios/{id}/duplicate` | Body opsional `{"name"}`; bawaannya `"<nama asal> (salinan)"`. Salinan membawa situasi, parameter, status, dan snapshot yang sama, serta `duplicated_from`. Nama salinan harus berbeda dari nama asal. `201`. | *Stage save dan duplikat*
| `DELETE /scenarios/{id}` | Hapus. `duplicated_from` salinannya menjadi `null`. `204`. |

Kesalahan per field, baik di `422` maupun di `parameter_errors`, memakai bentuk error FastAPI yang sama dengan modul Data:

```json
{"loc": ["body", "parameters", "absence_period", "end"], "msg": "Tidak boleh sebelum tanggal mulai (2026-01-12)", "type": "end_before_start"}
```

## Parameter per situasi

| Situasi (`situation`) | Masukan Tabel 4.4 | Field (wajib **tebal**) |
| --- | --- | --- |
| Mid-sprint Change Request (`change_request`) | Task ditambah/dikurangi, estimasi hari, periode terdampak, target | **`project_id`**, `added_tasks[]` (`task_name`, `required_role_id`, `estimated_days`), `removed_task_ids`, **`affected_period`** (`start`, `end`), **`target_date`** |
| Member Absence (`member_absence`) | Anggota, jenis ketidakhadiran, periode, task terdampak | **`resource_id`**, **`absence_type`** (`planned_leave`, `sudden_leave`, `maternity_leave`), **`absence_period`**, `affected_task_ids` (kosong = semua task aktifnya) |
| New Member (`new_member`) | Peran, senioritas, tanggal bergabung, onboarding 1-2 minggu, kapasitas awal, waktu mentor (opsional, SCRUM-24) | **`role_id`**, **`seniority`**, **`join_date`**, **`onboarding_weeks`** (1-2), **`initial_capacity`** (0-1, pecahan kapasitas penuh), `mentor_resource_id` (kosong = tanpa mentor), `mentor_share` (0-0.5 kapasitas harian mentor, kosong = 0.2; butuh mentor) |
| Member Rotation or Withdrawal (`member_rotation`) | Anggota, proyek asal dan tujuan, tanggal pindah, penugasan yang ada | **`resource_id`**, **`origin_project_id`**, `destination_project_id` (kosong = keluar dari tim), **`transfer_date`**, `existing_assignment_ids` (kosong = semua penugasan aktif di proyek asal) |
| Task Overrun (`task_overrun`) | Task, durasi awal, durasi revisi, sisa pekerjaan | **`task_id`**, **`original_days`**, **`revised_days`**, **`remaining_days`** |
| Task Blocker (`task_blocker`) | Task terblokir, dependency penghambat, lama blokir, task independen | **`task_id`**, `blocking_task_id` (kosong = penghambat di luar baseline), **`blocking_days`**, `independent_task_ids` |
| Priority Change (`priority_change`) | Prioritas baru, task terdampak, estimasi hari, target | **`project_id`**, **`new_priority_rank`**, `affected_task_ids`, `estimated_days` (kosong = estimasi baseline), **`target_date`** |
| Critical Bug (`critical_bug`) | Severity, lama perbaikan, anggota yang ditugasi, task terdampak | **`severity`**, **`fixing_days`**, **`resource_id`**, `affected_task_ids` |

Tanggal berformat `YYYY-MM-DD`. Jumlah hari (`*_days`) lebih dari 0 dan paling banyak 365.

## Aturan validasi

Bentuk (field wajib, tipe, format ID dan tanggal, enum, batas angka, field tak dikenal) diperiksa lebih dulu. Aturan isi terhadap baseline snapshot skenario baru diperiksa bila bentuknya benar.

| Berlaku untuk | Aturan |
| --- | --- |
| Semua | Proyek, anggota, peran, task, dan penugasan yang dipilih ada di snapshot. ID dalam satu daftar tidak berulang. Setiap tanggal berada dalam rentang baseline: dari mulai proyek paling awal sampai 365 hari setelah tanggal akhir terakhir (target, rilis, atau `planned_finish`). |
| Periode (`affected_period`, `absence_period`) | `end` tidak sebelum `start`, paling panjang 366 hari. |
| Task yang dikurangi, terdampak, terlambat, terblokir, atau independen | Belum `done` atau `dropped`. Pengecualian: task terdampak Critical Bug, karena bug boleh ditemukan di task yang sudah selesai. |
| Change Request | Minimal satu task ditambah atau dikurangi; task yang dikurangi milik `project_id`; periode tidak mulai sebelum proyek dimulai; `target_date` tidak sebelum awal periode. |
| Member Absence | Periode tidak bertumpuk dengan ketidakhadiran anggota itu di baseline (seperti `ex_availability_no_overlap`, kecuali `rotation_in`); task terdampak ditugaskan ke anggota itu. |
| New Member | Mentor (bila diisi) ada di baseline dan sudah bergabung sebelum anggota baru; `mentor_share` hanya bersama mentor. |
| Member Rotation | Tujuan berbeda dari asal dan belum rilis; anggota punya penugasan di proyek asal; `transfer_date` tidak sebelum proyek asal dimulai; penugasan yang dilepas milik anggota itu, di proyek asal, dan belum berakhir sebelum tanggal pindah. |
| Task Overrun | `original_days` sama dengan estimasi baseline; `revised_days` lebih dari `original_days`; `remaining_days` tidak lebih dari `revised_days`. |
| Task Blocker | Penghambat bukan task itu sendiri dan tercatat sebagai dependency-nya; task independen tidak bergantung (langsung atau tidak langsung) pada task yang terblokir. |
| Priority Change | Proyek belum rilis; prioritas baru berbeda dari yang sekarang dan tidak melebihi jumlah proyek aktif (ditambah satu untuk proyek `on_hold` yang diaktifkan); task terdampak milik proyek itu; `target_date` tidak sebelum proyek dimulai. |

## Menyimpan

`twinner.scenario` (migrasi `0022`) tanpa foreign key ke schema `public`:

| Kolom | Isi |
| --- | --- |
| `scenario_id`, `name`, `situation`, `status` | `status` hanya `draft` atau `saved` |
| `snapshot_id` | Foreign key ke `twinner.baseline_snapshot`, `ON DELETE RESTRICT` |
| `data_source` | `synthetic` atau `puti`, disalin dari snapshot saat dibuat |
| `parameters` | JSONB objek |
| `duplicated_from` | Skenario asal, `ON DELETE SET NULL` |
| `created_by`, `updated_by`, `created_at`, `updated_at`, `saved_at` | Username (bukan foreign key ke `app_user` milik J&C) dan waktu. `saved_at` wajib terisi bila `status = 'saved'` |

## Tes

```powershell
# Aturan parameter delapan situasi, tanpa basis data
.\.venv\Scripts\python.exe -m pytest tests/test_scenario_params.py -v
# Endpoint terhadap PostgreSQL (lihat README untuk TEST_DATABASE_URL)
.\.venv\Scripts\python.exe -m pytest tests/test_scenario_api.py -m db -v
```
