"""PRISMA 2020 flow diagram (adapted: AI triage + human re-read), in SVG.

The counts come from the database: imported queries, corpus, last prediction per
article and the most recent human annotation (relevance ≥ 0.5 = included).
"""

from __future__ import annotations

from html import escape

from app.sources import source_name

HUMAN_INCLUDE_AT = 0.5

LABELS = {
    "fr": {
        "via_citations": "dont par chaînage de citations",
        "identified_base": "Références identifiées",
        "identified_lists": "listes importées",
        "from_search": "Références de la recherche « {name} »",
        "criteria": "Hors critères de sélection",
        "c_status": "statut IA : {v}",
        "c_reviewed": "relus par un humain",
        "c_unreviewed": "non relus",
        "c_fulltext": "avec texte intégral",
        "c_abstract": "résumé seul",
        "c_q": "texte : « {v} »",
        "c_conf_min": "pertinence ≥ {v} %",
        "c_conf_max": "pertinence ≤ {v} %",
        "c_ids": "liste de {v} articles",
        "c_sample": "tirage au sort de {v}",
        "c_yes": "oui",
        "c_no": "non",
        "s_AUTO_ACCEPTED": "accepté",
        "s_AUTO_REJECTED": "rejeté",
        "s_NEEDS_HUMAN_REVIEW": "à relire",
        "s_NONE": "non analysé",
        "s_TO_REVIEW": "à relire / non relu",
        "ident": "Identification",
        "screen": "Tri",
        "incl": "Inclus",
        "removed": "Références retirées avant le tri",
        "dups": "Doublons",
        "errors": "Erreurs de récupération",
        "notscreened": "Pas encore triées",
        "screened": "Références triées",
        "excluded": "Références exclues",
        "by_ai": "par l'IA",
        "by_human": "par le relecteur",
        "sought": "Textes recherchés",
        "notretrieved": "Texte intégral non disponible (résumé seul)",
        "assessed": "Textes intégraux évalués",
        "pending": "En attente de relecture",
        "included": "Études incluses",
        "foot": "Adapté de PRISMA 2020 (tri automatique + relecture humaine) — Diggypaper",
    },
    "en": {
        "via_citations": "of which via citation chaining",
        "identified_base": "Records identified",
        "identified_lists": "imported lists",
        "from_search": "Records of search “{name}”",
        "criteria": "Not meeting selection criteria",
        "c_status": "AI status: {v}",
        "c_reviewed": "human-reviewed",
        "c_unreviewed": "not reviewed",
        "c_fulltext": "with full text",
        "c_abstract": "abstract only",
        "c_q": "text: “{v}”",
        "c_conf_min": "relevance ≥ {v}%",
        "c_conf_max": "relevance ≤ {v}%",
        "c_ids": "list of {v} articles",
        "c_sample": "random sample of {v}",
        "c_yes": "yes",
        "c_no": "no",
        "s_AUTO_ACCEPTED": "accepted",
        "s_AUTO_REJECTED": "rejected",
        "s_NEEDS_HUMAN_REVIEW": "needs review",
        "s_NONE": "not analysed",
        "s_TO_REVIEW": "to review / unreviewed",
        "ident": "Identification",
        "screen": "Screening",
        "incl": "Included",
        "removed": "Records removed before screening",
        "dups": "Duplicate records",
        "errors": "Retrieval errors",
        "notscreened": "Not yet screened",
        "screened": "Records screened",
        "excluded": "Records excluded",
        "by_ai": "by AI",
        "by_human": "by reviewer",
        "sought": "Reports sought for retrieval",
        "notretrieved": "Full text not available (abstract only)",
        "assessed": "Full-text reports assessed",
        "pending": "Awaiting human review",
        "included": "Studies included",
        "foot": "Adapted from PRISMA 2020 (automatic screening + human review) — Diggypaper",
    },
    "de": {
        "via_citations": "davon über Zitationsketten",
        "identified_base": "Identifizierte Datensätze",
        "identified_lists": "importierte Listen",
        "from_search": "Datensätze der Suche „{name}“",
        "criteria": "Auswahlkriterien nicht erfüllt",
        "c_status": "KI-Status: {v}",
        "c_reviewed": "manuell geprüft",
        "c_unreviewed": "nicht geprüft",
        "c_fulltext": "mit Volltext",
        "c_abstract": "nur Abstract",
        "c_q": "Text: „{v}“",
        "c_conf_min": "Relevanz ≥ {v} %",
        "c_conf_max": "Relevanz ≤ {v} %",
        "c_ids": "Liste von {v} Artikeln",
        "c_sample": "Zufallsstichprobe von {v}",
        "c_yes": "ja",
        "c_no": "nein",
        "s_AUTO_ACCEPTED": "akzeptiert",
        "s_AUTO_REJECTED": "abgelehnt",
        "s_NEEDS_HUMAN_REVIEW": "zu prüfen",
        "s_NONE": "nicht analysiert",
        "s_TO_REVIEW": "zu prüfen / ungeprüft",
        "ident": "Identifikation",
        "screen": "Screening",
        "incl": "Eingeschlossen",
        "removed": "Vor dem Screening entfernte Datensätze",
        "dups": "Duplikate",
        "errors": "Abruffehler",
        "notscreened": "Noch nicht gescreent",
        "screened": "Gescreente Datensätze",
        "excluded": "Ausgeschlossene Datensätze",
        "by_ai": "durch KI",
        "by_human": "durch Prüfer",
        "sought": "Gesuchte Volltexte",
        "notretrieved": "Volltext nicht verfügbar (nur Abstract)",
        "assessed": "Bewertete Volltexte",
        "pending": "Warten auf Prüfung",
        "included": "Eingeschlossene Studien",
        "foot": "Nach PRISMA 2020 angepasst (autom. Screening + menschliche Prüfung) — Diggypaper",
    },
    "es": {
        "via_citations": "de ellos por encadenamiento de citas",
        "identified_base": "Registros identificados",
        "identified_lists": "listas importadas",
        "from_search": "Registros de la búsqueda «{name}»",
        "criteria": "Fuera de los criterios de selección",
        "c_status": "estado IA: {v}",
        "c_reviewed": "revisados por un humano",
        "c_unreviewed": "sin revisar",
        "c_fulltext": "con texto completo",
        "c_abstract": "solo resumen",
        "c_q": "texto: «{v}»",
        "c_conf_min": "relevancia ≥ {v} %",
        "c_conf_max": "relevancia ≤ {v} %",
        "c_ids": "lista de {v} artículos",
        "c_sample": "muestra aleatoria de {v}",
        "c_yes": "sí",
        "c_no": "no",
        "s_AUTO_ACCEPTED": "aceptado",
        "s_AUTO_REJECTED": "rechazado",
        "s_NEEDS_HUMAN_REVIEW": "por revisar",
        "s_NONE": "sin analizar",
        "s_TO_REVIEW": "por revisar / sin revisar",
        "ident": "Identificación",
        "screen": "Cribado",
        "incl": "Incluidos",
        "removed": "Registros eliminados antes del cribado",
        "dups": "Duplicados",
        "errors": "Errores de recuperación",
        "notscreened": "Aún sin cribar",
        "screened": "Registros cribados",
        "excluded": "Registros excluidos",
        "by_ai": "por la IA",
        "by_human": "por el revisor",
        "sought": "Textos buscados",
        "notretrieved": "Texto completo no disponible (solo resumen)",
        "assessed": "Textos completos evaluados",
        "pending": "Pendientes de revisión",
        "included": "Estudios incluidos",
        "foot": "Adaptado de PRISMA 2020 (cribado automático + revisión humana) — Diggypaper",
    },
}


