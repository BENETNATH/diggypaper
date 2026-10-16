"""Retrait des modèles de décision (Laya, Kev, encodeurs ONNX)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-17 12:00:00.000000

Les modèles SYSTEMONE / ONNX_LOCAL du registre sont désactivés (les prédictions déjà produites
restent : ``model_used`` est un texte libre) et ne servent plus de pré-filtre. Les valeurs de
l'enum restent en base (PostgreSQL ne sait pas en retirer une proprement).
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
