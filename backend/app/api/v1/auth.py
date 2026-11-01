"""Authentication routes: invite, register, login, me, password reset."""

import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ACCESS_TOKEN_COOKIE,
    _load_user,
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
    PasswordReset,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.emails import Mailer
from app.i18n import default_lang, t
from app.invitations import render_invitation, upsert_invitation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# F7: anti brute-force limits per IP (15 min) - see app/api/ratelimit.py.
LOGIN_RATE_LIMIT = 10
REGISTER_RATE_LIMIT = 5
INVITE_RATE_LIMIT = 20
PASSWORD_RESET_RATE_LIMIT = 5
RESET_TOKEN_TTL = timedelta(hours=1)


def _rate_limit_dependency(scope: str, limit: int):
    """Builds a FastAPI dependency: 429 rate_limited if the per-IP limit is exceeded (F7)."""

    async def dependency(request: Request) -> None:
        ip = get_client_ip(request)
        key = f"{scope}:{ip}"
        if is_rate_limited(key, limit):
            raise HTTPException(status_code=429, detail="rate_limited")
        record(key)

    return dependency


class InviteRequest(BaseModel):
    email: EmailStr
    # m4: language restricted to supported languages (422 if invalid).
    language: Literal["fr", "en", "de", "es"] | None = None
    # Admin: the created account will be able to create projects ("creator").
    can_create_projects: bool = True


class RegisterRequest(BaseModel):
    token: str
    # F6: password policy - minimum 8 characters (422 otherwise).
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
    can_create_projects: bool = Field(validation_alias="may_create_projects")
    created_at: datetime


@router.get("/config")
async def public_config() -> dict:
    """Public settings (login page): link to the privacy policy."""
    return {"privacy_url": get_settings().privacy_url or None}


