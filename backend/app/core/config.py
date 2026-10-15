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
    # false en dev (HTTP), true en prod (HTTPS).
    cookie_secure: bool = False
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
    # Dossier des modèles ONNX (identifiants relatifs, ex. models/laya-400m-v1.onnx).
    # Langue par défaut (fr/en/de/es) : e-mails, nouveaux comptes, Accept-Language absent.
    default_language: str = "en"
    models_dir: str = "models"
    # Serveurs de modèles de décision (protocole /v1/systemone) pré-enregistrés au démarrage.
    laya_url: str = ""  # ex. http://laya:8000 (service du compose)
    kev_url: str = ""  # ex. http://kev:8080 (llama.cpp : llama serve -hf ggml-org/Kev-4B-GGUF)
    admin_email: str = ""
    admin_password: str = ""


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
    if problems:
        raise RuntimeError(
            "Secrets par défaut (dev) détectés : définir "
            + " et ".join(problems)
            + " avant de démarrer l'application (valeurs non utilisables en production)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
