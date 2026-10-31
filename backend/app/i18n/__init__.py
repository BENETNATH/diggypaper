"""i18n: language resolution (Accept-Language) + message catalog (fr/en/de/es)."""

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
    """Instance default language (``DEFAULT_LANGUAGE`` in .env), ``en`` if invalid."""
    from app.core.config import get_settings

    lang = get_settings().default_language.strip().lower()
    return lang if lang in SUPPORTED_LANGS else FALLBACK_LANG


def _parse_qvalue(part: str) -> tuple[str, float] | None:
    """Parses an Accept-Language entry (RFC 4647) → (code, q) or None if invalid.

    - code: the part before the first ';' (or all of it), region removed, lowercase.
    - q: default 1.0; q=0 → (code, 0.0); invalid q (non-numeric) → None.
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
    """Chooses the most relevant language among fr/en/de/es (fallback en).

    Parses the q-values (RFC 4647): sorted by descending q, then by order of
    appearance on a tie. q=0 and unknown codes are ignored.
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
        # descending q, then order of appearance (ascending index).
        candidates.sort(key=lambda c: (-c[0], c[1]))
        return candidates[0][2]
    return default_lang()


def t(lang: str, key: str, **params: str) -> str:
    """Translates a key; fallback en, then the key itself if absent."""
    table = _MESSAGES.get(lang, _MESSAGES[FALLBACK_LANG])
    template = table.get(key, _MESSAGES[FALLBACK_LANG].get(key, key))
    return template.format(**params) if params else template


class I18nMiddleware(BaseHTTPMiddleware):
    """Stores the resolved language in request.state.lang for the routes."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.lang = resolve_lang(request.headers.get("accept-language"))
        return await call_next(request)
