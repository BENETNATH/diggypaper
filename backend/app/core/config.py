"""Configuration applicative (pydantic-settings, chargée une seule fois)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Config depuis env (DATABASE_URL, JWT_SECRET, SMTP_*, ...) — stockage DB au lot 3."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/diggypaper"
    jwt_secret: str = "dev-secret-change-me-0123456789abcdef"  # ≥32 octets (HS256)
    access_token_expire_minutes: int = 60
    app_base_url: str = "http://localhost:8000"
    # Cookie JWT : secure=True uniquement en HTTPS (sinon le cookie n'est pas
    # envoyé par le navigateur sur HTTP → tous les appels auth sont 401).
    # true par défaut (prod, HTTPS ou reverse proxy TLS) ; false pour un essai en HTTP local.
    cookie_secure: bool = True
    # Sel d'anonymisation RGPD : sha256(anon_salt + email_original).
    anon_salt: str = "diggypaper-anon"
    # Clé maîtresse AES-256 (Module 7) : 64 hex chars ou 44 base64 chars.
    encryption_key: str = "dev-encryption-key-change-me-0000000000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    # F3 : vide par défaut = pas de CORS (le déploiement est same-origin derrière nginx).
    cors_origins: list[str] = []
    # Bootstrap du premier admin (idempotent) : si définis et qu'aucun admin n'existe,
    # le compte est créé au démarrage. Vide = pas de bootstrap.
    # Langue par défaut (fr/en/de/es) : e-mails, nouveaux comptes, Accept-Language absent.
    default_language: str = "en"
    admin_email: str = ""
    admin_password: str = ""
    # Plafond d'articles ramenés par un import ; 0 = illimité (les requêtes de plus de 10 000
    # résultats sont découpées par date).
    import_max_articles: int = 5000
    # Licence des métadonnées et résultats de l'export RO-Crate (les PDF/textes intégraux
    # gardent la licence de leurs éditeurs). URL + nom affiché ; adaptez-la à votre politique.
    crate_license_url: str = "https://creativecommons.org/licenses/by/4.0/"
    crate_license_name: str = "CC BY 4.0"
    # Lien « politique de confidentialité' (page de l'établissement) ; vide = non affiché.
    privacy_url: str = ""
    # Alerte disque (e-mail aux admins, bandeau) : au-delà de ce taux d'occupation ou sous cet
    # espace libre.
    disk_alert_percent: int = 85
    disk_alert_free_gb: int = 5
    # Projets inactifs : après N jours sans activité, les responsables sont prévenus ; sans
    # activité (ou « Conserver ») pendant le délai de grâce, le projet est supprimé. 0 = jamais.
    project_retention_days: int = 0
    project_retention_grace_days: int = 14


# Valeurs de dev par défaut : refusées en production (fail-fast, F5).
_DEV_JWT_SECRET = "dev-secret-change-me-0123456789abcdef"
_DEV_ANON_SALT = "diggypaper-anon"
_DEV_ENCRYPTION_KEY = "dev-encryption-key-change-me-0000000000"


def validate_settings(settings: Settings) -> None:
    """F5 : fail-fast si un secret est encore la valeur de dev par défaut.

    Lève RuntimeError en nommant les variables à définir
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
        raise RuntimeError("JWT_SECRET trop court : 32 caractères minimum.")
    from app.core.crypto import load_key

    load_key()  # ENCRYPTION_KEY malformée : échec au démarrage, pas au premier chiffrement
    if problems:
        raise RuntimeError(
            "Secrets par défaut (dev) détectés : définir "
            + " et ".join(problems)
            + " avant de démarrer l'application (valeurs non utilisables en production)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
