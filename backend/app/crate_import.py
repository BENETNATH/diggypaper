"""Import d'un RO-Crate produit par l'export de Diggypaper : un projet restauré à partir d'un zip.

Restauré : titre, question, items et parties de texte du projet ; articles (identifiants,
bibliographie, résumé, texte intégral sectionné, PDF) ; réponses du modèle (certitudes, extraits de
preuve) et réponses humaines finales. Le crate ne contient qu'une recherche (celle exportée) : elle
devient la recherche initiale du nouveau projet. Les relectures sont attribuées à la personne qui
importe (le crate ne porte pas les identités), sans note de pertinence.

Le zip est une entrée non fiable : seuls les fichiers attendus sont lus, avec des tailles bornées ;
rien n'est extrait sur le disque ni exécuté.
"""

from __future__ import annotations

import json
import re
import zipfile
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from app.article_text import classify_title
from app.fulltext import MAX_PDF_BYTES
from app.schema import check_fields

MAX_JSON = 5 * 1024 * 1024
MAX_TEXT = 10 * 1024 * 1024
MAX_ARTICLES = 200_000
MAX_TOTAL = 8 * 1024**3  # taille décompressée annoncée, tous fichiers confondus
_KEY = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class CrateError(ValueError):
    """Crate illisible ou non conforme (message court, affichable)."""


class _ReadBudget:
    """Octets réellement décompressés pour un crate (compteur, pas la taille annoncée).

    ``open_crate`` refuse déjà une somme de tailles déclarées > ``MAX_TOTAL`` ; ce compteur
    est la ceinture : il porte sur ce que ``zipfile`` produit vraiment, donc il ne dépend
    ni des en-têtes ni du comportement de ``zipfile`` (défense en profondeur, entrée non
    fiable). ``BadZipFile``/CRC : voir ``_read``.
    """

    __slots__ = ("left",)

    def __init__(self, limit: int) -> None:
        self.left = limit

    def check(self) -> None:
        """Refus avant toute nouvelle décompression quand le budget est épuisé."""
        if self.left <= 0:
            raise CrateError("too_large")

    def charge(self, size: int) -> None:
        self.left -= size
        if self.left < 0:
            raise CrateError("too_large")


def _budget(zf: zipfile.ZipFile) -> _ReadBudget:
    """Budget d'un crate, porté par l'objet ``ZipFile`` (créé par ``open_crate``)."""
    budget = getattr(zf, "_crate_budget", None)
    if budget is None:
        budget = _ReadBudget(MAX_TOTAL)
        zf._crate_budget = budget  # attribut privé : aucune signature publique à changer
    return budget


