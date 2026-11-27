"""twinner.dss_sweep dan twinner.dss_sweep_candidate: hasil sweep DSS per skenario

Jira: SCRUM-68. Satu baris `dss_sweep` per sweep DSS atas satu simulasi tersimpan (status,
kemajuan, bobot penilaian, penanda hasil), dan satu baris `dss_sweep_candidate` per kandidat
(nilai lever, parameter dan respons, skor per kriteria, perbandingan KPI, alasan). Sweep
terhapus bersama skenario atau simulasinya, kandidat bersama sweep-nya.

Tanpa foreign key ke schema `public` dan tanpa GRANT, sama seperti 0021 s.d. 0024.

Revision ID: 0025
Revises: 0024
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "twinner"
JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "dss_sweep",
        sa.Column("sweep_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("scenario_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("situation", sa.String(length=40), nullable=False),
        sa.Column("lever", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("total_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "completed_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("omitted_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("no_lever", sa.Text(), nullable=True),
        sa.Column("no_action_needed", sa.Boolean(), nullable=True),
        sa.Column("no_suitable_candidate", sa.Boolean(), nullable=True),
        sa.Column("weights", JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'cancelled')",
            name=op.f("ck_dss_sweep_status_valid"),
        ),
        sa.CheckConstraint("total_candidates >= 0", name=op.f("ck_dss_sweep_total_candidates_min")),
        sa.CheckConstraint(
            "completed_candidates >= 0 AND completed_candidates <= total_candidates",
            name=op.f("ck_dss_sweep_completed_candidates_range"),
        ),
        sa.CheckConstraint(
            "omitted_candidates >= 0", name=op.f("ck_dss_sweep_omitted_candidates_min")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(weights) = 'object'", name=op.f("ck_dss_sweep_weights_is_object")
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name=op.f("ck_dss_sweep_duration_ms_min")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            [f"{SCHEMA}.scenario.scenario_id"],
            name=op.f("fk_dss_sweep_scenario_id_scenario"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.simulation_run.run_id"],
            name=op.f("fk_dss_sweep_run_id_simulation_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sweep_id", name=op.f("pk_dss_sweep")),
        schema=SCHEMA,
    )
    op.create_table(
        "dss_sweep_candidate",
        sa.Column("candidate_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("sweep_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("value", JSON, nullable=True),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("parameters", JSON, nullable=False),
        sa.Column("actions", JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("criteria", JSON, nullable=False),
        sa.Column("vs_baseline", JSON, nullable=False),
        sa.Column("vs_current", JSON, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("replication", JSON, nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('done', 'failed', 'excluded')",
            name=op.f("ck_dss_sweep_candidate_status_valid"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_dss_sweep_candidate_position_min")),
        sa.CheckConstraint(
            "rank IS NULL OR rank >= 1", name=op.f("ck_dss_sweep_candidate_rank_min")
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name=op.f("ck_dss_sweep_candidate_score_range"),
        ),
        sa.CheckConstraint(
            "value IS NULL OR jsonb_typeof(value) IN ('string', 'number', 'null')",
            name=op.f("ck_dss_sweep_candidate_value_is_scalar"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name=op.f("ck_dss_sweep_candidate_parameters_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(actions) = 'array'", name=op.f("ck_dss_sweep_candidate_actions_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(criteria) = 'object'",
            name=op.f("ck_dss_sweep_candidate_criteria_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(vs_baseline) = 'array'",
            name=op.f("ck_dss_sweep_candidate_vs_baseline_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(vs_current) = 'array'",
            name=op.f("ck_dss_sweep_candidate_vs_current_is_array"),
        ),
        sa.CheckConstraint(
            "replication IS NULL OR jsonb_typeof(replication) = 'object'",
            name=op.f("ck_dss_sweep_candidate_replication_is_object"),
        ),
        sa.CheckConstraint("duration_ms >= 0", name=op.f("ck_dss_sweep_candidate_duration_ms_min")),
        sa.ForeignKeyConstraint(
            ["sweep_id"],
            [f"{SCHEMA}.dss_sweep.sweep_id"],
            name=op.f("fk_dss_sweep_candidate_sweep_id_dss_sweep"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_id", name=op.f("pk_dss_sweep_candidate")),
        sa.UniqueConstraint(
            "sweep_id", "position", name=op.f("uq_dss_sweep_candidate_sweep_id_position")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("dss_sweep_candidate", schema=SCHEMA)
    op.drop_table("dss_sweep", schema=SCHEMA)
