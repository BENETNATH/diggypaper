#!/usr/bin/env python3
"""Générer le .env de diggypaper à partir de .env.example.

Auto-génère les secrets (JWT_SECRET, ANON_SALT, ENCRYPTION_KEY), demande les
valeurs clés (port, admin, SMTP), préserve les commentaires de .env.example,
et écrit .env en chmod 600. Refuse d'écraser un .env existant sans --force.

Usage :
    python3 scripts/setup_env.py            # interactif
    python3 scripts/setup_env.py --yes      # valeurs par défaut (auto, non interactif)
    python3 scripts/setup_env.py --force    # régénérer (écrase le .env existant)
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
ENV = ROOT / ".env"


def _gen_secrets() -> dict[str, str]:
    """Secrets auto-générés (jamais demandés)."""
    return {
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ANON_SALT": secrets.token_urlsafe(32),
        "ENCRYPTION_KEY": secrets.token_hex(32),
    }


def _ask(prompt: str, default: str = "") -> str:
    val = input(f"  {prompt} [{default}]: ").strip()
    return val or default


def _interactive() -> dict[str, str]:
    vals: dict[str, str] = {}
    print("  --- Valeurs interactives ---")

    port = _ask("Port nginx", "8080")
    while not port.isdigit():
        port = _ask("Port nginx (entier)", "8080")
    vals["NGINX_PORT"] = port

    vals["APP_BASE_URL"] = _ask("URL publique de l'app", "http://localhost:8000")
    vals["ADMIN_EMAIL"] = _ask("Email admin bootstrap (vide = désactivé)", "")

    gen = input("  Générer un mot de passe admin aléatoire ? [O/n]: ").strip().lower()
    if gen in ("", "o", "y", "yes"):
        vals["ADMIN_PASSWORD"] = secrets.token_urlsafe(12)
        print(f"  ADMIN_PASSWORD: {vals['ADMIN_PASSWORD']}")
        print("  [!] À conserver — il ne sera plus affiché.")
    else:
        while True:
            vals["ADMIN_PASSWORD"] = input("  Mot de passe admin (min 8): ").strip()
            if len(vals["ADMIN_PASSWORD"]) >= 8:
                break
            print("  [ERREUR] Trop court (min 8).")

    if input("  Configurer SMTP ? [n/O]: ").strip().lower() in ("y", "yes", "o"):
        vals["SMTP_HOST"] = _ask("SMTP host", "smtp.gmail.com")
        vals["SMTP_PORT"] = _ask("SMTP port", "587")
        vals["SMTP_USER"] = _ask("SMTP user", "")
        vals["SMTP_PASSWORD"] = input("  SMTP password: ").strip()
        vals["SMTP_FROM"] = _ask("SMTP from", "")

    return vals


def _defaults() -> dict[str, str]:
    return {
        "NGINX_PORT": "8080",
        "APP_BASE_URL": "http://localhost:8000",
        "ADMIN_EMAIL": "",
        "ADMIN_PASSWORD": secrets.token_urlsafe(12),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Générer le .env de diggypaper.")
    ap.add_argument("--force", action="store_true", help="Écraser le .env existant")
    ap.add_argument("--yes", action="store_true", help="Valeurs par défaut, sans interaction")
    args = ap.parse_args()

    if not ENV_EXAMPLE.exists():
        print(f"[ERREUR] {ENV_EXAMPLE} introuvable.")
        sys.exit(1)

    if ENV.exists() and not args.force:
        print(f"[SKIP] {ENV} existe déjà. Utilisez --force pour régénérer.")
        sys.exit(0)

    overrides = {**_gen_secrets(), **(_defaults() if args.yes else _interactive())}

    lines = ENV_EXAMPLE.read_text().splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Titre : retirer le marqueur (EXEMPLE).
        if "(EXEMPLE)" in line:
            line = line.replace("(EXEMPLE)", "")
        # Clé connue non commentée (KEY=value) → remplacer la valeur.
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in overrides:
                out.append(f"{key}={overrides[key]}")
                continue
        # Clé connue commentée (ex. # SMTP_FROM=...) → décommenter si valeur fournie.
        if stripped.startswith("#") and "=" in stripped:
            ckey = stripped[1:].strip().split("=", 1)[0].strip()
            if ckey in overrides:
                out.append(f"{ckey}={overrides[ckey]}")
                continue
        out.append(line)

    ENV.write_text("\n".join(out) + "\n")
    ENV.chmod(0o600)
    print(f"[OK] {ENV} créé (chmod 600).")
    print("  Secrets générés : JWT_SECRET, ANON_SALT, ENCRYPTION_KEY")
    if args.yes:
        print(f"  ADMIN_PASSWORD: {overrides['ADMIN_PASSWORD']}")


if __name__ == "__main__":
    main()
