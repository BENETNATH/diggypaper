"""Reference list import: parsers (BibTeX, RIS, CSL, CSV, DOI/PMID) and resolution."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tasks_import import _articles_for, _efetch_xml, _make_project, _project_status

from app.refimport import clean_doi, dedupe, parse_references
from app.tasks import _import_pubmed

BIB = r"""
@comment{ignored}
@article{dupont2020,
  author = {Dupont, Jean Paul and Martin, A.},
  title = {{Effects of} sleep \& memory},
  journal = {Sleep Journal},
  year = 2020,
  doi = {10.1000/ABC.1},
  abstract = "A short {abstract}."
}
@book{b, title = {Only a title}}
"""
RIS = """TY  - JOUR
TI  - Ris title
AU  - Smith, John
JO  - Ris Journal
PY  - 2019/05/01
DO  - 10.2000/ris.1
AN  - PMID: 4242
ER  - 
TY  - JOUR
T1  - No id here
UR  - https://doi.org/10.3000/url.9.
ER  - 
"""


def test_plain_list():
    fmt, e = parse_references("10.1001/AbC.\nhttps://doi.org/10.1002/x\n12345\nPMID: 678\nbruit\n")
    assert fmt == "list"
    assert [(x["doi"], x["pmid"]) for x in e] == [
        ("10.1001/abc", None),
        ("10.1002/x", None),
        (None, "12345"),
        (None, "678"),
    ]


def test_bibtex():
    fmt, e = parse_references(BIB)
    assert fmt == "bibtex" and len(e) == 2
    assert e[0]["doi"] == "10.1000/abc.1" and e[0]["year"] == 2020
    assert e[0]["title"] == "Effects of sleep & memory"
    assert e[0]["authors"] == ["Dupont JP", "Martin A"]
    assert e[0]["abstract"] == "A short abstract."
    assert e[1]["title"] == "Only a title" and not e[1]["doi"]


def test_ris():
    fmt, e = parse_references(RIS)
    assert fmt == "ris" and len(e) == 2
    assert (e[0]["doi"], e[0]["pmid"], e[0]["year"]) == ("10.2000/ris.1", "4242", 2019)
    assert e[1]["doi"] == "10.3000/url.9"  # trailing punctuation removed


def test_csl_and_csv():
    csl = (
        '[{"title": "T", "DOI": "10.1005/Z", "author": [{"family": "Roe", "given": "Jane"}],'
        ' "issued": {"date-parts": [[2018, 3]]}}]'
    )
    fmt, e = parse_references(csl)
    assert fmt == "csl" and e[0]["authors"] == ["Roe J"] and e[0]["year"] == 2018
    fmt, e = parse_references(
        'Key,Title,DOI,Author\nk1,"Hello, world",10.1006/q,"Roe, Jane; Poe, Ed"\n'
    )
    assert (
        fmt == "csv" and e[0]["title"] == "Hello, world" and e[0]["authors"] == ["Roe J", "Poe E"]
    )


def test_dedupe_merges():
    out = dedupe(
        [
            {"doi": "10.1001/a", "pmid": None, "title": None, "journal": None},
            {"doi": "10.1001/a", "pmid": "9", "title": "T", "journal": "J"},
        ]
    )
    assert len(out) == 1 and out[0]["pmid"] == "9" and out[0]["title"] == "T"
    assert clean_doi("doi:10.1001/X)") == "10.1001/x"


def _handler():
    """Europe PMC : 10.1001/in-pubmed → PMID 111 ; Crossref : 10.1002/only-crossref."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "www.ebi.ac.uk":
            # Without parentheses, "A OR B AND SRC:MED" filters only B (seen in the wild).
            query = request.url.params["query"]
            assert "DOI:" not in query or query.startswith("(DOI:")
            rows = [{"pmid": "111", "doi": "10.1001/in-pubmed", "title": "x"}]
            return httpx.Response(200, json={"resultList": {"result": rows}})
        if path.endswith("efetch.fcgi"):
            return httpx.Response(200, text=_efetch_xml(["111"], with_pmcid=False))
        if host == "api.crossref.org":
            if path.endswith("10.1002/only-crossref"):
                msg = {
                    "DOI": "10.1002/only-crossref",
                    "title": ["Crossref title"],
                    "container-title": ["CR Journal"],
                    "author": [{"family": "Roe", "given": "Jane"}],
                    "issued": {"date-parts": [[2021]]},
                    "abstract": "<jats:p>Hello</jats:p>",
                }
                return httpx.Response(200, json={"message": msg})
            return httpx.Response(404)
        raise AssertionError(f"appel inattendu : {request.url}")

    return handler


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


async def test_import_references_resolves_each_source(engine):
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _, refs = parse_references("10.1001/in-pubmed\n10.1002/only-crossref\n10.1009/unknown\n")
    refs.append(
        {"doi": None, "pmid": None, "title": "File only", "year": 2015, "authors": ["Poe E"]}
        | {k: None for k in ("journal", "abstract", "language")}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler())) as http:
        result = await _import_pubmed(
            str(project.id), "references", session_factory=factory, http=http, refs=refs
        )
    assert (result["imported"], result["failed"], result["duplicates"]) == (3, 1, 0)
    assert result["sources"] == {"pubmed": 1, "crossref": 1, "file": 1}
    assert result["failed_items"] == ["10.1009/unknown"]
    by_title = {a.title: a for a in await _articles_for(engine, project.id)}
    assert by_title["Title 111"].pmid == "111" and by_title["Title 111"].doi == "10.1001/in-pubmed"
    cr = by_title["Crossref title"]
    assert (cr.pmid, cr.journal, cr.pub_year, cr.authors, cr.abstract) == (
        None,
        "CR Journal",
        2021,
        ["Roe J"],
        "Hello",
    )
    assert by_title["File only"].pub_year == 2015
    assert await _project_status(engine, project.id) == "FROZEN"
    # Re-import: everything is already there
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler())) as http:
        again = await _import_pubmed(
            str(project.id), "references", session_factory=factory, http=http, refs=refs
        )
    assert (again["imported"], again["duplicates"]) == (0, 3)


async def test_import_references_api(client, researcher_user, do_login, monkeypatch):
    from unittest.mock import MagicMock

    import app.tasks as tasks

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = (await client.post("/api/v1/projects", json={"title": "P"})).json()["id"]
    url = f"/api/v1/projects/{pid}/import-references"
    content = "10.1001/in-pubmed\n10.1001/in-pubmed\n555\n"
    dry = await client.post(url, json={"content": content, "dry_run": True})
    assert dry.status_code == 202
    assert dry.json() | {"max": 0} == {
        "format": "list",
        "count": 2,
        "with_doi": 1,
        "with_pmid": 1,
        "title_only": 0,
        "max": 0,
    }
    assert (await client.post(url, json={"content": "rien d'utile"})).status_code == 422
    assert (await client.post(url, json={"content": "[1, "})).status_code == 422
    result = MagicMock(id="job-1", state="PENDING")
    delay = MagicMock(return_value=result)
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", delay)
    resp = await client.post(url, json={"content": content})
    assert resp.status_code == 202 and resp.json()["job_id"] == "job-1"
    args = delay.call_args.args
    assert args[1] == "references (list, 2)" and len(args[2]) == 2
