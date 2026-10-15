from types import SimpleNamespace

from app.article_text import classify_title, compose_text, fill_prompt
from app.pubmed import _extract_sections

XML = """<article><body>
<sec><title>1. Introduction</title><p>intro</p></sec>
<sec><title>Materials and Methods</title><p>meth <i>x</i></p>
<sec><title>Sub</title><p>sub</p></sec></sec>
<sec><title>Results</title><p>res</p></sec>
</body></article>"""


def _art(**kw):
    base = dict(sections=None, abstract="abs", methods_text=None)
    return SimpleNamespace(**{**base, **kw})


def test_classify_and_extract():
    assert classify_title("Materials and Methods") == "methods"
    assert classify_title("Weird") == "other"
    secs = _extract_sections(XML)
    assert [s["kind"] for s in secs] == ["introduction", "methods", "results"]
    assert "meth x" in secs[1]["text"] and "sub" in secs[1]["text"]


def test_compose_text_selection_and_fallback():
    a = _art(sections=_extract_sections(XML))
    t = compose_text(a, ["results", "abstract"])
    assert "res" in t and "abs" in t and "meth" not in t
    assert "intro" in compose_text(a, ["all"])
    # pas de full text : repli methods_text puis abstract
    assert compose_text(_art(methods_text="M"), ["results"]) == "M"
    assert compose_text(_art(), None) == "abs"
    tpl = "{article_text}|{methods_text}"
    assert fill_prompt(tpl, _art(methods_text="M"), None) == "Methods\nM|M"
