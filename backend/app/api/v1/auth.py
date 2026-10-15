"""Routes d'authentification : invite, register, login, me."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ACCESS_TOKEN_COOKIE,
    get_client_ip,
    get_current_admin,
    get_current_user,
    get_db,
    get_mailer,
)
from app.api.ratelimit import is_rate_limited, record
from app.core.config import get_settings
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    decode_invite_token,
    hash_password,
    verify_password,
)
from app.db.models import (
    Invitation,
    LanguageCode,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.emails import Mailer
from app.i18n import default_lang, t
from app.invitations import render_invitation, upsert_invitation

router = APIRouter(prefix="/auth", tags=["auth"])

# F7 : limites anti brute-force par IP (15 min) — voir app/api/ratelimit.py.
LOGIN_RATE_LIMIT = 10
REGISTER_RATE_LIMIT = 5
INVITE_RATE_LIMIT = 20


def _rate_limit_dependency(scope: str, limit: int):
    """Fabrique une dépendance FastAPI : 429 rate_limited si la limite par IP est dépassée (F7)."""

    async def dependency(request: Request) -> None:
        ip = get_client_ip(request)
        key = f"{scope}:{ip}"
        if is_rate_limited(key, limit):
            raise HTTPException(status_code=429, detail="rate_limited")
        record(key)

    return dependency


class InviteRequest(BaseModel):
    email: EmailStr
    # m4 : language restreint aux langues supportées (422 si invalide).
    language: Literal["fr", "en", "de", "es"] | None = None


class RegisterRequest(BaseModel):
    token: str
    # F6 : politique de mot de passe — minimum 8 caractères (422 sinon).
    password: str = Field(min_length=8)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    consent: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    language_preference: str
    data_consent_given: bool
    created_at: datetime


@router.post(
    "/invite",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("invite", INVITE_RATE_LIMIT))],
)
async def invite(
    payload: InviteRequest,
    admin: Annotated[User, Depends(get_current_admin)],
    mailer: Annotated[Mailer, Depends(get_mailer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Admin : génère un JWT d'invitation (7 j) et envoie l'email avec le lien d'inscription."""
    language = payload.language or default_lang()
    inv = await upsert_invitation(db, payload.email, language, admin.id)
    subject, body, _ = await render_invitation(db, inv)
    await db.commit()
    await mailer.send(to=payload.email, subject=subject, body=body)
    return Response(status_code=202)


async def _commit_user(db: AsyncSession) -> None:
    """m1 : commit avec catcher IntegrityError (TOCTOU register) → 409, pas 500.

    Le check `existing` plus haut ne couvre pas la course 2-registers-concurrents :
    si le commit viole la contrainte unique email, on renvoie 409 proprement.
    """
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User already exists") from None


@router.post(
    "/register",
    status_code=201,
    dependencies=[Depends(_rate_limit_dependency("register", REGISTER_RATE_LIMIT))],
)
async def register(
    payload: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserOut:
    """Crée le compte à partir d'un JWT d'invitation valide (role RESEARCHER)."""
    try:
        claims = decode_invite_token(payload.token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired invitation token") from None
    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired invitation token")
    invitation = None
    if claims.get("iid"):  # invitation suivie : introuvable = révoquée par un admin / un LEAD
        try:
            invitation = await db.get(Invitation, uuid.UUID(claims["iid"]))
        except ValueError:
            invitation = None
        if invitation is None:
            raise HTTPException(status_code=401, detail="Invalid or expired invitation token")
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="User already exists")
    try:
        language = LanguageCode(claims.get("language", default_lang()))
    except ValueError:
        language = LanguageCode(default_lang())
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.RESEARCHER,
        language_preference=language,
    )
    db.add(user)
    await db.flush()
    # Invitation à un projet : le compte rejoint directement ce projet.
    try:
        project_id = uuid.UUID(claims["project_id"]) if claims.get("project_id") else None
        role = ProjectMemberRole(claims.get("role", "ANNOTATOR"))
    except ValueError:
        project_id, role = None, ProjectMemberRole.ANNOTATOR
    if project_id and await db.get(Project, project_id) is not None:
        db.add(ProjectMember(project_id=project_id, user_id=user.id, role_in_project=role))
    if invitation is not None:
        invitation.accepted_at = datetime.now(UTC)
    await _commit_user(db)
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post(
    "/login",
    dependencies=[Depends(_rate_limit_dependency("login", LOGIN_RATE_LIMIT))],
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Login : cookie JWT httpOnly/Secure + 200 {user} ; 403 consent_required si non accepté."""
    settings = get_settings()
    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if user is None:
        # m3 : oracle de timing — on vérifie un hash factice pour égaliser le temps
        # de réponse (user existe / n'existe pas).
        verify_password(payload.password, DUMMY_HASH)
        raise HTTPException(status_code=401, detail=t(request.state.lang, "invalid_credentials"))
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail=t(request.state.lang, "invalid_credentials"))
    if user.is_active is False:
        raise HTTPException(status_code=403, detail=t(request.state.lang, "account_disabled"))
    if user.data_consent_given is not True and payload.consent is not True:
        raise HTTPException(status_code=403, detail=t(request.state.lang, "consent_required"))
    if payload.consent is True and user.data_consent_given is not True:
        user.data_consent_given = True
        await db.commit()
    response = JSONResponse({"user": UserOut.model_validate(user).model_dump(mode="json")})
    # secure=True UNIQUEMENT en HTTPS (flag COOKIE_SECURE) : sur HTTP un cookie
    # Secure n'est jamais envoyé par le navigateur → tous les appels auth 401.
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=create_access_token(user),
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    language_preference: Literal["fr", "en", "de", "es"] | None = None


@router.patch("/me")
async def update_me(
    payload: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    """Profil : nom affiché et langue préférée."""
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or None
    if payload.language_preference is not None:
        user.language_preference = payload.language_preference
    await db.commit()
    return UserOut.model_validate(user)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)  # même politique que l'inscription


@router.post(
    "/change-password",
    status_code=204,
    dependencies=[Depends(_rate_limit_dependency("login", LOGIN_RATE_LIMIT))],
)
async def change_password(
    payload: PasswordChange,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=403, detail=t(request.state.lang, "invalid_credentials"))
    user.hashed_password = hash_password(payload.new_password)
    await db.commit()


@router.post("/logout", status_code=204)
async def logout() -> Response:
    """Déconnexion : efface le cookie httpOnly (le client ne peut pas le faire lui-même)."""
    response = Response(status_code=204)
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return response
