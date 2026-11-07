"""Field-value comparison helpers — re-run comparison + reviewer agreement.

The unit of comparison is a question field (``Search.fields``); the MAIN field
is the first one (the include/exclude decision). Values are normalized before
comparison so that equivalent labels count as agreement: "oui"/"yes"/"include"/
true → True, the negatives → False, other strings → normalized text.
"""

import re

YES_VALUES = {"true", "yes", "oui", "vrai", "1", "include", "inclusion", "included", "in"}
NO_VALUES = {"false", "no", "non", "faux", "0", "exclude", "exclusion", "excluded", "out"}


def main_field(fields: list[dict] | None) -> dict | None:
    """The main decision field (the first field of the question); None if unstructured."""
    return fields[0] if fields else None


def field_value(decision: dict | None, fields: list[dict] | None):
    """Value of the main field inside a decision (None if no field or no decision)."""
    f = main_field(fields)
    if f is None or not isinstance(decision, dict):
        return None
    return decision.get(f["key"])


def norm_value(v):
    """Normalize a field value for comparison: yes/no labels → bool, strings → text."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"\s+", " ", v.strip().lower())
        if s in YES_VALUES:
            return True
        if s in NO_VALUES:
            return False
        return s
    return v


def values_agree(a, b) -> bool:
    """True iff both answers are present and equal after normalization."""
    na, nb = norm_value(a), norm_value(b)
    return na is not None and nb is not None and na == nb
