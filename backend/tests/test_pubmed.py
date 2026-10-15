"""Tests du client NCBI E-utilities (app/pubmed) — 100 % offline (httpx.MockTransport)."""

import xml.etree.ElementTree as ET

import httpx
import pytest

from app.pubmed import NCBIError, fetch_metadata, fetch_methods, fetch_pmids, search_volume


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Pas de vrai sleep en tests (throttle NCBI 3/10 req/s)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- search_volume ---


async def test_search_volume_returns_count():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/esearch.fcgi"
        assert request.url.params["db"] == "pubmed"
        assert request.url.params["term"] == "cancer AND mice"
        assert request.url.params["retmax"] == "0"
        return httpx.Response(200, json={"esearchresult": {"count": "42"}})

    async with _client(handler) as http:
        count = await search_volume("cancer AND mice", None, http)
    assert count == 42


async def test_search_volume_sends_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "my-key"
        return httpx.Response(200, json={"esearchresult": {"count": "7"}})

    async with _client(handler) as http:
        count = await search_volume("cancer", "my-key", http)
    assert count == 7


# --- fetch_pmids ---


async def test_fetch_pmids_default_retmax_10000():
    """M1 : retmax par défaut = 10000 (max réel NCBI, pas 1000)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/esearch.fcgi"
        assert request.url.params["retmax"] == "10000"
        return httpx.Response(200, json={"esearchresult": {"idlist": ["1", "2"]}})

    async with _client(handler) as http:
        assert await fetch_pmids("cancer", None, http) == ["1", "2"]


async def test_fetch_pmids_cap_10000():
    """M1 : retmax > 10000 est plafonné à 10000."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["retmax"] == "10000"
        return httpx.Response(200, json={"esearchresult": {"idlist": ["1"]}})

    async with _client(handler) as http:
        assert await fetch_pmids("cancer", None, http, retmax=50000) == ["1"]


async def test_fetch_pmids_sends_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "my-key"
        return httpx.Response(200, json={"esearchresult": {"idlist": ["1"]}})

    async with _client(handler) as http:
        assert await fetch_pmids("cancer", "my-key", http) == ["1"]


# --- retry sur erreur transitoire (m5) ---


async def test_esearch_retry_on_503_then_success():
    """m5 : 503 transitoire → retry (2 max) puis succès (3 appels au total)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"esearchresult": {"count": "5"}})

    async with _client(handler) as http:
        assert await search_volume("cancer", None, http) == 5
    assert calls["n"] == 3


async def test_esearch_retry_exhausted_raises():
    """m5 : 503 persistant → lève après 2 retries (3 appels au total).

    C-1 : l'exception est NCBIError (message propre, sans l'URL ni la clé), pas
    httpx.HTTPStatusError (dont le str() contient l'URL complète avec api_key).
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="Service Unavailable")

    async with _client(handler) as http:
        with pytest.raises(NCBIError) as exc_info:
            await search_volume("cancer", "my-key", http)
    assert calls["n"] == 3
    assert str(exc_info.value) == "HTTP 503"
    assert "eutils" not in str(exc_info.value)
    assert "my-key" not in str(exc_info.value)


