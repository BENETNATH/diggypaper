"""Removal of the decision models (Laya, Kev, ONNX encoders)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-17 12:00:00.000000

The SYSTEMONE / ONNX_LOCAL registry models are disabled (predictions already produced
stay: ``model_used`` is free text) and no longer serve as pre-filter. The enum values
stay in the database (PostgreSQL cannot drop them cleanly).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEAD = "(SELECT name FROM model_registry WHERE backend_type IN ('SYSTEMONE', 'ONNX_LOCAL'))"


def upgrade() -> None:
    op.execute(f"UPDATE inference_jobs SET prefilter_model = NULL WHERE prefilter_model IN {_DEAD}")
    op.execute(
        "UPDATE model_registry SET is_active = false "
        "WHERE backend_type IN ('SYSTEMONE', 'ONNX_LOCAL')"
    )


def downgrade() -> None:
    pass
