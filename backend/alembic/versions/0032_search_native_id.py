"""Quick search: the native identifier (native_id) joins the blob.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-29 17:00:00.000000

Phase 3 multi-sources: non-PubMed articles carry their native identifier
(W..., normalized doi...) in ``native_id``. ``search_blob()`` (app/api/v1/projects.py)
includes it after ``pmid``: the 0028 trigram index is recreated on the new
expression (otherwise it is useless). PG-only, like 0028 (pg_trgm /
unaccent extensions).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must stay identical to ``search_blob()`` (app/api/v1/projects.py).
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
