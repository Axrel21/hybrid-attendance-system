"""Phase 3: recognition_event_log table."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_recognition_event_log"
down_revision: Union[str, None] = "002_attendance_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recognition_event_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gallery_identity", sa.String(length=200), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["lecture_id"],
            ["lectures.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recognition_event_log_lecture_id"),
        "recognition_event_log",
        ["lecture_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recognition_event_log_gallery_identity"),
        "recognition_event_log",
        ["gallery_identity"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recognition_event_log_gallery_identity"),
        table_name="recognition_event_log",
    )
    op.drop_index(
        op.f("ix_recognition_event_log_lecture_id"),
        table_name="recognition_event_log",
    )
    op.drop_table("recognition_event_log")
