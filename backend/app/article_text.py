"""Article text fed to the models: parts chosen per project.

``kinds`` (``Project.text_sections``): abstract, introduction, methods, results,
discussion, conclusion, other, or ``all`` (whole full text). Default: methods.
"""

from __future__ import annotations

KINDS = ("abstract", "introduction", "methods", "results", "discussion", "conclusion", "other")
DEFAULT_KINDS = ["methods"]

# Title keywords (lowercase) → kind; first match wins.
_TITLE_KINDS = (
    ("introduction", ("introduction", "background")),
    ("methods", ("method", "material", "méthode", "patients and", "experimental procedures")),
    ("results", ("result", "résultat", "findings")),
    ("discussion", ("discussion",)),
    ("conclusion", ("conclusion",)),
)


def classify_title(title: str | None) -> str:
    t = (title or "").strip().lower()
    for kind, words in _TITLE_KINDS:
        if any(w in t for w in words):
            return kind
    return "other"


def compose_text(article, kinds: list[str] | None = None) -> str:
    """Concatenates the requested parts; falls back to methods_text, then abstract, if empty."""
    kinds = kinds or DEFAULT_KINDS
    sections = article.sections or []
    parts: list[str] = []
    for kind in KINDS if "all" in kinds else kinds:
        if kind == "abstract":
            if article.abstract:
                parts.append(f"Abstract\n{article.abstract}")
            continue
        found = [s for s in sections if s.get("kind") == kind]
        if found:
            parts.extend(f"{s['title']}\n{s['text']}" for s in found)
        elif kind == "methods" and article.methods_text:
            parts.append(f"Methods\n{article.methods_text}")
    return "\n\n".join(parts) or article.methods_text or article.abstract or ""


def fill_prompt(template: str | None, article, kinds: list[str] | None = None) -> str:
    """Substitutes ``{article_text}`` (project parts) and ``{methods_text}`` (compat)."""
    return (
        (template or "")
        .replace("{article_text}", compose_text(article, kinds))
        .replace("{methods_text}", article.methods_text or "")
    )


def title_abstract(article) -> str:
    """Stage 1 (prefilter) text: title + abstract."""
    return "\n".join(x for x in (article.title, article.abstract) if x)
