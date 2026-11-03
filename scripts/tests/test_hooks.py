"""Test fonctionnel du loop complet hooks + gate (git réel, hooks réels).

Scénarios (le loop tel qu'il s'exécutera dans n'importe quel repo harnessed) :
1. commit sans review → bloqué
2. review (record) → commit passe
3. nouveau commit sans nouvelle review → bloqué (marqueur stale)
4. waiver (user) → commit passe, waiver retiré par post-commit
"""
import shutil
import subprocess
from pathlib import Path

import pytest


def find_scripts() -> Path:
    """Localise le scripts/ du harness (template ou installé)."""
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "pre-commit").exists():
            return p
        if (p / "scripts" / "pre-commit").exists():
            return p / "scripts"
        p = p.parent
    raise RuntimeError("scripts/ du harness introuvable")


SCRIPTS = find_scripts()


def git(cwd, *args, check=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture
def wired(tmp_path):
    """Dépôt git avec les hooks du harness installés (comme init.sh le fait)."""
    r = tmp_path / "repo"
    r.mkdir()
    dst = r / "scripts"
    shutil.copytree(SCRIPTS, dst)
    for f in dst.iterdir():
        f.chmod(0o755)
    git(r, "init", "-q", check=True)
    git(r, "config", "user.email", "t@t.t", check=True)
    git(r, "config", "user.name", "t", check=True)
    git(r, "config", "core.hooksPath", "scripts", check=True)
    # bootstrap du dépôt (HEAD absent au premier commit : record impossible,
    # --no-verify une fois ; dans un vrai repo, HEAD existe déjà au moment
    # où init.sh installe les hooks)
    (r / "base.txt").write_text("base\n")
    git(r, "add", "base.txt", check=True)
    git(r, "commit", "-q", "--no-verify", "-m", "chore: base", check=True)
    return r


def test_commit_without_review_blocked(wired):
    (wired / "x.txt").write_text("x\n")
    git(wired, "add", "x.txt", check=True)
    r = git(wired, "commit", "-m", "feat: x")
    assert r.returncode != 0
    assert "review" in r.stderr.lower() or "review" in r.stdout.lower()
    # le commit n'a pas eu lieu
    assert git(wired, "log", "--oneline").stdout.count("\n") == 1


def test_review_then_commit_passes(wired):
    (wired / "x.txt").write_text("x\n")
    git(wired, "add", "x.txt", check=True)
    subprocess.run(
        ["python3", str(wired / "scripts" / "review.py"), "record"],
        cwd=wired, check=True, capture_output=True,
    )
    git(wired, "commit", "-q", "-m", "feat: x", check=True)
    assert "feat: x" in git(wired, "log", "--oneline").stdout


def test_second_commit_without_new_review_blocked(wired):
    (wired / "x.txt").write_text("x\n")
    git(wired, "add", "x.txt", check=True)
    subprocess.run(
        ["python3", str(wired / "scripts" / "review.py"), "record"],
        cwd=wired, check=True, capture_output=True,
    )
    git(wired, "commit", "-q", "-m", "feat: x", check=True)
    # le prochain commit, sans nouvelle review, doit être bloqué
    (wired / "y.txt").write_text("y\n")
    git(wired, "add", "y.txt", check=True)
    r = git(wired, "commit", "-m", "feat: y")
    assert r.returncode != 0


def test_waiver_one_commit_then_gone(wired):
    (wired / "x.txt").write_text("x\n")
    git(wired, "add", "x.txt", check=True)
    waiver = wired / ".git" / "hermes-review-waived"
    subprocess.run(["sh", str(wired / "scripts" / "waive-review.sh")],
                   cwd=wired, check=True, capture_output=True)
    assert waiver.exists()
    git(wired, "commit", "-q", "-m", "chore: waived", check=True)
    # post-commit a retiré le waiver
    assert not waiver.exists()
    # et le commit suivant re-requiert une review
    (wired / "y.txt").write_text("y\n")
    git(wired, "add", "y.txt", check=True)
    r = git(wired, "commit", "-m", "feat: y")
    assert r.returncode != 0
