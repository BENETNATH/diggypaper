"""i18n : résolution de langue (Accept-Language) + catalogue de messages (fr/en/de/es)."""

import json
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SUPPORTED_LANGS: tuple[str, ...] = ("fr", "en", "de", "es")
FALLBACK_LANG = "en"  # catalogue : langue de repli pour une clé manquante

_MESSAGES: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).parent / "messages.json").read_text(encoding="utf-8")
)


def default_lang() -> str:
    """Langue par défaut de l'instance (``DEFAULT_LANGUAGE`` dans .env), ``en`` si invalide."""
    from app.core.config import get_settings

    lang = get_settings().default_language.strip().lower()
    return lang if lang in SUPPORTED_LANGS else FALLBACK_LANG


def _parse_qvalue(part: str) -> tuple[str, float] | None:
    """Parse une entrée Accept-Language (RFC 4647) → (code, q) ou None si invalide.

    - code : partie avant le premier ';' (ou le tout), région retirée, minuscule.
    - q : défaut 1.0 ; q=0 → (code, 0.0) ; q invalide (non numérique) → None.
    """
    part = part.strip()
    if not part:
        return None
    code, _, params = part.partition(";")
    code = code.strip().lower().split("-")[0]
    if not code:
        return None
    q = 1.0
    for param in params.split(";"):
        param = param.strip().lower()
        if param.startswith("q="):
            raw = param[2:].strip()
            try:
                q = float(raw)
            except ValueError:
                return None  # q invalide → entrée ignorée
            break
    return code, q


def resolve_lang(accept_language: str | None) -> str:
    """Choisit la langue la plus pertinente parmi fr/en/de/es (fallback en).

    Parse les q-values (RFC 4647) : tri par q décroissant, puis par ordre
    d'apparition en cas d'égalité. q=0 et codes inconnus sont ignorés.
    """
    candidates: list[tuple[float, int, str]] = []
    if accept_language:
        for index, part in enumerate(accept_language.split(",")):
            parsed = _parse_qvalue(part)
            if parsed is None:
                continue
            code, q = parsed
            if q <= 0:  # q=0 → langue explicitement non souhaitée
                continue
            if code not in SUPPORTED_LANGS:
                continue
            candidates.append((q, index, code))
    if candidates:
        # q décroissant, puis ordre d'apparition (index croissant).
        candidates.sort(key=lambda c: (-c[0], c[1]))
        return candidates[0][2]
    return default_lang()


def t(lang: str, key: str, **params: str) -> str:
    """Traduit une clé ; fallback en, puis la clé elle-même si absente."""
    table = _MESSAGES.get(lang, _MESSAGES[FALLBACK_LANG])
    template = table.get(key, _MESSAGES[FALLBACK_LANG].get(key, key))
    return template.format(**params) if params else template


class I18nMiddleware(BaseHTTPMiddleware):
    """Stocke la langue résolue dans request.state.lang pour les routes."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.lang = resolve_lang(request.headers.get("accept-language"))
        return await call_next(request)
