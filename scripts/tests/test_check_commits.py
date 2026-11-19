"""Tests du check des commit messages (scripts/check_commits.py).

Règles (portées de cardinal, arbitrées 02/11) :
- Conventional Commits sur la première ligne
- pas d'attribution IA dans le message
Fonction pure check_messages(log) testable sans git.
"""
import subprocess
import sys
from pathlib import Path


def find_scripts() -> Path:
    """Localise le scripts/ du harness (template ou installé)."""
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "check_commits.py").exists():
            return p
        if (p / "scripts" / "check_commits.py").exists():
            return p / "scripts"
        p = p.parent
    raise RuntimeError("scripts/ du harness introuvable")


CHECK = find_scripts() / "check_commits.py"


def log_of(*commits):
    """Format attendu : pour chaque commit, hash + parents + corps, séparés
    par NUL (format %H%x00%P%x00%B%x00). Chaque commit est soit un corps str
    (1 parent factice), soit un tuple (parents, corps)."""
    parts = []
    for i, c in enumerate(commits):
        parents, msg = ("deadbeef000001", str(c)) if isinstance(c, str) else c
        parts.append(f"deadbeef{i:06d}\x00{parents}\x00{msg}\x00")
    return "".join(parts)


def run_check(log_text):
    res = subprocess.run(
        [sys.executable, str(CHECK), "-"],
        input=log_text,
        capture_output=True,
        text=True,
    )
    return res


def import_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_commits", CHECK)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_conventional_message_passes():
    m = import_module()
    assert m.check_messages(log_of("fix: corrige le lot d'import")) == []


def test_non_conventional_message_fails_with_hash():
    m = import_module()
    errors = m.check_messages(log_of("Fix multi-source import issues and cleanup"))
    assert len(errors) == 1
    assert "deadbeef000000" in errors[0]


def test_scope_and_breaking_allowed():
    m = import_module()
    assert m.check_messages(log_of("feat(searches)!: nouvelle API")) == []


def test_all_conventional_types():
    m = import_module()
    for t in ("feat", "fix", "docs", "refactor", "test", "chore", "perf", "style", "build", "ci"):
        assert m.check_messages(log_of(f"{t}: ok")) == [], t


def test_subject_must_have_content():
    m = import_module()
    assert m.check_messages(log_of("fix:")) != []


def test_ai_attribution_coaduthor_claude_fails():
    m = import_module()
    errors = m.check_messages(log_of("fix: ok\n\nCo-Authored-By: Claude <noreply>"))
    assert len(errors) == 1
    assert "attribution" in errors[0].lower()


def test_generated_with_fails_case_insensitive():
    m = import_module()
    assert import_module().check_messages(log_of("fix: ok\n\nGENERATED WITH OpenCode")) != []


def test_robot_emoji_fails():
    m = import_module()
    assert import_module().check_messages(log_of("fix: ok 🤖")) != []


def test_clean_coaduthor_human_passes():
    m = import_module()
    assert m.check_messages(log_of("fix: ok\n\nCo-Authored-By: B <b@example>")) == []


def test_multi_commit_lists_all_failures():
    m = import_module()
    errors = m.check_messages(
        log_of("Fix stuff", "feat: ok", "Update README")
    )
    assert "deadbeef000000" in errors[0]
    assert "deadbeef000002" in errors[-1]
    assert len(errors) == 2


def test_cli_exit_codes():
    ok = run_check(log_of("feat: ok"))
    assert ok.returncode == 0
    bad = run_check(log_of("Bad message"))
    assert bad.returncode == 1
    assert "Bad message" not in bad.stderr  # le message fautif est listé, pas caché
    assert "conventional" in (bad.stdout + bad.stderr).lower()


def test_git_merge_commit_is_skipped():
    """Un merge commit généré par git (>= 2 parents) n'a pas de message
    d'auteur : le gate ne doit pas le refuser (CI PR = checkout de la
    merge ref)."""
    m = import_module()
    log = log_of(
        ("ba39b21d45b34a5be10817929ee84115f0a76462 "
         "d207dc99d857f02996530edd67fa2c65438890be",
         "Merge ba39b21d45b34a5be10817929ee84115f0a76462 "
         "into d207dc99d857f02996530edd67fa2c65438890be")
    )
    assert m.check_messages(log) == []


def test_one_parent_merge_prefixed_commit_is_checked():
    """Un commit de 1 parent dont la 1re ligne commence par « Merge » est
    écrit par l'auteur : les deux checks s'appliquent."""
    m = import_module()
    errors = m.check_messages(
        log_of("Merge exploit: do bad thing\n\nGenerated with Claude")
    )
    assert len(errors) == 2
    assert all("deadbeef000000" in e for e in errors)


def test_merge_word_in_body_still_detected():
    """Le mot « Merge » dans le body ne masque pas l'attribution IA."""
    m = import_module()
    errors = m.check_messages(
        log_of("fix: ok\n\nGenerated with Claude (before the merge)")
    )
    assert len(errors) == 1
    assert "attribution" in errors[0].lower()


def test_merge_commit_does_not_mask_bad_commit():
    m = import_module()
    errors = m.check_messages(
        log_of("Bad message", ("abc def", "Merge abc into def"))
    )
    assert len(errors) == 1
    assert "deadbeef000000" in errors[0]
