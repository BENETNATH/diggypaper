#!/usr/bin/env python3
"""Review gate — marqueur de review + vérification avant commit.

Mécanique (portée de cardinal, ADR 0005 : les gates attrapent les erreurs
d'un agent coopératif, pas les contournements) :

  review.py record   après review d'un diff, enregistre HEAD + fichiers staged
                     dans .git/hermes-review.json (jamais dans l'arbre de
                     travail).
  review.py check    appelé par scripts/pre-commit : refuse (exit 2) si le
                     marqueur est absent, plus ancien que le dernier commit,
                     ou si un fichier staged n'y figure pas.

Le waiver (.git/hermes-review-waived) est réservé à l'utilisateur
(scripts/waive-review.sh), valable pour un commit, retiré par
scripts/post-commit.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER = "hermes-review.json"
WAIVER = "hermes-review-waived"


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def fail(msg):
    print(f"harness review-gate : {msg}", file=sys.stderr)
    sys.exit(2)


def git_dir(cwd):
    r = sh(["git", "rev-parse", "--git-dir"], cwd)
    if r.returncode != 0:
        fail("pas dans un dépôt git")
    p = Path(r.stdout.strip())
    return p if p.is_absolute() else Path(cwd) / p


def head(cwd):
    r = sh(["git", "rev-parse", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def staged(cwd):
    r = sh(["git", "diff", "--cached", "--name-only"], cwd)
    if r.returncode != 0:
        return []
    return sorted(set(r.stdout.split()))


def record(cwd):
    h = head(cwd)
    if h is None:
        fail("aucun commit de base (HEAD inexistant)")
    files = staged(cwd)
    marker = {
        "head": h,
        "files": files,
        "reviewer": os.environ.get("REVIEWER", "hermes"),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (git_dir(cwd) / MARKER).write_text(json.dumps(marker, indent=2) + "\n")
    print(
        f"harness review-gate : review enregistrée "
        f"({len(files)} fichier(s), HEAD {h[:8]}, reviewer {marker['reviewer']})"
    )


def check(cwd):
    gd = git_dir(cwd)
    if (gd / WAIVER).exists():
        return  # waiver user-only : ce commit passe, post-commit retirera le waiver
    m = gd / MARKER
    if not m.exists():
        fail("review manquante : réviser le diff puis `scripts/review.py record`")
    marker = json.loads(m.read_text())
    h = head(cwd)
    if h is None or marker.get("head") != h:
        old = (marker.get("head") or "?")[:8]
        new = (h or "?")[:8]
        fail(f"marqueur plus ancien que le dernier commit ({old} ≠ HEAD {new}) : réviser et re-`record`")
    missing = [f for f in staged(cwd) if f not in set(marker.get("files", []))]
    if missing:
        fail("fichier(s) non couverts par la review : " + ", ".join(missing))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("record", "check"):
        fail("usage : review.py record|check")
    cwd = os.getcwd()
    if sys.argv[1] == "record":
        record(cwd)
    else:
        check(cwd)


if __name__ == "__main__":
    main()
