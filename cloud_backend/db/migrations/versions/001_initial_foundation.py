"""Initial foundation schema: classrooms, subjects, students, enrollments, lectures."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "classrooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("building", sa.String(length=100), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("credit_hours", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "students",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_no", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("gallery_identity", sa.String(length=200), nullable=True),
        sa.Column("enrolled_since", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_no"),
    )
    op.create_table(
        "enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("semester", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_enrollments_student_id"),
        "enrollments",
        ["student_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_enrollments_subject_id"),
        "enrollments",
        ["subject_id"],
        unique=False,
    )
    op.create_table(
        "lectures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "attendance_window_minutes",
            sa.Integer(),
            nullable=False,
            server_default="15",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["classroom_id"], ["classrooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_lectures_classroom_id"),
        "lectures",
        ["classroom_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_lectures_subject_id"),
        "lectures",
        ["subject_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lectures_subject_id"), table_name="lectures")
    op.drop_index(op.f("ix_lectures_classroom_id"), table_name="lectures")
    op.drop_table("lectures")
    op.drop_index(op.f("ix_enrollments_subject_id"), table_name="enrollments")
    op.drop_index(op.f("ix_enrollments_student_id"), table_name="enrollments")
    op.drop_table("enrollments")
    op.drop_table("students")
    op.drop_table("subjects")
    op.drop_table("classrooms")
