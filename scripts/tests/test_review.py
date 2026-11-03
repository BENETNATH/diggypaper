"""Tests du review gate (scripts/review.py).

TDD : ces tests précèdent l'implémentation. Le gate mécanique refuse tout
commit contenant un fichier que la review n'a jamais vu (marqueur dans le
git dir). Waiver user-only via .git/hermes-review-waived.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def find_scripts() -> Path:
    """Localise le scripts/ du harness : le plus proche ancêtre du fichier
    de test contenant review.py (directement ou dans un scripts/)."""
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "review.py").exists():
            return p
        if (p / "scripts" / "review.py").exists():
            return p / "scripts"
        p = p.parent
    raise RuntimeError("scripts/ du harness introuvable")


SCRIPTS = find_scripts()
REVIEW = SCRIPTS / "review.py"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


def run_review(cwd, *args):
    return subprocess.run(
        [sys.executable, str(REVIEW), *args], cwd=cwd, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "t")
    # premier commit de base
    (r / "base.txt").write_text("base\n")
    git(r, "add", "base.txt")
    git(r, "commit", "-q", "-m", "chore: base")
    return r


def test_check_without_marker_refuses(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    res = run_review(repo, "check")
    assert res.returncode == 2
    assert "record" in res.stderr + res.stdout


def test_record_then_check_same_staged_passes(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    assert run_review(repo, "record").returncode == 0
    res = run_review(repo, "check")
    assert res.returncode == 0, res.stderr


def test_marker_stale_after_commit_refuses(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    run_review(repo, "record")
    git(repo, "commit", "-q", "-m", "chore: step")  # déplace HEAD
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    res = run_review(repo, "check")
    assert res.returncode == 2
    assert "stale" in (res.stderr + res.stdout).lower() or "plus ancien" in res.stderr + res.stdout


def test_unseen_staged_file_refuses_and_is_listed(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    run_review(repo, "record")
    # nouveau fichier jamais vu par la review
    (repo / "surprise.py").write_text("print(1)\n")
    git(repo, "add", "surprise.py")
    res = run_review(repo, "check")
    assert res.returncode == 2
    assert "surprise.py" in res.stderr + res.stdout


def test_fix_of_reviewed_file_passes(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    run_review(repo, "record")
    (repo / "a.txt").write_text("a v2\n")  # même fichier, contenu modifié
    git(repo, "add", "a.txt")
    assert run_review(repo, "check").returncode == 0


def test_staged_deletion_of_reviewed_file_passes(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    run_review(repo, "record")
    git(repo, "rm", "-q", "a.txt")  # staged deletion
    assert run_review(repo, "check").returncode == 0


def test_waiver_allows_commit_then_marker_clean(tmp_path, repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    # waiver écrit par le seul ayant le droit : l'user
    (repo / ".git" / "hermes-review-waived").write_text("waived\n")
    res = run_review(repo, "check")
    assert res.returncode == 0, res.stderr


def test_record_writes_marker_in_git_dir_only(repo):
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    run_review(repo, "record")
    marker = repo / ".git" / "hermes-review.json"
    assert marker.exists()
    data = json.loads(marker.read_text())
    assert data["files"] == ["a.txt"]
    assert "head" in data
    # le marqueur n'est pas dans l'arbre de travail
    st = git(repo, "status", "--porcelain").stdout
    assert "hermes-review" not in st
