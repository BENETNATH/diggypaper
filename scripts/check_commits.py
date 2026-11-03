#!/usr/bin/env python3
"""Commit message gate — Conventional Commits + pas d'attribution IA.

Utilisé par la CI (job `commits`, PR uniquement) :

  git log --format=%H%x00%B%x00 base..head | scripts/check_commits.py -

Règles (arbitrées le 02/11, portées de cardinal) :
- première ligne conforme Conventional Commits :
  (feat|fix|docs|refactor|test|chore|perf|style|build|ci)(scope)?: sujet
- pas d'attribution IA dans le message (les commits restent ceux de
  l'utilisateur).

Fonction pure check_messages(log) -> list[erreurs] ; exit 1 si erreurs.
"""
import re
import sys

TYPES = (
    "feat", "fix", "docs", "refactor", "test",
    "chore", "perf", "style", "build", "ci",
)
CONVENTIONAL = re.compile(rf"^({'|'.join(TYPES)})(\([^)]+\))?!?: .+")

# motifs d'attribution IA, insensibles à la casse
AI_PATTERNS = (
    "generated with",
    "co-authored-by: claude",
    "co-authored-by: gpt",
    "co-authored-by: chatgpt",
    "co-authored-by: openai",
    "co-authored-by: gemini",
    "co-authored-by: copilot",
    "co-authored-by: opencode",
    "co-authored-by: hermes",
    "co-authored-by: qwen",
    "\U0001F916",  # 🤖
)


def check_messages(log: str) -> list:
    """log = séquence de 'hash\\x00message\\x00' (format %H%x00%B%x00)."""
    errors = []
    parts = [p for p in log.split("\x00")]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    for i in range(0, len(parts) - 1, 2):
        commit = parts[i].strip()
        body = parts[i + 1]
        if not commit and not body.strip():
            continue
        first_line = body.strip().splitlines()[0] if body.strip() else ""
        if not CONVENTIONAL.match(first_line):
            errors.append(
                f"{commit}: conventional commits : première ligne non "
                f"conforme : {first_line!r}"
            )
        low = body.lower()
        for pat in AI_PATTERNS:
            if pat in low:
                errors.append(f"{commit}: attribution IA détectée : {pat!r}")
                break
    return errors


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("-",):
        # "-" = stdin ; un chemin de fichier est accepté aussi
        pass
    if len(sys.argv) >= 2 and sys.argv[1] != "-":
        log = open(sys.argv[1]).read()
    else:
        log = sys.stdin.read()
    errors = check_messages(log)
    for e in errors:
        print(e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
