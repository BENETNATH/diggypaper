"""Régression pypdf + fontTools : parsing de l'encoding CFF Type1 (Type1C).

pypdf logue « fontTools is required to fully parse the encoding of a CFF Type1
font… » et tombe sur le fallback WinAnsi (texte faux) quand fontTools est absent
du venv. Fixture ``tests/data/cff_type1.pdf`` : police CFF sous-enssemblée,
slots 100/101/102 → asterisk/plus/space, sans ToUnicode.
"""

from pathlib import Path

from pypdf import PdfReader

FIXTURE = Path(__file__).parent / "data" / "cff_type1.pdf"


def test_cff_type1_no_fonttools_warning(caplog):
    """R1 : aucun warning « fontTools is required » (logger pypdf, WARNING)."""
    with caplog.at_level("WARNING", logger="pypdf"):
        PdfReader(FIXTURE).pages[0].extract_text()
    offenders = [r.message for r in caplog.records if "fontTools is required" in r.message]
    assert not offenders, offenders


def test_cff_type1_extracted_text():
    """R2 : texte dérivé de l'encoding interne du CFF, pas du fallback WinAnsi."""
    assert PdfReader(FIXTURE).pages[0].extract_text() == "*+ "
