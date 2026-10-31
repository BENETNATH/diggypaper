"""Model routes (ModelRegistry) - Module 3 (Arena).

Read: the models accessible to the user (cf. ``app.model_access``). Write: ADMIN
for the platform's models (local, servers); any user for their own
external models (OpenAI-compatible API), which they can share with users or projects.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from redis import asyncio as redis_asyncio
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import hardware
from app.api.deps import get_current_user, get_db, rate_limit_user
from app.core.crypto import encrypt_secret
from app.db.base import SessionLocal
from app.db.models import (
    BenchmarkRun,
    BenchmarkStatus,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    ModelShare,
    Project,
    ProjectMember,
    Search,
    User,
    UserRole,
)
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    infer_with,
    make_inference_service,
    model_lock_key,
    ollama_ref,
    public_model_ref,
)
from app.model_access import access_filter, check_public_url, check_url_secrets, is_internal_url
from app.ollama_mode import COMPOSE_ROOT, MODE
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
    """Generative model hosted elsewhere (OpenAI-compatible API)."""

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
    """A non-admin cannot point the server at the internal network."""
    if user.role == UserRole.ADMIN:
        return
    try:
        check_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _check_url_secrets(url: str) -> None:
    """422 route-level (fixed detail): the pydantic 422 would return the URL (the secret)."""
    try:
        check_url_secrets(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _check_openai_identifier(ident: str) -> None:
    """OPENAI_API = ``<base_url>#<model>``: one "#", two non-empty parts, base without secret."""
    url, sep, name = ident.partition("#")
    if not sep or "#" in name or not url.strip() or not name.strip():
        raise HTTPException(status_code=422, detail="model_identifier must be <base_url>#<model>")
    _check_url_secrets(url)


def _payload(m: ModelRegistry, user: User | None = None) -> dict:
    """Same view for everyone: an OPENAI_API never comes out as-is (model name + endpoint)."""
    if m.backend_type == "OPENAI_API":
        ident, endpoint = public_model_ref("OPENAI_API", m.model_identifier)
    else:
        ident, endpoint = m.model_identifier, None
    return {
        "mine": user is not None and m.owner_id == user.id,
        "platform": m.owner_id is None,
        "internal": m.backend_type != "OPENAI_API" or is_internal_url(endpoint),
        "id": str(m.id),
        "name": m.name,
        "backend_type": m.backend_type,
        "model_identifier": ident or "" if m.backend_type == "OPENAI_API" else m.model_identifier,
        "endpoint": endpoint,
        "ram_usage_mb": m.ram_usage_mb,
        "is_active": m.is_active,
    }


logger = logging.getLogger(__name__)

# Ollama downloads in progress/finished, by model name.
# ponytail: in-memory state (1 uvicorn process); DB table if multiple workers.
_PULLS: dict[str, dict] = {}


class PullRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300, pattern=r"^[\w.\-:/@]+$")


async def _ollama_base(db: AsyncSession) -> str:
    """Ollama root URL = llm_api_base_url without the /v1 suffix."""
    base = (await get_setting(db, "llm_api_base_url")).rstrip("/")
    return base.removesuffix("/v1")


async def _require_ollama(db: AsyncSession) -> str:
    """Refuses the Ollama actions forbidden by the deployment mode (409, precise detail).

    Returns the Ollama base (otherwise the existing behavior is unchanged)."""
    if MODE == "off":
        raise HTTPException(status_code=409, detail="ollama_disabled_by_deployment")
    base = await _ollama_base(db)
    if MODE == "host" and base == COMPOSE_ROOT:
        raise HTTPException(status_code=409, detail="ollama_base_url_is_compose_default")
    return base


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
    """Does the service answer over HTTP (any code)? Quick test, never blocking."""
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
    """Ollama service of this instance (compose profile): configured? reachable? Server
    resources and recommended models. External models (OpenAI-compatible API)
    always work."""
    if MODE == "off":
        ollama = {"mode": MODE, "configured": False, "reachable": False}  # AUCUN ping
    else:
        base = await _ollama_base(db)
        if MODE == "host" and base == COMPOSE_ROOT:
            logger.warning(
                "OLLAMA_MODE=host mais llm_api_base_url pointe encore sur le conteneur du "
                "compose (%s) : définir LLM_API_BASE_URL sur l'Ollama de la machine hôte",
                base,
            )
        configured = bool(base) and not (MODE == "host" and base == COMPOSE_ROOT)
        reachable = await _reachable(base, "/api/tags") if configured else False
        ollama = {"mode": MODE, "configured": configured, "reachable": reachable}
    hw = hardware.detect()
    return {
        "ollama": ollama,
        "hardware": hw,
        "recommendation": hardware.recommend(hw),
    }


