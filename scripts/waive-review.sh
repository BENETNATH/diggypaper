#!/bin/sh
# managed: harness
# Waive le review-gate pour le PROCHAIN commit UNIQUEMENT (réservé à
# l'utilisateur — jamais l'assistant, jamais l'agent). Le waiver est
# retiré automatiquement par scripts/post-commit.
set -eu
g="$(git rev-parse --git-dir)"
head="$(git rev-parse HEAD 2>/dev/null || echo 'aucun')"
printf 'waived by %s at %s (head: %s)\n' \
  "${USER:-?}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$head" > "$g/hermes-review-waived"
echo "harness review-gate : waived pour le prochain commit (head $head)."
