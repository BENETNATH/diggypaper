"""Chat on the corpus — pure core (Chat RAG, spec 2026-11-07-chat-rag §1).

No DB, no network: context assembly, message building, citation extraction.
The sections come from ``app.semantic_search.semantic_search`` (RRF-ranked,
pre-excerpted) — the same retrieval the RAG endpoint uses.
"""

from __future__ import annotations

import json
import re

#: Top-K of the RAG injected into the prompt (spec §1).
CHAT_CONTEXT_SECTIONS = 6
#: History bound: user/assistant PAIRS kept (spec §1 — ≤ 6).
HISTORY_MAX_PAIRS = 6
#: Character budget of the context block (spec §1).
DEFAULT_CONTEXT_BUDGET = 6000
#: Max length of a single history message (defensive — the client bounds it).
HISTORY_MSG_MAX = 4000

#: Results mode (spec 2026-11-09-sous-analyse-criteres R5): the top answers
#: injected (K fixed — no context window in the model registry) and the corpus
#: passages kept for the citations (k reduced).
RESULTS_TOP_K = 30
RESULTS_CORPUS_K = 6

_CITATION_RE = re.compile(r"\[(\d+)\]")


def build_context(
    sections: list[dict], budget_chars: int = DEFAULT_CONTEXT_BUDGET
) -> tuple[str, list[dict]]:
    """Numbers the retrieved sections ``[n]`` within a character budget.

    A section is NEVER cut in the middle: when it does not fit in the
    remaining budget it is excluded whole (numbering keeps the retrieval
    rank, so citations map back to the result list). Returns ``(block,
    included)`` — ``included`` carries each section's ``n`` for the
    citations payload.
    """
    block = ""
    included: list[dict] = []
    n = 0
    for section in sections:
        n += 1
        article = section.get("article") or {}
        entry = f"[{n}] {article.get('title') or ''} — {section.get('section_title') or ''}: "
        entry += str(section.get("excerpt") or "")
        entry = entry.rstrip()
        if not entry or len(entry) > budget_chars:
            continue
        if block and len(block) + len(entry) + 2 > budget_chars:
            continue
        block = block + f"\n\n{entry}" if block else entry
        included.append({**section, "n": n})
    return block, included


def build_results_context(
    responses: list[dict],
    stats: dict,
    sections: list[dict],
    budget_chars: int = DEFAULT_CONTEXT_BUDGET,
) -> tuple[str, list[dict]]:
    """The results-mode context (R5): the rendered answers (NOT citable — they
    are data, not corpus passages), the whole-lot statistics verbatim (compact
    JSON), and the numbered corpus passages ``[n]`` (the only citable items).

    The responses eat the budget left by the corpus block: when it is tight,
    the LEAST relevant ones (end of the retrieval ranking) are dropped whole.
    Returns ``(block, included)`` — ``included`` = the corpus passages, for
    the citations payload (same contract as ``build_context``).
    """
    corpus_block, included = build_context(sections, budget_chars)
    stats_json = json.dumps(stats, ensure_ascii=False, separators=(",", ":"))
    remaining = budget_chars - len(corpus_block) - len(stats_json) - 60
    lines: list[str] = []
    for r in responses:
        article = r.get("article") or {}
        line = f"- {article.get('title') or ''} : {r.get('excerpt') or ''}".rstrip()
        if not line:
            continue
        if lines and len("\n".join(lines)) + len(line) + 1 > remaining:
            break
        lines.append(line)
    block = (
        f"RÉPONSES ({len(lines)}):\n"
        + "\n".join(lines)
        + f"\n\nSTATISTIQUES (lot entier):\n{stats_json}\n\nCORPUS:\n{corpus_block}"
    )
    return block, included


def build_chat_messages(
    system: str,
    history: list[dict],
    question: str,
    context_block: str,
) -> list[dict]:
    """``[system]`` + history (bounded, user/assistant only) + the final user
    turn carrying the context and the question (spec §1)."""
    clean = [
        m
        for m in (history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    clean = clean[-2 * HISTORY_MAX_PAIRS :]
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(
        {"role": m["role"], "content": str(m["content"])[:HISTORY_MSG_MAX]} for m in clean
    )
    messages.append(
        {"role": "user", "content": f"CONTEXTE:\n{context_block}\n\nQUESTION:\n{question}"}
    )
    return messages


def extract_citations(text: str, n_sections: int) -> list[int]:
    """The ``[n]`` markers of a free-text answer: sorted, deduplicated, and
    bounded to ``[1..n_sections]`` (out-of-range markers are dropped — the
    model cannot cite a section it was not given)."""
    seen: set[int] = set()
    for raw in _CITATION_RE.findall(text or ""):
        n = int(raw)
        if 1 <= n <= n_sections:
            seen.add(n)
    return sorted(seen)