@router.post(
    "/invite",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("invite", INVITE_RATE_LIMIT))],
)
async def invite(
    payload: InviteRequest,
    admin: Annotated[User, Depends(get_current_admin)],
    mailer: Annotated[Mailer | None, Depends(get_mailer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Admin: generates an invitation JWT (7 d) and sends the email with the registration link."""
    language = payload.language or default_lang()
    inv = await upsert_invitation(
        db, payload.email, language, admin.id, can_create_projects=payload.can_create_projects
    )
    subject, body, link = await render_invitation(db, inv)
    await db.commit()
    try:
        if mailer is None:
            raise RuntimeError("SMTP not configured")
        await mailer.send(to=payload.email, subject=subject, body=body)
    except Exception:  # noqa: BLE001 - SMTP missing/down: the admin forwards the link themselves
        logger.warning("invitation e-mail not sent", exc_info=True)
        return JSONResponse({"email_sent": False, "link": link}, status_code=202)
    return JSONResponse({"email_sent": True, "link": None}, status_code=202)


async def _commit_user(db: AsyncSession) -> None:
    """m1: commit with an IntegrityError catcher (TOCTOU register) -> 409, not 500.

    The `existing` check above does not cover the two-concurrent-registers race:
    if the commit violates the unique email constraint, we return 409 cleanly.
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
    """Creates the account from a valid invitation JWT (role RESEARCHER)."""
    try:
        claims = decode_invite_token(payload.token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired invitation token") from None
    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired invitation token")
    invitation = None
    if claims.get("iid"):  # tracked invitation: not found = revoked by an admin / a LEAD
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
        hashed_password=await asyncio.to_thread(hash_password, payload.password),
        full_name=payload.full_name,
        role=UserRole.RESEARCHER,
        language_preference=language,
        can_create_projects=bool(invitation and invitation.can_create_projects),
    )
    db.add(user)
    await db.flush()
    # Invitation to a project: the account joins that project directly.
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
    """Login: httpOnly/Secure JWT cookie + 200 {user}; 403 consent_required if not accepted."""
    settings = get_settings()
    user = (
        await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    ).scalar_one_or_none()
    if user is None:
        # m3: timing oracle - we verify a dummy hash to equalize the
        # response time (user exists / does not exist).
        await asyncio.to_thread(verify_password, payload.password, DUMMY_HASH)
        raise HTTPException(status_code=401, detail=t(request.state.lang, "invalid_credentials"))
    if not await asyncio.to_thread(verify_password, payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail=t(request.state.lang, "invalid_credentials"))
    if user.is_active is False:
        raise HTTPException(status_code=403, detail=t(request.state.lang, "account_disabled"))
    if user.data_consent_given is not True and payload.consent is not True:
        raise HTTPException(status_code=403, detail=t(request.state.lang, "consent_required"))
    if payload.consent is True and user.data_consent_given is not True:
        user.data_consent_given = True
        await db.commit()
    response = JSONResponse({"user": UserOut.model_validate(user).model_dump(mode="json")})
    # secure=True ONLY on HTTPS (COOKIE_SECURE flag): on HTTP a Secure
    # cookie is never sent by the browser -> all auth calls 401.
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
    """Profile: display name and preferred language."""
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or None
    if payload.language_preference is not None:
        user.language_preference = payload.language_preference
    await db.commit()
    return UserOut.model_validate(user)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)  # same policy as registration


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
    if not await asyncio.to_thread(verify_password, payload.current_password, user.hashed_password):
        raise HTTPException(status_code=403, detail=t(request.state.lang, "invalid_credentials"))
    user.hashed_password = await asyncio.to_thread(hash_password, payload.new_password)
    user.token_version = (user.token_version or 0) + 1  # cuts the other sessions
    await db.commit()


def _token_digest(token: str) -> str:
    """SHA-256 hex of the reset token (only the digest is stored)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    # same policy as register / change-password (422 otherwise).
    new_password: str = Field(min_length=8)


@router.post(
    "/password-reset/request",
    dependencies=[Depends(_rate_limit_dependency("password_reset", PASSWORD_RESET_RATE_LIMIT))],
)
async def password_reset_request(
    payload: PasswordResetRequest,
    mailer: Annotated[Mailer | None, Depends(get_mailer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Sends the reset link (1 h TTL). Always 200 with the same generic body,
    whatever the email exists or not (no user enumeration)."""
    user = (
        await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    ).scalar_one_or_none()
    if user is not None and user.is_active:
        lang = user.language_preference.value if user.language_preference else default_lang()
        token = secrets.token_urlsafe(32)
        db.add(
            PasswordReset(
                user_id=user.id,
                token_hash=_token_digest(token),
                expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
            )
        )
        await db.commit()
        # same public link pattern as the invitation emails (app/invitations.py).
        link = f"{get_settings().app_base_url}/reset?token={token}"
        try:
            if mailer is None:
                raise RuntimeError("SMTP not configured")
            await mailer.send(
                to=user.email,
                subject=t(lang, "reset_subject"),
                body=t(lang, "reset_body", link=link),
            )
        except Exception:  # noqa: BLE001 - SMTP down: the link is lost, never leaked
            logger.warning("password reset e-mail not sent", exc_info=True)
    return {"detail": "ok"}


@router.post(
    "/password-reset/confirm",
    dependencies=[Depends(_rate_limit_dependency("login", LOGIN_RATE_LIMIT))],
)
async def password_reset_confirm(
    payload: PasswordResetConfirm, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    """Sets the new password from a valid (unexpired, unconsumed) reset token."""
    reset = (
        await db.execute(
            select(PasswordReset).where(PasswordReset.token_hash == _token_digest(payload.token))
        )
    ).scalar_one_or_none()
    if reset is None or reset.consumed_at is not None:
        raise HTTPException(status_code=404, detail="invalid_token")
    # SQLite returns naive datetimes: re-attach UTC before comparing (cf. app/maintenance.py).
    expires = reset.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="expired_token")
    user = await db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="invalid_token")
    user.hashed_password = await asyncio.to_thread(hash_password, payload.new_password)
    user.token_version = (user.token_version or 0) + 1  # cuts the other sessions
    reset.consumed_at = datetime.now(UTC)
    await db.commit()
    return {"detail": "ok"}


@router.post("/logout", status_code=204)
async def logout(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    """Logout: revokes the JWT (token_version) and clears the httpOnly cookie."""
    try:
        user = await _load_user(request, db)
        user.token_version = (user.token_version or 0) + 1
        await db.commit()
    except HTTPException:
        pass  # already logged out / invalid token: just wipe the cookie
    response = Response(status_code=204)
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return response
