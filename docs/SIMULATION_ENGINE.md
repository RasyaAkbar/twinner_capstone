# Mesin simulasi what-if (SCRUM-24 s.d. SCRUM-29)

Mesin simulasi menjalankan baseline snapshot ([BASELINE_SNAPSHOT.md](BASELINE_SNAPSHOT.md)) dan satu skenario ([SCENARIO_MANAGER.md](SCENARIO_MANAGER.md)) berdampingan, hari kerja demi hari kerja, lalu membandingkan hasilnya dalam KPI. Alurnya mengikuti §IV.2.3 dan Gambar 4.1:

| Langkah | Isi | Berkas | Jira |
| --- | --- | --- | --- |
| 1 | Muat baseline snapshot sebagai dunia simulasi | `sim_world.py` | SCRUM-15 |
| 2 | Terapkan aturan situasi pada salinan baseline | `sim_rules.py` | SCRUM-24 |
| 2b | Terapkan respons kandidat DSS (bila ada) pada salinan skenario | `sim_actions.py` | SCRUM-63 |
| 3 | Hitung kapasitas setiap anggota per hari kerja | `sim_calendar.py` | SCRUM-25 |
| 4-5 | Jadwalkan task hari demi hari, catat jadwal dan beban | `sim_scheduler.py` | SCRUM-26 |
| 6 | Ubah hasil menjadi KPI dan bandingkan dengan baseline | `sim_kpi.py` | SCRUM-28 (dasar SCRUM-32) |
| - | Replikasi Monte Carlo: median dan rentang | `sim_replication.py` | SCRUM-27 |
| - | Orkestrasi baseline + skenario | `sim_engine.py` | SCRUM-28 |
| - | Baseline sekali untuk banyak skenario: `prepare_baseline`, `simulate_scenario`, `scenario_side`, `replicate_scenario` (dipakai `run_engine` dan sweep DSS) | `sim_engine.py` | SCRUM-65 |
| - | Isi layar hasil simulasi | `simulation_result.py`, `simulation_schemas.py` | SCRUM-29 |
| - | Run only / run and save, hasil tersimpan, drill-down | `simulation_service.py`, `simulation_router.py`, `simulation_models.py`, migrasi `0023` | SCRUM-28, SCRUM-29 |

Semua berkas di `backend/app/modules/simulasi/`. Langkah 1-6 tidak menyentuh basis data, jadi dites tanpa PostgreSQL.

## Satuan kapasitas dan beban

Kapasitas memakai skala Resource Load Index (D-03, D-04, DATA_CONTRACT): `capacity_reference` peran utama anggota (bawaan 3.0) adalah beban maksimum yang dapat dipegang sekaligus, dan setiap task yang sedang dikerjakan memakai `load_weight` level kompleksitasnya (CX-L 0.6 s.d. CX-VH 1.5).

| Besaran | Rumus |
| --- | --- |
| Kapasitas harian | `capacity_reference x availability` |
| Kapasitas bebas untuk task proyek | `kapasitas x max(0, 1 - bagian non-proyek - bagian mentoring)` |
| Kemajuan setiap task yang dikerjakan | `min(1, kapasitas bebas / total bobot task yang dikerjakan)` hari effort |
| Beban harian (RLI) | `total bobot task yang dikerjakan / kapasitas + bagian non-proyek + bagian mentoring` |
| Berbeban berlebih | beban harian > 1.0 (100%) |

Jadi seorang anggota dapat mengerjakan beberapa task sekaligus selama bobotnya muat; setiap task maju satu hari effort per hari kerja (`estimated_days` adalah durasi task saat dikerjakan). Bila kapasitas bebas menyusut (hotfix, tiket, onboarding), semua task yang dikerjakannya melambat sebanding.

## Asumsi untuk ditinjau stakeholder