def _read(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    budget = _budget(zf)
    budget.check()  # budget épuisé : refus immédiat, sans rien décompresser de plus
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise CrateError(f"missing:{name}") from None
    if info.file_size > limit:
        raise CrateError(f"too_large:{name}")
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as exc:
        # en-tête menteur (CRC faux), zip chiffré, compression exotique : entrée illisible.
        # Sans ça, une archive forgée remontait un BadZipFile brut (500 sur l'API, tâche cassée).
        raise CrateError(f"invalid_zip:{name}") from exc
    if len(data) > limit:
        raise CrateError(f"too_large:{name}")
    budget.charge(len(data))  # octets réellement décompressés, tous fichiers confondus
    return data


def _json(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        obj = json.loads(_read(zf, name, MAX_JSON).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CrateError(f"invalid_json:{name}") from None
    if not isinstance(obj, dict):
        raise CrateError(f"invalid_json:{name}")
    return obj


def open_crate(path: str | Path) -> tuple[zipfile.ZipFile, dict]:
    """Ouvre le zip et renvoie ``project.json`` après les contrôles de conformité."""
    if not zipfile.is_zipfile(path):
        raise CrateError("not_a_zip")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        # central directory corrompue / zip64 exotique : refus propre, pas de 500
        raise CrateError("not_a_zip") from exc
    try:
        names = zf.namelist()
        if len(names) > MAX_ARTICLES * 5 or sum(i.file_size for i in zf.infolist()) > MAX_TOTAL:
            raise CrateError("too_large")
        if any(n.startswith(("/", "\\")) or ".." in n.split("/") for n in names):
            raise CrateError("unsafe_path")
        meta = _json(zf, "ro-crate-metadata.json")
        if "ro/crate" not in json.dumps(meta.get("@graph", [])[:1]):
            raise CrateError("not_an_ro_crate")
        info = _json(zf, "project.json")
        if not str(info.get("title") or "").strip():
            raise CrateError("no_title")
    except BaseException:
        zf.close()  # refus d'un crate : pas de descripteur laissé ouvert (uploads répétés)
        raise
    return zf, info


def article_keys(zf: zipfile.ZipFile) -> list[str]:
    """Clés des dossiers ``articles/<clé>/`` qui ont un ``metadata.json``."""
    keys = []
    for n in zf.namelist():
        m = re.fullmatch(r"articles/([^/]+)/metadata\.json", n)
        if m and _KEY.fullmatch(m.group(1)):
            keys.append(m.group(1))
    if len(keys) > MAX_ARTICLES:
        raise CrateError("too_many_articles")
    return keys


def _clean(value, limit: int = 100_000) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("\x00", "").strip()[:limit] or None


def _strings(value) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out = [s for s in (_clean(v, 500) for v in value) if s]
    return out or None


def sections_from_markdown(md: str) -> list[dict]:
    """``fulltext.md`` (« ## titre » + texte) → sections ; le résumé est lu dans les métadonnées."""
    sections, title, buf = [], None, []

    def flush() -> None:
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if title is not None and text and title.strip().lower() != "abstract":
            sections.append({"title": title, "kind": classify_title(title), "text": text})

    for line in md.replace("\x00", "").splitlines():
        if line.startswith("## "):
            flush()
            title, buf = line[3:].strip(), []
        elif title is not None:
            buf.append(line)
    flush()
    return sections


def read_article(zf: zipfile.ZipFile, key: str) -> dict:
    """Un article du crate : métadonnées validées, sections, PDF éventuel."""
    meta = _json(zf, f"articles/{key}/metadata.json")
    base = f"articles/{key}/"
    sections: list[dict] = []
    try:
        sections = sections_from_markdown(_read(zf, base + "fulltext.md", MAX_TEXT).decode("utf-8"))
    except (CrateError, UnicodeDecodeError):
        pass
    pdf = None
    try:
        data = _read(zf, base + "fulltext.pdf", MAX_PDF_BYTES)
        pdf = data if data.startswith(b"%PDF") else None
    except CrateError:
        pass
    date = _clean(meta.get("pub_date"), 20)
    pmid = _clean(meta.get("pmid"), 20)
    source = _clean(meta.get("source"), 50)
    native_id = _clean(meta.get("native_id"), 200)
    if source is None and pmid:  # crate ancien : seule la recherche PubMed avait un PMID
        source, native_id = "pubmed", pmid
    return {
        "source": source,
        "native_id": native_id,
        "pmid": pmid,
        "pmcid": _clean(meta.get("pmcid"), 20),
        "doi": _clean(meta.get("doi"), 200),
        "title": _clean(meta.get("title"), 2000),
        "abstract": _clean(meta.get("abstract")),
        "journal": _clean(meta.get("journal"), 500),
        "pub_date": date,
        "pub_year": int(date[:4]) if date and date[:4].isdigit() else None,
        "authors": _strings(meta.get("authors")),
        "pub_types": _strings(meta.get("pub_types")),
        "original_language": _clean(meta.get("language"), 30) or "en",
        "fulltext_source": _clean(meta.get("fulltext_source"), 50),
        "fulltext_url": _clean(meta.get("fulltext_url"), 1000),
        "sections": sections or None,
        "methods_text": " ".join(s["text"] for s in sections if s["kind"] == "methods") or None,
        "pdf": pdf,
        "ai": meta.get("ai") if isinstance(meta.get("ai"), dict) else None,
        "human": [h for h in meta.get("human") or [] if isinstance(h, dict)],
    }


def project_definition(info: dict) -> dict:
    """question, items et parties de texte du projet (items validés comme à la saisie)."""
    fields = info.get("fields")
    try:
        fields = check_fields(fields) if fields else None
    except ValueError:
        raise CrateError("invalid_fields") from None
    sections = info.get("text_sections")
    return {
        "question": _clean(info.get("question"), 2000),
        "fields": fields,
        "text_sections": _strings(sections),
    }


def iter_articles(zf: zipfile.ZipFile) -> Iterator[tuple[str, dict]]:
    for key in article_keys(zf):
        try:
            yield key, read_article(zf, key)
        except CrateError:
            continue  # un dossier abîmé n'empêche pas de restaurer les autres


def now() -> datetime:
    return datetime.now(UTC)