def prisma_counts(
    articles, preds: dict, annos: dict, import_jobs, origin: dict | None = None
) -> dict:
    """Count of each box of the diagram (cf. ``render_svg``).

    ``origin`` (search other than the initial one): ``{name, size, criteria, labels}`` —
    identification then starts from the origin search's references, and those the
    selection criteria reject replace the "duplicates" (which are the import ones).
    """
    total = len(articles)
    identified = failed = 0
    if origin is not None:
        identified = max(origin["size"], total)
    else:
        for j in import_jobs:
            r = j.result_json or {}
            if j.status is not None and j.status.value == "COMPLETED":
                identified += (
                    int(r.get("imported", 0))
                    + int(r.get("failed", 0))
                    + int(r.get("duplicates_doi", 0))  # doublons DOI écartés à l'import
                )
                failed += int(r.get("failed", 0))
        identified = max(identified, total + failed)
    dups = identified - failed - total  # références déjà présentes (ré-imports)

    c = {"included": 0, "ai_excluded": 0, "human_excluded": 0, "pending": 0, "untriaged": 0}
    retrieved = 0  # parmi les références non exclues : celles avec texte intégral
    not_excluded = 0
    for a in articles:
        human = (annos.get(a.id) or [None])[-1]
        pred = preds.get(a.id)
        if human is not None:
            score = (human.decision_json or {}).get("confidence_score", 1.0)
            kind = "included" if score >= HUMAN_INCLUDE_AT else "human_excluded"
        elif pred is None:
            kind = "untriaged"
        elif pred.status is not None and pred.status.value == "AUTO_ACCEPTED":
            kind = "included"
        elif pred.status is not None and pred.status.value == "AUTO_REJECTED":
            kind = "ai_excluded"
        else:
            kind = "pending"
        c[kind] += 1
        if kind in ("included", "pending"):
            not_excluded += 1
            retrieved += bool(a.sections)
    screened = total - c["untriaged"]
    return {
        "via_citations": sum(1 for a in articles if getattr(a, "origin", None) == "citation"),
        "identified": identified,
        "failed": failed,
        "duplicates": 0 if origin else dups,
        "by_criteria": dups if origin else 0,
        "origin": origin,
        "not_screened": c["untriaged"],
        "screened": screened,
        "excluded": c["ai_excluded"] + c["human_excluded"],
        "excluded_ai": c["ai_excluded"],
        "excluded_human": c["human_excluded"],
        "sought": not_excluded,
        "not_retrieved": not_excluded - retrieved,
        "assessed": retrieved,
        "pending": c["pending"],
        "included": c["included"],
    }


