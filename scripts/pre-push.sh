#!/bin/sh
# Run the same backend checks as GitHub CI, plus bandit, before every push.
# Install (fresh clone): git config core.hooksPath scripts
# (or copy this file to .git/hooks/pre-push)
set -e

cd "$(git rev-parse --show-toplevel)/backend"

for tool in ruff bandit; do
  command -v "$tool" >/dev/null || { echo "pre-push: $tool not found (uv tool install $tool)"; exit 1; }
done

ruff check .
ruff format --check .
# -ll: fail on Medium+ severity; the 9 Low findings (e.g. try/except pass) are accepted.
bandit -r app -ll
