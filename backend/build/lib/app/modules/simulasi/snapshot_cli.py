"""Impor baseline snapshot dari folder berisi 11 CSV final sources (SCRUM-15).

Contoh dari folder backend, dengan data sintetis (docs/SYNTHETIC_DATA.md):
    python -m synthetic --out synthetic-output --seed 42 --as-of 2026-11-06
    python -m app.modules.simulasi.snapshot_cli synthetic-output/final_sources \\
        --source synthetic --label "sintetis seed 42 per 2026-11-06"

Aturan pemeriksaan sama dengan POST /api/v1/simulasi/baseline-snapshots/import.
Kode keluar: 0 berhasil, 1 ada kesalahan data (tidak ada yang disimpan), 2 folder tidak terbaca.
"""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.modules.simulasi.snapshot_schemas import SnapshotImportRequest


def read_folder(folder: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8-sig") for path in sorted(folder.glob("*.csv"))
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.simulasi.snapshot_cli", description=__doc__
    )
    parser.add_argument("folder", type=Path, help="folder berisi projects.csv ... holidays.csv")
    parser.add_argument("--source", choices=("synthetic", "puti"), required=True)
    parser.add_argument("--label", default=None)
    parser.add_argument("--dry-run", action="store_true", help="hanya memeriksa, tanpa menyimpan")
    args = parser.parse_args(argv)

    if not args.folder.is_dir():
        print(f"folder {args.folder} tidak ada", file=sys.stderr)
        return 2
    try:
        files = read_folder(args.folder)
        request = SnapshotImportRequest(
            data_source=args.source, label=args.label, files=files, dry_run=args.dry_run
        )
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        print(f"berkas tidak terbaca: {exc}", file=sys.stderr)
        return 2

    from app.db.session import SessionLocal
    from app.modules.simulasi.snapshot_service import import_snapshot

    with SessionLocal() as db:
        result = import_snapshot(db, request, imported_by="cli")

    for error in result.errors:
        place = ":".join(str(part) for part in (error.file, error.row) if part is not None)
        column = f" [{error.field}]" if error.field else ""
        record = f" {error.record_id}" if error.record_id else ""
        print(f"{place or 'permintaan'}{record}{column}: {error.message}", file=sys.stderr)
    if result.error_count:
        shown = len(result.errors)
        print(f"{result.error_count} kesalahan ({shown} ditampilkan); tidak ada yang disimpan")
        return 1
    rows = ", ".join(f"{table} {count}" for table, count in result.row_counts.items())
    if result.snapshot is None:
        print(f"lolos pemeriksaan (dry run, tidak disimpan): {rows}")
    else:
        print(f"snapshot {result.snapshot.snapshot_id} dibuat ({args.source}): {rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