def describe_criteria(spec: dict, labels: dict, lang: str = "fr") -> list[str]:
    """Selection criteria (the ``Selection`` of a derived search) as short sentences."""
    L = LABELS.get(lang, LABELS["en"])
    out = []
    if spec.get("status"):
        out.append(L["c_status"].format(v=L.get(f"s_{spec['status']}", spec["status"])))
    for key, val in (spec.get("answers") or {}).items():
        shown = L["c_yes" if val is True else "c_no"] if isinstance(val, bool) else val
        out.append(f"{labels.get(key, key)} = {shown}")
    if "reviewed" in spec:
        out.append(L["c_reviewed" if spec["reviewed"] else "c_unreviewed"])
    if "has_fulltext" in spec:
        out.append(L["c_fulltext" if spec["has_fulltext"] else "c_abstract"])
    if spec.get("q"):
        out.append(L["c_q"].format(v=spec["q"]))
    for key in ("confidence_min", "confidence_max"):
        if spec.get(key) is not None:
            out.append(L["c_conf_" + key[-3:]].format(v=round(spec[key] * 100)))
    if spec.get("n_ids"):
        out.append(L["c_ids"].format(v=spec["n_ids"]))
    if spec.get("sample_n"):
        out.append(L["c_sample"].format(v=spec["sample_n"]))
    return out


_W, _H, _GAP = 300, 88, 40


def _clip(text: str, width: int = 46) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _box(x, y, w, h, title, lines, fill="#ffffff") -> str:
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" '
        'stroke="#334155" stroke-width="1.2"/>',
        f'<text x="{x + w / 2}" y="{y + 22}" text-anchor="middle" font-weight="700" '
        f'font-size="13">{escape(title)}</text>',
    ]
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{x + w / 2}" y="{y + 42 + i * 16}" text-anchor="middle" '
            f'font-size="12">{escape(line)}</text>'
        )
    return "".join(out)


def _arrow(x1, y1, x2, y2) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#334155" stroke-width="1.4" '
        'marker-end="url(#a)"/>'
    )


def identified_parts(articles, import_jobs) -> tuple[list[str], bool]:
    """Actually present sources (readable labels) + the "imported lists" part.

    Job without ``source`` (row predating migration 0030) = PubMed, like the
    backfill: ``kind`` NULL/"import"/"citations". "imported lists":
    references/crate jobs or articles without a source."""
    sources: list[str] = []
    for j in import_jobs:
        s = j.source or ("pubmed" if j.kind in (None, "import", "citations") else None)
        if s and s not in sources:
            sources.append(s)
    if "pubmed" in sources:
        sources.remove("pubmed")
        sources.insert(0, "pubmed")
    lists = any(j.kind in ("references", "crate") for j in import_jobs) or any(
        a.source is None for a in articles
    )
    return [source_name(s) for s in sources], lists


