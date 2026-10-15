"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-12 07:33:02.254388

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import ClauseElement

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSON portable : JSON générique (SQLite), JSONB sur PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


# --- UUID par défaut côté base (spec : DEFAULT gen_random_uuid()) -----------------
# SQLite rejette `DEFAULT gen_random_uuid()` (fonction inconnue) : le défaut est donc
# compilé par dialecte, exactement comme dans app/db/models.py. La migration reste
# auto-suffisante (pas d'import du code applicatif) : PG → gen_random_uuid(),
# SQLite → NULL (le défaut Python uuid4 de l'ORM couvre les inserts côté app).
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
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "ADMIN",
                "RESEARCHER",
                name="userrole",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="RESEARCHER",
            nullable=True,
        ),
        sa.Column(
            "language_preference",
            sa.Enum(
                "fr",
                "en",
                "de",
                "es",
                name="languagecode",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="fr",
            nullable=True,
        ),
        sa.Column(
            "data_consent_given", sa.Boolean(), server_default=sa.text("false"), nullable=True
        ),
        sa.Column("anonymized_hash", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "FROZEN",
                "PROCESSING",
                "COMPLETED",
                name="projectstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="DRAFT",
            nullable=True,
        ),
        sa.Column(
            "default_language",
            sa.Enum(
                "fr",
                "en",
                "de",
                "es",
                name="languagecode",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="en",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role_in_project",
            sa.Enum(
                "LEAD",
                "ANNOTATOR",
                name="projectmemberrole",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="ANNOTATOR",
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("pmid", sa.String(), nullable=True),
        sa.Column("pmcid", sa.String(), nullable=True),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("methods_text", sa.Text(), nullable=True),
        sa.Column(
            "original_language",
            sa.Enum(
                "fr",
                "en",
                "de",
                "es",
                name="languagecode",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="en",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "backend_type",
            sa.Enum(
                "OLLAMA",
                "LLAMA_CPP",
                "TRANSFORMERS",
                name="backendtype",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="OLLAMA",
            nullable=True,
        ),
        sa.Column("model_identifier", sa.String(), nullable=False),
        sa.Column("ram_usage_mb", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_template_id", sa.Uuid(), nullable=True),
        sa.Column("models_tested", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="benchmarkstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="PENDING",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("benchmark_run_id", sa.Uuid(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("execution_time_seconds", sa.Float(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("precision_score", sa.Float(), nullable=True),
        sa.Column("recall_score", sa.Float(), nullable=True),
        sa.Column("f1_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["benchmark_run_id"], ["benchmark_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt_schema", sa.Text(), nullable=True),
        sa.Column(
            "prompt_type",
            sa.Enum(
                "INCLUSION",
                "EXTRACTION",
                name="prompttype",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="EXTRACTION",
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "TESTED",
                "PRODUCTION",
                name="promptstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="DRAFT",
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "gold_articles",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("ground_truth_json", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "article_predictions",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_template_id", sa.Uuid(), nullable=True),
        sa.Column("model_used", sa.String(), nullable=False),
        sa.Column("decision_json", JSONB, nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("evidence_quotes", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "AUTO_ACCEPTED",
                "AUTO_REJECTED",
                "NEEDS_HUMAN_REVIEW",
                name="predictionstatus",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="NEEDS_HUMAN_REVIEW",
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_template_id"], ["prompt_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "human_annotations",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("annotator_hash", sa.String(), nullable=True),
        sa.Column("decision_json", JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "GLOBAL",
                "PROJECT",
                "USER",
                name="settingscope",
                native_enum=False,
                create_constraint=True,
                length=None,
            ),
            server_default="GLOBAL",
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("key_name", sa.String(), nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "project_id", "user_id", "key_name"),
    )

    # Index sur les colonnes FK (pratique 2026 : indexer les colonnes de jointure).
    op.create_index("ix_articles_project_id", "articles", ["project_id"])
    op.create_index("ix_benchmark_runs_project_id", "benchmark_runs", ["project_id"])
    op.create_index(
        "ix_benchmark_results_benchmark_run_id", "benchmark_results", ["benchmark_run_id"]
    )
    op.create_index("ix_prompt_templates_project_id", "prompt_templates", ["project_id"])
    op.create_index("ix_gold_articles_project_id", "gold_articles", ["project_id"])
    op.create_index("ix_gold_articles_article_id", "gold_articles", ["article_id"])
    op.create_index("ix_article_predictions_article_id", "article_predictions", ["article_id"])
    op.create_index(
        "ix_article_predictions_prompt_template_id", "article_predictions", ["prompt_template_id"]
    )
    op.create_index("ix_human_annotations_article_id", "human_annotations", ["article_id"])
    op.create_index("ix_human_annotations_user_id", "human_annotations", ["user_id"])

    # Unicité stricte de system_settings par scope : la UniqueConstraint ci-dessus a
    # des trous NULL (NULL ≠ NULL en UNIQUE) ; ces index uniques partiels les ferment.
    # sqlite_where est requis pour que l'unicité soit effective (et testable) sur
    # SQLite ; postgresql_where couvre le DDL de production.
    op.create_index(
        "uq_settings_global",
        "system_settings",
        ["key_name"],
        unique=True,
        postgresql_where=sa.text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
        sqlite_where=sa.text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
    )
    op.create_index(
        "uq_settings_project",
        "system_settings",
        ["project_id", "key_name"],
        unique=True,
        postgresql_where=sa.text("scope = 'PROJECT' AND user_id IS NULL"),
        sqlite_where=sa.text("scope = 'PROJECT' AND user_id IS NULL"),
    )
    op.create_index(
        "uq_settings_user",
        "system_settings",
        ["user_id", "key_name"],
        unique=True,
        postgresql_where=sa.text("scope = 'USER' AND project_id IS NULL"),
        sqlite_where=sa.text("scope = 'USER' AND project_id IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_settings_user", table_name="system_settings")
    op.drop_index("uq_settings_project", table_name="system_settings")
    op.drop_index("uq_settings_global", table_name="system_settings")
    op.drop_index("ix_human_annotations_user_id", table_name="human_annotations")
    op.drop_index("ix_human_annotations_article_id", table_name="human_annotations")
    op.drop_index("ix_article_predictions_prompt_template_id", table_name="article_predictions")
    op.drop_index("ix_article_predictions_article_id", table_name="article_predictions")
    op.drop_index("ix_gold_articles_article_id", table_name="gold_articles")
    op.drop_index("ix_gold_articles_project_id", table_name="gold_articles")
    op.drop_index("ix_prompt_templates_project_id", table_name="prompt_templates")
    op.drop_index("ix_benchmark_results_benchmark_run_id", table_name="benchmark_results")
    op.drop_index("ix_benchmark_runs_project_id", table_name="benchmark_runs")
    op.drop_index("ix_articles_project_id", table_name="articles")
    op.drop_table("system_settings")
    op.drop_table("human_annotations")
    op.drop_table("article_predictions")
    op.drop_table("gold_articles")
    op.drop_table("prompt_templates")
    op.drop_table("benchmark_results")
    op.drop_table("benchmark_runs")
    op.drop_table("model_registry")
    op.drop_table("articles")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
