"""Attendance records and events schema for Phase 2."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_attendance_records"
down_revision: Union[str, None] = "001_initial_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attendance_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            sa.String(length=30),
            nullable=False,
            server_default="undetected",
        ),
        sa.Column("exception_type", sa.String(length=30), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lecture_id",
            "student_id",
            name="uq_attendance_records_lecture_student",
        ),
    )
    op.create_index(
        op.f("ix_attendance_records_lecture_id"),
        "attendance_records",
        ["lecture_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_records_student_id"),
        "attendance_records",
        ["student_id"],
        unique=False,
    )
    op.create_table(
        "attendance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attendance_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("from_state", sa.String(length=30), nullable=False),
        sa.Column("to_state", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attendance_record_id"],
            ["attendance_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_attendance_events_attendance_record_id"),
        "attendance_events",
        ["attendance_record_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_attendance_events_attendance_record_id"),
        table_name="attendance_events",
    )
    op.drop_table("attendance_events")
    op.drop_index(
        op.f("ix_attendance_records_student_id"),
        table_name="attendance_records",
    )
    op.drop_index(
        op.f("ix_attendance_records_lecture_id"),
        table_name="attendance_records",
    )
    op.drop_table("attendance_records")
