from typing import Optional, Tuple
import re
from collections import Counter
from bs4 import BeautifulSoup
import spacy
from langdetect import detect

# uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_trf-3.8.0/en_core_web_trf-3.8.0-py3-none-any.whl
# uv pip install https://github.com/explosion/spacy-models/releases/download/fr_core_news_lg-3.8.0/fr_core_news_lg-3.8.0-py3-none-any.whl
_nlp_cache = {}

# ---------------------------------------------------------------------------
# Regex fallback patterns
# ---------------------------------------------------------------------------
_COMPANY_SUFFIXES = [
    # English / International
    r"\bInc\b",
    r"\bIncorporated\b",
    r"\bLLC\b",
    r"\bL\.L\.C\b",
    r"\bLLP\b",
    r"\bL\.L\.P\b",
    r"\bLtd\b",
    r"\bLimited\b",
    r"\bCo\b",
    r"\bCompany\b",
    r"\bCorp\b",
    r"\bCorporation\b",
    r"\bPLC\b",

    # German
    r"\bGmbH\b",
    r"\bAG\b",
    r"\bKG\b",
    r"\bOHG\b",
    r"\bGbR\b",

    # Dutch / Belgian
    r"\bB\.V\.\b",
    r"\bN\.V\.\b",

    # Italian
    r"\bS\.p\.A\.\b",

    # Spanish
    r"\bS\.L\.\b",

    # French / Francophone
    r"\bS\.A\.\b",
    r"\bSA\b",
    r"\bS\.A\.S\b",
    r"\bSAS\b",
    r"\bS\.A\.R\.L\b",
    r"\bSARL\b",
    r"\bS\.R\.L\b",
    r"\bSoci[eé]t[eé]\b",
    r"\bCompagnie\b",
    r"\bEntreprise\b",
    r"\bEntreprises\b",
    r"\bÉtablissements\b",
    r"\bEtablissements\b",
    r"\bCabinet\b",
    r"\bAgence\b",
    r"\bBureau\b",
    r"\bAtelier\b",
    r"\bet Cie\b",
    r"\b& Cie\b",
    r"\b& Co\b",

    # Asia / Oceania
    r"\bPty\b",
    r"\bPte\b",
    r"\bK\.K\.\b",
]

_COMPANY_RE = re.compile(
    r"(?:" + "|".join(_COMPANY_SUFFIXES) + r")",
    re.IGNORECASE,
)

_TEXT_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "a", "strong", "em", "b", "i", "label",
    "dt", "dd", "figcaption", "blockquote", "section", "article",
    "header", "footer", "address", "nav", "pre", "code", "small",
    "caption", "option", "summary",
}

def _get_nlp(lang: str):
    model = "fr_core_news_lg" if lang == "fr" else "en_core_web_trf"
    if model not in _nlp_cache:
        _nlp_cache[model] = spacy.load(model)
    return _nlp_cache[model]

def _strip_html(html: str) -> str:
    """Parse HTML and return clean text for NER processing. Remove headers, scripts, styles, and excessive whitespace."""
    bs = BeautifulSoup(html, "html.parser")
    # Keep only the body content if available, otherwise use the whole text
    if bs.body:
        bs = bs.body
    
    # Remove script and style elements
    for node in bs(["script", "style", "noscript", "svg" , "head"]):
        node.decompose()
    return bs.get_text(" ", strip=True)
 

def _regex_extract_company(html: str) -> Optional[str]:
    """Fallback extraction using company suffix regex patterns.
    Searches HTML elements for matching patterns and returns the full text
    content of the containing tag as the company name.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(_TEXT_TAGS):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if _COMPANY_RE.search(text):
            return text
    return None


def extract_name_company(html: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract the most likely person name and company name from the given text using spaCy NER and heuristic scoring."""
    if not html:
        return None, None
    
    text = _strip_html(html)

    lang = "en"
    try:
        lang = detect(text)
    except Exception:
        pass

    doc = _get_nlp(lang)(text)

    person_counts = Counter(e.text for e in doc.ents if e.label_ == "PERSON")
    org_counts    = Counter(e.text for e in doc.ents if e.label_ == "ORG")

    spacy_person = person_counts.most_common(1)[0][0] if person_counts else None
    spacy_org = org_counts.most_common(1)[0][0] if org_counts else None

    return spacy_person, spacy_org or _regex_extract_company(html)