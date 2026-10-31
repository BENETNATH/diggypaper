"""Post-job notification (email) — Module 4.

On a Celery job's completion (import / benchmark), an email is sent to the LEAD
of the project, in their preferred language, with the key statistics and a direct
link to the dashboard. **Best-effort**: a sending failure is logged but does not
break the job.
"""

import logging
import uuid

from app.db.models import Project, User
from app.i18n import t

logger = logging.getLogger(__name__)


def _format_stats(job_type: str, stats: dict) -> str:
    """Key statistics as text (language-independent)."""
    lines = []
    if job_type == "benchmark":
        for r in stats.get("results", []):
            lines.append(
                f"- {r['model_name']}: F1={r['f1_score']:.2f} "
                f"acc={r['accuracy']:.2f} t={r['execution_time_seconds']:.1f}s"
            )
        lines.insert(
            0, f"Modèles testés: {stats.get('models', 0)}, articles: {stats.get('articles', 0)}"
        )
    elif job_type == "import":
        lines.append(f"Importés: {stats.get('imported', 0)}, échecs: {stats.get('failed', 0)}")
        if stats.get("truncated"):
            lines.append(f"(tronqué: count réel {stats.get('total_count', '?')})")
    else:
        lines.append(str(stats))
    return "\n".join(lines) if lines else "—"


async def notify_job_complete(
    project_id: str | uuid.UUID,
    job_type: str,
    stats: dict,
    session_factory=None,
    mailer=None,
    app_base_url: str = "http://localhost:8000",
) -> None:
    """Sends the end-of-job email to the project's LEAD (best-effort).

    - ``session_factory`` injectable (tests); else the caller provides its session.
    - ``mailer`` injectable (tests); else ``None`` → no sending (the caller passes its mailer).
    - Never raises: every failure is logged (the main job is not broken).
    """
    try:
        if isinstance(project_id, uuid.UUID):
            project_uuid = project_id
        else:
            project_uuid = uuid.UUID(str(project_id))
        async with session_factory() as session:
            project = await session.get(Project, project_uuid)
            if project is None:
                logger.warning("notify: project not found %s", project_id)
                return
            lead = None
            if project.created_by is not None:
                lead = await session.get(User, project.created_by)
            if lead is None or not lead.email:
                logger.warning("notify: no lead email for project %s", project_id)
                return
            lang = lead.language_preference or "en"
            link = f"{app_base_url.rstrip('/')}/projects/{project.id}"
            subject = t(lang, "job_done_subject", job_type=job_type)
            body = t(
                lang,
                "job_done_body",
                job_type=job_type,
                project_title=project.title,
                stats=_format_stats(job_type, stats),
                link=link,
            )
        if mailer is None:
            logger.info("notify: no mailer, skipping send (project %s)", project_id)
            return
        await mailer.send(lead.email, subject, body)
        logger.info("notify: job %s completion email sent (project %s)", job_type, project_id)
    except Exception:
        logger.exception("notify: failed to send job completion email (project %s)", project_id)
