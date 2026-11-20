"""SCRUM-15: pembaca dan validator CSV baseline snapshot, tanpa basis data."""

import csv
import importlib.util
import io
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.data import models as jc_models
from app.modules.data.enums import TaskStatus
from app.modules.simulasi.snapshot_import import find_cycles, parse_snapshot_files
from app.modules.simulasi.snapshot_models import DATA_SOURCES, SNAPSHOT_TABLES
from app.modules.simulasi.snapshot_schemas import ROW_MODELS, SnapshotImportRequest
from app.modules.simulasi.snapshot_service import checksum
from synthetic.final_sources import write_final_sources
from synthetic.world import build_world

BACKEND_DIR = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 11, 6)


@pytest.fixture(scope="module")
def clean_files(tmp_path_factory) -> dict[str, str]:
    """11 CSV final sources sintetis seed 42, persis seperti keluaran `python -m synthetic`."""
    folder = tmp_path_factory.mktemp("final_sources")
    write_final_sources(build_world(42, AS_OF), folder)
    return {path.name: path.read_text(encoding="utf-8") for path in folder.glob("*.csv")}


@pytest.fixture
def files(clean_files) -> dict[str, str]:
    return dict(clean_files)


def read_rows(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def write_rows(header: list[str], rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def edit(files: dict[str, str], table: str, index: int, **values: str) -> int:
    """Ubah kolom baris data ke-`index` (mulai 0); kembalikan nomor barisnya di berkas."""
    header, rows = read_rows(files[f"{table}.csv"])
    rows[index].update(values)
    files[f"{table}.csv"] = write_rows(header, rows)
    return index + 2


def append(files: dict[str, str], table: str, **values: str) -> int:
    header, rows = read_rows(files[f"{table}.csv"])
    rows.append({column: values.get(column, "") for column in header})
    files[f"{table}.csv"] = write_rows(header, rows)
    return len(rows) + 1


def errors_of(files: dict[str, str]) -> list[tuple]:
    result = parse_snapshot_files(files)
    assert result.tables == {}
    return [(e.file, e.row, e.record_id, e.field, e.message) for e in result.errors]


# --- kontrak dengan Data Dictionary ----------------------------------------------------------


def test_row_models_follow_the_data_dictionary_columns():
    """Model baris snapshot harus sama dengan tabel J&C: nama dan urutan kolom."""
    tables = jc_models.Base.metadata.tables
    for table, model in ROW_MODELS.items():
        assert list(model.model_fields) == [c.name for c in tables[table].columns], table
        assert next(iter(model.model_fields)) == tables[table].primary_key.columns[0].name


def test_table_lists_agree():
    assert tuple(ROW_MODELS) == SNAPSHOT_TABLES
    path = BACKEND_DIR / "migrations" / "versions" / "0021_twinner_baseline_snapshot.py"
    spec = importlib.util.spec_from_file_location("migration_0021", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.TABLES == SNAPSHOT_TABLES


# --- data bersih -------------------------------------------------------------------------


def test_clean_synthetic_files_pass(clean_files):
    result = parse_snapshot_files(clean_files)

    assert result.errors == []
    assert set(result.tables) == set(SNAPSHOT_TABLES)
    assert result.row_counts["tasks"] == len(result.tables["tasks"]) == 136
    for table in SNAPSHOT_TABLES:
        assert result.row_counts[table] == len(result.tables[table])
    task = result.tables["tasks"][0]
    assert task.task_id == "TSK-0001"
    assert isinstance(task.planned_start, date)
    assert isinstance(task.status, TaskStatus)


def test_rows_are_sorted_by_primary_key_so_checksum_ignores_file_order(files, clean_files):
    header, rows = read_rows(files["tasks.csv"])
    files["tasks.csv"] = write_rows(header, list(reversed(rows)))

    def digest(f):
        tables = parse_snapshot_files(f).tables
        return checksum(
            {t: [r.model_dump(mode="json") for r in rows] for t, rows in tables.items()}
        )

    assert digest(files) == digest(clean_files)
    edit(files, "tasks", 0, task_name="Nama lain")
    assert digest(files) != digest(clean_files)


def test_file_names_may_have_folder_uppercase_and_no_extension(clean_files):
    renamed = {f"final_sources/{name.upper()}": text for name, text in clean_files.items()}
    renamed["holidays"] = renamed.pop("final_sources/HOLIDAYS.CSV")
    assert parse_snapshot_files(renamed).errors == []


def test_header_only_file_is_an_empty_table_and_bom_is_accepted(files):
    header, _ = read_rows(files["change_events.csv"])
    files["change_events.csv"] = ",".join(header) + "\n"
    files["holidays.csv"] = "﻿" + files["holidays.csv"]
    result = parse_snapshot_files(files)
    assert result.errors == []
    assert result.tables["change_events"] == []
    assert result.row_counts["change_events"] == 0


def test_columns_may_come_in_any_order(files):
    header, rows = read_rows(files["roles.csv"])
    files["roles.csv"] = write_rows(list(reversed(header)), rows)
    assert parse_snapshot_files(files).errors == []


# --- berkas dan judul kolom ----------------------------------------------------------------


def test_missing_and_unknown_files(files):
    del files["holidays.csv"]
    files["catatan.txt"] = "bebas"
    errors = errors_of(files)
    assert ("catatan.txt", None, None, None) == errors[-1][:4]
    assert "Berkas tidak dikenal" in errors[-1][4]
    assert errors[0][:2] == ("holidays.csv", None)
    assert "Berkas tidak ada" in errors[0][4]


def test_same_table_sent_twice(files):
    files["final_sources/projects.csv"] = files["projects.csv"]
    assert any("Dikirim dua kali" in e[4] for e in errors_of(files))


def test_wrong_header_is_reported_once_without_foreign_key_noise(files):
    files["projects.csv"] = files["projects.csv"].replace("process_owner", "pemilik", 1)
    errors = errors_of(files)
    # tasks dan change_events menunjuk projects, tetapi tidak ikut dilaporkan.
    assert errors == [
        (
            "projects.csv",
            1,
            None,
            None,
            "Judul kolom tidak sesuai Data Dictionary: kolom hilang: process_owner; "
            "kolom tidak dikenal: pemilik",
        )
    ]


def test_semicolon_separator_gets_a_hint(files):
    header, rows = read_rows(files["roles.csv"])
    files["roles.csv"] = ";".join(header) + "\n"
    assert errors_of(files) == [
        ("roles.csv", 1, None, None, "Pemisah kolom harus koma (,), bukan titik koma (;)")
    ]


def test_empty_file(files):
    files["holidays.csv"] = ""
    errors = errors_of(files)
    assert errors == [
        ("holidays.csv", 1, None, None, "Berkas kosong; baris pertama harus berisi judul kolom")
    ]


def test_wrong_number_of_cells(files):
    files["roles.csv"] += "ROLE-XX,Peran,3.0,deskripsi,lebih\nROLE-YY,Peran\n"
    lines = len(files["roles.csv"].splitlines())
    assert errors_of(files) == [
        ("roles.csv", lines - 1, "ROLE-XX", None, "Jumlah kolom melebihi judul kolom"),
        ("roles.csv", lines, "ROLE-YY", None, "Jumlah kolom kurang dari judul kolom"),
    ]


# --- isi kolom -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column", "value", "message"),
    [
        ("tasks", "planned_start", "05/10/2026", "Tanggal harus berformat YYYY-MM-DD"),
        ("tasks", "planned_start", "5 Okt 2026", "Tanggal harus berformat YYYY-MM-DD"),
        ("tasks", "planned_start", "2026-02-30", "Tanggal 2026-02-30 tidak ada di kalender"),
        ("tasks", "estimated_days", "", "Wajib diisi"),
        (
            "tasks",
            "estimated_days",
            "1,5",
            "Harus angka dengan titik sebagai pemisah desimal, misalnya 1.5",
        ),
        ("tasks", "estimated_days", "0", "Harus lebih dari 0"),
        (
            "tasks",
            "estimated_days",
            "nan",
            "Harus angka dengan titik sebagai pemisah desimal, misalnya 1.5",
        ),
        ("tasks", "priority_rank", "satu", "Harus bilangan bulat"),
        (
            "tasks",
            "status",
            "selesai",
            "Nilai tidak sah, gunakan salah satu dari 'not_started', "
            "'in_progress', 'blocked', 'on_hold', 'in_qa', 'done' or 'dropped'",
        ),
        ("tasks", "dod_fulfilled", "ya", "Isi true atau false"),
        ("tasks", "project_id", "PRJ-1", "Format tidak sah, contoh PRJ-001"),
        (
            "resources",
            "secondary_role_ids",
            "ROLE-DM, ROLE-FE",
            "Format tidak sah, contoh ROLE-DM;ROLE-FE",
        ),
        ("resources", "join_date", "", "Wajib diisi"),
        ("projects", "project_name", "x" * 201, "Paling banyak 200 karakter"),
        ("task_dependencies", "propagation_factor", "1.5", "Paling besar 1"),
    ],
)
def test_bad_cell_is_reported_with_file_row_and_column(files, table, column, value, message):
    line = edit(files, table, 3, **{column: value})
    [(file, row, _, field, text)] = errors_of(files)
    assert (file, row, field, text) == (f"{table}.csv", line, column, message)


def test_booleans_accept_any_case(files):
    edit(files, "projects", 0, dor_fulfilled="TRUE")
    assert parse_snapshot_files(files).errors == []


def test_bad_id_format_and_duplicate_id(files):
    edit(files, "availability", 0, availability_id="AVL-9")
    header, rows = read_rows(files["holidays.csv"])
    files["holidays.csv"] = write_rows(header, rows + [rows[0]])
    errors = errors_of(files)
    assert (
        "holidays.csv",
        len(rows) + 2,
        rows[0]["holiday_date"],
        "holiday_date",
        "ID sudah dipakai di baris 2",
    ) in errors
    assert (
        "availability.csv",
        2,
        "AVL-9",
        "availability_id",
        "Format tidak sah, contoh AVL-001",
    ) in errors


# --- aturan dalam baris -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "values", "column", "message"),
    [
        (
            "availability",
            {"date_start": "2026-10-05", "date_end": "2026-10-01"},
            "date_end",
            "Tidak boleh sebelum date_start (2026-10-05)",
        ),
        (
            "projects",
            {
                "start_date": "2026-01-10",
                "target_launch_date": "2026-01-01",
                "actual_launch_date": "",
            },
            "target_launch_date",
            "Tidak boleh sebelum start_date (2026-01-10)",
        ),
        (
            "complexity_levels",
            {"min_days": "5", "max_days": "2"},
            "max_days",
            "Tidak boleh lebih kecil dari min_days (5)",
        ),
        (
            "task_dependencies",
            {"dependency_type": "hard", "propagation_factor": "0.3"},
            "propagation_factor",
            "Dependency hard harus bernilai 1.0",
        ),
    ],
)
def test_row_rules(files, table, values, column, message):
    line = edit(files, table, 0, **values)
    [(file, row, _, field, text)] = errors_of(files)
    assert (file, row, field, text) == (f"{table}.csv", line, column, message)


def test_finish_without_start(files):
    _, rows = read_rows(files["tasks.csv"])
    index = next(i for i, r in enumerate(rows) if r["actual_finish"])
    line = edit(files, "tasks", index, actual_start="")
    assert errors_of(files) == [
        (
            "tasks.csv",
            line,
            rows[index]["task_id"],
            "actual_start",
            "Wajib diisi bila actual_finish terisi",
        )
    ]


def test_self_dependency(files):
    _, rows = read_rows(files["task_dependencies.csv"])
    line = edit(files, "task_dependencies", 0, depends_on_task_id=rows[0]["task_id"])
    assert errors_of(files) == [
        (
            "task_dependencies.csv",
            line,
            rows[0]["dependency_id"],
            "depends_on_task_id",
            "Task tidak boleh bergantung pada dirinya sendiri",
        )
    ]


# --- antartabel dan antarbaris ---------------------------------------------------------------


def test_unknown_foreign_keys(files):
    line = edit(files, "assignments", 0, resource_id="RES-099")
    edit(files, "resources", 0, secondary_role_ids="ROLE-DM;ROLE-ZZ")
    errors = errors_of(files)
    _, assignments = read_rows(files["assignments.csv"])
    _, resources = read_rows(files["resources.csv"])
    assert errors == [
        (
            "resources.csv",
            2,
            resources[0]["resource_id"],
            "secondary_role_ids",
            "Peran ROLE-ZZ tidak ada di roles.csv",
        ),
        (
            "assignments.csv",
            line,
            assignments[0]["assignment_id"],
            "resource_id",
            "Anggota RES-099 tidak ada di resources.csv",
        ),
    ]


def test_invalid_row_with_readable_id_still_counts_for_foreign_keys(files):
    """Satu sel salah di projects.csv tidak membuat semua task proyek itu ikut salah."""
    edit(files, "projects", 0, start_date="kemarin")
    errors = errors_of(files)
    assert [e[:4] for e in errors] == [("projects.csv", 2, "PRJ-001", "start_date")]


def test_dependency_cycle(files):
    _, rows = read_rows(files["task_dependencies.csv"])
    first = rows[0]
    line = append(
        files,
        "task_dependencies",
        dependency_id="DEP-9999",
        task_id=first["depends_on_task_id"],
        depends_on_task_id=first["task_id"],
        dependency_type="soft",
        propagation_factor="0.3",
    )
    [(file, row, record, field, message)] = errors_of(files)
    # Dilaporkan di sisi siklus yang paling bawah di berkas, yaitu baris yang baru ditambahkan.
    assert (file, row, record, field) == (
        "task_dependencies.csv",
        line,
        "DEP-9999",
        "depends_on_task_id",
    )
    # Titik awal jalur bergantung pada urutan penelusuran, jadi kedua arah sah.
    a, b = first["task_id"], first["depends_on_task_id"]
    assert message in {
        f"Dependency melingkar: {a} -> {b} -> {a} (-> berarti bergantung pada)",
        f"Dependency melingkar: {b} -> {a} -> {b} (-> berarti bergantung pada)",
    }


def test_duplicate_dependency_pair(files):
    _, rows = read_rows(files["task_dependencies.csv"])
    line = append(files, "task_dependencies", **{**rows[0], "dependency_id": "DEP-9999"})
    assert errors_of(files) == [
        (
            "task_dependencies.csv",
            line,
            "DEP-9999",
            "depends_on_task_id",
            f"Dependency yang sama sudah ada: {rows[0]['dependency_id']} (baris 2)",
        )
    ]


def test_overlapping_absence(files):
    _, rows = read_rows(files["availability.csv"])
    base = next(r for r in rows if r["absence_type"] != "rotation_in")
    line = append(files, "availability", **{**base, "availability_id": "AVL-999"})
    assert errors_of(files) == [
        (
            "availability.csv",
            line,
            "AVL-999",
            "date_start",
            f"Periode bertumpuk dengan {base['availability_id']} (baris "
            f"{rows.index(base) + 2}) milik {base['resource_id']}; ketidakhadiran satu anggota "
            "tidak boleh bertumpuk",
        )
    ]


def test_rotation_in_may_overlap(files):
    _, rows = read_rows(files["availability.csv"])
    base = next(r for r in rows if r["absence_type"] != "rotation_in")
    append(
        files,
        "availability",
        **{**base, "availability_id": "AVL-999", "absence_type": "rotation_in"},
    )
    assert parse_snapshot_files(files).errors == []


def test_active_projects_need_unique_priority(files):
    _, rows = read_rows(files["projects.csv"])
    active = [
        i for i, r in enumerate(rows) if r["status"] in ("planning", "development", "testing")
    ]
    first, second = active[:2]
    line = edit(files, "projects", second, priority_rank=rows[first]["priority_rank"])
    [(file, row, record, field, message)] = errors_of(files)
    assert (file, row, record, field) == (
        "projects.csv",
        line,
        rows[second]["project_id"],
        "priority_rank",
    )
    assert message.startswith(
        f"Prioritas {rows[first]['priority_rank']} sudah dipakai proyek "
        f"aktif {rows[first]['project_id']}"
    )


def test_every_bad_cell_is_collected_not_just_the_first(files):
    header, rows = read_rows(files["tasks.csv"])
    for row in rows:
        row["phase"] = "x"
        row["status"] = "x"
        row["dod_fulfilled"] = "x"
        row["planned_start"] = "x"
    files["tasks.csv"] = write_rows(header, rows)
    result = parse_snapshot_files(files)
    assert len(result.errors) == 4 * len(rows) > 500


def test_find_cycles():
    edges = [
        ("A", "B", 2, "DEP-1"),
        ("B", "C", 3, "DEP-2"),
        ("C", "A", 4, "DEP-3"),
        ("D", "E", 5, "DEP-4"),
        ("E", "F", 6, "DEP-5"),
        ("D", "F", 7, "DEP-6"),
        ("G", "G2", 8, "DEP-7"),
        ("G2", "G", 9, "DEP-8"),
    ]
    assert find_cycles(edges) == [
        (4, "DEP-3", ["A", "B", "C", "A"]),
        (9, "DEP-8", ["G", "G2", "G"]),
    ]
    assert find_cycles(edges[3:6]) == []
    # Penelusuran dari X menutup siklus lewat Y -> X (baris 5), tetapi yang dilaporkan sisi
    # siklus paling bawah di berkas: X -> Y (baris 10).
    assert find_cycles([("X", "Y", 10, "DEP-10"), ("Y", "X", 5, "DEP-5")]) == [
        (10, "DEP-10", ["X", "Y", "X"])
    ]
    # Task yang bergantung pada dirinya sendiri juga siklus.
    assert find_cycles([("Z", "Z", 3, "DEP-3")]) == [(3, "DEP-3", ["Z", "Z"])]


# --- permintaan ------------------------------------------------------------------------------


def test_request_rejects_unknown_source_and_non_boolean_dry_run():
    with pytest.raises(ValidationError):
        SnapshotImportRequest(data_source="excel", files={"a": "b"})
    with pytest.raises(ValidationError):
        SnapshotImportRequest(data_source="puti", files={"a": "b"}, dry_run="false")
    assert set(DATA_SOURCES) == {"synthetic", "puti"}
