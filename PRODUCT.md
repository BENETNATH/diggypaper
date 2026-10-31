# PRODUCT — Diggypaper

Sovereign, collaborative platform for **systematic literature review**.

## What it does

1. **Build a corpus** — import from PubMed, Europe PMC, OpenAlex or HAL (query, reference
   list, linked citations, RO-Crate restore); fetch open full texts (PMC, Europe PMC,
   Unpaywall, OpenAlex) and accept hand-deposited PDFs (institutional access).
2. **Ask questions to the corpus** — a *question* is asked to a *lot* of articles with
   answer items (yes/no, choice, free text). A lot is the whole corpus or a subset picked
   by criteria (previous answers, reviewed, full text, relevance range, random draw).
   Questions can be scheduled in sequence and derived from earlier results.
3. **Analyse with models** — CPU-only LLMs (Ollama local, or OpenAI-compatible external
   APIs), optionally in a two-step chain: a pre-filter (title + abstract) then the main
   analysis on the chosen text. Confident answers are accepted/dropped automatically;
   uncertain ones go to human review.
4. **Human review** — annotators read the article, quote evidence, confirm or correct the
   model's answer. Gold references accumulate as the validated truth; benchmarks compare
   prompts and models on a test lot.
5. **Export** — Excel, RIS, RO-Crate (full corpus) and a PRISMA 2020 flow diagram.

## Who it is for

- **Researchers** (project leads) who run systematic reviews and are not necessarily
  English speakers: the UI ships in French, English, German and Spanish.
- **Annotators** with review-only rights on a project.
- **Administrators** who manage models, users, invitations and system settings.

## Hard constraints

- **Sovereignty**: self-hosted, no cloud dependency for analysis; external model APIs are
  optional and their data flow must be disclosed in the UI.
- **CPU-only**: models run on server CPU (Ollama); the UI must never promise GPU speeds.
- **GDPR privacy by design**: consent before data processing, per-user data export,
  anonymisation.
- **Roles are enforced in the UI, not only server-side**: import/analysis/benchmark pages
  are leads-only; annotators see results and review.

## Tone

Operate mode: dense, precise, no marketing. Every page has loading / error / empty /
success states. Statuses and errors must be in the user's language (backend error details
are mapped to i18n strings — see `frontend/src/lib/api.ts: errorKey`).
