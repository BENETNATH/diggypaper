"""Routes admin : System Settings & API Keys (Module 7).

- ``GET  /admin/settings`` : valeurs par scope (secrets masqués, non définis → null).
- ``PUT  /admin/settings`` : upsert/delete par clé (MASK → inchangé, "" → delete).
- ``POST /admin/settings/smtp/test`` : envoie un email de test (config DB, fallback env).

Admin uniquement (``get_current_admin``). Les secrets ne sont JAMAIS renvoyés en
clair : masque ``••••••••`` si défini, ``null`` sinon.
"""

import logging
import uuid
from typing import Annotated, Literal

from aiosmtplib import SMTPAuthenticationError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_db
from app.core.config import get_settings
from app.core.crypto import MASK
from app.db.models import SettingScope, User
from app.emails import Mailer
from app.settings.keys import KNOWN_KEYS
from app.settings.service import delete_setting, get_all, get_setting, set_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[User, Depends(get_current_admin)]

_EMAIL_ADAPTER = TypeAdapter(EmailStr)

#: Clés SMTP lues en DB pour ``Mailer.from_settings`` (DB > env).
_SMTP_SETTING_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "use_tls",
)


def _smtp_error_category(exc: Exception) -> str:
    """Catégorie d'erreur SMTP pour le message client (le détail reste en log)."""
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
    """Valeurs par scope : GLOBAL toujours, PROJECT/USER si query param fourni."""
    result: dict[str, dict[str, str | None]] = {"GLOBAL": await get_all(db)}
    if project_id is not None:
        result["PROJECT"] = await get_all(db, scope=SettingScope.PROJECT, project_id=project_id)
    if user_id is not None:
        result["USER"] = await get_all(db, scope=SettingScope.USER, user_id=user_id)
    return result


@router.put("/settings")
async def update_settings(
    payload: SettingsUpdate, db: DbDep, admin: AdminDep
) -> dict[str, str | None]:
    """Upsert/delete par clé. Renvoie l'état après mise à jour (masqué)."""
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
    # m3 : transactionnel — toutes les clés sont appliquées ou aucune.
    # Pas de ``db.begin()`` : la session peut avoir une transaction autobegun
    # (ex. le SELECT du login) → InvalidRequestError. commit/rollback explicites.
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
    """Envoie un email de test avec la config SMTP effective (DB, fallback env).

    ``to`` absent → email de l'admin courant. 200 {sent: true} ou
    502 {sent: false, error} si l'envoi échoue. Le détail (traceback) est loggé
    côté serveur ; le client ne reçoit qu'une catégorie (jamais le password).
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