@router.get("/ollama")
async def list_ollama(db: DbDep, user: UserDep) -> dict:
    """Models present on the Ollama server + registration state."""
    await _require_admin(user)
    base = await _require_ollama(db)
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


async def _ollama_delete(db: AsyncSession, name: str) -> None:
    """Deletes a model on the Ollama side (idempotent: 404 = already deleted).
    502 ``ollama_unreachable`` (network) or ``ollama_delete_failed`` (HTTP >= 400 != 404)."""
    base = await _ollama_base(db)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.request("DELETE", f"{base}/api/delete", json={"name": name})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="ollama_unreachable") from exc
    if resp.status_code >= 400 and resp.status_code != 404:
        raise HTTPException(status_code=502, detail="ollama_delete_failed")


async def _pull(
    base: str, key: str, ref: str | None = None, reg_name: str | None = None, ram: int | None = None
) -> None:
    ref = ref or key
    state = _PULLS[key]
    try:
        # A pull streams for hours, so no read timeout; connect/pool stay bounded.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)
        ) as http:
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
    """Downloads a model on Ollama (progress via GET /models/ollama)."""
    await _require_admin(user)
    await _require_ollama(db)
    if _PULLS.get(payload.name, {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="pull already running")
    _PULLS[payload.name] = {"state": "running", "status": "starting", "completed": 0, "total": 0}
    asyncio.create_task(_pull(await _ollama_base(db), payload.name))
    return {"name": payload.name, "state": "running"}


@router.post("/ollama/register", status_code=201)
async def register_ollama(payload: PullRequest, db: DbDep, user: UserDep) -> dict:
    """Registers a model already present on Ollama."""
    await _require_admin(user)
    await _require_ollama(db)
    await _register(db, payload.name)
    return {"name": payload.name}


@router.get("")
async def list_models(
    db: DbDep, user: UserDep, project_id: Annotated[uuid.UUID | None, Query()] = None
) -> dict:
    """Active models accessible to the user (``project_id``: those usable there)."""
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
    if payload.backend_type == "OLLAMA":
        await _require_ollama(db)
    if payload.backend_type == "OPENAI_API":
        _check_openai_identifier(payload.model_identifier)
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
    """A minimal call to check URL + key + model name (sanitized error)."""
    _check_url_secrets(payload.base_url)
    if "#" in payload.model:
        raise HTTPException(status_code=422, detail="model must not contain '#'")
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
    """External model: owned by its creator (an admin creates a platform model)."""
    _check_url_secrets(payload.base_url)
    if "#" in payload.model:
        raise HTTPException(status_code=422, detail="model must not contain '#'")
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
        if m.backend_type == "OPENAI_API":
            _check_openai_identifier(payload.model_identifier)
        m.model_identifier = payload.model_identifier
    await db.commit()
    await db.refresh(m)
    return _payload(m)


def _redis():
    return redis_asyncio.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=2
    )


async def _model_busy(m: ModelRegistry) -> bool:
    """An inference lock is held on this model. Redis unreachable -> False (the ping stays
    best-effort: the button must not die with the broker)."""
    r = _redis()
    try:
        return await r.get(model_lock_key(m.backend_type, m.model_identifier)) is not None
    except (RedisError, OSError):
        logger.warning("test modèle : lock %s illisible (Redis)", m.name)
        return False
    finally:
        await r.aclose()


