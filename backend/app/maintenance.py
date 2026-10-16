"""Entretien du serveur : alerte d'espace disque (admins) et projets inactifs (responsables).

Lancé périodiquement par le worker (celery beat). Rien n'est supprimé tant que
``PROJECT_RETENTION_DAYS`` vaut 0 (défaut) ; sinon : avertissement des responsables à N jours
d'inactivité, suppression à la fin du délai de grâce si rien ne s'est passé depuis.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cleanup import delete_projects
from app.core.config import get_settings
from app.db.models import (
    Article,
    ArticleFile,
    BenchmarkRun,
    ExportJob,
    HumanAnnotation,
    ImportJob,
    InferenceJob,
    Project,
    ProjectMember,
    Search,
    User,
    UserRole,
)
from app.i18n import t

logger = logging.getLogger(__name__)

GB = 1024**3
_ALERT_EVERY = 24 * 3600


def _export_dir() -> Path:
    return Path(os.environ.get("EXPORT_DIR", "/app/exports"))


def disk_status() -> dict:
    """Occupation du disque du serveur (celui des volumes Docker) et seuils d'alerte."""
    settings = get_settings()
    directory = _export_dir() if _export_dir().exists() else Path("/")
    total, used, free = shutil.disk_usage(directory)
    percent = round(used / total * 100, 1) if total else 0.0
    return {
        "total": total,
        "used": used,
        "free": free,
        "percent": percent,
        "low": percent >= settings.disk_alert_percent or free < settings.disk_alert_free_gb * GB,
        "alert_percent": settings.disk_alert_percent,
        "alert_free_gb": settings.disk_alert_free_gb,
    }


async def db_bytes(db: AsyncSession) -> int | None:
    """Taille de la base (PostgreSQL) ; None ailleurs."""
    if db.get_bind().dialect.name != "postgresql":
        return None
    return (await db.execute(select(func.pg_database_size(func.current_database())))).scalar_one()


async def last_activity(db: AsyncSession) -> dict:
    """{id du projet : dernière activité} (import, analyse, relecture, recherche, benchmark,
    export, « Conserver », sinon création)."""
    last: dict = {}

    def bump(rows) -> None:
        for pid, ts in rows:
            if pid is not None and ts is not None:
                ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                last[pid] = max(last.get(pid, ts), ts)

    for model in (ImportJob, InferenceJob, Search, BenchmarkRun, ExportJob):
        bump(
            (
                await db.execute(
                    select(model.project_id, func.max(model.created_at)).group_by(model.project_id)
                )
            ).all()
        )
    bump(
        (
            await db.execute(
                select(Article.project_id, func.max(HumanAnnotation.created_at))
                .join(Article, Article.id == HumanAnnotation.article_id)
                .group_by(Article.project_id)
            )
        ).all()
    )
    bump((await db.execute(select(Project.id, Project.created_at))).all())
    bump((await db.execute(select(Project.id, Project.kept_at))).all())
    return last


