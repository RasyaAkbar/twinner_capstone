"""twinner.scenario: skenario what-if Scenario Manager

Jira: SCRUM-22 (tWinner). Satu baris per skenario dengan situasi keputusan (Tabel 4.2),
parameter situasi sebagai JSONB (Tabel 4.4), status draft/saved, dan baseline snapshot yang
menjadi dasarnya beserta penanda asal datanya.

Tanpa foreign key ke schema `public` dan tanpa GRANT, sama seperti 0021.

Revision ID: 0022
Revises: 0021
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "twinner"
SITUATIONS = (
    "change_request",
    "member_absence",
    "new_member",
    "member_rotation",
    "task_overrun",
    "task_blocker",
    "priority_change",
    "critical_bug",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "scenario",
        sa.Column("scenario_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("situation", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("data_source", sa.String(length=20), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("duplicated_from", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _one_of("situation", SITUATIONS), name=op.f("ck_scenario_situation_valid")
        ),
        sa.CheckConstraint(
            _one_of("status", ("draft", "saved")), name=op.f("ck_scenario_status_valid")
        ),
        sa.CheckConstraint(
            _one_of("data_source", ("synthetic", "puti")),
            name=op.f("ck_scenario_data_source_valid"),
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_scenario_name_not_blank")),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'", name=op.f("ck_scenario_parameters_is_object")
        ),
        sa.CheckConstraint(
            "status <> 'saved' OR saved_at IS NOT NULL",
            name=op.f("ck_scenario_saved_has_saved_at"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{SCHEMA}.baseline_snapshot.snapshot_id"],
            name=op.f("fk_scenario_snapshot_id_baseline_snapshot"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["duplicated_from"],
            [f"{SCHEMA}.scenario.scenario_id"],
            name=op.f("fk_scenario_duplicated_from_scenario"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("scenario_id", name=op.f("pk_scenario")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("scenario", schema=SCHEMA)