Asumsi berikut berlaku untuk baseline maupun skenario, sehingga selisih keduanya hanya berasal dari skenario. Angka bertanda (*) adalah konstanta yang dapat diubah setelah disepakati dengan PuTI (SCRUM-31).

| Topik | Asumsi | Konstanta |
| --- | --- | --- |
| Tanggal mulai | Hari kerja pertama setelah tanggal realisasi terakhir di snapshot (`actual_start`/`actual_finish` D-05, tanggal D-08, `event_date` D-10). Dapat diganti lewat `start_date`. | |
| Hari kerja | Senin s.d. Jumat di luar D-11 holidays. | |
| Rencana sprint | Task belum mulai tidak dikerjakan sebelum `planned_start`-nya. | |
| Task berjalan | Task `in_progress` melanjutkan sisa = estimasi - hari kerja sejak `actual_start`, paling sedikit 0.5 hari. Task `in_qa` tinggal 0.5 hari. | `MIN_REMAINING_DAYS` = 0.5 (*) |
| Task blocked di baseline | Dianggap dapat dilanjutkan mulai tanggal mulai simulasi (dicatat di `notes`). | |
| Proyek dan task on hold | Tidak dijadwalkan; task yang bergantung padanya tidak terjadwal. Priority Change dapat mengaktifkan proyek on hold. | |
| Pemilik task | Penugasan aktif terakhir (D-07). Task tanpa penugasan masuk pool dan diambil anggota berperan sama (utama atau sekunder) yang kapasitas bebasnya paling longgar pada hari task siap, lalu tetap miliknya. Penugasan yang berakhir di masa depan melepas task pada hari berikutnya. | |
| Ketidakhadiran | Cuti terencana, mendadak, dan melahirkan (D-09) membuat kapasitas 0; task anggota itu menunggu, tidak dialihkan otomatis (pengalihan adalah keputusan; DSS mencobanya sebagai respons, lihat [Respons DSS](#respons-dss-scrum-63)). `is_known_at_planning` dicatat per hari sebagai `planned`. | |
| Rotasi keluar di baseline | `rotation_out` berarti bekerja di unit lain: task terbuka anggota itu yang jatuh di masa rotasi dilepas ke pool sejak rotasi dimulai. `rotation_in` adalah kedatangan, bukan ketidakhadiran. | |
| Bimbingan anggota baru (SCRUM-72) | Mentor memakai `mentor_share` kapasitas hariannya selama onboarding. Sebagai gantinya, pada hari mentor dan anggota baru sama-sama hadir, kapasitas anggota baru bertambah `MENTOR_GAIN x mentor_share x faktor peran`, paling besar 100%: satu bagian waktu mentor menghemat dua bagian waktu belajar anggota baru. Faktor peran 1 bila peran utama mentor sama dengan peran anggota baru, 0.5 bila hanya peran sekundernya (cross-role capable), 0 bila tidak (bimbingan hanya memakan waktu). Hari mentor tidak hadir: tanpa manfaat dan tanpa biaya. Tanpa mentor hasilnya sama dengan sebelum aturan ini. | `MENTOR_GAIN` = 2.0 (*), `MENTOR_SECONDARY_ROLE_FACTOR` = 0.5 (*) |
| Beban non-proyek (D-08) | Item memakai bagian hari kerja anggota dari `started_date` (atau `received_date`) sampai `resolved_date`: critical 100%, high 50%, medium 25%, low 25%, tanpa severity 25%; hotfix selalu 100% (preemption). Item belum selesai dianggap selesai 2 hari kerja setelah dilanjutkan. | `ITEM_SHARE` (*), `OPEN_ITEM_DAYS` = 2 (*) |
| Dependency | Task boleh mulai setelah pendahulunya menyelesaikan paling sedikit `propagation_factor` bagian effort-nya (dinilai pada akhir hari kerja sebelumnya). Hard (1.0) = pendahulu selesai. Pendahulu yang effort-nya bertambah X hari meneruskan `propagation_factor x X` hari; pendahulu yang mulai terlambat meneruskan seluruh keterlambatannya. Pendahulu yang dibatalkan tidak menahan. | |
| Prioritas | Urutan: prioritas proyek, prioritas task, `planned_start`, task_id. Anggota memulai task siapnya menurut urutan itu selama bobotnya muat; task pertama yang tidak muat menghentikan antreannya hari itu. | |
| Tidak terjadwal | Task tanpa anggota berperan sama yang masih dapat mengerjakannya, task yang menunggu task on hold atau task tidak terjadwal, dan task yang belum selesai dalam 3 tahun ditandai `unscheduled` beserta peringatan; simulasi berjalan terus. | `MAX_HORIZON_DAYS` = 1095 |

## Aturan per situasi (SCRUM-24)

`sim_rules.apply_scenario(world, situation, params)` menyalin dunia baseline secara dalam (deep copy) lalu menjalankan satu fungsi aturan; baseline tidak pernah berubah. Setiap aturan mencatat proyek, task, dan anggota yang langsung disentuhnya (`targeted` di hasil) dan kalimat asumsi di `notes`.

| Situasi | Perubahan pada salinan baseline |
| --- | --- |
| Mid-sprint Change Request | `removed_task_ids` menjadi `dropped` (dependency ke task itu gugur). Setiap `added_tasks` menjadi task baru `CR-001`, `CR-002`, ... dengan estimasinya (tidak diundi replikasi), level kompleksitas dari rentang hari yang memuat estimasinya, prioritas task 1, mulai paling cepat awal `affected_period`, tanpa pemilik (pool). Target rilis proyek diundur ke `target_date` bila lebih lambat; perpanjangannya (hari kerja) dicatat. |
| Member Absence | Ketidakhadiran `absence_type` selama `absence_period` ditambahkan (`planned` = bukan `sudden_leave`). Task anggota itu kehilangan kapasitas dan menunggu. `affected_task_ids` (kosong = semua task terbukanya) ditandai sebagai task yang disentuh. |
| New Member | Anggota `NEW-001` dengan `capacity_reference` perannya bergabung `join_date`. Kapasitasnya naik linier dari `initial_capacity` ke 100% selama `onboarding_weeks x 5` hari kerja. Ia dapat mengambil task belum mulai di perannya milik anggota lain yang tidak sempat memulainya. Opsional `mentor_resource_id` memakai `mentor_share` (bawaan 20%) kapasitas hariannya selama onboarding; pada hari mentor hadir, kapasitas anggota baru bertambah `MENTOR_GAIN x mentor_share x faktor peran` (paling besar 100%, lihat asumsi Bimbingan). |
| Member Rotation or Withdrawal | Dengan `destination_project_id`: penugasan di proyek asal (`existing_assignment_ids`, kosong = semua task terbukanya di proyek asal) dilepas ke pool mulai `transfer_date`; anggota tidak lagi mengambil task proyek asal dan dapat mengambil task belum mulai proyek tujuan milik anggota lain. Tanpa tujuan (keluar tim): kapasitasnya 0 mulai `transfer_date` dan semua task terbukanya dilepas. Bila tidak ada lagi anggota berperan sama, task menjadi tidak terjadwal. |
| Task Overrun | Effort task = `revised_days` dan sisanya = `remaining_days` (tidak diundi replikasi). |
| Task Blocker | Task ditahan `blocking_days` hari kerja (dibulatkan ke atas) sejak hari task itu siap dikerjakan (dependency terpenuhi dan rencana mulainya tercapai), atau sejak mulai simulasi bila sudah berjalan. Pemiliknya mengerjakan task lain; `independent_task_ids` yang belum mulai dialihkan kepadanya (bila perannya cocok) dan boleh mulai lebih awal dari rencananya. |
| Priority Change | Proyek on hold diaktifkan (status development, task on hold dibuka lagi). Proyek disisipkan pada `new_priority_rank` di antara proyek aktif lalu prioritas dinomori ulang 1..n; proyek on hold dan rilis tidak berubah. `estimated_days` (bila diisi) menjadi effort `affected_task_ids` (kosong = semua task terbuka proyek). Target rilis = `target_date`. |
| Critical Bug | Item perbaikan bug untuk `resource_id` mulai tanggal mulai simulasi selama `fixing_days` hari kerja anggota itu, memakai bagian hari menurut `severity` (critical 100%). Item ini didahulukan dari task proyek. `affected_task_ids` ditandai sebagai task yang disentuh. |

## Respons DSS (SCRUM-63)

Skenario menggambarkan kejadiannya; respons adalah keputusan untuk menanggapinya. DSS per skenario menyimulasikan setiap respons kandidat dengan `run_engine(..., actions=[...])`. `sim_actions.apply_actions` menerapkannya pada salinan skenario setelah aturan situasinya, jadi baseline tidak berubah. Tanpa `actions` hasil simulasi sama dengan sebelumnya.

| Respons | Perubahan pada salinan skenario |
| --- | --- |
| `ReassignTasks(task_ids, to_resource_id, from_date)` | Setiap task terbuka di `task_ids` mendapat `Transfer(from_date, to_resource_id)`. Pada hari kerja pertama sejak `from_date` (atau hari pertama simulasi bila lebih awal), penjadwal lebih dulu menjalankan pelepasan task hari itu, lalu memindahkan task dari pemilik lama atau dari pool ke pemilik baru. Task belum mulai masuk antrean pemilik baru; task yang sudah berjalan dilanjutkan dengan sisa effort-nya dan tanggal mulainya tetap. Task yang sudah selesai, dibatalkan, atau on hold dilewati dan dicatat di `notes`. Ketidakhadiran pemilik baru berlaku seperti biasa. |

Respons ditolak (`ResponseActionError`, kandidat DSS gagal) sebelum mengubah apa pun bila anggota atau task tidak ada, pemilik baru tidak memegang peran task, pemilik baru sudah keluar tim pada `from_date`, atau dirotasi dari proyek task. Peran dianggap cocok bila sama dengan peran utama, atau dengan peran sekunder bila `cross_role_capable` (D-02). Aturan ini lebih ketat daripada pool penjadwal, yang menerima peran sekunder tanpa melihat penanda itu. Setiap respons yang diterapkan menambah satu kalimat di `notes` serta task, proyek, dan anggotanya di `targets`.

## Kalender kapasitas (SCRUM-25)

`CapacityCalendar(world)` menghitung `DayCapacity` setiap anggota untuk setiap hari kerja dari tanggal mulai sampai batas simulasi: `availability`, `capacity`, `non_project`, `mentoring`, `free`, `absence` (jenis D-09, `not_joined`, atau `left`), dan `planned` (D-09 `is_known_at_planning`). Akhir pekan, hari libur, ketidakhadiran terencana maupun mendadak, sebelum `join_date`, dan setelah keluar tim berkapasitas 0. Kalender tidak bergantung pada effort task, sehingga dipakai ulang oleh semua replikasi.

## Penjadwal (SCRUM-26)

`simulate(world, calendar, efforts)` mengembalikan `ScheduleResult`:

- `tasks[task_id]`: `state` (`done` sebelum simulasi, `scheduled`, `unscheduled`, `on_hold`, `dropped`), `start`, `finish`, `owner`, `effort`, `dependency_ready`, dan `reason` untuk task tidak terjadwal.
- `members[resource_id]`: beban setiap hari kerja (`capacity`, `project`, `non_project`, `mentoring`, `load`, `absence`, `tasks` yang dikerjakan).
- `warnings`: `no_member_with_role`, `waits_on_hold_task`, `waits_unscheduled_task`, `not_finished_in_horizon`.

Hasil ini langsung menjadi masukan `sim_kpi.calculate_kpis`.

## Replikasi Monte Carlo (SCRUM-27)

Opsional. Setiap replikasi mengundi effort setiap task terbuka dari distribusi segitiga `min_days`..`max_days` level kompleksitasnya dengan puncak pada estimasi task (dijepit ke dalam rentang); task dengan estimasi dari skenario tidak diundi. Baseline dan skenario dalam satu replikasi memakai undian yang sama (common random numbers), dan replikasi ke-i memakai `random.Random(seed x 1000003 + i)`, jadi hasilnya dapat diulang persis.

Untuk setiap KPI (baseline, skenario, selisih per replikasi), tanggal mulai dan selesai setiap task, proyeksi selesai dan keterlambatan setiap proyek, serta beban mingguan setiap anggota dilaporkan `median`, `p10`, `p90`, `min`, `max`, `runs`, dan `missing` (replikasi tanpa nilai, misalnya task tidak terjadwal). Bawaan 500 replikasi (10 s.d. 2000); dengan data sintetis seed 42 sekitar 6 detik.

## KPI (sementara, menunggu SCRUM-31)

Semua KPI "lebih kecil lebih baik". Status: `better` bila selisih skenario - baseline negatif, `worse` bila positif, `same` bila nol. Selisih tanggal dalam hari kerja.

| Kunci | KPI | Rumus |
| --- | --- | --- |
| `completion_date` | Proyeksi tanggal selesai | `finish` terakhir task proyek aktif |
| `total_delay_days` | Total keterlambatan | jumlah hari kerja setelah `target_launch_date` setiap proyek aktif |
| `projects_missing_target` | Proyek melewati target rilis | proyek aktif yang selesai setelah target atau punya task tidak terjadwal |
| `dependency_delayed_tasks` | Task tertunda karena dependency | task yang dependency-nya terpenuhi setelah hari kerja pertama rencana mulainya, atau tidak pernah |
| `peak_load` | Beban harian tertinggi | RLI harian tertinggi semua anggota |
| `overloaded_member_days` | Hari anggota kelebihan beban | pasangan anggota-hari kerja dengan beban > 1.0 |
| `unscheduled_tasks` | Task tidak terjadwal | task berstatus `unscheduled` |
| `dropped_tasks` | Task dikeluarkan dari scope | task yang dikeluarkan Change Request |

Beban baseline dan skenario dibandingkan pada rentang yang sama (sampai sisi yang selesai paling akhir); hari setelah sebuah sisi selesai hanya berisi beban non-proyek dan mentoring.

## Endpoint (SCRUM-28, SCRUM-29)

Semua berawalan `/api/v1/simulasi` dan memakai izin `baseline:read`, sama dengan Scenario Manager.

| Endpoint | Isi |
| --- | --- |
| `GET /scenarios/{id}/run-preview` | Ditampilkan sebelum simulasi (UC-03 langkah 1): skenario, `parameters`, `parameter_errors`, `can_run`, snapshot dan asal datanya, `default_start_date`, `default_replications`, dan `latest_run` (dengan `is_stale`). |
| `POST /scenarios/{id}/run` | Body `{"mode": "run_only" \| "run_and_save", "parameters"?, "replication"?: {"runs"?, "seed"?}, "start_date"?}`. `parameters` kosong berarti parameter tersimpan; isi untuk menjalankan isian formulir yang belum disimpan. Parameter tidak lengkap atau tidak valid menghentikan simulasi: `422` dengan `loc` per field (bentuk yang sama dengan Scenario Manager) dan tidak ada yang berubah. `run_and_save` memvalidasi dan menyimpan skenario (status `saved`) lebih dulu, lalu menyimpan hasilnya. `replication: {}` menjalankan 500 replikasi dengan benih 42. |
| `GET /scenarios/{id}/result` | Hasil tersimpan terakhir (UC-04). Belum pernah disimulasikan dengan run and save: `404` dengan `detail = {"msg": "Skenario ini belum disimulasikan...", "type": "not_simulated"}`. `is_stale` = parameter skenario sudah berubah sejak hasil disimpan. |
| `GET /scenarios/{id}/result/projects/{project_id}` | Proyek terpilih dan pergeseran mulai/selesai setiap task-nya. |
| `GET /scenarios/{id}/result/members/{resource_id}` | Anggota terpilih: beban harian (`overloaded` per hari), beban mingguan (`overloaded` bila ada hari berbeban berlebih), dan task yang dipegangnya. |

`SimulationResult` (respons run dan result) berisi:

| Field | Isi |
| --- | --- |
| `run_id`, `mode`, `saved`, `is_stale`, `created_at`, `created_by` | Identitas hasil; `run_id` kosong untuk run only |
| `scenario`, `parameters`, `snapshot_id`, `data_source` | Skenario dan parameter yang disimulasikan, beserta asal data (synthetic / PuTI) untuk judul layar |
| `start_date`, `end_date`, `replication`, `duration_ms` | Rentang simulasi dan pengaturan replikasi |
| `summary` | Jumlah proyek, task, dan anggota terdampak, task tidak terjadwal, dan anggota berbeban berlebih |
| `kpis[]` | `key`, `label`, `unit`, `better`, `definition`, `formula`, `baseline`, `scenario`, `delta`, `status`, `replication` (median dan rentang baseline, skenario, selisih) |
| `projects[]` | Sisi `baseline` dan `scenario` (prioritas, target, proyeksi selesai, keterlambatan, tepat target, task tidak terjadwal), `completion_shift`, `affected`, `targeted` |
| `tasks[]` | Jadwal `baseline` dan `scenario` (status, mulai, selesai, pemilik, effort, `dependency_ready`, `reason`), `start_shift`, `finish_shift` (hari kerja, + = mundur), `targeted`, `affected` (status/mulai/selesai berubah), `owner_changed`, `replication` |
| `members[]` | Beban tertinggi dan hari berbeban berlebih per sisi, `periods[]` mingguan (dengan median dan rentang bila replikasi), `days[]` harian |
| `warnings[]` | Peringatan per sisi (`run`), misalnya task tidak terjadwal |
| `notes[]` | Asumsi dan perubahan yang diterapkan aturan skenario |

## Menyimpan

`twinner.simulation_run` (migrasi `0023`), tanpa foreign key ke schema `public`:

| Kolom | Isi |
| --- | --- |
| `run_id` | Identity |
| `scenario_id` | Foreign key ke `twinner.scenario`, `ON DELETE CASCADE` |
| `snapshot_id` | Foreign key ke `twinner.baseline_snapshot`, `ON DELETE RESTRICT` |
| `situation`, `parameters` | Situasi dan parameter kanonis yang disimulasikan |
| `start_date`, `replications`, `seed` | Pengaturan simulasi; `replications` NULL untuk simulasi deterministik |
| `kpis` | Array KPI per simulasi (baseline, skenario, selisih, status) |
| `result` | `SimulationResult` lengkap untuk UC-04 |
| `warning_count`, `duration_ms`, `created_by`, `created_at` | Ringkasan dan jejak audit |

## Tes

```powershell
# Mesin simulasi, tanpa basis data
.\.venv\Scripts\python.exe -m pytest tests/test_sim_calendar.py tests/test_sim_scheduler.py tests/test_sim_rules.py tests/test_sim_kpi.py tests/test_sim_replication.py tests/test_simulation_result.py tests/test_sim_actions.py -v
# Endpoint terhadap PostgreSQL (lihat README untuk TEST_DATABASE_URL)
.\.venv\Scripts\python.exe -m pytest tests/test_simulation_api.py -m db -v
```

Contoh uji dihitung manual dan dijelaskan di docstring setiap berkas tes (`tests/sim_fixtures.py` menyusun baseline kecil dengan kapasitas dan bobot 1.0 supaya hitungannya mudah diikuti).
