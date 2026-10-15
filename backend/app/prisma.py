"""Diagramme de flux PRISMA 2020 (adapté : tri IA + relecture humaine), en SVG.

Les effectifs viennent de la base : requêtes importées, corpus, dernière prédiction par
article et annotation humaine la plus récente (pertinence ≥ 0,5 = inclus).
"""

from __future__ import annotations

from html import escape

HUMAN_INCLUDE_AT = 0.5

LABELS = {
    "fr": {
        "ident": "Identification",
        "screen": "Tri",
        "incl": "Inclus",
        "identified": "Références identifiées dans PubMed",
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
        "ident": "Identification",
        "screen": "Screening",
        "incl": "Included",
        "identified": "Records identified from PubMed",
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
        "ident": "Identifikation",
        "screen": "Screening",
        "incl": "Eingeschlossen",
        "identified": "In PubMed identifizierte Datensätze",
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
        "ident": "Identificación",
        "screen": "Cribado",
        "incl": "Incluidos",
        "identified": "Registros identificados en PubMed",
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


def prisma_counts(articles, preds: dict, annos: dict, import_jobs) -> dict:
    """Effectifs de chaque case du diagramme (cf. ``render_svg``)."""
    total = len(articles)
    identified = failed = 0
    for j in import_jobs:
        r = j.result_json or {}
        if j.status is not None and j.status.value == "COMPLETED":
            identified += int(r.get("imported", 0)) + int(r.get("failed", 0))
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
        "identified": identified,
        "failed": failed,
        "duplicates": dups,
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


_W, _H, _GAP = 300, 88, 40


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


def render_svg(c: dict, lang: str = "fr") -> str:
    """Diagramme PRISMA 2020 (colonne principale à gauche, exclusions à droite)."""
    L = LABELS.get(lang, LABELS["en"])
    left, right = 60, 410
    rows = [20, 20 + _H + _GAP, 20 + 2 * (_H + _GAP), 20 + 3 * (_H + _GAP), 20 + 4 * (_H + _GAP)]
    height = rows[-1] + _H + 50
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 {height}" '
        'font-family="Helvetica,Arial,sans-serif" fill="#0f172a">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#334155"/></marker></defs>',
        f'<rect width="740" height="{height}" fill="#ffffff"/>',
    ]
    # bandes de phase (étiquettes verticales)
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

    def n(k: str) -> str:
        return f"n = {c[k]}"

    parts.append(_box(left, rows[0], _W, _H, L["identified"], [n("identified")]))
    parts.append(
        _box(
            right,
            rows[0],
            _W,
            _H,
            L["removed"],
            [
                f"{L['dups']} ({n('duplicates')})",
                f"{L['errors']} ({n('failed')})",
                f"{L['notscreened']} ({n('not_screened')})",
            ],
            "#f8fafc",
        )
    )
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
    """PDF (1 page vectorielle) du diagramme produit par ``render_svg``.

    Ne lit que les éléments que ``render_svg`` écrit : rect, text (éventuellement pivoté de -90°),
    line (avec pointe de flèche si ``marker-end``). Police Helvetica (WinAnsi : accents FR/DE/ES).
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(svg)
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
