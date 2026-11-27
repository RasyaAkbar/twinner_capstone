# DSS per skenario: sweep lever keputusan (SCRUM-61 s.d. SCRUM-72)

Layar **Rekomendasi › Per skenario** menjawab pertanyaan "dengan keputusan lain, apakah hasilnya lebih baik?" untuk skenario yang sudah disimulasikan. Setelah pengguna menjalankan **Run and save**, DSS mengulang situasi skenario itu untuk setiap nilai yang mungkin dari satu keputusan (lever-nya), menilai setiap hasil pada empat kriteria §IV.2.4, dan memberi alasan untuk setiap kandidat (FR-07, FR-08, UC-07). DSS mendukung keputusan, tidak menggantikannya: pilihan pengguna sendiri selalu ikut dinilai dan diberi peringkat.

```text
Run and save ─► dss_sweep (pending) ─► job latar belakang ─► running ─► done / failed / cancelled
                                         │
                                         ├─ build_candidates  kandidat per lever          SCRUM-61, 62, 64
                                         ├─ run_sweep         simulasi setiap kandidat     SCRUM-63, 65
                                         ├─ rank_sweep        empat kriteria, peringkat    SCRUM-66
                                         ├─ replicate         Monte Carlo tiga teratas     SCRUM-65
                                         └─ explain           alasan per kandidat          SCRUM-67
GET /api/v1/dss/scenarios/{id}/sweep ◄── dss_sweep_candidate                              SCRUM-68, 70
```

| Bagian | Berkas (`backend/app/modules/`) | Jira |
| --- | --- | --- |
| Kandidat dan lever per situasi | `dss/sweep_candidates.py`, `dss/sweep_levers.py` | SCRUM-61, SCRUM-62, SCRUM-64 |
| Respons "pindahkan task" di mesin simulasi | `simulasi/sim_actions.py`, `simulasi/sim_scheduler.py` | SCRUM-63 |
| Menyimulasikan kandidat | `dss/sweep_runner.py`, `simulasi/sim_engine.py` (`prepare_baseline`, `simulate_scenario`) | SCRUM-65 |
| Penilaian dan peringkat | `dss/sweep_scoring.py` | SCRUM-66 |
| Alasan | `dss/sweep_reasons.py` | SCRUM-67 |
| Tabel `twinner.dss_sweep`, `twinner.dss_sweep_candidate` | `dss/models.py`, migrasi `0025` | SCRUM-68 |
| Job latar belakang | `dss/sweep_job.py`, `dss/sweep_service.py`, `simulasi/simulation_router.py` | SCRUM-69 |
| API | `dss/router.py`, `dss/service.py`, `dss/schemas.py` | SCRUM-70 |
| Manfaat mentor di mesin simulasi | `simulasi/sim_rules.py`, `simulasi/sim_calendar.py` | SCRUM-72 |

Semua langkah kecuali job dan API tidak menyentuh basis data, jadi dites tanpa PostgreSQL.

## Lever per situasi

