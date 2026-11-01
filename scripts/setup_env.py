#!/usr/bin/env python3
"""Generate diggypaper's .env from .env.example.

Auto-generates the secrets (JWT_SECRET, ANON_SALT, ENCRYPTION_KEY), asks for
the key values (port, admin, SMTP), preserves the .env.example comments and
writes .env with chmod 600. Refuses to overwrite an existing .env without
--force.

Usage:
    python3 scripts/setup_env.py            # interactive
    python3 scripts/setup_env.py --yes      # default values (auto, non-interactive)
    python3 scripts/setup_env.py --force    # regenerate (overwrites the existing .env)
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
    """Auto-generated secrets (never prompted for)."""
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
    print("  --- Interactive values ---")

    port = _ask("Nginx port", "8080")
    while not port.isdigit():
        port = _ask("Nginx port (integer)", "8080")
    vals["NGINX_PORT"] = port

    vals["APP_BASE_URL"] = _ask("Public URL of the app", "http://localhost:8000")
    vals["ADMIN_EMAIL"] = _ask("Bootstrap admin email (empty = disabled)", "")

    gen = input("  Generate a random admin password? [Y/n]: ").strip().lower()
    if gen in ("", "y", "yes"):
        vals["ADMIN_PASSWORD"] = secrets.token_urlsafe(12)
        # Never printed: the password only lives in .env (chmod 600).
        print("  ADMIN_PASSWORD: generated (stored in .env, chmod 600)")
    else:
        while True:
            vals["ADMIN_PASSWORD"] = input("  Admin password (min 8): ").strip()
            if len(vals["ADMIN_PASSWORD"]) >= 8:
                break
            print("  [ERROR] Too short (min 8).")

    if input("  Configure SMTP? [n/Y]: ").strip().lower() in ("y", "yes"):
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
    ap = argparse.ArgumentParser(description="Generate diggypaper's .env.")
    ap.add_argument("--force", action="store_true", help="Overwrite the existing .env")
    ap.add_argument("--yes", action="store_true", help="Default values, no interaction")
    args = ap.parse_args()

    if not ENV_EXAMPLE.exists():
        print(f"[ERROR] {ENV_EXAMPLE} not found.")
        sys.exit(1)

    if ENV.exists() and not args.force:
        print(f"[SKIP] {ENV} already exists. Use --force to regenerate.")
        sys.exit(0)

    overrides = {**_gen_secrets(), **(_defaults() if args.yes else _interactive())}

    lines = ENV_EXAMPLE.read_text().splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Title: strip the (EXAMPLE) marker.
        if "(EXAMPLE)" in line:
            line = line.replace("(EXAMPLE)", "")
        # Known uncommented key (KEY=value) -> replace the value.
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in overrides:
                out.append(f"{key}={overrides[key]}")
                continue
        # Known commented key (e.g. # SMTP_FROM=...) -> uncomment if a value is provided.
        if stripped.startswith("#") and "=" in stripped:
            ckey = stripped[1:].strip().split("=", 1)[0].strip()
            if ckey in overrides:
                out.append(f"{ckey}={overrides[ckey]}")
                continue
        out.append(line)

    ENV.write_text("\n".join(out) + "\n")
    ENV.chmod(0o600)
    print(f"[OK] {ENV} created (chmod 600).")
    print("  Generated secrets: JWT_SECRET, ANON_SALT, ENCRYPTION_KEY")
    if args.yes:
        print("  ADMIN_PASSWORD: generated (stored in .env, chmod 600)")


if __name__ == "__main__":
    main()
