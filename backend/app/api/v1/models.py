"""Routes modèle (ModelRegistry) — Module 3 (Arena).

Lecture : les modèles accessibles à l'utilisateur (cf. ``app.model_access``). Écriture : ADMIN
pour les modèles de la plateforme (locaux, serveurs) ; tout utilisateur pour ses propres
modèles externes (API compatible OpenAI), qu'il peut partager avec des utilisateurs ou projets.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import hardware
from app.api.deps import get_current_user, get_db, rate_limit_user
from app.core.crypto import encrypt_secret
from app.db.base import SessionLocal
from app.db.models import ModelRegistry, ModelShare, Project, ProjectMember, User, UserRole
from app.inference import InferenceError, OpenAICompatibleService
from app.model_access import access_filter, check_public_url
from app.settings.service import get_setting

router = APIRouter(prefix="/models", tags=["models"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

_BACKENDS = {"OLLAMA", "LLAMA_CPP", "TRANSFORMERS", "OPENAI_API"}


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    backend_type: str = "OLLAMA"
    model_identifier: str = Field(min_length=1, max_length=500)
    ram_usage_mb: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("backend_type")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        if v not in _BACKENDS:
            raise ValueError(f"backend_type must be one of {sorted(_BACKENDS)}")
        return v


class ExternalModel(BaseModel):
    """Modèle génératif hébergé ailleurs (API compatible OpenAI)."""

    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)


class ModelUpdate(BaseModel):
    is_active: bool | None = None
    ram_usage_mb: int | None = Field(default=None, ge=0)
    model_identifier: str | None = Field(default=None, min_length=1, max_length=500)


async def _require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin required")


def _require_owner_or_admin(m: ModelRegistry, user: User) -> None:
    if user.role != UserRole.ADMIN and m.owner_id != user.id:
        raise HTTPException(status_code=403, detail="not allowed")


def _check_url(url: str, user: User) -> None:
    """Un non-admin ne peut pas pointer le serveur vers le réseau interne."""
    if user.role == UserRole.ADMIN:
        return
    try:
        check_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _payload(m: ModelRegistry, user: User | None = None) -> dict:
    return {
        "mine": user is not None and m.owner_id == user.id,
        "platform": m.owner_id is None,
        "id": str(m.id),
        "name": m.name,
        "backend_type": m.backend_type,
        "model_identifier": m.model_identifier,
        "ram_usage_mb": m.ram_usage_mb,
        "is_active": m.is_active,
    }


logger = logging.getLogger(__name__)

# Téléchargements Ollama en cours/terminés, par nom de modèle.
# ponytail: état en mémoire (1 process uvicorn) ; table DB si plusieurs workers.
_PULLS: dict[str, dict] = {}


class PullRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300, pattern=r"^[\w.\-:/@]+$")


async def _ollama_base(db: AsyncSession) -> str:
    """URL racine d'Ollama = llm_api_base_url sans le suffixe /v1."""
    base = (await get_setting(db, "llm_api_base_url")).rstrip("/")
    return base.removesuffix("/v1")


async def _register(
    db: AsyncSession,
    identifier: str,
    name: str | None = None,
    backend: str = "OLLAMA",
    ram: int | None = None,
) -> None:
    name = name or identifier
    exists = (
        await db.execute(select(ModelRegistry).where(ModelRegistry.name == name))
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            ModelRegistry(
                name=name, backend_type=backend, model_identifier=identifier, ram_usage_mb=ram
            )
        )
    else:  # modèle pré-rempli (seed) : on met à jour l'identifiant réellement installé
        exists.model_identifier = identifier
        exists.is_active = True
    await db.commit()


async def _reachable(url: str, path: str = "") -> bool:
    """Le service répond-il en HTTP (n'importe quel code) ? Test rapide, jamais bloquant."""
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=2) as http:
            await http.get(f"{url.rstrip('/')}{path}")
        return True
    except httpx.HTTPError:
        return False


@router.get("/capabilities")
async def capabilities(db: DbDep, user: UserDep) -> dict:
    """Service Ollama de cette instance (profil du compose) : configuré ? joignable ? Ressources du
    serveur et modèles conseillés. Les modèles externes (API compatible OpenAI) fonctionnent
    toujours."""
    ollama = await _ollama_base(db) if await get_setting(db, "llm_api_base_url") else ""
    hw = hardware.detect()
    return {
        "ollama": {"configured": bool(ollama), "reachable": await _reachable(ollama, "/api/tags")},
        "hardware": hw,
        "recommendation": hardware.recommend(hw),
    }


