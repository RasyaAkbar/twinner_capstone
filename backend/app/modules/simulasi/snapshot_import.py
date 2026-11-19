"""Pembaca dan validator CSV baseline snapshot (SCRUM-15), tanpa menyentuh basis data.

Sebelas berkas D-01 s.d. D-11 dibaca dan diperiksa sebagai satu kesatuan. Snapshot hanya boleh
disimpan bila tidak ada satu kesalahan pun, karena mesin simulasi (SCRUM-24) menganggap
baseline sudah konsisten: setiap foreign key menunjuk baris yang ada dan dependency task tidak
melingkar (penjadwalan hari demi hari tidak akan pernah selesai pada siklus).

Aturan yang diperiksa sama dengan constraint skema J&C (DATA_CONTRACT bagian 6) ditambah dua
aturan validator J&C yang tidak bisa dijaga constraint: format tanggal ISO dan siklus
dependency (SYNTHETIC_DATA bagian "Data kotor"). Setiap kesalahan menyebut berkas, nomor baris,
ID baris, kolom, dan pesan yang bisa dibaca pengguna.
"""

import csv
import io
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.modules.data.enums import ACTIVE_PROJECT_STATUSES, AbsenceType, DependencyType
from app.modules.simulasi.snapshot_models import SNAPSHOT_TABLES
from app.modules.simulasi.snapshot_schemas import (
    ID_EXAMPLES,
    MAX_TOTAL_CHARS,
    PRIMARY_KEYS,
    ROW_MODELS,
    SnapshotImportError,
    SnapshotRow,
)

MAX_ROWS_PER_TABLE = 100_000
MAX_REPORTED_ERRORS = 500

# (kolom, tabel tujuan) setiap foreign key D-01 s.d. D-11. secondary_role_ids diperiksa terpisah
# karena berisi daftar role_id dalam satu string.
FOREIGN_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "resources": (("primary_role_id", "roles"),),
    "tasks": (
        ("project_id", "projects"),
        ("required_role_id", "roles"),
        ("complexity_id", "complexity_levels"),
    ),
    "task_dependencies": (("task_id", "tasks"), ("depends_on_task_id", "tasks")),
    "assignments": (("task_id", "tasks"), ("resource_id", "resources"), ("role_in_task", "roles")),
    "workload_items": (("resource_id", "resources"), ("related_project_id", "projects")),
    "availability": (("resource_id", "resources"),),
    "change_events": (("project_id", "projects"), ("affected_task_id", "tasks")),
}
TARGET_LABELS = {
    "roles": "Peran",
    "complexity_levels": "Level kompleksitas",
    "projects": "Proyek",
    "resources": "Anggota",
    "tasks": "Task",
}


def file_name(table: str) -> str:
    return f"{table}.csv"


