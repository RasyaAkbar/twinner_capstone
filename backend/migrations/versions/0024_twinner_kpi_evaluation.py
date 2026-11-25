"""twinner.kpi_evaluation: hasil evaluasi KPI per simulasi tersimpan

Jira: SCRUM-32 (KPI Calculator and Comparator). Satu baris per simulasi tersimpan dan
konfigurasi KPI: konfigurasi lengkap dan hash-nya, daftar KPI (baseline, skenario, selisih,
status, median dan rentang), dan hasil lengkap untuk dashboard evaluasi. Hasil terhapus bersama
simulasinya.

Tanpa foreign key ke schema `public` dan tanpa GRANT, sama seperti 0021 s.d. 0023.

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "twinner"


def upgrade() -> None:
    op.create_table(
        "kpi_evaluation",
        sa.Column("evaluation_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kpis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'", name=op.f("ck_kpi_evaluation_config_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(kpis) = 'array'", name=op.f("ck_kpi_evaluation_kpis_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result) = 'object'", name=op.f("ck_kpi_evaluation_result_is_object")
        ),
        sa.CheckConstraint("duration_ms >= 0", name=op.f("ck_kpi_evaluation_duration_ms_min")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.simulation_run.run_id"],
            name=op.f("fk_kpi_evaluation_run_id_simulation_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evaluation_id", name=op.f("pk_kpi_evaluation")),
        sa.UniqueConstraint(
            "run_id", "config_hash", name=op.f("uq_kpi_evaluation_run_id_config_hash")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("kpi_evaluation", schema=SCHEMA)
