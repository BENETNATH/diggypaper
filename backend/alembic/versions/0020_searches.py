"""Recherches : question + lot d'articles ; prédictions, relectures, analyses et gold rattachés

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-16 09:00:00.000000

"""

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _hash(question, fields) -> str:
    """Même empreinte que app.searches.definition_hash (figée ici : une migration ne dépend
    pas du code courant)."""
    blob = json.dumps([(question or "").strip(), fields], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def upgrade() -> None:
    op.create_table(
        "searches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("question", sa.Text()),
        sa.Column("fields", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column(
            "text_sections", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
        ),
        sa.Column("read_windows", sa.Integer()),
        sa.Column("definition_hash", sa.String()),
        sa.Column("kind", sa.String(), server_default="standard", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lot_all", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("searches.id", ondelete="SET NULL")),
        sa.Column(
            "parent_spec", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_searches_project_id", "searches", ["project_id"])
    op.create_table(
        "search_articles",
        sa.Column(
            "search_id",
            sa.Uuid(),
            sa.ForeignKey("searches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    for table in (
        "article_predictions",
        "inference_jobs",
        "human_annotations",
        "gold_articles",
        "export_jobs",
    ):
        op.add_column(
            table,
            sa.Column("search_id", sa.Uuid(), sa.ForeignKey("searches.id", ondelete="CASCADE")),
        )
    op.add_column(
        "human_annotations",
        sa.Column("blind", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    op.add_column(
        "benchmark_runs",
        sa.Column("search_id", sa.Uuid(), sa.ForeignKey("searches.id", ondelete="CASCADE")),
    )
    op.add_column("benchmark_runs", sa.Column("variants", json_type))
    op.add_column("benchmark_runs", sa.Column("options", json_type))
    op.add_column("benchmark_results", sa.Column("variant", sa.String()))
    op.add_column("benchmark_results", sa.Column("n_scored", sa.Integer()))
    op.add_column("benchmark_results", sa.Column("details", json_type))
    op.add_column("inference_jobs", sa.Column("options", json_type))
    op.create_table(
        "benchmark_predictions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "benchmark_run_id",
            sa.Uuid(),
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("variant", sa.String()),
        sa.Column("decision_json", json_type),
        sa.Column("confidence_score", sa.Float()),
        sa.Column("error", sa.String()),
        sa.Column("seconds", sa.Float()),
    )
    op.create_index(
        "ix_benchmark_predictions_run_id", "benchmark_predictions", ["benchmark_run_id"]
    )

    op.add_column(
        "gold_articles",
        sa.Column(
            "source_annotation_id",
            sa.Uuid(),
            sa.ForeignKey("human_annotations.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "gold_articles",
        sa.Column("added_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )

    # Données existantes : une « Recherche initiale » par projet, sur tout le corpus.
    bind = op.get_bind()
    projects = bind.execute(
        sa.text("SELECT id, question, fields, text_sections, read_windows FROM projects")
    ).all()
    searches = sa.table(
        "searches",
        sa.column("id", sa.Uuid()),
        sa.column("project_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("question", sa.Text()),
        sa.column("fields", sa.JSON()),
        sa.column("text_sections", sa.JSON()),
        sa.column("read_windows", sa.Integer()),
        sa.column("definition_hash", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("lot_all", sa.Boolean()),
    )
    for p in projects:
        fields = p.fields if not isinstance(p.fields, str) else json.loads(p.fields)
        sections = (
            p.text_sections if not isinstance(p.text_sections, str) else json.loads(p.text_sections)
        )
        bind.execute(
            sa.insert(searches).values(
                id=uuid.uuid4(),
                project_id=p.id,
                name="Recherche initiale",
                question=p.question,
                fields=fields,
                text_sections=sections,
                read_windows=p.read_windows,
                definition_hash=_hash(p.question, fields),
                is_default=True,
                lot_all=True,
            )
        )
    default = "(SELECT s.id FROM searches s WHERE s.is_default AND s.project_id = {pid})"
    op.execute(
        "UPDATE article_predictions SET search_id = "
        + default.format(
            pid="(SELECT a.project_id FROM articles a WHERE a.id = article_predictions.article_id)"
        )
    )
    op.execute(
        "UPDATE human_annotations SET search_id = "
        + default.format(
            pid="(SELECT a.project_id FROM articles a WHERE a.id = human_annotations.article_id)"
        )
    )
    op.execute(
        "UPDATE inference_jobs SET search_id = " + default.format(pid="inference_jobs.project_id")
    )
    op.execute(
        "UPDATE gold_articles SET search_id = " + default.format(pid="gold_articles.project_id")
    )


def downgrade() -> None:
    op.drop_table("benchmark_predictions")
    op.drop_column("inference_jobs", "options")
    for col in ("details", "n_scored", "variant"):
        op.drop_column("benchmark_results", col)
    for col in ("options", "variants", "search_id"):
        op.drop_column("benchmark_runs", col)
    op.drop_column("gold_articles", "added_by")
    op.drop_column("gold_articles", "source_annotation_id")
    op.drop_column("human_annotations", "blind")
    for table in (
        "export_jobs",
        "gold_articles",
        "human_annotations",
        "inference_jobs",
        "article_predictions",
    ):
        op.drop_column(table, "search_id")
    op.drop_table("search_articles")
    op.drop_index("ix_searches_project_id", table_name="searches")
    op.drop_table("searches")
