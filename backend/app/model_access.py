"""Who can use which model (and hence spend CPU / API credits).

A model is usable by: an ADMIN; its owner (external model defined by a
user); the users it is shared with; the LEADs of a project it is shared
with. Platform models (``owner_id`` NULL) are usable only by sharing.
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from urllib.parse import urlsplit

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelRegistry, ModelShare, ProjectMember, User, UserRole


def access_filter(user: User, project_id: uuid.UUID | None = None):
    """SQL condition "this model is accessible to me". With ``project_id``: within that project."""
    if user.role == UserRole.ADMIN:
        return true()
    if project_id is not None:
        project_scope = ModelShare.project_id == project_id
    else:  # overview: shares made to one of my projects
        project_scope = ModelShare.project_id.in_(
            select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        )
    shared = select(ModelShare.model_id).where(or_(ModelShare.user_id == user.id, project_scope))
    return or_(ModelRegistry.owner_id == user.id, ModelRegistry.id.in_(shared))


async def usable_models(
    db: AsyncSession, user: User, names: list[str], project_id: uuid.UUID
) -> list[ModelRegistry]:
    """Models from ``names`` that ``user`` can run in this project (the others are omitted)."""
    rows = await db.execute(
        select(ModelRegistry).where(ModelRegistry.name.in_(names), access_filter(user, project_id))
    )
    return list(rows.scalars().all())


def check_public_url(url: str) -> None:
    """Refuses internal URLs (SSRF) for an external model entered by a non-admin.

    ponytail: check at registration; a later DNS rewrite would bypass it
    (pin the resolved IP if the instance accepts hostile users).
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url must be an http(s) URL")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("base_url host cannot be resolved") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("base_url must be a public address")


def check_url_secrets(url: str) -> None:
    """A base URL never carries a secret: the token goes in ``api_key`` (encrypted).

    Userinfo (``user:pw@``, even user alone), query (even empty) and fragment (our
    URL/model separator) are refused. Fixed messages: the URL is never echoed.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise ValueError("base_url is not a valid URL") from None
    if "@" in parts.netloc or "?" in url or "#" in url:
        raise ValueError("base_url must not contain credentials, query or fragment")


def is_internal_url(url: str | None) -> bool:
    """True if the URL points to localhost or a non-global IP (internal network).

    No DNS: a domain name is considered external (consistent with ``check_public_url``,
    which resolves because it refuses; here we do not wait on the network to display a badge).
    """
    if not url:
        return False
    try:
        host = (urlsplit(url.strip()).hostname or "").strip("[]").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False