async def test_timeout_raises_ncbi_error():
    """C-1 : timeout persistant → NCBIError('timeout') (pas d'exception httpx brute)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async with _client(handler) as http:
        with pytest.raises(NCBIError) as exc_info:
            await search_volume("cancer", None, http)
    assert calls["n"] == 3
    assert str(exc_info.value) == "timeout"


async def test_fetch_methods_503_raises_ncbi_error():
    """C-1 : fetch_methods 503 → NCBIError (sans l'URL)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    async with _client(handler) as http:
        with pytest.raises(NCBIError) as exc_info:
            await fetch_methods("PMC111", http)
    assert str(exc_info.value) == "HTTP 503"
    assert "ncbi.nlm.nih.gov" not in str(exc_info.value)


async def test_efetch_retry_on_timeout():
    """m5 : timeout transitoire → retry puis succès."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, text=_EFETCH_XML)

    async with _client(handler) as http:
        articles = await fetch_metadata(["111", "222"], None, http)
    assert len(articles) == 2
    assert calls["n"] == 2


async def test_no_retry_on_parse_error():
    """m5 : erreur de parsing (XML invalide) → pas de retry (1 seul appel)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<not-xml")

    async with _client(handler) as http:
        with pytest.raises(ET.ParseError):
            await fetch_metadata(["111"], None, http)
    assert calls["n"] == 1


# --- limite de taille XML (I5) ---


async def test_xml_size_limit_metadata():
    """I5 : payload XML efetch > 10 Mo → refusé (défense en profondeur)."""
    big = "<PubmedArticleSet><x>" + "A" * (10 * 1024 * 1024 + 1) + "</x></PubmedArticleSet>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    async with _client(handler) as http:
        with pytest.raises(ValueError, match="too large"):
            await fetch_metadata(["111"], None, http)


async def test_xml_size_limit_methods():
    """I5 : payload XML PMC OA > 10 Mo → refusé."""
    big = "<PMCPublication><x>" + "A" * (10 * 1024 * 1024 + 1) + "</x></PMCPublication>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    async with _client(handler) as http:
        with pytest.raises(ValueError, match="too large"):
            await fetch_methods("PMC111", http)


# --- fetch_metadata ---

# Structure réelle PubMed (vérifiée 2026-10-12 sur efetch) :
# - PMID est enfant direct de MedlineCitation (PAS dans Article).
# - ArticleIdList est dans PubmedData (sibling de MedlineCitation, PAS dedans).
_EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <ArticleTitle>Article one</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Abstract one.</AbstractText>
        </Abstract>
        <Language>English</Language>
        <ELocationID EIdType="doi" ValidYN="Y">10.1/one</ELocationID>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">111</ArticleId>
        <ArticleId IdType="pmc">PMC111</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>222</PMID>
      <Article>
        <ArticleTitle>Article two</ArticleTitle>
        <Abstract>
          <AbstractText>Abstract two.</AbstractText>
        </Abstract>
        <Language>Français</Language>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">222</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


async def test_fetch_metadata_sends_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "my-key"
        return httpx.Response(200, text=_EFETCH_XML)

    async with _client(handler) as http:
        articles = await fetch_metadata(["111", "222"], "my-key", http)
    assert len(articles) == 2


async def test_fetch_metadata_parses_xml():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/efetch.fcgi"
        assert request.url.params["db"] == "pubmed"
        assert request.url.params["retmode"] == "xml"
        assert request.url.params["id"] == "111,222"
        return httpx.Response(200, text=_EFETCH_XML)

    async with _client(handler) as http:
        articles = await fetch_metadata(["111", "222"], None, http)
    assert len(articles) == 2
    a1, a2 = articles
    assert a1["pmid"] == "111"
    assert a1["pmcid"] == "PMC111"
    assert a1["doi"] == "10.1/one"
    assert a1["title"] == "Article one"
    assert a1["abstract"] == "Abstract one."
    assert a1["language"] == "English"
    assert a2["pmid"] == "222"
    assert a2["pmcid"] is None
    assert a2["doi"] is None
    assert a2["title"] == "Article two"
    assert a2["abstract"] == "Abstract two."
    assert a2["language"] == "Français"


async def test_fetch_metadata_empty_list():
    async with _client(lambda r: (_ for _ in ()).throw(AssertionError("no call"))) as http:
        assert await fetch_metadata([], None, http) == []


# --- fetch_methods ---

# Full-text PMC en JATS (tags minuscules : sec/title/p) — vérifié 2026-10-12
# sur efetch db=pmc (l'ancien oa.fcgi est obsolète, 404 systématique).
_PMC_XML = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>Introduction</title>
        <p>Intro text.</p>
      </sec>
      <sec>
        <title>Methods</title>
        <p>We used a randomised design.</p>
        <sec>
          <title>Participants</title>
          <p>100 participants.</p>
        </sec>
      </sec>
      <sec>
        <title>Results</title>
        <p>Results text.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>"""


async def test_fetch_methods_extracts_methods_section():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/efetch.fcgi"
        assert request.url.params["db"] == "pmc"
        assert request.url.params["id"] == "PMC111"
        return httpx.Response(200, text=_PMC_XML)

    async with _client(handler) as http:
        methods = await fetch_methods("PMC111", http)
    assert methods is not None
    assert "We used a randomised design" in methods
    assert "100 participants" in methods
    assert "Intro text" not in methods
    assert "Results text" not in methods


async def test_fetch_methods_none_without_pmcid():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("NCBI ne doit pas être appelé sans pmcid")

    async with _client(handler) as http:
        assert await fetch_methods("", http) is None


async def test_fetch_methods_none_when_no_methods_section():
    xml = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>Results</title>
        <p>Only results.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    async with _client(handler) as http:
        assert await fetch_methods("PMC999", http) is None


def test_parse_biblio_metadata():  # noqa: E501
    from app.pubmed import _parse_pubmed_xml

    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
    <Journal><Title>Nature</Title><JournalIssue><PubDate><Year>2024</Year><Month>May</Month>
    </PubDate></JournalIssue></Journal><ArticleTitle>T</ArticleTitle>
    <AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author>
    <Author><CollectiveName>The Group</CollectiveName></Author>
    <Author><LastName>Poe</LastName><Initials>P</Initials></Author></AuthorList>
    <PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
    </Article></MedlineCitation><PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.1/x</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
    <PubmedArticle><MedlineCitation><PMID>2</PMID><Article>
    <Journal><ISOAbbreviation>J Neu</ISOAbbreviation><JournalIssue><PubDate>
    <MedlineDate>2023 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
    <ArticleDate><Year>2022</Year><Month>12</Month><Day>31</Day></ArticleDate>
    </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    a, b = _parse_pubmed_xml(xml)
    assert (a["journal"], a["pub_date"], a["doi"]) == ("Nature", "2024-05", "10.1/x")
    assert a["authors"] == ["Doe J", "The Group", "Poe P"] and a["pub_types"] == ["Review"]
    assert (b["journal"], b["pub_date"]) == ("J Neu", "2022-12-31")  # date électronique d'abord
