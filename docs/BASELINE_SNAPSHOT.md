# Baseline snapshot untuk skenario (SCRUM-15)

Simulasi *what-if* tWinner berjalan terhadap **baseline snapshot**: salinan beku data proyek dan sumber daya D-01 s.d. D-11 pada satu waktu. Setiap impor membuat snapshot baru dengan ID, waktu impor, dan penanda asal data (`synthetic` atau `puti`). Snapshot tidak pernah diubah setelah dibuat, sehingga skenario yang menunjuk `snapshot_id` yang sama selalu dihitung dari data yang sama. UC-06 memakai `snapshot_id` dan `checksum` untuk memperingatkan perbandingan skenario dari snapshot berbeda.

| Bagian | Berkas |
| --- | --- |
| Tabel `twinner.baseline_snapshot` dan `twinner.baseline_snapshot_table` | `backend/app/modules/simulasi/snapshot_models.py`, migrasi `0021` |
| Validator CSV (tanpa basis data) | `backend/app/modules/simulasi/snapshot_import.py` |
| Impor dan loader | `backend/app/modules/simulasi/snapshot_service.py` |
| Endpoint | `backend/app/modules/simulasi/router.py` |
| CLI | `backend/app/modules/simulasi/snapshot_cli.py` |

## Menyimpan

Tabel tWinner berada di schema `twinner` (DATA_CONTRACT bagian 4) tanpa foreign key ke schema `public`.

- `baseline_snapshot`: satu baris per impor dengan kolom `snapshot_id`, `data_source`, `label`, `imported_by`, `created_at`, `row_counts`, dan `checksum` (SHA-256 isi snapshot dalam bentuk kanonis; isi yang sama menghasilkan nilai yang sama).
- `baseline_snapshot_table`: isi setiap tabel D-01 s.d. D-11 sebagai satu array JSONB per snapshot. Kolom setiap baris sama dengan Data Dictionary (DATA_CONTRACT bagian 6) dan diurutkan menurut primary key.

Isi tabel disimpan sebagai JSONB, bukan sebagai salinan 11 tabel relasional, karena snapshot selalu dibaca utuh oleh mesin simulasi dan tidak pernah di-query per baris. Keabsahannya dijaga validator saat impor.

## Mengimpor

Kesebelas berkas wajib dikirim bersamaan, yaitu `projects.csv`, `resources.csv`, `roles.csv`, `complexity_levels.csv`, `tasks.csv`, `task_dependencies.csv`, `assignments.csv`, `workload_items.csv`, `availability.csv`, `change_events.csv`, dan `holidays.csv`. Formatnya mengikuti DATA_CONTRACT bagian 5: UTF-8 (BOM boleh), pemisah koma, baris pertama berisi nama kolom (urutannya bebas), tanggal `YYYY-MM-DD`, boolean `true`/`false`, titik sebagai pemisah desimal, dan sel kosong berarti NULL. Tabel tanpa data cukup berisi baris judul kolom. Keluaran `python -m synthetic` (folder `final_sources/`) dan ekspor CSV cadangan J&C sudah sesuai format ini.

### Lewat API

`POST /api/v1/simulasi/baseline-snapshots/import`, izin `import:run` (Administrator Data):

```json
{
  "data_source": "synthetic",
  "label": "sintetis seed 42 per 2026-11-06",
  "dry_run": false,
  "files": {
    "projects.csv": "project_id,project_name,process_owner,...\nPRJ-001,...",
    "tasks.csv": "task_id,project_id,...\n..."
  }
}
```

Isi CSV dikirim sebagai teks di dalam JSON, sama seperti impor D-08 (`/data/workload-items/import`). `dry_run` bawaannya `true`, yaitu hanya memeriksa tanpa menyimpan. Respons selalu `200`, dengan isi berikut:

| Field | Isi |
| --- | --- |
| `applied` | `true` bila snapshot disimpan |
| `row_counts` | Jumlah baris data per tabel |
| `error_count`, `errors` | Semua kesalahan dihitung; paling banyak 500 yang pertama dikirim. Setiap kesalahan berisi `file`, `row` (baris judul kolom = 1), `record_id`, `field`, dan `message` |
| `snapshot` | Ringkasan snapshot baru bila `applied` |

Bila ada satu kesalahan saja, tidak ada yang disimpan. Permintaan yang bentuknya salah, misalnya `data_source` tidak dikenal, ditolak FastAPI dengan `422` biasa.

### Lewat CLI

