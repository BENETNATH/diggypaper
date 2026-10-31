"""import_jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 09:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import ClauseElement

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON: plain JSON (SQLite), JSONB on PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


# --- DB-side default UUID (same pattern as 0001) -----------------
# SQLite rejects `DEFAULT gen_random_uuid()` (unknown function): the default is
# compiled per dialect, exactly like in app/db/models.py.
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
    # M3 (lot 4A): import_jobs table - persists the PubMed query + job
    # status + result/error (MODULE 6: reproducibility).
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="importjobstatus",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_project_id", "import_jobs", ["project_id"])

    # m3 (lot 4A): articles.original_language -> free VARCHAR (not an enum) -
    # languages outside en/fr/de/es keep their raw PubMed label (no
    # forcing 'en'). The `languagecode` CHECK is removed.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("languagecode", "articles", type_="check")
    else:
        # SQLite: the CHECK is in the column DDL -> batch (recreate).
        with op.batch_alter_table("articles") as batch_op:
            batch_op.drop_constraint("languagecode", type_="check")

    # M2 (lot 4A): dedup on re-import - one article per
    # (project_id, pmid), pmid non NULL (partial unique index).
    op.create_index(
        "uq_articles_project_pmid",
        "articles",
        ["project_id", "pmid"],
        unique=True,
        postgresql_where=sa.text("pmid IS NOT NULL"),
        sqlite_where=sa.text("pmid IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_articles_project_pmid",
        table_name="articles",
        postgresql_where=sa.text("pmid IS NOT NULL"),
        sqlite_where=sa.text("pmid IS NOT NULL"),
    )
    # m3: restore the enum CHECK on original_language.
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "languagecode", "articles", "original_language IN ('fr', 'en', 'de', 'es')"
        )
    else:
        with op.batch_alter_table("articles") as batch_op:
            batch_op.create_check_constraint(
                "languagecode", "original_language IN ('fr', 'en', 'de', 'es')"
            )
    op.drop_index("ix_import_jobs_project_id", table_name="import_jobs")
    op.drop_table("import_jobs")
