"""model_registry.backend_type accepte SYSTEMONE (modèles de décision Laya / Kev)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        "(backend_type IN ('OLLAMA','LLAMA_CPP','TRANSFORMERS','ONNX_LOCAL','SYSTEMONE'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM model_registry WHERE backend_type = 'SYSTEMONE'")
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        "(backend_type IN ('OLLAMA','LLAMA_CPP','TRANSFORMERS','ONNX_LOCAL'))"
    )
