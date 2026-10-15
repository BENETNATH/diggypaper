"""projects.question/fields, benchmark progress, inference prefilter, backend ONNX_LOCAL

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nouveau backend ONNX_LOCAL : le CHECK de l'enum doit l'accepter.
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        "(backend_type IN ('OLLAMA','LLAMA_CPP','TRANSFORMERS','ONNX_LOCAL'))"
    )
    op.add_column("inference_jobs", sa.Column("prefilter_model", sa.String(), nullable=True))
    op.add_column("benchmark_runs", sa.Column("progress", postgresql.JSONB(), nullable=True))
    op.add_column("projects", sa.Column("question", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("fields", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "fields")
    op.drop_column("projects", "question")
    op.drop_column("benchmark_runs", "progress")
    op.drop_column("inference_jobs", "prefilter_model")
    op.execute("DELETE FROM model_registry WHERE backend_type = 'ONNX_LOCAL'")
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        "(backend_type IN ('OLLAMA','LLAMA_CPP','TRANSFORMERS'))"
    )
