"""Ressources du serveur (CPU, RAM, GPU) et modèles conseillés pour le tri.

Lecture de /proc et des cgroups : le conteneur du backend voit le noyau de l'hôte. Les débits
sont des ordres de grandeur (CPU récent) à confirmer avec un benchmark sur le serveur.
"""

from __future__ import annotations

import os
from pathlib import Path


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def detect() -> dict:
    """{cores, ram_gb, avx, avx2, avx512, gpu} ; ``gpu`` = périphérique NVIDIA visible.

    RAM lue dans /proc/meminfo : celle de l'hôte (pas le plafond du conteneur backend).
    """
    flags: set[str] = set()
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("flags"):
            flags = set(line.partition(":")[2].split())
            break
    meminfo = [ln.split() for ln in _read("/proc/meminfo").splitlines()]
    ram_kb = next((int(f[1]) for f in meminfo if f and f[0] == "MemTotal:"), 0)
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return {
        "cores": cores,
        "ram_gb": round(ram_kb / 1024**2),
        "avx": "avx" in flags,
        "avx2": "avx2" in flags,
        "avx512": "avx512f" in flags,
        "gpu": Path("/dev/nvidia0").exists(),
    }


# (tag Ollama, RAM nécessaire en Go, clé de libellé). Modèles « instruct » sans mode réflexion :
# un modèle qui raisonne avant de répondre multiplie le temps par article.
_TINY = ("qwen2.5:3b-instruct", 3, "tiny")
_SMALL = ("qwen3:4b-instruct-2507-q4_K_M", 4, "small")
_GEMMA = ("gemma3:4b", 5, "small")
_MOE = ("qwen3:30b-a3b-instruct-2507-q4_K_M", 22, "moe")
_GPU = ("gemma3:12b", 10, "gpu")


def recommend(hw: dict) -> dict:
    """Profil du serveur et modèles conseillés (du plus recommandé au moins recommandé)."""
    ram = hw["ram_gb"]
    if hw["gpu"]:
        profile, models = "gpu", [_GPU, _MOE if ram >= 32 else _SMALL]
    elif ram >= 32:
        profile, models = "large", [_MOE, _SMALL]
    elif ram >= 16:
        profile, models = "medium", [_SMALL, _GEMMA]
    else:
        profile, models = "small", [_TINY]
    return {
        "profile": profile,
        "slow_cpu": not hw["avx2"] and not hw["gpu"],  # llama.cpp sans AVX2 : ×2 à ×3 plus lent
        "models": [
            {"tag": t, "ram_gb": r, "kind": k, "fits": r <= max(ram - 4, 0)} for t, r, k in models
        ],
    }
