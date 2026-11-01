"""Application configuration (pydantic-settings, loaded once)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Config from env (DATABASE_URL, JWT_SECRET, SMTP_*, ...) — DB storage in lot 3."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/diggypaper"
    jwt_secret: str = "dev-secret-change-me-0123456789abcdef"  # ≥32 octets (HS256)
    access_token_expire_minutes: int = 60
    app_base_url: str = "http://localhost:8000"
    # JWT cookie: secure=True only on HTTPS (otherwise the browser does not send
    # the cookie over HTTP → every auth call is 401).
    # Default true (prod, HTTPS or TLS reverse proxy); false for local HTTP testing.
    cookie_secure: bool = True
    # Sel d'anonymisation RGPD : sha256(anon_salt + email_original).
    anon_salt: str = "diggypaper-anon"
    # AES-256 master key (Module 7): 64 hex chars or 44 base64 chars.
    encryption_key: str = "dev-encryption-key-change-me-0000000000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    # F3: empty by default = no CORS (the deployment is same-origin behind nginx).
    cors_origins: list[str] = []
    # First-admin bootstrap (idempotent): if defined and no admin exists, the account
    # is created at startup. Empty = no bootstrap.
    # Default language (fr/en/de/es): emails, new accounts, missing Accept-Language.
    default_language: str = "en"
    admin_email: str = ""
    admin_password: str = ""
    # Cap on articles fetched by an import; 0 = unlimited (queries returning more than
    # 10,000 results are split by date).
    import_max_articles: int = 5000
    # License of the RO-Crate export metadata and results (full PDFs/texts keep their
    # publishers' license). Displayed URL + name; adapt it to your policy.
    crate_license_url: str = "https://creativecommons.org/licenses/by/4.0/"
    crate_license_name: str = "CC BY 4.0"
    # "Privacy policy" link (institution page); empty = not displayed.
    privacy_url: str = ""
    # Disk alert (email to admins, banner): beyond this usage rate or below this free space.
    disk_alert_percent: int = 85
    disk_alert_free_gb: int = 5
    # Inactive projects: after N days without activity, the leads are warned; with no
    # activity (or "Keep") during the grace period, the project is deleted. 0 = never.
    project_retention_days: int = 0
    project_retention_grace_days: int = 14


# Dev default values: rejected in production (fail-fast, F5).
_DEV_JWT_SECRET = "dev-secret-change-me-0123456789abcdef"
_DEV_ANON_SALT = "diggypaper-anon"
_DEV_ENCRYPTION_KEY = "dev-encryption-key-change-me-0000000000"


def validate_settings(settings: Settings) -> None:
    """F5: fail-fast if a secret is still its dev default value.

    Raises RuntimeError naming the variables to define
    (JWT_SECRET / ANON_SALT / ENCRYPTION_KEY).
    """
    problems = []
    if settings.jwt_secret == _DEV_JWT_SECRET:
        problems.append("JWT_SECRET")
    if settings.anon_salt == _DEV_ANON_SALT:
        problems.append("ANON_SALT")
    if settings.encryption_key == _DEV_ENCRYPTION_KEY:
        problems.append("ENCRYPTION_KEY")
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET too short: 32 characters minimum.")
    from app.core.crypto import load_key

    load_key()  # malformed ENCRYPTION_KEY: fail at startup, not at the first encryption
    if problems:
        raise RuntimeError(
            "Default (dev) secrets detected: set "
            + " and ".join(problems)
            + " before starting the application (values not usable in production)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