@router.get("/ollama")
async def list_ollama(db: DbDep, user: UserDep) -> dict:
    """Modèles présents sur le serveur Ollama + état d'enregistrement."""
    await _require_admin(user)
    base = await _ollama_base(db)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{base}/api/tags")
            resp.raise_for_status()
            tags = resp.json().get("models", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="ollama_unreachable") from exc
    registered = set((await db.execute(select(ModelRegistry.name))).scalars().all())
    return {
        "items": [
            {"name": t["name"], "size": t.get("size"), "registered": t["name"] in registered}
            for t in tags
        ],
        "pulls": _PULLS,
    }


async def _pull(
    base: str, key: str, ref: str | None = None, reg_name: str | None = None, ram: int | None = None
) -> None:
    ref = ref or key
    state = _PULLS[key]
    try:
        async with httpx.AsyncClient(timeout=None) as http:
            async with http.stream("POST", f"{base}/api/pull", json={"name": ref}) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if "error" in ev:
                        raise RuntimeError(str(ev["error"])[:200])
                    state.update(
                        status=ev.get("status", ""),
                        completed=ev.get("completed", state["completed"]),
                        total=ev.get("total", state["total"]),
                    )
        async with SessionLocal() as db:
            await _register(db, ref, reg_name or ref, "OLLAMA", ram)
        state["state"] = "done"
    except Exception as exc:
        logger.warning("ollama pull %s failed: %s", key, type(exc).__name__)
        state.update(state="error", error=str(exc)[:200] or type(exc).__name__)


