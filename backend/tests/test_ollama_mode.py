"""Tests du flag de déploiement Ollama (app/ollama_mode.py) — lecture env, normalisation."""

from app import ollama_mode


def test_ollama_mode_absent_defaults_docker(monkeypatch):
    """Flag absent → docker (invariant : comportement actuel)."""
    monkeypatch.delenv("OLLAMA_MODE", raising=False)
    assert ollama_mode.resolve_mode() == "docker"
    assert ollama_mode.MODE == "docker"  # lu à l'import, défaut docker


def test_ollama_mode_invalid_defaults_docker_with_warning(monkeypatch, caplog):
    """Valeur invalide → docker + warning ; valeur vide → docker SANS warning."""
    monkeypatch.setenv("OLLAMA_MODE", "banana")
    assert ollama_mode.resolve_mode() == "docker"
    assert any(r.levelname == "WARNING" and "banana" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.setenv("OLLAMA_MODE", "")
    assert ollama_mode.resolve_mode() == "docker"
    assert caplog.records == []


def test_ollama_mode_case_and_space_normalized(monkeypatch):
    """Casse et espaces normalisés (strip + lower)."""
    monkeypatch.setenv("OLLAMA_MODE", " HOST ")
    assert ollama_mode.resolve_mode() == "host"
    monkeypatch.setenv("OLLAMA_MODE", "Off")
    assert ollama_mode.resolve_mode() == "off"