def render_svg(
    c: dict, lang: str = "fr", sources: list[str] | None = None, lists: bool | None = None
) -> str:
    """PRISMA 2020 diagram (main column on the left, exclusions on the right).

    ``sources``/``lists`` (``identified_parts``): the actually present sources in the
    "identified" label; ``None``/``None`` (historical caller) → static label
    (PubMed, imported lists)."""
    L = LABELS.get(lang, LABELS["en"])

    def n(k: str) -> str:
        return f"n = {c[k]}"

    left, right = 60, 410
    origin = c.get("origin")
    removed = [
        f"{L['dups']} ({n('duplicates')})",
        f"{L['errors']} ({n('failed')})",
        f"{L['notscreened']} ({n('not_screened')})",
    ]
    if origin:  # recherche dérivée : les critères de sélection remplacent doublons et erreurs
        crit = [_clip(x) for x in describe_criteria(origin["criteria"], origin["labels"], lang)]
        crit = crit[:8] + ["…"] * (len(crit) > 8)
        removed = [
            f"{L['criteria']} ({n('by_criteria')})",
            *crit,
            f"{L['notscreened']} ({n('not_screened')})",
        ]
    top_h = max(_H, 50 + 16 * len(removed) - 8)
    shift = top_h - _H
    rows = [20] + [20 + k * (_H + _GAP) + shift for k in range(1, 5)]
    height = rows[-1] + _H + 50
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 {height}" '
        'font-family="Helvetica,Arial,sans-serif" fill="#0f172a">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#334155"/></marker></defs>',
        f'<rect width="740" height="{height}" fill="#ffffff"/>',
    ]
    # phase bands (vertical labels)
    for name, y0, y1 in (
        (L["ident"], rows[0], rows[0] + _H),
        (L["screen"], rows[1], rows[3] + _H),
        (L["incl"], rows[4], rows[4] + _H),
    ):
        parts.append(
            f'<rect x="8" y="{y0}" width="30" height="{y1 - y0}" rx="4" fill="#dbeafe"/>'
            f'<text transform="translate(28 {(y0 + y1) / 2}) rotate(-90)" text-anchor="middle" '
            f'font-size="12" font-weight="700">{escape(name)}</text>'
        )

    if origin:
        first = L["from_search"].format(name=origin["name"])
    elif sources is None and lists is None:  # appelant historique : libellé statique
        ident_parts = [source_name("pubmed"), L["identified_lists"]]
    else:
        ident_parts = list(sources or [])
        if lists:
            ident_parts.append(L["identified_lists"])
    if not origin:
        first = f"{L['identified_base']} ({', '.join(ident_parts)})"
    first_lines = [n("identified")]
    if c.get("via_citations"):  # PRISMA 2020 : identification par d'autres méthodes
        first_lines.append(f"{L['via_citations']} ({n('via_citations')})")
    parts.append(_box(left, rows[0], _W, _H, first, first_lines))
    parts.append(_box(right, rows[0], _W, top_h, L["removed"], removed, "#f8fafc"))
    parts.append(_box(left, rows[1], _W, _H, L["screened"], [n("screened")]))
    parts.append(
        _box(
            right,
            rows[1],
            _W,
            _H,
            L["excluded"],
            [
                n("excluded"),
                f"{L['by_ai']} {c['excluded_ai']} · {L['by_human']} {c['excluded_human']}",
            ],
            "#f8fafc",
        )
    )
    parts.append(_box(left, rows[2], _W, _H, L["sought"], [n("sought")]))
    parts.append(_box(right, rows[2], _W, _H, L["notretrieved"], [n("not_retrieved")], "#f8fafc"))
    parts.append(_box(left, rows[3], _W, _H, L["assessed"], [n("assessed")]))
    parts.append(_box(right, rows[3], _W, _H, L["pending"], [n("pending")], "#f8fafc"))
    parts.append(_box(left, rows[4], _W, _H, L["included"], [n("included")], "#dcfce7"))
    cx = left + _W / 2
    for i in range(4):  # flèches verticales
        parts.append(_arrow(cx, rows[i] + _H, cx, rows[i + 1]))
    for i in range(4):  # flèches vers les exclusions (sauf ligne 4 : alignée sur l'évaluation)
        y = rows[i] + _H / 2
        parts.append(_arrow(left + _W, y, right, y))
    parts.append(
        f'<text x="370" y="{height - 14}" text-anchor="middle" font-size="10" '
        f'fill="#475569">{escape(L["foot"])}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def svg_to_pdf(svg: str) -> bytes:
    """PDF (1 vector page) of the diagram produced by ``render_svg``.

    Only reads the elements ``render_svg`` writes: rect, text (possibly rotated -90°),
    line (with arrowhead if ``marker-end``). Helvetica font (WinAnsi: FR/DE/ES accents).
    """
    import xml.etree.ElementTree as ET

    # ElementTree resolves no external entities (no XXE); svg size is bounded upstream.
    root = ET.fromstring(svg)  # nosec B314
    vb = [float(v) for v in root.get("viewBox", "0 0 740 800").split()]
    width, height = vb[2], vb[3]
    ns = "{http://www.w3.org/2000/svg}"

    def rgb(color: str | None) -> str:
        if not color or color == "none" or not color.startswith("#"):
            return "0 0 0"
        return " ".join(f"{int(color[i : i + 2], 16) / 255:.3f}" for i in (1, 3, 5))

    def y(v: float) -> float:  # origine SVG en haut, PDF en bas
        return height - v

    ops: list[str] = []
    for el in root.iter():
        tag = el.tag.removeprefix(ns)
        a = el.attrib
        if tag == "rect":
            x0, y0, w, h = (float(a.get(k, 0)) for k in ("x", "y", "width", "height"))
            fill = a.get("fill")
            stroke = a.get("stroke")
            ops.append(f"{rgb(fill)} rg {rgb(stroke)} RG {a.get('stroke-width', 0)} w")
            paint = "B" if stroke else "f"
            ops.append(f"{x0:.2f} {y(y0 + h):.2f} {w:.2f} {h:.2f} re {paint}")
        elif tag == "line":
            x1, y1, x2, y2 = (float(a[k]) for k in ("x1", "y1", "x2", "y2"))
            ops.append(f"0.2 0.25 0.33 RG 1.4 w {x1:.2f} {y(y1):.2f} m {x2:.2f} {y(y2):.2f} l S")
            if a.get("marker-end"):  # pointe pleine à l'extrémité
                dx, dy = x2 - x1, y2 - y1
                n = (dx * dx + dy * dy) ** 0.5 or 1
                ux, uy = dx / n, dy / n
                bx, by = x2 - 7 * ux, y2 - 7 * uy
                p1 = (bx - 3.5 * uy, by + 3.5 * ux)
                p2 = (bx + 3.5 * uy, by - 3.5 * ux)
                ops.append(
                    f"0.2 0.25 0.33 rg {x2:.2f} {y(y2):.2f} m {p1[0]:.2f} {y(p1[1]):.2f} l "
                    f"{p2[0]:.2f} {y(p2[1]):.2f} l f"
                )
        elif tag == "text":
            text = "".join(el.itertext())
            size = float(a.get("font-size", 12))
            font = "/F2" if a.get("font-weight") == "700" else "/F1"
            data = text.encode("cp1252", "replace")
            data = data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            wd = 0.5 * size * len(text)  # largeur approchée (Helvetica) pour centrer
            x0, y0 = float(a.get("x", 0)), float(a.get("y", 0))
            fill = rgb(a.get("fill", "#0f172a"))
            if "rotate" in a.get("transform", ""):  # libellés de phase verticaux
                tx, ty = a["transform"].split("translate(")[1].split(")")[0].split()
                m = f"0 1 -1 0 {float(tx):.2f} {y(float(ty)):.2f}"
                ops.append(
                    f"BT {fill} rg {font} {size} Tf {m} Tm {-wd / 2:.2f} {-size * 0.35:.2f} Td "
                    f"({data.decode('latin-1')}) Tj ET"
                )
            else:
                dx = -wd / 2 if a.get("text-anchor") == "middle" else 0
                ops.append(
                    f"BT {fill} rg {font} {size} Tf {x0 + dx:.2f} {y(y0):.2f} Td "
                    f"({data.decode('latin-1')}) Tj ET"
                )
    content = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out