@router.post("/{model_id}/test", dependencies=[Depends(rate_limit_user("model_test", 10))])
async def test_installed(model_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Minimal call to confirm an installed model is operational (sanitized error).

    The model is called with its real service (instance settings for the
    local backends, URL + encrypted key for ``OPENAI_API``); the ping warms it
    as a bonus. The result is a state (200), not an HTTP fault.
    """
    m = await db.get(ModelRegistry, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    _require_owner_or_admin(m, user)
    if m.backend_type == "OLLAMA":
        await _require_ollama(db)  # mode off / host + base compose : pas d'appel (409)
    if m.backend_type != "OPENAI_API" and await _model_busy(m):
        return {"ok": False, "error": "model_busy"}  # une analyse génère déjà sur ce modèle
    default = await make_inference_service(db)
    try:
        await infer_with(m, default, 'Reply with the JSON {"ok": true}.', "ping")
    except InferenceError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


async def _in_use(db: AsyncSession, name: str) -> bool:
    """The model is referenced by an analysis or a PENDING/RUNNING benchmark, or a
    scheduled analysis waiting."""
    active = (InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING)
    job = await db.execute(
        select(InferenceJob.id)
        .where(
            InferenceJob.status.in_(active),
            or_(InferenceJob.model_used == name, InferenceJob.prefilter_model == name),
        )
        .limit(1)
    )
    if job.first() is not None:
        return True
    # Python filter: no JSONB containment portable SQLite/PG.
    runs = await db.execute(
        select(BenchmarkRun.models_tested).where(
            BenchmarkRun.status.in_((BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING))
        )
    )
    if any(name in (tested or []) for tested in runs.scalars()):
        return True
    # Scheduled analysis waiting: it will start with this model (JSON: Python filter).
    waiting = await db.execute(select(Search.schedule).where(Search.schedule_state == "waiting"))
    return any(
        name in (cfg.get("model"), cfg.get("prefilter_model")) for cfg in waiting.scalars() if cfg
    )


@router.delete("/{model_id}", status_code=204)
async def delete_model(model_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    m = await db.get(ModelRegistry, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    _require_owner_or_admin(m, user)
    if m.backend_type == "OLLAMA":
        await _require_admin(user)  # poids serveur : jamais sur le seul invariant owner_id NULL
    if await _in_use(db, m.name):
        raise HTTPException(status_code=409, detail="model_in_use")
    if m.backend_type == "OLLAMA":
        # Weights removed BEFORE the registry row (502 -> row kept), only if Ollama
        # is managed by the instance and no other row points at the same weights.
        try:
            await _require_ollama(db)
            managed = True
        except HTTPException:
            managed = False
        # "qwen3" == "qwen3:latest" for Ollama: we compare the normalized identifiers.
        others = (
            await db.execute(
                select(ModelRegistry.model_identifier).where(
                    ModelRegistry.backend_type == "OLLAMA", ModelRegistry.id != m.id
                )
            )
        ).scalars()
        shared = any(ollama_ref(o) == ollama_ref(m.model_identifier) for o in others)
        if managed and not shared:
            await _ollama_delete(db, m.model_identifier)
    await db.delete(m)
    await db.commit()


# --- Sharing a model (users / projects) -----------------------------


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
    """Allows a user (e-mail) or the LEADs of a project to use this model."""
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
# Preference order for CPU inference: good size/quality trade-off first;
# non-quantized (BF16/F16/F32) and very heavy formats are pushed to the end of the list.
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
    """Search on Hugging Face: GGUF models (installed via Ollama)."""
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
                # decision model (no chat): not installable as a generic model
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
    """Installable variants of a repo: GGUF quantizations."""
    await _require_admin(user)
    tree = await _hf_get(f"/api/models/{repo}/tree/main", {"recursive": "true"})
    files = [f for f in tree if f.get("type") == "file"]
    # One entry per quantization (fragmented files grouped), sorted for the CPU.
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
    # Recommended: the first available "CPU-friendly" variant (Q4_K_M preferred).
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
    """Installs a model found on Hugging Face (followed via GET /models/ollama -> pulls)."""
    await _require_admin(user)
    await _require_ollama(db)  # AVANT tout appel HF
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
