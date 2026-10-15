"""inference_jobs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import ClauseElement

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSON portable : JSON générique (SQLite), JSONB sur PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


# --- UUID par défaut côté base (même pattern que 0001/0002) --------------
class _UuidGenExpr(ClauseElement):
    __visit_name__ = "uuid_gen_expr"


@compiles(_UuidGenExpr, "postgresql")
def _uuid_gen_pg(element, compiler, **kw):
    return "gen_random_uuid()"


@compiles(_UuidGenExpr, "sqlite")
def _uuid_gen_sqlite(element, compiler, **kw):
    return "NULL"


def _uuid_default() -> DefaultClause:
    return DefaultClause(_UuidGenExpr())


def upgrade() -> None:
    """Upgrade schema."""
    # MODULE 4 (lot 5) : table inference_jobs — persiste le statut du job
    # d'inférence massive + les statistiques clés (MODULE 6 : reproductibilité,
    # notifications in-app par polling).
    op.create_table(
        "inference_jobs",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_template_id", sa.Uuid(), nullable=True),
        sa.Column("model_used", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="inferencejobstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="PENDING",
            nullable=True,
        ),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_template_id"], ["prompt_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inference_jobs_project_id", "inference_jobs", ["project_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_inference_jobs_project_id", table_name="inference_jobs")
    op.drop_table("inference_jobs")
