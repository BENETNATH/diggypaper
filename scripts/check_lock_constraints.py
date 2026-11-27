#!/usr/bin/env python3
"""CI guard: backend/constraints.txt is the single source of truth for versions.

Fails if
  - a package pinned in constraints.txt has another version in uv.lock (drift), or
  - a direct dependency of pyproject.toml is not pinned in constraints.txt
    (it would float in the Docker image and in CI).

Fix a drift: `cd backend && uv lock --upgrade-package <name>==<constraints version>`.
"""

import re
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


pins = {}
for line in (BACKEND / "constraints.txt").read_text().splitlines():
    if "==" in line and not line.lstrip().startswith("#"):
        name, version = line.split("==", 1)
        pins[norm(name)] = version.strip()

lock = {norm(p["name"]): p.get("version") for p in tomllib.loads((BACKEND / "uv.lock").read_text())["package"]}
deps = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]

errors = [f"{n}: constraints {v} != uv.lock {lock[n]}" for n, v in pins.items() if n in lock and lock[n] != v]
errors += [
    f"{n}: direct dependency not pinned in constraints.txt"
    for n in (norm(re.match(r"[A-Za-z0-9_.-]+", d).group()) for d in deps)
    if n not in pins
]
for e in errors:
    print(e)
sys.exit(1 if errors else 0)
