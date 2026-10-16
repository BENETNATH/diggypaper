"""Recherche rapide : sans accents et indexée (pg_trgm + unaccent)

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-18 09:00:00.000000

``immutable_unaccent`` (unaccent est seulement STABLE, donc inutilisable dans un index) et un
index trigramme sur « titre journal pmid doi auteurs » : la recherche par sous-chaîne de la liste
des résultats reste rapide sur des dizaines de milliers d'articles. L'expression doit rester
identique à ``search_blob()`` (app/api/v1/projects.py).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BLOB = (
    "lower(immutable_unaccent(coalesce(title, '') || ' ' || coalesce(journal, '') || ' ' || "
    "coalesce(pmid, '') || ' ' || coalesce(doi, '') || ' ' || coalesce(CAST(authors AS TEXT), '')))"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text AS "
        "$$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$ "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT"
    )
    op.execute(
        f"CREATE INDEX ix_articles_search_trgm ON articles USING gin (({_BLOB}) gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_articles_search_trgm")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
