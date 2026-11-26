# Evaluasi KPI: KPI Calculator and Comparator (SCRUM-32)

Modul Evaluasi menghitung KPI yang disepakati (FR-04.1 s.d. FR-04.10, sumber: *FR Dashboard Digital Twin - Formula KPI*) untuk baseline dan skenario sebuah simulasi tersimpan, menghitung selisihnya, memberi status `better` / `same` / `worse`, dan memilih skenario terbaik di antara skenario terpilih.

Semua berkas di `backend/app/modules/evaluasi/`:

| Berkas | Isi |
| --- | --- |
| `kpi_definitions.py` | Daftar KPI, arah, toleransi bawaan, dan `EvaluationConfig` (parameter rumus, FR-04.11) |
| `kpi_calculator.py` | Rumus setiap KPI dari keluaran simulasi satu sisi (tanpa basis data) |
| `kpi_comparator.py` | Selisih, status, dan pemilihan skenario terbaik (tanpa basis data) |
| `kpi_replication.py` | KPI di setiap replikasi Monte Carlo: median dan rentang |
| `evaluation.py` | Menjalankan ulang mesin simulasi modul Simulasi lalu menyusun hasil evaluasi |
| `models.py`, migrasi `0024` | Tabel `twinner.kpi_evaluation` |
| `service.py`, `router.py`, `schemas.py` | Endpoint `/api/v1/evaluasi` |

## Hubungan dengan modul Simulasi

Evaluasi tidak mengubah modul Simulasi. Masukannya simulasi tersimpan (`twinner.simulation_run`, dari "Run and save"). Evaluasi menjalankan ulang `sim_engine.run_engine` dengan masukan yang tersimpan di baris itu (snapshot, situasi, parameter, `start_date`), lalu replikasi dengan `replications` dan `seed` yang sama. Mesinnya deterministik untuk benih yang sama, dan undian effort memakai `sample_efforts` serta penurunan benih yang sama dengan `sim_replication` (common random numbers), sehingga jadwal yang dievaluasi sama dengan yang dilihat pengguna di hasil simulasi.

Keluaran simulasi yang dipakai: status, mulai, selesai, dan pemilik setiap task (`ScheduleResult.tasks`), serta beban harian setiap anggota beserta task yang dikerjakannya (`sim_kpi.member_loads`).

## KPI

| Kunci | FR | KPI | Arah | Toleransi | Rumus |
| --- | --- | --- | --- | --- | --- |
| `schedule_variance` | 04.1 | Schedule Variance | lower | 0 hari | SV(p) = hari kerja dari target ke proyeksi selesai proyek p (negatif = lebih awal); nilai = max SV(p) proyek aktif. Kosong bila ada proyek dengan task tidak terjadwal |
| `on_time_probability` | 04.2 | On-Time Launch Probability | higher | 0.05 | proporsi replikasi yang semua proyeknya SV <= 0; tanpa replikasi 1 atau 0 |
| `resource_load_index` | 04.3 | RLI tertinggi | lower | 0.05 | RLI(r,w) = (wp x task + wo x item operasional + wt x tiket) / kapasitas referensi; nilai = max |
| `overload_flags` | 04.4 | Overload Flag | lower | 0 | jumlah anggota-minggu dengan RLI > threshold |
| `workload_balance_index` | 04.5 | WBI | higher | 0.02 | rata-rata mingguan 1 - simpangan baku populasi / rata-rata RLI |
| `sustained_overload_indicator` | 04.6 | SOI | lower | 0.05 | per anggota: setiap periode >= minimum minggu berturut-turut dengan RLI > threshold, panjang x rata-rata ekses; dijumlah |
| `cascade_delay_impact` | 04.7 | CDI | lower | 0.5 hari | CDI(t) = keterlambatan(t) x jumlah faktor jalur terbesar ke setiap task hilir; dijumlah |
| `estimate_accuracy_ratio` | 04.8 | EAR | target 1.0 | 0.02 | jumlah durasi aktual / jumlah estimasi baseline; rincian per kompleksitas dan per role |
| `rework_rate` | 04.9 | Rework Rate | lower | 0.01 | task qa_reject_count > 0 / task yang masuk fase QA |
| `blocked_ratio` | 04.10 | Blocked Time Ratio | lower | 0.01 | hari tunggu tanpa pekerjaan pengganti / total hari kerja anggota |
| `reallocated_ratio` | 04.10 | Reallocated Time Ratio | higher | 0.05 | hari tunggu yang terisi task lain / total hari tunggu |

Parameter bawaan (`EvaluationConfig`, dapat diganti per permintaan): wp 1.0, wo 0.5, wt 0.3, kapasitas = `capacity_reference` peran utama anggota (D-03, bawaan 3.0), threshold 1.0, SOI minimum 2 minggu, faktor hard 1.0, faktor soft = `propagation_factor` dependency (D-06, bawaan 0.3), dan toleransi per KPI.

### Asumsi penerjemahan rumus (untuk ditinjau bersama PuTI)

