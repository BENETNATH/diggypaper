"""Removes LaTeX (preamble \\documentclass...) from already-imported PMC full texts

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17 19:00:00.000000

JATS formulas contain a LaTeX copy (``tex-math``) that was concatenated to the text.
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEX = re.compile(r"\\documentclass.*?\\end\{document\}", re.S)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, sections, methods_text FROM articles "
            "WHERE sections::text LIKE '%documentclass%' OR methods_text LIKE '%documentclass%'"
        )
    ).all()
    for art_id, sections, methods in rows:
        if isinstance(sections, str):
            sections = json.loads(sections)
        for sec in sections or []:
            sec["text"] = re.sub(
                r"\s+", " ", _TEX.sub(" [formula] ", sec.get("text") or "")
            ).strip()
        bind.execute(
            sa.text(
                "UPDATE articles SET sections = CAST(:s AS JSONB), methods_text = :m WHERE id = :i"
            ),
            {
                "s": json.dumps(sections, ensure_ascii=False),
                "m": _TEX.sub(" [formula] ", methods) if methods else methods,
                "i": art_id,
            },
        )


def downgrade() -> None:
    pass
