"""twinner.simulation_run: hasil simulasi tersimpan

Jira: SCRUM-28 (Run and save) dan SCRUM-29 (melihat hasil simulasi). Satu baris per simulasi
yang disimpan: parameter yang disimulasikan, tanggal mulai, jumlah replikasi dan benihnya,
daftar KPI, dan hasil lengkap untuk layar Simulation result. Hasil terhapus bersama skenarionya.

Tanpa foreign key ke schema `public` dan tanpa GRANT, sama seperti 0021 dan 0022.

Revision ID: 0023
Revises: 0022
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "twinner"


def upgrade() -> None:
    op.create_table(
        "simulation_run",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("scenario_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("situation", sa.String(length=40), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("replications", sa.Integer(), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("kpis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name=op.f("ck_simulation_run_parameters_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(kpis) = 'array'", name=op.f("ck_simulation_run_kpis_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result) = 'object'", name=op.f("ck_simulation_run_result_is_object")
        ),
        sa.CheckConstraint(
            "replications IS NULL OR replications > 0",
            name=op.f("ck_simulation_run_replications_positive"),
        ),
        sa.CheckConstraint(
            "warning_count >= 0", name=op.f("ck_simulation_run_warning_count_min")
        ),
        sa.CheckConstraint("duration_ms >= 0", name=op.f("ck_simulation_run_duration_ms_min")),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            [f"{SCHEMA}.scenario.scenario_id"],
            name=op.f("fk_simulation_run_scenario_id_scenario"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{SCHEMA}.baseline_snapshot.snapshot_id"],
            name=op.f("fk_simulation_run_snapshot_id_baseline_snapshot"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_simulation_run")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("simulation_run", schema=SCHEMA)