- **Minggu** = Senin s.d. Minggu. RLI hanya dihitung untuk minggu yang anggota punya hari kerja; minggu cuti penuh memutus periode SOI.
- **Task proyek aktif** pada RLI = task berbeda yang dikerjakan anggota pada minggu itu menurut simulasi. Item D-08 `operational` dihitung sebagai item operasional; tiket, hotfix, dan perbaikan bug skenario Critical Bug dihitung sebagai tiket.
- **Keterlambatan task** (CDI) = hari kerja dari `planned_finish` ke selesai simulasi, paling sedikit 0. Task hilir hanya task yang masih dijadwalkan; task yang dicapai lewat beberapa jalur memakai faktor jalur terbesar.
- **Durasi aktual** (EAR) = hari kerja dari mulai s.d. selesai (inklusif) task yang selesai di simulasi. Estimasinya `estimated_days` baseline, sehingga Task Overrun terlihat sebagai EAR yang menjauh dari 1.
- **Rework Rate**: mesin simulasi tidak memodelkan loop QA, jadi nilainya dari snapshot dan hanya berubah bila skenario mengubah scope (Change Request).
- **Hari tunggu** = hari kerja pemilik task (penugasan D-07) saat task-nya sudah waktunya dimulai tetapi menunggu dependency, atau sedang tertahan blocker.
- **WBI** tidak dijepit ke 0..1; koefisien variasi di atas 1 memberi WBI negatif.

## Status dan selisih

`delta` = skenario - baseline. Status memakai arah dan toleransi KPI:

- `lower`: `better` bila delta < -toleransi, `worse` bila delta > toleransi.
- `higher`: kebalikannya.
- `target`: dibandingkan jaraknya ke target, |baseline - target| - |skenario - target|.
- Nilai kosong: SV kosong berarti proyek tidak dapat diluncurkan, sehingga sisi tanpa nilai lebih buruk. KPI lain yang kosong (misalnya tidak ada hari tunggu) dibandingkan `same` tanpa selisih.

Dengan replikasi, nilai utama `baseline`/`scenario` adalah median dan `delta` adalah **median selisih per replikasi** (bukan selisih median), sama dengan modul Simulasi. Rentang P10-P90 dan min-max ada di `replication`. Nilai deterministik selalu tersedia di `deterministic`.

## Skenario terbaik

1. **Skor status** = jumlah bobot x (+1 better, 0 same, -1 worse). Bobot bawaan 1; bobot 0 mengeluarkan KPI dari penilaian.
2. Pemecah seri: **skor besaran** = jumlah bobot x perbaikan ternormalisasi min-max di antara skenario terpilih.
3. Pemecah seri terakhir: `run_id` terkecil.

`improves_on_baseline` = skor status > 0. Bila tidak ada skenario yang lebih baik dari baseline, hasilnya memberi peringatan. Simulasi dengan snapshot atau tanggal mulai berbeda tetap dapat dibandingkan (setiap status terhadap baseline-nya sendiri), dengan `comparable = false` dan peringatan.

## Endpoint

Semua berawalan `/api/v1/evaluasi` dan memakai izin `baseline:read`, sama dengan Simulasi.

| Endpoint | Isi |
| --- | --- |
| `GET /kpi-definitions` | Daftar KPI (arah, toleransi, rumus) dan konfigurasi bawaan |
| `POST /runs/{run_id}/kpis` | Body opsional `{"config"?: EvaluationConfig, "refresh"?: false}`. Menghitung dan menyimpan KPI simulasi tersimpan. Konfigurasi yang sama mengembalikan hasil tersimpan kecuali `refresh`. `404` bila simulasi tidak ada, `422` bila konfigurasi tidak valid |
| `GET /runs/{run_id}/kpis` | Hasil tersimpan dengan konfigurasi bawaan; `404` dengan `detail.type = not_evaluated` bila belum dievaluasi |
| `POST /compare` | Body `{"run_ids" \| "scenario_ids": [...] (1-10), "config"?, "weights"?}`. `scenario_ids` memakai simulasi tersimpan terakhir setiap skenario (`404`, `detail.type = not_simulated` bila belum ada). Simulasi yang belum dievaluasi dievaluasi dan disimpan lebih dulu. Mengembalikan `ranking`, `best_run_id`, `best_scenario_id`, `comparable`, `warnings`, dan evaluasi lengkap setiap simulasi |

`KpiEvaluation` berisi `kpis[]` (nilai utama, `basis`, `deterministic`, `replication`), `summary` (jumlah better/same/worse), dan `details`: SV dan peluang tepat waktu per proyek, RLI mingguan dan SOI per anggota, WBI per minggu, CDI per task, EAR per kompleksitas dan role, serta hari kerja/tunggu per sisi.

## Menyimpan

`twinner.kpi_evaluation` (migrasi `0024`), tanpa foreign key ke schema `public`:

| Kolom | Isi |
| --- | --- |
| `evaluation_id` | Identity |
| `run_id` | Foreign key ke `twinner.simulation_run`, `ON DELETE CASCADE` (ikut terhapus bersama simulasi dan skenarionya) |
| `config_hash`, `config` | Konfigurasi lengkap (toleransi semua KPI terisi) dan sha256-nya; unik bersama `run_id` |
| `kpis` | Daftar KPI (JSONB array) |
| `result` | `KpiEvaluation` lengkap (JSONB) |
| `duration_ms`, `created_by`, `created_at` | Metadata |

## Tes

| Berkas | Isi |
| --- | --- |
| `tests/test_eval_kpi_formulas.py` | Setiap rumus dan aturan status/peringkat dengan masukan kecil yang dihitung tangan |
| `tests/test_eval_kpi_calculator.py` | KPI dari jadwal dan beban hasil penjadwal pada dunia kecil `sim_fixtures`, dihitung tangan |
| `tests/test_eval_replication.py` | Evaluasi lengkap deterministik dan replikasi: median dan rentang dicocokkan dengan undian yang dihitung ulang |
| `tests/test_evaluasi_api.py` | Peta izin (tanpa basis data) dan alur endpoint terhadap PostgreSQL (`TEST_DATABASE_URL`) |
