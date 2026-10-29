"""Recherche rapide : l'identifiant natif (native_id) entre dans le blob.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-29 17:00:00.000000

Phase 3 multi-sources : les articles hors PubMed portent leur identifiant natif
(W…, doi normalisé…) dans ``native_id``. ``search_blob()`` (app/api/v1/projects.py)
l'inclut après le ``pmid`` : l'index trigramme 0028 est recréé sur la nouvelle
expression (sinon il ne sert plus). PG-only, comme 0028 (extensions pg_trgm /
unaccent).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Doit rester identique à ``search_blob()`` (app/api/v1/projects.py).
_BLOB = (
    "lower(immutable_unaccent(coalesce(title, '') || ' ' || coalesce(journal, '') || ' ' || "
    "coalesce(pmid, '') || ' ' || coalesce(native_id, '') || ' ' || coalesce(doi, '') || ' ' || "
    "coalesce(CAST(authors AS TEXT), '')))"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_articles_search_trgm")
    op.execute(
        f"CREATE INDEX ix_articles_search_trgm ON articles USING gin (({_BLOB}) gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_articles_search_trgm")
    op.execute(
        "CREATE INDEX ix_articles_search_trgm ON articles USING gin (( "
        "lower(immutable_unaccent(coalesce(title, '') || ' ' || coalesce(journal, '') || ' ' || "
        "coalesce(pmid, '') || ' ' || coalesce(doi, '') || ' ' || coalesce(CAST(authors AS TEXT), "
        "''))))) gin_trgm_ops"
    )
