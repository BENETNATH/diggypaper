"""Corpus watch (veille automatisée, spec 2026-11-07-veille-automatisee).

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-07

``watches``: a project re-runs a source query on a cadence, imports + analyzes
the new articles against a search's criteria, and alerts the lead when some
match. The watch links to the inference job it launched (``inference_job_id``)
so the post-inference hook can evaluate matches + e-mail. Imported articles
keep ``origin="watch"`` (already in the ``articles.origin`` free set) for PRISMA
traceability.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import ClauseElement, DefaultClause
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles

from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON: plain JSON (SQLite), JSONB on PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


# --- DB-side default UUID (same pattern as 0002) -----------------
# SQLite rejects `DEFAULT gen_random_uuid()` (unknown function): the default is
# compiled per dialect.
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
    op.create_table(
        "watches",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("match_threshold", sa.Float(), nullable=True),
        sa.Column("notify_all", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_matched", JSONB, nullable=True),
        sa.Column("inference_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "PAUSED",
                name="watchstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["search_id"], ["searches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inference_job_id"], ["inference_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watches_project_id", "watches", ["project_id"])
    op.create_index("ix_watches_inference_job_id", "watches", ["inference_job_id"])


def downgrade() -> None:
    op.drop_index("ix_watches_inference_job_id", table_name="watches")
    op.drop_index("ix_watches_project_id", table_name="watches")
    op.drop_table("watches")