async def project_usage(db: AsyncSession, now: datetime | None = None) -> list[dict]:
    """Un enregistrement par projet : taille des PDF, activité, échéance de suppression."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    activity = await last_activity(db)
    pdf = dict(
        (
            await db.execute(
                select(
                    Article.project_id, func.coalesce(func.sum(func.length(ArticleFile.data)), 0)
                )
                .join(Article, Article.id == ArticleFile.article_id)
                .group_by(Article.project_id)
            )
        ).all()
    )
    arts = dict(
        (
            await db.execute(select(Article.project_id, func.count()).group_by(Article.project_id))
        ).all()
    )
    out = []
    for p in (await db.execute(select(Project))).scalars():
        warned = p.retention_warned_at
        warned = warned.replace(tzinfo=UTC) if warned and not warned.tzinfo else warned
        last = activity.get(p.id, now)
        out.append(
            {
                "id": str(p.id),
                "title": p.title,
                "articles": arts.get(p.id, 0),
                "pdf_bytes": int(pdf.get(p.id, 0)),
                "last_activity": last.isoformat(),
                "idle_days": max(0, (now - last).days),
                "warned_at": warned.isoformat() if warned else None,
                "delete_on": (
                    (warned + timedelta(days=settings.project_retention_grace_days)).isoformat()
                    if warned
                    else None
                ),
            }
        )
    return sorted(out, key=lambda r: (-r["pdf_bytes"], -r["idle_days"]))


async def _lead_recipients(db: AsyncSession, project: Project) -> list[User]:
    leads = (
        (
            await db.execute(
                select(User)
                .join(ProjectMember, ProjectMember.user_id == User.id)
                .where(
                    ProjectMember.project_id == project.id, ProjectMember.role_in_project == "LEAD"
                )
            )
        )
        .scalars()
        .all()
    )
    if not leads and project.created_by:
        creator = await db.get(User, project.created_by)
        leads = [creator] if creator else []
    return [u for u in leads if u.email]


async def _send(mailer, user: User, subject_key: str, body_key: str, **params) -> None:
    lang = user.language_preference or "en"
    try:
        await mailer.send(user.email, t(lang, subject_key, **params), t(lang, body_key, **params))
    except Exception:  # noqa: BLE001 - un e-mail perdu ne doit pas arrêter l'entretien
        logger.exception("maintenance: e-mail non envoyé (%s)", subject_key)


async def retention_sweep(db: AsyncSession, mailer, now: datetime | None = None) -> dict:
    """Avertit les responsables des projets inactifs, supprime ceux dont le délai est écoulé."""
    settings = get_settings()
    out = {"warned": 0, "deleted": 0}
    if settings.project_retention_days <= 0:
        return out
    now = now or datetime.now(UTC)
    base = settings.app_base_url.rstrip("/")
    grace = timedelta(days=settings.project_retention_grace_days)
    activity = await last_activity(db)
    doomed = []
    for p in (await db.execute(select(Project))).scalars().all():
        last = activity.get(p.id, now)
        idle = now - last
        warned = p.retention_warned_at
        warned = warned.replace(tzinfo=UTC) if warned and not warned.tzinfo else warned
        if warned and last > warned:  # activité depuis l'avertissement : on repart de zéro
            p.retention_warned_at = warned = None
        if idle < timedelta(days=settings.project_retention_days):
            continue
        if warned is None:
            p.retention_warned_at = now
            for u in await _lead_recipients(db, p):
                await _send(
                    mailer,
                    u,
                    "retention_warn_subject",
                    "retention_warn_body",
                    project=p.title,
                    days=idle.days,
                    date=(now + grace).date().isoformat(),
                    link=f"{base}/projects/{p.id}",
                )
            out["warned"] += 1
        elif now - warned >= grace:
            doomed.append((p, idle.days, await _lead_recipients(db, p)))
    await db.commit()
    for p, days, leads in doomed:
        title = p.title
        await delete_projects(db, [p.id])
        await db.commit()
        for u in leads:
            await _send(
                mailer,
                u,
                "retention_deleted_subject",
                "retention_deleted_body",
                project=title,
                days=days,
            )
        out["deleted"] += 1
    return out


async def disk_alert(db: AsyncSession, mailer, now: float | None = None) -> bool:
    """E-mail aux admins si le disque est presque plein (au plus un par jour)."""
    status = disk_status()
    if not status["low"]:
        return False
    marker = _export_dir() / ".disk_alert"
    now = now or time.time()
    try:
        if now - marker.stat().st_mtime < _ALERT_EVERY:
            return False
    except OSError:
        pass
    pdf = (
        await db.execute(select(func.coalesce(func.sum(func.length(ArticleFile.data)), 0)))
    ).scalar_one()
    base = get_settings().app_base_url.rstrip("/")
    params = {
        "percent": status["percent"],
        "free": round(status["free"] / GB, 1),
        "total": round(status["total"] / GB, 1),
        "db": round((await db_bytes(db) or 0) / GB, 1),
        "pdf": round(int(pdf) / GB, 1),
        "link": f"{base}/settings",
    }
    admins = (await db.execute(select(User).where(User.role == UserRole.ADMIN))).scalars().all()
    for u in admins:
        if u.email:
            await _send(mailer, u, "disk_alert_subject", "disk_alert_body", **params)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        logger.warning(
            "maintenance: marqueur d'alerte disque non écrit (alerte répétée au prochain passage)"
        )
    return True


async def run(db: AsyncSession, mailer) -> dict:
    """Un passage d'entretien : alerte disque puis rétention des projets."""
    return {"disk_alert": await disk_alert(db, mailer), **await retention_sweep(db, mailer)}
