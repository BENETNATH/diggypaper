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


def cohen_kappa(pairs: list[tuple]) -> float | None:
    """Cohen's kappa on normalized (value_a, value_b) pairs.

    Binary only: if the pairs contain more than two distinct values, the
    multivariate kappa is out of scope — return None (callers report the raw
    agreement instead). None also when there are no pairs, or when
    ``pe == 1`` (every pair identical: kappa undefined 0/0).
    """
    if not pairs:
        return None
    distinct = sorted({v for a, b in pairs for v in (a, b)}, key=repr)
    if len(distinct) != 2:
        return None
    n = len(pairs)
    pos = distinct[1]
    p1a = sum(1 for a, _ in pairs if a == pos)
    p1b = sum(1 for _, b in pairs if b == pos)
    po = sum(1 for a, b in pairs if a == b) / n
    pe = (p1a / n) * (p1b / n) + ((n - p1a) / n) * ((n - p1b) / n)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)