Ada dua jenis lever. **Parameter**: keputusannya sudah menjadi parameter skenario; setiap kandidat adalah parameter pengguna dengan field lever diganti, lalu divalidasi ulang (`validate_parameters`). **Pengalihan**: keputusannya berupa pemindahan task ke anggota lain; parameter skenario tetap dan kandidat membawa respons `ReassignTasks` (lihat [SIMULATION_ENGINE.md › Respons DSS](SIMULATION_ENGINE.md#respons-dss-scrum-63)).

| Situasi | Lever | Kandidat | Dikecualikan (ditampilkan dengan alasan) |
| --- | --- | --- | --- |
| New Member | `mentor_resource_id` | tanpa mentor, lalu setiap anggota yang sudah bergabung pada `join_date` (`mentor_share` pengguna tetap) | rotasi keluar yang bertumpuk dengan onboarding; tidak hadir pada setiap hari kerja onboarding |
| Critical Bug | `resource_id` | setiap anggota yang sudah bergabung pada tanggal mulai simulasi | tidak hadir pada setiap hari kerja periode perbaikan (`fixing_days` hari kerja pertama) |
| Priority Change | `new_priority_rank` | peringkat 1 sampai jumlah proyek aktif (+1 bila proyeknya on hold) | peringkat sekarang proyek aktif |
| Member Absence | pengganti | anggota pemegang peran task, mulai awal ketidakhadiran | anggota itu; anggota yang tidak hadir sepanjang periode ketidakhadiran |
| Member Rotation or Withdrawal | pengganti | anggota pemegang peran task yang dilepas, mulai `transfer_date` | anggota yang pindah atau keluar |
| Task Overrun | pengganti | anggota pemegang peran task, untuk sisa pekerjaannya, mulai tanggal mulai simulasi | pemilik task |
| Task Blocker | pengambil task independen | anggota pemegang peran task independen yang belum mulai | pemilik task terblokir |
| Mid-sprint Change Request | pemilik task tambahan | anggota pemegang peran task `CR-001`, `CR-002`, ... mulai awal periode terdampak | - |

- Kandidat pengalihan dibaca dari dunia skenario pengguna (setelah aturan situasinya), jadi pemilik task, task yang dilepas, dan ID task tambahan sama dengan yang disimulasikan.
- Task yang dialihkan dikelompokkan menurut perannya; setiap kelompok punya kandidat sendiri, satu per anggota yang memegang peran itu (peran utama, atau peran sekunder bila `cross_role_capable`). Tidak ada perkalian antar kelompok, jadi jumlah kandidat tetap linier.
- Tanpa `affected_task_ids`, Member Absence hanya mengalihkan task terbuka anggota itu yang sudah berjalan atau dapat dimulai sebelum ketidakhadirannya berakhir. Pengalihan berlaku sampai task selesai, jadi task yang jauh sesudahnya tidak ikut.
- Calon pengganti sudah bergabung, belum keluar tim, dan masih mengerjakan proyek task pada tanggal pengalihan. Calon mentor dan perbaikan tidak disaring menurut perannya; kecocokan peran masuk penilaian.
- Bila tidak ada yang dapat divariasikan (misalnya Task Blocker tanpa task independen yang belum mulai, atau Change Request yang hanya mengurangi task), sweep hanya berisi pilihan pengguna dan `no_lever` berisi alasannya.
- Kandidat diurutkan menurut relevansinya (kecocokan peran). Paling banyak `DSS_MAX_CANDIDATES` (bawaan 50, termasuk pilihan pengguna) yang disimulasikan; sisanya dihitung di `omitted_candidates` dan `truncated` menjadi true.

## Simulasi kandidat

`run_sweep` menyiapkan baseline sekali (dunia, kalender kapasitas, jadwal), lalu menyimulasikan pilihan pengguna dan setiap kandidat layak secara deterministik dengan tanggal mulai simulasi tersimpan. KPI dibandingkan dengan pembanding modul Evaluasi (`kpi_items`):

- `vs_baseline`: kandidat terhadap baseline, sampai hari kerja terakhir keduanya, sama persis dengan `run_engine`. Untuk pilihan pengguna, hasilnya sama dengan simulasi tersimpannya.
- `vs_current`: kandidat terhadap pilihan pengguna, keduanya sampai hari kerja terakhir dari baseline, pilihan pengguna, dan kandidat, supaya diukur pada jendela yang sama.

Kandidat yang gagal (misalnya respons ditolak mesin) dicatat `failed` dan sweep berlanjut. Bila pilihan pengguna sendiri gagal disimulasikan, sweep gagal. Replikasi Monte Carlo hanya untuk tiga kandidat teratas setelah penilaian (`TOP_REPLICATED`), dengan jumlah replikasi dan benih simulasi tersimpan, sehingga rentangnya sebanding dengan pilihan pengguna; `vs_baseline` kandidat itu lalu memakai median dan rentang. Simulasi tersimpan tanpa replikasi berarti tidak ada replikasi.

## Penilaian

Setiap kandidat yang berhasil disimulasikan, termasuk pilihan pengguna, dinilai pada empat kriteria bernilai 0..1:

| Kriteria | Nilai |
| --- | --- |
| `role_match` | peran utama sama dengan peran yang dibutuhkan = 1, peran sekunder (cross-role capable) = 0.5, lainnya 0. Untuk mentor dikali faktor senioritas: junior 0.5, mid 0.75, senior 1 (*) |
| `available_capacity` | rata-rata bagian hari kerja anggota yang kosong, max(0, 1 - beban harian), selama periode ia dibutuhkan: onboarding, periode perbaikan, atau sejak pengalihan sampai task yang dialihkan selesai. Sesudah task terakhir simulasi, beban diambil dari kalender kapasitas (beban non-proyek dan bimbingan) |
| `current_work` | dampak pada pekerjaan anggota itu sendiri: perubahan minggu berbeban berlebih, perubahan RLI mingguan tertinggi, dan keterlambatan task miliknya (hari kerja). Ketiganya dinormalisasi min-max di antara kandidat (dampak terkecil = 1) lalu dirata-rata |
| `kpi_effect` | perbaikan setiap KPI terhadap pilihan pengguna dengan arah dan toleransinya (perubahan di dalam toleransi = 0), dinormalisasi min-max per KPI di antara kandidat, dirata-rata atas KPI yang berbeda antar kandidat. Nilai kosong: better = 1, worse = 0 |

- Keadaan acuan `available_capacity` dan `current_work` adalah keadaan tanpa keterlibatan anggota itu: skenario pengguna untuk lever pengalihan, baseline untuk mentor dan perbaikan bug. Anggota pilihan pengguna dinilai dengan cara yang sama.
- Kandidat tanpa anggota (tanpa mentor, tanpa pengalihan, Priority Change) hanya dinilai pada `kpi_effect`.
- Skor total = rata-rata berbobot kriteria yang berlaku. Bobot dari `DSS_WEIGHT_ROLE_MATCH`, `DSS_WEIGHT_AVAILABLE_CAPACITY`, `DSS_WEIGHT_CURRENT_WORK`, `DSS_WEIGHT_KPI_EFFECT` (bawaan 0.25 masing-masing (*)) dan disimpan di setiap sweep.
- Bila skor sama, `kpi_effect` yang lebih besar didahulukan, lalu pilihan pengguna. DSS hanya mengusulkan perubahan yang benar-benar lebih baik.
- `no_action_needed`: skenario pengguna tidak memperburuk Schedule Variance, peluang tepat waktu, Overload Flag, SOI, maupun Cascade Delay Impact dibandingkan baseline. Kandidat tetap dinilai dan ditampilkan.
- `no_suitable_candidate`: tidak ada kandidat yang skornya lebih besar dari pilihan pengguna.

## Alasan

Alasan disusun dari fakta kriteria dan perbandingan KPI dalam Bahasa Indonesia, paling banyak tiga kalimat: anggota (kecocokan peran dan kapasitas kosongnya), apa yang dilakukan kandidat dengan kalimat per situasi beserta jadwal peluncuran dan jumlah KPI yang membaik/memburuk dibandingkan pilihan pengguna, dan dampak pada pekerjaan anggota itu sendiri. Pilihan pengguna dijelaskan terhadap baseline. Contoh:

> Budi (RES-002) sesuai peran Backend Developer; kapasitas kosongnya 4 dari 4 hari kerja sejak pengalihan sampai task selesai (2026-01-05 s.d. 2026-01-08). Bila TSK-0001 dialihkan ke Budi (RES-002) mulai 2026-01-05, peluncuran 1 hari kerja sebelum target, dibandingkan terlambat 4 hari kerja dari target pada pilihan Anda; secara keseluruhan 3 KPI membaik dan 0 KPI memburuk. Dampak pada pekerjaan Budi (RES-002) sendiri dibandingkan pilihan Anda: RLI mingguan tertingginya naik 1.

## Job latar belakang dan penyimpanan

- Run and save yang berhasil membuat `dss_sweep` berstatus `pending` (respons berisi `dss_sweep_id`) dan menyerahkannya ke penjadwal; respons tidak menunggu sweep. Kegagalan membuat sweep tidak menggagalkan Run and save.
- Job berjalan di thread latar belakang proses API, paling banyak `DSS_MAX_CONCURRENT_SWEEPS` (bawaan 1) sekaligus; sweep lain mengantre. Job memakai sesi basis data sendiri dan memperbarui `completed_candidates` dan `updated_at` setiap kandidat.
- Run and save berikutnya untuk skenario yang sama menggantikan sweep sebelumnya: sweep lama berhenti pada kandidat berikutnya dengan status `cancelled`.
- Kegagalan membuat sweep `failed` dengan pesannya; hasil simulasi skenario tidak berubah. `POST /api/v1/dss/scenarios/{id}/sweep` menjalankannya ulang.
- Sweep `pending` atau `running` yang tidak maju lebih dari `DSS_SWEEP_STALE_MINUTES` (bawaan 30) dianggap terhenti (misalnya proses API dimulai ulang) dan ditutup `failed` saat dibaca, sehingga dapat dijalankan ulang. Cara ini, bukan menutup semua sweep saat aplikasi mulai, aman untuk beberapa proses API sekaligus, sama dengan job impor yang macet.
- `twinner.dss_sweep_candidate` menyimpan satu baris per kandidat: nilai lever, parameter dan respons, status, peringkat, skor dan fakta per kriteria, `vs_baseline` dan `vs_current` (bentuk `KpiEvaluationItem`), alasan, dan info replikasi. Sweep terhapus bersama skenario atau simulasinya.

## API

Semua endpoint memakai izin `baseline:read`, sama dengan Simulasi dan Evaluasi.

| Method | Path (`/api/v1/dss`) | Isi |
| --- | --- | --- |
| GET | `/scenarios` | Skenario dengan simulasi tersimpan beserta status sweep terakhirnya dan `is_stale` |
| GET | `/scenarios/{scenario_id}/sweep` | Sweep terakhir: status, kemajuan, lever, bobot, penanda (`truncated`, `no_lever`, `no_action_needed`, `no_suitable_candidate`), peringkat pilihan pengguna (`current_rank` dari `ranked_candidates`), dan kandidat menurut peringkat lalu yang dikecualikan atau gagal |
| GET | `/scenarios/{scenario_id}/sweep/candidates/{candidate_id}` | Rincian kandidat: parameter, respons, fakta kriteria, `vs_baseline`, `vs_current`, replikasi |
| POST | `/scenarios/{scenario_id}/sweep` | Jalankan ulang sweep untuk simulasi tersimpan terakhir (202); 409 bila masih berjalan |

404 bila skenario tidak ada, `detail.type = not_simulated` bila belum pernah Run and save, dan `no_sweep` bila simulasi tersimpannya belum punya sweep. `is_stale` true bila parameter skenario berubah sejak simulasi yang dipakai sweep.

## Pengaturan

| Env | Bawaan | Arti |
| --- | --- | --- |
| `DSS_MAX_CANDIDATES` | 50 | Kandidat yang disimulasikan per sweep, termasuk pilihan pengguna |
| `DSS_MAX_CONCURRENT_SWEEPS` | 1 | Sweep yang berjalan bersamaan per proses API |
| `DSS_SWEEP_STALE_MINUTES` | 30 | Menit tanpa kemajuan sebelum sweep dianggap terhenti |
| `DSS_WEIGHT_*` | 0.25 | Bobot empat kriteria penilaian |

## Asumsi untuk ditinjau stakeholder

Angka bertanda (*) adalah usulan tim yang belum disepakati dengan PuTI (masukan SCRUM-49).

| Topik | Asumsi | Konstanta |
| --- | --- | --- |
| Bobot kriteria | Empat kriteria sama penting | `DSS_WEIGHT_*` = 0.25 (*) |
| Senioritas mentor | Mentor yang lebih senior lebih cocok | junior 0.5, mid 0.75, senior 1 (*) |
| Manfaat mentor (SCRUM-72) | Satu bagian waktu mentor menambah dua bagian kapasitas anggota baru selama onboarding, bila mentor memegang perannya; peran sekunder setengahnya; hari mentor tidak hadir tanpa manfaat | `MENTOR_GAIN` = 2.0 (*), `MENTOR_SECONDARY_ROLE_FACTOR` = 0.5 (*) |
| Pengalihan karena ketidakhadiran | Hanya task yang berjalan atau dapat dimulai sebelum ketidakhadiran berakhir; pengalihan berlaku sampai task selesai | - |
| Task Overrun | Hanya pengganti, bukan pembantu: satu task satu pemilik di penjadwal | - |
| Monte Carlo | Hanya tiga kandidat teratas; perbandingan dengan pilihan pengguna tetap deterministik | `TOP_REPLICATED` = 3 |

Konsekuensi yang perlu diketahui: kandidat tanpa anggota (misalnya "Tanpa mentor") hanya dinilai pada `kpi_effect`. Bila tidak ada kandidat yang mengubah KPI, nilainya 1 sehingga ia berada di atas anggota yang kecocokan peran atau kapasitasnya kurang dari sempurna. Pada dataset sintetis, mentor berada di peringkat pertama pada 3 dari 6 percobaan New Member (peran dan lama onboarding berbeda); pada sisanya "Tanpa mentor" pertama karena tidak ada KPI yang berbeda.

## Waktu proses

Diukur pada dataset sintetis seed 42 (136 task, 13 anggota) di laptop pengembang, dengan replikasi 500 kali untuk tiga kandidat teratas:

| Situasi | Kandidat (disimulasikan / dikecualikan) | Menyusun kandidat | Simulasi semua kandidat | Replikasi teratas x 500 |
| --- | --- | --- | --- | --- |
| Member Absence | 2 / 1 | 0.01 s | 0.13 s | 10.4 s (1 kandidat) |
| Member Rotation | 3 / 0 | 0.01 s | 0.18 s | 19.7 s (2 kandidat) |
| Task Overrun | 5 / 0 | 0.01 s | 0.26 s | 30.8 s |
| New Member | 12 / 2 | 0.01 s | 0.97 s | 30.1 s |
| Critical Bug | 11 / 2 | 0.01 s | 0.55 s | 48.5 s |
| Priority Change | 2 / 1 | 0.02 s | 0.51 s | 17.2 s (1 kandidat) |
| Task Blocker | 6 / 0 | 0.02 s | 0.59 s | 47.4 s |
| Mid-sprint Change Request | 8 / 0 | 0.01 s | 0.56 s | 46.5 s |

Simulasi deterministik semua kandidat selesai di bawah satu detik; hampir seluruh waktu sweep adalah replikasi Monte Carlo (sekitar 10 sampai 16 detik per kandidat untuk 500 replikasi, bervariasi menurut beban mesin). Sweep terpanjang sekitar 50 detik, di bawah batas 60 detik SCRUM-65, jadi `DSS_MAX_CANDIDATES` belum perlu diturunkan. Bila perlu lebih cepat, replikasi baseline dapat dipakai ulang antar kandidat karena undiannya sama untuk benih yang sama; itu kira-kira separuh waktu replikasi. Simulasi tersimpan tanpa replikasi tidak menjalankan replikasi sama sekali.

## Tes

```powershell
# Tanpa basis data: kandidat, lever, respons mesin, runner, penilaian, alasan, manfaat mentor
.\.venv\Scripts\python.exe -m pytest tests/test_dss_sweep_candidates.py tests/test_dss_parameter_levers.py tests/test_dss_reassignment_levers.py tests/test_sim_actions.py tests/test_dss_sweep_runner.py tests/test_dss_sweep_scoring.py tests/test_dss_sweep_reasons.py tests/test_sim_rules.py tests/test_sim_calendar.py -v
# Alur API terhadap PostgreSQL (TEST_DATABASE_URL, lihat README): Run and save sampai rekomendasi
.\.venv\Scripts\python.exe -m pytest tests/test_dss_api.py -m db -v
```

Skor di tes penilaian dan angka di tes alasan dihitung manual dan dijelaskan di docstring setiap berkas tes.
