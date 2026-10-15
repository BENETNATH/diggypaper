"""Qui peut utiliser quel modèle (et donc dépenser CPU / crédits API).

Un modèle est utilisable par : un ADMIN ; son propriétaire (modèle externe défini par un
utilisateur) ; les utilisateurs auxquels il est partagé ; les LEAD d'un projet auquel il est
partagé. Les modèles de la plateforme (``owner_id`` NULL) ne sont utilisables que par partage.
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
    """Condition SQL « ce modèle m'est accessible ». Avec ``project_id`` : dans ce projet."""
    if user.role == UserRole.ADMIN:
        return true()
    if project_id is not None:
        project_scope = ModelShare.project_id == project_id
    else:  # vue d'ensemble : partages faits à l'un de mes projets
        project_scope = ModelShare.project_id.in_(
            select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        )
    shared = select(ModelShare.model_id).where(or_(ModelShare.user_id == user.id, project_scope))
    return or_(ModelRegistry.owner_id == user.id, ModelRegistry.id.in_(shared))


async def usable_models(
    db: AsyncSession, user: User, names: list[str], project_id: uuid.UUID
) -> list[ModelRegistry]:
    """Modèles de ``names`` que ``user`` peut lancer dans ce projet (les autres sont omis)."""
    rows = await db.execute(
        select(ModelRegistry).where(ModelRegistry.name.in_(names), access_filter(user, project_id))
    )
    return list(rows.scalars().all())


def check_public_url(url: str) -> None:
    """Refuse les URL internes (SSRF) pour un modèle externe saisi par un non-admin.

    ponytail: contrôle à l'enregistrement ; une réécriture DNS ultérieure le contournerait
    (épingler l'IP résolue si l'instance accepte des utilisateurs hostiles).
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
