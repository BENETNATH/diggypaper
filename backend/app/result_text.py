"""Rendered text of a search's answers (spec 2026-11-09-sous-analyse-criteres R4).

The unit indexed per (article, search) — "Modèle rongeur : Oui (92 %) ·
Durée (jours) : 120 j (88 %)" — is a pure function of the latest prediction's
``decision_json`` + ``field_confidences`` and the search's field labels.
"""

from __future__ import annotations

_SEP = " · "


def render_response(
    decision: dict | None,
    field_confidences: dict | None,
    fields: list[dict] | None,
) -> str | None:
    """One line per field that HAS a value, in the search's field order:
    ``{label} : {value} (conf %)``. ``None`` when nothing is renderable
    (no fields defined, no answer for any field — e.g. an ERROR prediction)."""
    if not fields:
        return None
    confs = field_confidences or {}
    decision = decision or {}
    parts: list[str] = []
    for field in fields:
        value = decision.get(field.get("key"))
        if value is None:
            continue
        if value is True:
            value = "Oui"
        elif value is False:
            value = "Non"
        else:
            value = str(value)
        part = f"{field.get('label') or field.get('key')} : {value}"
        conf = confs.get(field.get("key"))
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            part += f" ({int(round(conf * 100))} %)"
        parts.append(part)
    return _SEP.join(parts) if parts else None
