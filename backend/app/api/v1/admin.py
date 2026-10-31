"""Admin routes: System Settings & API Keys (Module 7).

- ``GET  /admin/settings``: values per scope (secrets masked, undefined -> null).
- ``PUT  /admin/settings``: upsert/delete per key (MASK -> unchanged, "" -> delete).
- ``POST /admin/settings/smtp/test``: sends a test email (DB config, env fallback).

Admin only (``get_current_admin``). Secrets are NEVER returned in
plain: mask ``••••••••`` if defined, ``null`` otherwise.
"""

import logging
import uuid
from typing import Annotated, Literal

from aiosmtplib import SMTPAuthenticationError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app import maintenance
from app.api.deps import get_current_admin, get_db
from app.core.config import get_settings
from app.core.crypto import MASK
from app.db.models import SettingScope, User
from app.emails import Mailer
from app.settings.keys import DEFAULTS, KNOWN_KEYS, SECRET_KEYS
from app.settings.service import delete_setting, get_all, get_setting, set_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[User, Depends(get_current_admin)]

_EMAIL_ADAPTER = TypeAdapter(EmailStr)

#: SMTP keys read from DB for ``Mailer.from_settings`` (DB > env).
_SMTP_SETTING_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "use_tls",
)


def _smtp_error_category(exc: Exception) -> str:
    """SMTP error category for the client message (detail stays in the log)."""
    if isinstance(exc, SMTPAuthenticationError):
        return "authentication"
    if isinstance(exc, (ConnectionError, TimeoutError, ConnectionRefusedError)):
        return "connection"
    if isinstance(exc, ValueError):  # config invalide (from_settings / ConnectionConfig)
        return "configuration"
    return "other"


class SettingsUpdate(BaseModel):
    scope: Literal["GLOBAL", "PROJECT", "USER"]
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    values: dict[str, str]

    @model_validator(mode="after")
    def _check_scope(self) -> "SettingsUpdate":
        if self.scope == "GLOBAL" and (self.project_id is not None or self.user_id is not None):
            raise ValueError("project_id/user_id not allowed for GLOBAL scope")
        if self.scope == "PROJECT":
            if self.project_id is None:
                raise ValueError("project_id is required for PROJECT scope")
            if self.user_id is not None:
                raise ValueError("user_id not allowed for PROJECT scope")
        if self.scope == "USER":
            if self.user_id is None:
                raise ValueError("user_id is required for USER scope")
            if self.project_id is not None:
                raise ValueError("project_id not allowed for USER scope")
        return self


class SmtpTestRequest(BaseModel):
    to: EmailStr | None = None


@router.get("/settings")
async def read_settings(
    db: DbDep,
    admin: AdminDep,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, dict[str, str | None]]:
    """Values per scope: GLOBAL always, PROJECT/USER if the query param is given."""
    result: dict[str, dict[str, str | None]] = {"GLOBAL": await get_all(db)}
    if project_id is not None:
        result["PROJECT"] = await get_all(db, scope=SettingScope.PROJECT, project_id=project_id)
    if user_id is not None:
        result["USER"] = await get_all(db, scope=SettingScope.USER, user_id=user_id)
    return result


@router.get("/settings/env")
async def settings_from_environment(admin: AdminDep) -> dict[str, dict]:
    """Values that come from the server ENVIRONMENT (.env) when the database defines none:
    these are the ones that apply while the setting is empty in the UI. Secrets are
    never returned (``set`` only)."""
    s = get_settings()
    env = {
        "llm_api_base_url": DEFAULTS.get("llm_api_base_url", ""),
        "smtp_host": s.smtp_host,
        "smtp_port": str(s.smtp_port) if s.smtp_host else "",
        "smtp_user": s.smtp_user,
        "smtp_password": s.smtp_password,
        "smtp_from_email": s.smtp_from,
        "use_tls": ("true" if s.smtp_use_tls else "false") if s.smtp_host else "",
    }
    return {
        key: {"set": bool(value), "value": None if key in SECRET_KEYS else (value or None)}
        for key, value in env.items()
    }


@router.put("/settings")
async def update_settings(
    payload: SettingsUpdate, db: DbDep, admin: AdminDep
) -> dict[str, str | None]:
    """Upsert/delete per key. Returns the state after update (masked)."""
    scope = SettingScope(payload.scope)
    for key in payload.values:
        if key not in KNOWN_KEYS:
            raise HTTPException(status_code=422, detail=f"unknown setting key: {key}")
    for key, value in payload.values.items():
        if value in (MASK, ""):
            continue  # MASK → inchangé, "" → delete : pas de validation
        if key == "smtp_port":
            try:
                port = int(value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="smtp_port must be an integer") from exc
            if not 1 <= port <= 65535:
                raise HTTPException(status_code=422, detail="smtp_port must be in [1, 65535]")
        elif key == "smtp_from_email":
            try:
                _EMAIL_ADAPTER.validate_python(value)
            except ValidationError as exc:
                raise HTTPException(
                    status_code=422, detail="smtp_from_email must be a valid email"
                ) from exc
    # m3: transactional - all keys are applied or none.
    # No ``db.begin()``: the session may have an autobegun transaction
    # (e.g. the SELECT of the login) -> InvalidRequestError. Explicit commit/rollback.
    try:
        for key, value in payload.values.items():
            if value == MASK:
                continue  # inchangé
            if value == "":
                await delete_setting(
                    db, key, scope, payload.project_id, payload.user_id, commit=False
                )
            else:
                await set_setting(
                    db, key, value, scope, payload.project_id, payload.user_id, commit=False
                )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("update_settings failed, transaction rolled back")
        raise HTTPException(status_code=500, detail="Internal server error") from None
    return await get_all(db, scope, payload.project_id, payload.user_id)


@router.post("/settings/smtp/test")
async def test_smtp(payload: SmtpTestRequest, db: DbDep, admin: AdminDep) -> dict:
    """Sends a test email with the effective SMTP config (DB, env fallback).

    ``to`` absent -> email of the current admin. 200 {sent: true} or
    502 {sent: false, error} if the send fails. The detail (traceback) is logged
    server-side; the client only gets a category (never the password).
    """
    to = payload.to or admin.email
    db_settings = {key: await get_setting(db, key) for key in _SMTP_SETTING_KEYS}
    try:
        mailer = Mailer.from_settings(get_settings(), db_settings)
        await mailer.send(
            to,
            "Diggypaper — test SMTP",
            "Cet email confirme que la configuration SMTP fonctionne.",
        )
    except Exception as exc:  # noqa: BLE001 — tout échec (config ou envoi) → 502
        logger.exception("SMTP test failed")
        return JSONResponse(
            status_code=502,
            content={"sent": False, "error": f"SMTP test failed: {_smtp_error_category(exc)}"},
        )
    return {"sent": True}


@router.get("/storage/status")
async def storage_status(admin: AdminDep) -> dict:
    """Disk usage (light: feeds the administrators' alert banner)."""
    return maintenance.disk_status()


@router.get("/storage")
async def storage(db: DbDep, admin: AdminDep) -> dict:
    """Disk, database, PDFs and projects (size, last activity, deletion deadline)."""
    settings = get_settings()
    projects = await maintenance.project_usage(db)
    return {
        "disk": maintenance.disk_status(),
        "db_bytes": await maintenance.db_bytes(db),
        "pdf_bytes": sum(p["pdf_bytes"] for p in projects),
        "retention_days": settings.project_retention_days,
        "grace_days": settings.project_retention_grace_days,
        "projects": projects[:50],
    }
