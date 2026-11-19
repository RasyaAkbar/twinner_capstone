"""twinner.baseline_snapshot: baseline untuk skenario what-if

Jira: SCRUM-15 (tWinner). Tabel pertama tWinner di schema `twinner` (DATA_CONTRACT bagian 4,
schema dibuat migrasi 0014). Satu baris `baseline_snapshot` per impor, beserta isi D-01 s.d.
D-11 sebagai array JSONB per tabel di `baseline_snapshot_table`. Snapshot tidak pernah diubah
setelah dibuat; skenario menunjuk `snapshot_id`.

Tanpa foreign key ke schema `public`, dan tanpa GRANT: default privileges jc_app (0014) hanya
berlaku di schema `public`, sehingga jc_app tetap tidak punya akses ke tabel di sini.

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "twinner"
TABLES = (
    "roles",
    "complexity_levels",
    "holidays",
    "projects",
    "resources",
    "tasks",
    "task_dependencies",
    "assignments",
    "workload_items",
    "availability",
    "change_events",
)


def upgrade() -> None:
    op.create_table(
        "baseline_snapshot",
        sa.Column("snapshot_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("data_source", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("imported_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("row_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "data_source IN ('synthetic', 'puti')",
            name=op.f("ck_baseline_snapshot_data_source_valid"),
        ),
        sa.CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name=op.f("ck_baseline_snapshot_checksum_format")
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_baseline_snapshot")),
        schema=SCHEMA,
    )
    op.create_table(
        "baseline_snapshot_table",
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("table_name", sa.String(length=40), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            f"table_name IN ({', '.join(repr(t) for t in TABLES)})",
            name=op.f("ck_baseline_snapshot_table_table_name_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(data) = 'array'", name=op.f("ck_baseline_snapshot_table_data_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{SCHEMA}.baseline_snapshot.snapshot_id"],
            name=op.f("fk_baseline_snapshot_table_snapshot_id_baseline_snapshot"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "table_name", name=op.f("pk_baseline_snapshot_table")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("baseline_snapshot_table", schema=SCHEMA)
    op.drop_table("baseline_snapshot", schema=SCHEMA)
