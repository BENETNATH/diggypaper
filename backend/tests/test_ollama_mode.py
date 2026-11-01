"""Tests for the Ollama deployment flag (app/ollama_mode.py) — env reading, normalization."""

from app import ollama_mode


def test_ollama_mode_absent_defaults_docker(monkeypatch):
    """Flag absent → docker (invariant : comportement actuel)."""
    monkeypatch.delenv("OLLAMA_MODE", raising=False)
    assert ollama_mode.resolve_mode() == "docker"
    assert ollama_mode.MODE == "docker"  # read at import, default docker


def test_ollama_mode_invalid_defaults_docker_with_warning(monkeypatch, caplog):
    """Invalid value -> docker + warning; empty value -> docker WITHOUT warning."""
    monkeypatch.setenv("OLLAMA_MODE", "banana")
    assert ollama_mode.resolve_mode() == "docker"
    assert any(r.levelname == "WARNING" and "banana" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.setenv("OLLAMA_MODE", "")
    assert ollama_mode.resolve_mode() == "docker"
    assert caplog.records == []


def test_ollama_mode_case_and_space_normalized(monkeypatch):
    """Case and spaces normalized (strip + lower)."""
    monkeypatch.setenv("OLLAMA_MODE", " HOST ")
    assert ollama_mode.resolve_mode() == "host"
    monkeypatch.setenv("OLLAMA_MODE", "Off")
    assert ollama_mode.resolve_mode() == "off"
