"""model_registry : backend OPENAI_API (URL + clé chiffrée + modèle d'une API externe)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'OLLAMA','LLAMA_CPP','TRANSFORMERS','ONNX_LOCAL','SYSTEMONE'"


def upgrade() -> None:
    op.add_column("model_registry", sa.Column("api_key_enc", sa.LargeBinary(), nullable=True))
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        f"(backend_type IN ({_OLD},'OPENAI_API'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM model_registry WHERE backend_type = 'OPENAI_API'")
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        f"ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK (backend_type IN ({_OLD}))"
    )
    op.drop_column("model_registry", "api_key_enc")
