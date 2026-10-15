"""Envoi d'emails via FastAPI-Mail (SMTP, config depuis env ou settings DB)."""

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import Settings

_TRUTHY = ("1", "true", "yes", "on")


class Mailer:
    """Wrapper FastAPI-Mail injectable (mocké en tests via get_mailer).

    La config SMTP provient de l'env (``Settings``). ``from_settings`` permet
    d'overwriter par les settings DB (Module 7) : DB en priorité, env en fallback.
    """

    def __init__(self, settings: Settings) -> None:
        config = ConnectionConfig(
            MAIL_SERVER=settings.smtp_host,
            MAIL_PORT=settings.smtp_port,
            MAIL_USERNAME=settings.smtp_user,
            MAIL_PASSWORD=settings.smtp_password,
            MAIL_FROM=settings.smtp_from,
            MAIL_STARTTLS=settings.smtp_use_tls,
            MAIL_SSL_TLS=False,
            USE_CREDENTIALS=bool(settings.smtp_user),
        )
        self._mail = FastMail(config)

    @classmethod
    def from_settings(
        cls, settings: Settings, db_settings: dict[str, str | None] | None = None
    ) -> "Mailer":
        """Mailer avec config SMTP effective : settings DB (priorité) > env (fallback).

        ``db_settings`` est un dictionnaire de clés settings (``smtp_host``,
        ``smtp_port``, ``smtp_user``, ``smtp_password``, ``smtp_from_email``,
        ``use_tls``) aux valeurs réelles (démasquées). Les valeurs vides/absentes
        laissent la valeur env.
        """
        db = db_settings or {}

        def pick(db_key: str, env_value: str) -> str:
            value = db.get(db_key)
            return value if value not in (None, "") else env_value

        host = pick("smtp_host", settings.smtp_host)
        port_raw = pick("smtp_port", str(settings.smtp_port))
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError(f"smtp_port must be an integer (got {port_raw!r})") from exc
        user = pick("smtp_user", settings.smtp_user)
        password = pick("smtp_password", settings.smtp_password)
        from_email = pick("smtp_from_email", settings.smtp_from)
        if not from_email:
            raise ValueError("smtp_from_email not configured")
        raw_tls = db.get("use_tls")
        if raw_tls not in (None, ""):
            use_tls = raw_tls.strip().lower() in _TRUTHY
        else:
            use_tls = settings.smtp_use_tls
        return cls(
            settings.model_copy(
                update={
                    "smtp_host": host,
                    "smtp_port": port,
                    "smtp_user": user,
                    "smtp_password": password,
                    "smtp_from": from_email,
                    "smtp_use_tls": use_tls,
                }
            )
        )

    async def send(self, to: str, subject: str, body: str) -> None:
        await self._mail.send_message(
            MessageSchema(recipients=[to], subject=subject, body=body, subtype=MessageType.plain)
        )


_SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from_email", "use_tls")


async def mailer_from_db(db) -> "Mailer | None":
    """Mailer avec la config SMTP des paramètres (DB > env) ; ``None`` si inutilisable."""
    from app.core.config import get_settings
    from app.settings.service import get_setting

    try:
        values = {k: await get_setting(db, k) for k in _SMTP_KEYS}
        settings = get_settings()
        if not (values.get("smtp_host") or settings.smtp_host):
            return None
        return Mailer.from_settings(settings, values)
    except Exception:  # noqa: BLE001 - config invalide : pas d'e-mail, lien affiché à la place
        return None