def table_for(name: str) -> str:
    """`final_sources/Projects.csv` dan `projects` sama-sama berarti tabel `projects`."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return base.removesuffix(".csv")


@dataclass(frozen=True)
class ParseResult:
    """`tables` hanya berisi data bila `errors` kosong; baris diurutkan menurut primary key."""

    tables: dict[str, list[SnapshotRow]]
    row_counts: dict[str, int]
    errors: list[SnapshotImportError]


@dataclass
class _Table:
    rows: list[tuple[int, SnapshotRow]] = field(default_factory=list)
    # Primary key setiap baris yang terbaca, termasuk baris yang kolom lainnya salah. Dipakai
    # untuk cek foreign key, supaya satu kolom salah di projects.csv tidak membuat semua task
    # proyek itu ikut dilaporkan.
    ids: set[str] = field(default_factory=set)
    total: int = 0
    # Berkas hilang, judul kolom salah, atau CSV rusak: foreign key ke tabel ini tidak dicek.
    broken: bool = False


class _Errors:
    def __init__(self) -> None:
        self.items: list[SnapshotImportError] = []

    def add(
        self,
        table: str | None,
        row: int | None,
        record_id: str | None,
        column: str | None,
        message: str,
    ) -> None:
        self.items.append(
            SnapshotImportError(
                file=file_name(table) if table in ROW_MODELS else table,
                row=row,
                record_id=record_id,
                field=column,
                message=message,
            )
        )


def parse_snapshot_files(files: Mapping[str, str]) -> ParseResult:
    errors = _Errors()
    contents = _match_files(files, errors)
    if sum(len(text) for text in files.values()) > MAX_TOTAL_CHARS:
        errors.add(None, None, None, None, f"Total isi berkas melebihi {MAX_TOTAL_CHARS} karakter")
        return ParseResult({}, {}, errors.items)

    parsed: dict[str, _Table] = {}
    for table in SNAPSHOT_TABLES:
        if table in contents:
            parsed[table] = _read_table(table, contents[table], errors)
        else:
            parsed[table] = _Table(broken=True)

    _check_foreign_keys(parsed, errors)
    _check_secondary_roles(parsed, errors)
    _check_active_priority(parsed["projects"], errors)
    _check_dependency_pairs(parsed["task_dependencies"], errors)
    _check_dependency_cycles(parsed["task_dependencies"], errors)
    _check_availability_overlap(parsed["availability"], errors)

    order = {table: index for index, table in enumerate(SNAPSHOT_TABLES)}
    items = sorted(
        errors.items,
        key=lambda e: (
            e.file is not None,
            order.get(table_for(e.file or ""), len(order)),
            e.row or 0,
        ),
    )
    row_counts = {table: parsed[table].total for table in SNAPSHOT_TABLES if table in contents}
    if items:
        return ParseResult({}, row_counts, items)
    tables = {
        table: sorted(
            (row for _, row in parsed[table].rows),
            key=lambda row, pk=PRIMARY_KEYS[table]: _sort_key(getattr(row, pk)),
        )
        for table in SNAPSHOT_TABLES
    }
    return ParseResult(tables, row_counts, [])


def _sort_key(value: Any) -> tuple[int, Any]:
    # TSK-0002 sebelum TSK-0010, dan PRJ-999 sebelum PRJ-1000.
    return (len(value), value) if isinstance(value, str) else (0, value)


# --- berkas dan judul kolom ---------------------------------------------------------


def _match_files(files: Mapping[str, str], errors: _Errors) -> dict[str, str]:
    contents: dict[str, str] = {}
    sent_as: dict[str, str] = {}
    expected = ", ".join(file_name(table) for table in SNAPSHOT_TABLES)
    for name, text in files.items():
        table = table_for(name)
        if table not in ROW_MODELS:
            errors.add(
                name, None, None, None, f"Berkas tidak dikenal. Nama yang dipakai: {expected}"
            )
        elif table in contents:
            errors.add(table, None, None, None, f"Dikirim dua kali: {sent_as[table]} dan {name}")
        else:
            contents[table] = text
            sent_as[table] = name
    for table in SNAPSHOT_TABLES:
        if table not in contents:
            errors.add(
                table,
                None,
                None,
                None,
                "Berkas tidak ada. Kesebelas tabel D-01 s.d. D-11 wajib dikirim; tabel tanpa "
                "data cukup berisi baris judul kolom",
            )
    return contents


def _header_problem(header: list[str], columns: list[str]) -> str | None:
    if not header:
        return "Berkas kosong; baris pertama harus berisi judul kolom"
    if len(header) == 1 and ";" in header[0]:
        return "Pemisah kolom harus koma (,), bukan titik koma (;)"
    missing = [c for c in columns if c not in header]
    unknown = [c for c in header if c not in columns]
    parts = []
    if missing:
        parts.append("kolom hilang: " + ", ".join(missing))
    if unknown:
        parts.append("kolom tidak dikenal: " + ", ".join(unknown))
    if len(set(header)) != len(header):
        parts.append("ada nama kolom ganda")
    if parts:
        return "Judul kolom tidak sesuai Data Dictionary: " + "; ".join(parts)
    return None


def _read_table(table: str, content: str, errors: _Errors) -> _Table:
    model = ROW_MODELS[table]
    columns = list(model.model_fields)
    pk = PRIMARY_KEYS[table]
    result = _Table()
    reader = csv.DictReader(io.StringIO(content.removeprefix("﻿")))
    try:
        header = [(name or "").strip() for name in reader.fieldnames or []]
    except csv.Error as exc:
        errors.add(table, 1, None, None, f"CSV tidak terbaca: {exc}")
        result.broken = True
        return result
    problem = _header_problem(header, columns)
    if problem:
        errors.add(table, 1, None, None, problem)
        result.broken = True
        return result
    reader.fieldnames = header

    first_line: dict[str, int] = {}
    try:
        for record in reader:
            line = reader.line_num
            extra = record.pop(None, None)
            if not extra and not any((value or "").strip() for value in record.values()):
                continue  # baris kosong, misalnya di akhir berkas
            result.total += 1
            if result.total > MAX_ROWS_PER_TABLE:
                errors.add(table, line, None, None, f"Lebih dari {MAX_ROWS_PER_TABLE} baris data")
                result.broken = True
                return result

            raw_id = (record.get(pk) or "").strip() or None
            if raw_id is not None:
                if raw_id in first_line:
                    errors.add(
                        table, line, raw_id, pk, f"ID sudah dipakai di baris {first_line[raw_id]}"
                    )
                    continue
                first_line[raw_id] = line
                result.ids.add(raw_id)
            if extra:
                errors.add(table, line, raw_id, None, "Jumlah kolom melebihi judul kolom")
                continue
            if any(record[column] is None for column in columns):
                errors.add(table, line, raw_id, None, "Jumlah kolom kurang dari judul kolom")
                continue

            values = {column: record[column].strip() or None for column in columns}
            try:
                row = model.model_validate(values)
            except ValidationError as exc:
                for error in exc.errors():
                    column = str(error["loc"][0]) if error.get("loc") else None
                    errors.add(table, line, raw_id, column, _message(error))
                continue
            problems = ROW_RULES.get(table, _no_rules)(row)
            for column, message in problems:
                errors.add(table, line, raw_id, column, message)
            if not problems:
                result.rows.append((line, row))
    except csv.Error as exc:
        errors.add(table, reader.line_num, None, None, f"CSV tidak terbaca: {exc}")
        result.broken = True
    return result


def _message(error: Mapping[str, Any]) -> str:
    """Pesan error Pydantic dalam bahasa pengguna aplikasi."""
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    if kind == "value_error":
        return str(ctx.get("error", error.get("msg", "Nilai tidak sah")))
    # Sel kosong dibaca sebagai None, sehingga kolom wajib yang kosong gagal sebagai tipe salah.
    if error.get("input") is None or kind in ("missing", "string_too_short"):
        return "Wajib diisi"
    if kind.startswith("bool"):
        return "Isi true atau false"
    if kind == "enum":
        return f"Nilai tidak sah, gunakan salah satu dari {ctx.get('expected')}"
    if kind == "string_pattern_mismatch":
        example = ID_EXAMPLES.get(str(ctx.get("pattern")))
        return f"Format tidak sah, contoh {example}" if example else "Format tidak sah"
    if kind == "string_too_long":
        return f"Paling banyak {ctx.get('max_length')} karakter"
    if kind.startswith("int"):
        return "Harus bilangan bulat"
    if kind.startswith("float") or kind == "finite_number":
        return "Harus angka dengan titik sebagai pemisah desimal, misalnya 1.5"
    if kind == "greater_than":
        return f"Harus lebih dari {_number(ctx.get('gt'))}"
    if kind == "greater_than_equal":
        return f"Paling kecil {_number(ctx.get('ge'))}"
    if kind == "less_than_equal":
        return f"Paling besar {_number(ctx.get('le'))}"
    return str(error.get("msg", "Nilai tidak sah"))


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


# --- aturan per baris (sama dengan CHECK constraint skema J&C) -------------------------------

Problems = list[tuple[str, str]]


def _no_rules(row: Any) -> Problems:
    return []


def _not_before(row: Any, later: str, earlier: str) -> Problems:
    late, early = getattr(row, later), getattr(row, earlier)
    if late is not None and early is not None and late < early:
        return [(later, f"Tidak boleh sebelum {earlier} ({early.isoformat()})")]
    return []


def _project_rules(row: Any) -> Problems:
    return _not_before(row, "target_launch_date", "start_date") + _not_before(
        row, "actual_launch_date", "start_date"
    )


def _complexity_rules(row: Any) -> Problems:
    if row.max_days < row.min_days:
        return [("max_days", f"Tidak boleh lebih kecil dari min_days ({row.min_days:g})")]
    return []


def _task_rules(row: Any) -> Problems:
    problems = _not_before(row, "planned_finish", "planned_start")
    problems += _not_before(row, "actual_finish", "actual_start")
    if row.actual_finish is not None and row.actual_start is None:
        problems.append(("actual_start", "Wajib diisi bila actual_finish terisi"))
    return problems


def _dependency_rules(row: Any) -> Problems:
    problems = []
    if row.task_id == row.depends_on_task_id:
        problems.append(("depends_on_task_id", "Task tidak boleh bergantung pada dirinya sendiri"))
    if row.dependency_type == DependencyType.HARD and row.propagation_factor != 1.0:
        problems.append(("propagation_factor", "Dependency hard harus bernilai 1.0"))
    return problems


def _assignment_rules(row: Any) -> Problems:
    return _not_before(row, "released_date", "assigned_date")


def _workload_rules(row: Any) -> Problems:
    return (
        _not_before(row, "started_date", "received_date")
        + _not_before(row, "resolved_date", "received_date")
        + _not_before(row, "resolved_date", "started_date")
    )


def _availability_rules(row: Any) -> Problems:
    return _not_before(row, "date_end", "date_start")


ROW_RULES: dict[str, Callable[[Any], Problems]] = {
    "projects": _project_rules,
    "complexity_levels": _complexity_rules,
    "tasks": _task_rules,
    "task_dependencies": _dependency_rules,
    "assignments": _assignment_rules,
    "workload_items": _workload_rules,
    "availability": _availability_rules,
}


# --- aturan antarbaris dan antartabel ---------------------------------------------------


def _pk(table: str, row: SnapshotRow) -> str:
    return str(getattr(row, PRIMARY_KEYS[table]))


def _check_foreign_keys(parsed: dict[str, _Table], errors: _Errors) -> None:
    for table, references in FOREIGN_KEYS.items():
        for column, target in references:
            if parsed[target].broken:
                continue  # berkas tujuannya sudah dilaporkan rusak atau hilang
            known = parsed[target].ids
            for line, row in parsed[table].rows:
                value = getattr(row, column)
                if value is not None and value not in known:
                    errors.add(
                        table,
                        line,
                        _pk(table, row),
                        column,
                        f"{TARGET_LABELS[target]} {value} tidak ada di {file_name(target)}",
                    )


def _check_secondary_roles(parsed: dict[str, _Table], errors: _Errors) -> None:
    if parsed["roles"].broken:
        return
    known = parsed["roles"].ids
    for line, row in parsed["resources"].rows:
        unknown = [r for r in (row.secondary_role_ids or "").split(";") if r and r not in known]
        if unknown:
            errors.add(
                "resources",
                line,
                row.resource_id,
                "secondary_role_ids",
                f"Peran {', '.join(unknown)} tidak ada di {file_name('roles')}",
            )


def _check_active_priority(projects: _Table, errors: _Errors) -> None:
    """Sama dengan index unik parsial uq_projects_active_priority (DIG-20)."""
    first: dict[int, tuple[int, str]] = {}
    for line, row in projects.rows:
        if row.status not in ACTIVE_PROJECT_STATUSES:
            continue
        if row.priority_rank in first:
            other_line, other_id = first[row.priority_rank]
            errors.add(
                "projects",
                line,
                row.project_id,
                "priority_rank",
                f"Prioritas {row.priority_rank} sudah dipakai proyek aktif {other_id} "
                f"(baris {other_line}); prioritas proyek aktif harus unik",
            )
        else:
            first[row.priority_rank] = (line, row.project_id)


def _check_dependency_pairs(dependencies: _Table, errors: _Errors) -> None:
    first: dict[tuple[str, str], tuple[int, str]] = {}
    for line, row in dependencies.rows:
        pair = (row.task_id, row.depends_on_task_id)
        if pair in first:
            other_line, other_id = first[pair]
            errors.add(
                "task_dependencies",
                line,
                row.dependency_id,
                "depends_on_task_id",
                f"Dependency yang sama sudah ada: {other_id} (baris {other_line})",
            )
        else:
            first[pair] = (line, row.dependency_id)


def find_cycles(edges: list[tuple[str, str, int, str]]) -> list[tuple[int, str, list[str]]]:
    """Cari dependency melingkar dengan DFS iteratif.

    `edges` berisi (task_id, depends_on_task_id, baris, dependency_id). Hasilnya satu
    (baris, dependency_id, jalur) per siklus yang ditemukan, misalnya jalur [A, B, A]: A
    bergantung pada B dan B bergantung pada A. Siklus tidak punya satu sisi yang "salah", jadi
    yang dilaporkan adalah sisi siklus yang paling bawah di berkas, biasanya baris terbaru.
    """
    graph: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for task, depends_on, line, dependency_id in edges:
        graph[task].append((depends_on, line, dependency_id))
    white, gray, black = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    cycles: list[tuple[int, str, list[str]]] = []
    for start in sorted(graph, key=_sort_key):
        if color[start] != white:
            continue
        color[start] = gray
        path = [start]
        # Sisi (baris, dependency_id) yang dilalui untuk masuk ke path[k].
        via: list[tuple[int, str]] = [(0, "")]
        stack = [iter(graph[start])]
        while stack:
            for target, line, dependency_id in stack[-1]:
                if color[target] == gray:
                    index = path.index(target)
                    last_line, last_id = max(via[index + 1 :] + [(line, dependency_id)])
                    cycles.append((last_line, last_id, path[index:] + [target]))
                elif color[target] == white:
                    color[target] = gray
                    path.append(target)
                    via.append((line, dependency_id))
                    stack.append(iter(graph[target]))
                    break
            else:
                color[path.pop()] = black
                via.pop()
                stack.pop()
    return cycles


def _check_dependency_cycles(dependencies: _Table, errors: _Errors) -> None:
    edges = [
        (row.task_id, row.depends_on_task_id, line, row.dependency_id)
        for line, row in dependencies.rows
    ]
    for line, dependency_id, path in find_cycles(edges):
        errors.add(
            "task_dependencies",
            line,
            dependency_id,
            "depends_on_task_id",
            "Dependency melingkar: " + " -> ".join(path) + " (-> berarti bergantung pada)",
        )


def _check_availability_overlap(availability: _Table, errors: _Errors) -> None:
    """Sama dengan exclusion constraint ex_availability_no_overlap (DIG-106).

    Rentang tanggal inklusif. rotation_in dikecualikan karena berarti anggota hadir.
    """
    by_resource: dict[str, list[tuple[date, date, int, str]]] = defaultdict(list)
    for line, row in availability.rows:
        if row.absence_type != AbsenceType.ROTATION_IN:
            by_resource[row.resource_id].append(
                (row.date_start, row.date_end, line, row.availability_id)
            )
    for resource_id, periods in by_resource.items():
        periods.sort()
        _, latest_end, latest_line, latest_id = periods[0]
        for start, end, line, availability_id in periods[1:]:
            if start <= latest_end:
                errors.add(
                    "availability",
                    line,
                    availability_id,
                    "date_start",
                    f"Periode bertumpuk dengan {latest_id} (baris {latest_line}) milik "
                    f"{resource_id}; ketidakhadiran satu anggota tidak boleh bertumpuk",
                )
            if end > latest_end:
                latest_end, latest_line, latest_id = end, line, availability_id