@router.post("/ollama/pull", status_code=202)
async def pull_ollama(payload: PullRequest, db: DbDep, user: UserDep) -> dict:
    """Télécharge un modèle sur Ollama (progression via GET /models/ollama)."""
    await _require_admin(user)
    if _PULLS.get(payload.name, {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="pull already running")
    _PULLS[payload.name] = {"state": "running", "status": "starting", "completed": 0, "total": 0}
    asyncio.create_task(_pull(await _ollama_base(db), payload.name))
    return {"name": payload.name, "state": "running"}


@router.post("/ollama/register", status_code=201)
async def register_ollama(payload: PullRequest, db: DbDep, user: UserDep) -> dict:
    """Enregistre un modèle déjà présent sur Ollama."""
    await _require_admin(user)
    await _register(db, payload.name)
    return {"name": payload.name}


@router.get("")
async def list_models(
    db: DbDep, user: UserDep, project_id: Annotated[uuid.UUID | None, Query()] = None
) -> dict:
    """Modèles actifs accessibles à l'utilisateur (``project_id`` : ceux utilisables là)."""
    items = (
        (
            await db.execute(
                select(ModelRegistry)
                .where(ModelRegistry.is_active.is_(True), access_filter(user, project_id))
                .order_by(ModelRegistry.name)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_payload(m, user) for m in items]}


@router.post("", status_code=201)
async def create_model(payload: ModelCreate, db: DbDep, user: UserDep) -> dict:
    await _require_admin(user)
    existing = (
        await db.execute(select(ModelRegistry).where(ModelRegistry.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="model name already exists")
    m = ModelRegistry(
        name=payload.name,
        backend_type=payload.backend_type,
        model_identifier=payload.model_identifier,
        ram_usage_mb=payload.ram_usage_mb,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _payload(m)


@router.post("/external/test", dependencies=[Depends(rate_limit_user("model_test", 10))])
async def test_external(payload: ExternalModel, user: UserDep) -> dict:
    """Un appel minimal pour vérifier URL + clé + nom du modèle (erreur assainie)."""
    _check_url(payload.base_url, user)
    svc = OpenAICompatibleService(
        payload.base_url, payload.api_key, timeout=30, public_only=user.role != UserRole.ADMIN
    )
    try:
        await svc.infer(payload.model, 'Reply with the JSON {"ok": true}.', "ping")
    except InferenceError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@router.post("/external", status_code=201)
async def register_external(payload: ExternalModel, db: DbDep, user: UserDep) -> dict:
    """Modèle externe : propriété de son créateur (un admin crée un modèle de la plateforme)."""
    _check_url(payload.base_url, user)
    name = payload.name.strip()
    if (await db.execute(select(ModelRegistry).where(ModelRegistry.name == name))).first():
        raise HTTPException(status_code=409, detail="model name already exists")
    m = ModelRegistry(
        name=name,
        backend_type="OPENAI_API",
        model_identifier=f"{payload.base_url.strip()}#{payload.model.strip()}",
        api_key_enc=encrypt_secret(payload.api_key) if payload.api_key else None,
        owner_id=None if user.role == UserRole.ADMIN else user.id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _payload(m, user)


@router.patch("/{model_id}")
async def update_model(model_id: uuid.UUID, payload: ModelUpdate, db: DbDep, user: UserDep) -> dict:
    m = await db.get(ModelRegistry, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    _require_owner_or_admin(m, user)
    if user.role != UserRole.ADMIN and (payload.ram_usage_mb or payload.model_identifier):
        raise HTTPException(status_code=403, detail="admin required")  # seul is_active
    if payload.is_active is not None:
        m.is_active = payload.is_active
    if payload.ram_usage_mb is not None:
        m.ram_usage_mb = payload.ram_usage_mb
    if payload.model_identifier is not None:
        m.model_identifier = payload.model_identifier
    await db.commit()
    await db.refresh(m)
    return _payload(m)


@router.delete("/{model_id}", status_code=204)
async def delete_model(model_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    m = await db.get(ModelRegistry, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    _require_owner_or_admin(m, user)
    await db.delete(m)
    await db.commit()


# --- Partage d'un modèle (utilisateurs / projets) -----------------------------


class ShareCreate(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    project_id: uuid.UUID | None = None


async def _owned(db: AsyncSession, model_id: uuid.UUID, user: User) -> ModelRegistry:
    m = await db.get(ModelRegistry, model_id)
    if m is None or (user.role != UserRole.ADMIN and m.owner_id != user.id):
        raise HTTPException(status_code=404, detail="model not found")  # pas de fuite d'existence
    return m


@router.get("/{model_id}/shares")
async def list_shares(model_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _owned(db, model_id, user)
    rows = (
        await db.execute(
            select(ModelShare, User, Project)
            .join(User, User.id == ModelShare.user_id, isouter=True)
            .join(Project, Project.id == ModelShare.project_id, isouter=True)
            .where(ModelShare.model_id == model_id)
            .order_by(ModelShare.created_at)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(sh.id),
                "type": "project" if sh.project_id else "user",
                "label": p.title if p is not None else (u.full_name or u.email if u else "?"),
                "email": u.email if u is not None else None,
                "project_id": str(sh.project_id) if sh.project_id else None,
            }
            for sh, u, p in rows
        ]
    }


@router.post("/{model_id}/shares", status_code=201)
async def add_share(model_id: uuid.UUID, payload: ShareCreate, db: DbDep, user: UserDep) -> dict:
    """Autorise un utilisateur (e-mail) ou les LEAD d'un projet à utiliser ce modèle."""
    m = await _owned(db, model_id, user)
    if (payload.email is None) == (payload.project_id is None):
        raise HTTPException(status_code=422, detail="give either email or project_id")
    share = ModelShare(model_id=m.id)
    if payload.email is not None:
        target = (
            await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
        ).scalar_one_or_none()
        if target is None or target.anonymized_hash is not None:
            raise HTTPException(status_code=404, detail="user not found")
        share.user_id = target.id
    else:
        project = await db.get(Project, payload.project_id)
        member = await db.get(ProjectMember, (payload.project_id, user.id)) if project else None
        if project is None or (user.role != UserRole.ADMIN and member is None):
            raise HTTPException(status_code=404, detail="project not found")
        share.project_id = project.id
    dup = await db.execute(
        select(ModelShare.id).where(
            ModelShare.model_id == m.id,
            ModelShare.user_id == share.user_id,
            ModelShare.project_id == share.project_id,
        )
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="already shared")
    db.add(share)
    await db.commit()
    return {"id": str(share.id)}


@router.delete("/{model_id}/shares/{share_id}", status_code=204)
async def remove_share(model_id: uuid.UUID, share_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    await _owned(db, model_id, user)
    share = await db.get(ModelShare, share_id)
    if share is None or share.model_id != model_id:
        raise HTTPException(status_code=404, detail="share not found")
    await db.delete(share)
    await db.commit()


# --- Recherche Hugging Face + installation ---------------------------------

_HF = "https://huggingface.co"
_REPO_RE = r"^[A-Za-z0-9][\w.\-]*/[A-Za-z0-9][\w.\-]*$"
# Ordre de préférence pour l'inférence CPU : bon compromis taille / qualité d'abord ;
# les formats non quantifiés (BF16/F16/F32) et très lourds sont relégués en fin de liste.
_CPU_PREFERENCE = [
    "Q4_K_M",
    "Q4_K_S",
    "Q5_K_M",
    "Q4_0",
    "Q5_K_S",
    "IQ4_XS",
    "Q3_K_M",
    "Q6_K",
    "Q8_0",
]


def _quant_rank(quant: str | None) -> int:
    if quant in _CPU_PREFERENCE:
        return _CPU_PREFERENCE.index(quant)
    return len(_CPU_PREFERENCE) + (1 if quant in ("BF16", "F16", "F32") else 0)


_QUANT_RE = re.compile(r"(IQ\d[\w]*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32)", re.IGNORECASE)


async def _hf_get(path: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(f"{_HF}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="huggingface_unreachable") from exc


@router.get("/search")
async def search_hf(
    user: UserDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> dict:
    """Recherche sur Hugging Face : modèles GGUF (installés via Ollama)."""
    await _require_admin(user)
    data = await _hf_get(
        "/api/models",
        {
            "search": q,
            "filter": "gguf",
            "limit": 20,
            "sort": "downloads",
            "direction": -1,
        },
    )
    return {
        "items": [
            {
                "id": m["id"],
                "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0),
                # modèle de décision (sans chat) : non installable comme modèle générique
                "decision": "decision-model" in (m.get("tags") or []),
            }
            for m in data
            if re.match(_REPO_RE, m.get("id", ""))
        ]
    }


@router.get("/hf/files")
async def hf_files(
    user: UserDep,
    repo: Annotated[str, Query(pattern=_REPO_RE)],
) -> dict:
    """Variantes installables d'un dépôt : quantifications GGUF."""
    await _require_admin(user)
    tree = await _hf_get(f"/api/models/{repo}/tree/main", {"recursive": "true"})
    files = [f for f in tree if f.get("type") == "file"]
    # Une entrée par quantification (fichiers fragmentés regroupés), triées pour le CPU.
    groups: dict[str, dict] = {}
    for f in files:
        name = f["path"]
        if not name.lower().endswith(".gguf") or "mmproj" in name.lower():
            continue
        m = _QUANT_RE.search(name)
        quant = m.group(1).upper() if m else None
        g = groups.setdefault(quant or name, {"quant": quant, "file": name, "size": 0})
        g["size"] += f.get("size") or 0
    items = sorted(groups.values(), key=lambda g: _quant_rank(g["quant"]))
    # Recommandée : la première variante « CPU-friendly » disponible (Q4_K_M de préférence).
    for g in items:
        g["recommended"] = False
    if items and _quant_rank(items[0]["quant"]) < len(_CPU_PREFERENCE):
        items[0]["recommended"] = True
    return {"items": items}


class HFInstall(BaseModel):
    repo: str = Field(pattern=_REPO_RE)
    quant: str | None = Field(default=None, max_length=30, pattern=r"^[\w]+$")
    name: str | None = Field(default=None, max_length=100)


@router.post("/hf/install", status_code=202)
async def install_hf(payload: HFInstall, db: DbDep, user: UserDep) -> dict:
    """Installe un modèle trouvé sur Hugging Face (suivi via GET /models/ollama → pulls)."""
    await _require_admin(user)
    info = await _hf_get(f"/api/models/{payload.repo}")
    if isinstance(info, dict) and "decision-model" in (info.get("tags") or []):
        raise HTTPException(status_code=422, detail="decision_model")  # pas de chat : inutilisable
    short = payload.repo.split("/")[1]
    name = (payload.name or short).strip() or short
    key = f"hf:{payload.repo}:{payload.quant or ''}"
    if _PULLS.get(key, {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="install already running")
    _PULLS[key] = {
        "state": "running",
        "status": "starting",
        "completed": 0,
        "total": 0,
        "name": name,
    }
    ref = f"hf.co/{payload.repo}" + (f":{payload.quant}" if payload.quant else "")
    asyncio.create_task(_pull(await _ollama_base(db), key, ref, name, None))
    return {"key": key, "state": "running"}