Dari folder `backend/`, dengan `DATABASE_URL` menunjuk basis data yang sudah `alembic upgrade head`:

```powershell
.\.venv\Scripts\python.exe -m synthetic --out synthetic-output --seed 42 --as-of 2026-11-06
.\.venv\Scripts\python.exe -m app.modules.simulasi.snapshot_cli synthetic-output/final_sources --source synthetic --label "sintetis seed 42 per 2026-11-06"
```

Tambahkan `--dry-run` untuk hanya memeriksa. Kode keluar `0` berarti berhasil, `1` berarti ada kesalahan data (dicetak per baris ke stderr dan tidak ada yang disimpan), dan `2` berarti folder atau berkas tidak terbaca.

### Yang diperiksa

| Tingkat | Aturan |
| --- | --- |
| Berkas | 11 berkas lengkap, tidak ada nama yang tidak dikenal atau dikirim dua kali, CSV terbaca, judul kolom sesuai Data Dictionary (ada petunjuk khusus bila pemisahnya titik koma) |
| Baris | Jumlah sel sesuai judul, kolom wajib terisi, format ID, nilai enum, tipe angka/tanggal/boolean, panjang teks, primary key tidak ganda |
| Dalam baris | Sama dengan CHECK constraint skema J&C, misalnya `planned_finish >= planned_start`, `actual_finish` butuh `actual_start`, dependency hard `propagation_factor = 1.0`, dan task tidak bergantung pada dirinya sendiri |
| Antartabel | Setiap foreign key menunjuk baris yang ada, termasuk setiap peran di `secondary_role_ids` |
| Antarbaris | Prioritas proyek aktif unik (`uq_projects_active_priority`), pasangan dependency unik, ketidakhadiran satu anggota tidak bertumpuk (`ex_availability_no_overlap`, kecuali `rotation_in`), dan **dependency tidak melingkar** |

Pemeriksaan dependency melingkar tidak ada di constraint basis data J&C, tetapi wajib bagi tWinner karena penjadwalan hari demi hari (langkah 4 simulasi) tidak akan selesai pada siklus.

Foreign key ke berkas yang hilang atau judul kolomnya salah tidak diperiksa, supaya satu berkas rusak tidak memicu ratusan kesalahan turunan. Baris yang salah di kolom lain tetapi ID-nya terbaca tetap dianggap ada untuk pemeriksaan foreign key.

## Membaca

Izin `baseline:read` (Kaur/Asman dan System Analyst, pengambil keputusan UC-01):

| Endpoint | Isi |
| --- | --- |
| `GET /api/v1/simulasi/baseline-snapshots?limit=50` | Ringkasan snapshot, terbaru lebih dulu, dengan `is_current` |
| `GET /api/v1/simulasi/baseline-snapshots/current` | Snapshot terbaru lengkap: `projects`, `tasks`, `task_dependencies`, `assignments`, kapasitas (`resources`, `roles`, `workload_items`), ketidakhadiran (`availability`), `holidays`, serta `complexity_levels` dan `change_events`. Bila belum ada snapshot, hasilnya `404` dengan pesan "Belum ada baseline snapshot..." (UC-01 alur alternatif) |
| `GET /api/v1/simulasi/baseline-snapshots/{snapshot_id}` | Satu snapshot lengkap, misalnya snapshot yang ditunjuk skenario lama |

Snapshot terbaru adalah snapshot dengan `snapshot_id` terbesar. Skenario baru dikaitkan ke snapshot ini.

## Loader untuk skenario dan simulasi

```python
from app.modules.simulasi.snapshot_service import (
    SnapshotNotFoundError,
    current_snapshot_id,
    load_snapshot,
)

snapshot_id = current_snapshot_id(db)      # None bila belum ada baseline (UC-01)
baseline = load_snapshot(db, snapshot_id)  # BaselineSnapshotData; tanpa argumen = terbaru
for task in baseline.tasks:                # TaskRow: date, enum, float sudah bertipe
    ...
```

Setiap panggilan `load_snapshot` membuat objek baru dari basis data. Aturan skenario (SCRUM-24) boleh mengubah objek itu atau salinannya (`baseline.model_copy(deep=True)`) tanpa mengubah baseline yang tersimpan.

## Batas

| Batas | Nilai |
| --- | --- |
| Isi satu berkas | 5.000.000 karakter |
| Total isi berkas | 20.000.000 karakter |
| Baris per tabel | 100.000 |
| Kesalahan yang dikirim | 500 pertama (`error_count` tetap menghitung semuanya) |
