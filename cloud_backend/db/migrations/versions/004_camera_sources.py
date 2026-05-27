"""D.2A: camera source registry and classroom-scoped recognition log fields."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_camera_sources"
down_revision: Union[str, None] = "003_recognition_event_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "camera_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("camera_id", sa.String(length=100), nullable=False),
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["classroom_id"], ["classrooms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("camera_id"),
    )
    op.create_index(
        op.f("ix_camera_sources_camera_id"),
        "camera_sources",
        ["camera_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_camera_sources_classroom_id"),
        "camera_sources",
        ["classroom_id"],
        unique=False,
    )
    op.add_column(
        "recognition_event_log",
        sa.Column("classroom_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "recognition_event_log",
        sa.Column("camera_id", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_recognition_event_log_classroom_id",
        "recognition_event_log",
        "classrooms",
        ["classroom_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_recognition_event_log_classroom_id"),
        "recognition_event_log",
        ["classroom_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recognition_event_log_camera_id"),
        "recognition_event_log",
        ["camera_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recognition_event_log_camera_id"),
        table_name="recognition_event_log",
    )
    op.drop_index(
        op.f("ix_recognition_event_log_classroom_id"),
        table_name="recognition_event_log",
    )
    op.drop_constraint(
        "fk_recognition_event_log_classroom_id",
        "recognition_event_log",
        type_="foreignkey",
    )
    op.drop_column("recognition_event_log", "camera_id")
    op.drop_column("recognition_event_log", "classroom_id")
    op.drop_index(op.f("ix_camera_sources_classroom_id"), table_name="camera_sources")
    op.drop_index(op.f("ix_camera_sources_camera_id"), table_name="camera_sources")
    op.drop_table("camera_sources")
