"""Envoi d'emails via aiosmtplib (SMTP, config depuis env ou settings DB)."""

import ssl
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings

_TRUTHY = ("1", "true", "yes", "on")

# Un SMTP injoignable ne doit pas faire attendre la réponse HTTP 60 s
# (défaut) : l'échec renvoie vite le lien à transmettre.
SMTP_TIMEOUT = 10.0


def build_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    """Contexte client : store de confiance système + bundle optionnel.

    ``ca_bundle`` (``SMTP_CA_BUNDLE``) couvre les relais servant une racine
    privée ou une chaîne incomplète absente du store standard — sans jamais
    désactiver la vérification. Un fichier absent ou invalide est signalé en
    ``ValueError`` (config), jamais ignoré.
    """
    context = ssl.create_default_context()
    if ca_bundle:
        if not Path(ca_bundle).is_file():
            raise ValueError(f"SMTP_CA_BUNDLE not found: {ca_bundle!r}")
        try:
            context.load_verify_locations(cafile=ca_bundle)
        except ssl.SSLError as exc:
            raise ValueError(f"SMTP_CA_BUNDLE is not a valid PEM: {ca_bundle!r}") from exc
    return context


class Mailer:
    """Injectable aiosmtplib wrapper (mocked in tests via get_mailer).

    The SMTP config comes from the env (``Settings``). ``from_settings`` allows
    overriding it from the DB settings (Module 7): DB first, env as fallback.
    ``smtp_ca_bundle`` is env-only : a file path is a deployment concern, not
    a value stored in settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # 465 = TLS implicite (pas de greeting ESMTP, un STARTTLS y timeout) ;
        # tout autre port : STARTTLS seulement si le flag est actif.
        self._use_tls = settings.smtp_port == 465
        self._start_tls = settings.smtp_use_tls and settings.smtp_port != 465
        self._context = build_ssl_context(settings.smtp_ca_bundle or None)

    @classmethod
    def from_settings(
        cls, settings: Settings, db_settings: dict[str, str | None] | None = None
    ) -> "Mailer":
        """Mailer with the effective SMTP config: DB settings (priority) > env (fallback).

        ``db_settings`` is a dict of settings keys (``smtp_host``,
        ``smtp_port``, ``smtp_user``, ``smtp_password``, ``smtp_from_email``,
        ``use_tls``) to their real values (unmasked). Empty/absent values leave
        the env value in place.
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
        try:
            validate_email(from_email, check_deliverability=False)
        except EmailNotValidError as exc:
            raise ValueError(f"smtp_from_email is invalid ({from_email!r})") from exc
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
        s = self._settings
        creds = {"username": s.smtp_user, "password": s.smtp_password} if s.smtp_user else {}
        async with aiosmtplib.SMTP(
            hostname=s.smtp_host,
            port=s.smtp_port,
            start_tls=self._start_tls,
            use_tls=self._use_tls,
            timeout=SMTP_TIMEOUT,
            tls_context=self._context,
            **creds,
        ) as session:
            msg = EmailMessage()
            msg["From"] = s.smtp_from
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            await session.mail(s.smtp_from)
            await session.rcpt(to)
            await session.data(msg.as_bytes())


_SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from_email", "use_tls")


async def mailer_from_db(db) -> "Mailer | None":
    """Mailer with the settings' SMTP config (DB > env); ``None`` if unusable."""
    from app.core.config import get_settings
    from app.settings.service import get_setting

    try:
        values = {k: await get_setting(db, k) for k in _SMTP_KEYS}
        settings = get_settings()
        if not (values.get("smtp_host") or settings.smtp_host):
            return None
        return Mailer.from_settings(settings, values)
    except Exception:  # noqa: BLE001 - invalid config: no e-mail, link shown instead
        return None
