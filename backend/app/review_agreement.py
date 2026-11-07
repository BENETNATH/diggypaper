"""Reviewer agreement metrics (spec 2026-11-07-accord-reviews) — purely calculational.

Aggregates data already stored: human re-reads (``HumanAnnotation``) and AI
predictions (``ArticlePrediction``). The unit of measurement is a question field
(``Search.fields``); the MAIN field (the first one, the include/exclude
decision) carries the global metric. No new inference, no new text.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agreement import cohen_kappa, main_field, norm_value, values_agree
from app.db.models import Article, ArticlePrediction, HumanAnnotation, Search, User
from app.i18n import t
from app.searches import in_search

META = None  # no meta keys: every field of the question counts


async def reviewer_counts(db: AsyncSession, scope: Search) -> list[tuple[User, int]]:
    """Distinct human reviewers of the search, most active first (tie: newest first)."""
    rows = (
        await db.execute(
            select(HumanAnnotation.user_id, func.count(HumanAnnotation.id))
            .where(in_search(HumanAnnotation.search_id, scope))
            .where(HumanAnnotation.user_id.is_not(None))
            .group_by(HumanAnnotation.user_id)
            .order_by(func.count(HumanAnnotation.id).desc())
        )
    ).all()
    out: list[tuple[User, int]] = []
    for user_id, count in rows:
        user = await db.get(User, user_id)
        if user is not None:
            out.append((user, count))
    return out


def _field_pairs(
    dec_a: dict, dec_b: dict, fields: list[dict]
) -> tuple[list[tuple], dict[str, tuple]]:
    """(value_a, value_b) per answered field, plus the raw per-field values."""
    pairs: list[tuple] = []
    raw: dict[str, tuple] = {}
    for f in fields:
        key = f["key"]
        va = dec_a.get(key)
        vb = dec_b.get(key)
        if va is None or vb is None:
            continue
        raw[key] = (va, vb)
        pairs.append((norm_value(va), norm_value(vb)))
    return pairs, raw


def _block(n_pairs: int, n_agree: int) -> dict:
    acc = n_agree / n_pairs if n_pairs else None
    return {"n": n_pairs, "n_agree": n_agree, "acc": acc, "kappa": None}


def _user_ref(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "email": user.email}


async def human_human(db: AsyncSession, scope: Search, user_a: User, user_b: User) -> dict:
    """Human-human agreement: intersection of the two lots, per field + global main field."""
    ann_a: dict[uuid.UUID, dict] = {}
    ann_b: dict[uuid.UUID, dict] = {}
    rows_a = (
        (
            await db.execute(
                select(HumanAnnotation).where(
                    HumanAnnotation.user_id == user_a.id,
                    in_search(HumanAnnotation.search_id, scope),
                )
            )
        )
        .scalars()
        .all()
    )
    for ann in rows_a:
        if ann.article_id is not None:
            ann_a[ann.article_id] = ann.decision_json or {}
    rows_b = (
        (
            await db.execute(
                select(HumanAnnotation).where(
                    HumanAnnotation.user_id == user_b.id,
                    in_search(HumanAnnotation.search_id, scope),
                )
            )
        )
        .scalars()
        .all()
    )
    for ann in rows_b:
        if ann.article_id is not None:
            ann_b[ann.article_id] = ann.decision_json or {}

    common = sorted(set(ann_a) & set(ann_b))
    fields = scope.fields or []
    f = main_field(fields)
    main_key = f["key"] if f else None

    # global (main field) + per-field
    main_pairs: list[tuple] = []
    by_field: list[dict] = []
    disagreements: list[dict] = []
    titles: dict[uuid.UUID, str] = {}
    if common:
        rows = (
            await db.execute(select(Article.id, Article.title).where(Article.id.in_(common)))
        ).all()
        titles = {r.id: r.title for r in rows}

    per_field_pairs: dict[str, list[tuple]] = {f2["key"]: [] for f2 in fields}
    per_field_raw: dict[str, dict[uuid.UUID, tuple]] = {f2["key"]: {} for f2 in fields}
    for aid in common:
        da, db2 = ann_a[aid], ann_b[aid]
        pairs, raw = _field_pairs(da, db2, fields)
        if main_key is not None and main_key in raw:
            va, vb = raw[main_key]
            main_pairs.append((norm_value(va), norm_value(vb)))
            if not values_agree(va, vb):
                diffs = {
                    k2: {"a": r2[0], "b": r2[1]}
                    for k2, r2 in raw.items()
                    if k2 != main_key and not values_agree(r2[0], r2[1])
                }
                disagreements.append(
                    {
                        "article_id": aid,
                        "title": titles.get(aid),
                        "value_a": va,
                        "value_b": vb,
                        "fields_diff": diffs,
                    }
                )
        for key, (va, vb) in raw.items():
            if key in per_field_pairs:
                per_field_pairs[key].append((norm_value(va), norm_value(vb)))
                per_field_raw[key][aid] = (va, vb)

    n = len(main_pairs)
    n_agree = sum(1 for a, b in main_pairs if a == b)
    block = _block(n, n_agree)
    block["kappa"] = cohen_kappa(main_pairs)
    for f2 in fields:
        key = f2["key"]
        p = per_field_pairs[key]
        nb = len(p)
        na = sum(1 for a, b in p if a == b)
        by_field.append(
            {
                "field": key,
                "n": nb,
                "n_agree": na,
                "acc": na / nb if nb else None,
                "kappa": cohen_kappa(p),
            }
        )
    return {
        "user_a": _user_ref(user_a),
        "user_b": _user_ref(user_b),
        "n": n,
        "n_agree": n_agree,
        "acc": block["acc"],
        "kappa": block["kappa"],
        "by_field": by_field,
        "disagreements": disagreements,
    }


async def ai_human(db: AsyncSession, scope: Search, model: str, user: User) -> dict | None:
    """AI-human agreement: the model as second reviewer vs one human reviewer.

    Intersection of the model's predictions (existing ones, no re-inference) and
    the user's annotations, on the main field. None when there is no
    intersection.
    """
    preds: dict[uuid.UUID, dict] = {}
    rows = (
        (
            await db.execute(
                select(ArticlePrediction).where(
                    ArticlePrediction.model_used == model,
                    in_search(ArticlePrediction.search_id, scope),
                )
            )
        )
        .scalars()
        .all()
    )
    for p in rows:
        if p.article_id is not None:
            preds[p.article_id] = p.decision_json or {}
    anns: dict[uuid.UUID, dict] = {}
    rows2 = (
        (
            await db.execute(
                select(HumanAnnotation).where(
                    HumanAnnotation.user_id == user.id,
                    in_search(HumanAnnotation.search_id, scope),
                )
            )
        )
        .scalars()
        .all()
    )
    for ann in rows2:
        if ann.article_id is not None:
            anns[ann.article_id] = ann.decision_json or {}
    common = sorted(set(preds) & set(anns))
    f = main_field(scope.fields or [])
    if f is None or not common:
        return None
    main_key = f["key"]
    pairs: list[tuple] = []
    for aid in common:
        pv, hv = preds[aid].get(main_key), anns[aid].get(main_key)
        if pv is None or hv is None:
            continue
        pairs.append((norm_value(pv), norm_value(hv)))
    n = len(pairs)
    if not n:
        return None
    n_agree = sum(1 for a, b in pairs if a == b)
    return {
        "model": model,
        "user": _user_ref(user),
        "n": n,
        "n_agree": n_agree,
        "acc": n_agree / n,
        "kappa": cohen_kappa(pairs),
    }


def _fmt_kappa(k) -> str:
    return "n.a." if k is None else f"{k:.2f}"


def build_summary(lang: str, humans: dict, ai: dict | None, n_disagreements: int) -> str:
    """PRISMA-ready text (spec §6), in the resolved language. Numbers use the
    plain dot separator regardless of language (machine-generated export)."""
    if humans["n"] == 0:
        return t(lang, "agreement_summary_empty")
    text = t(
        lang,
        "agreement_summary",
        n=humans["n"],
        acc=f"{humans['acc'] * 100:.1f}",
        kappa=_fmt_kappa(humans["kappa"]),
        dis=str(n_disagreements),
    )
    if ai is not None:
        text += t(
            lang,
            "agreement_summary_ai",
            model=ai["model"],
            ai_acc=f"{ai['acc'] * 100:.1f}",
        )
    return text
