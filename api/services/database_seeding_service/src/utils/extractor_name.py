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
    "Inc", "LLC", "GmbH", "Pty", "Ltd", "Co", "Company", "Corp", "Corporation",
    "Enterprises", "Consulting", "Group", "sa", "sarl", "Solutions", "Holdings",
    "Services", "SNS", "L\\.L\\.C", "LLP", "L\\.L\\.P", "PLC", "B\\.V\\.", "N\\.V\\.",
    "S\\.A\\.", "S\\.A\\.S", "S\\.A\\.R\\.L", "S\\.R\\.L", "S\\.L\\.", "S\\.p\\.A\\.",
    "K\\.K\\.", "Pte", "AG", "KG", "OHG", "GbR", "Technologies", "International",
    "Global", "Partners", "Associates", "Agency", "Studio", "Labs", "Digital",
    "Media", "Creative", "Industries", "Systems", "Networks", "Capital",
    "Ventures", "Investments", "Properties", "Logistics", "Distribution",
    "Trading", "Manufacturing", "Engineering", "Construction", "Healthcare",
    "Pharma", "Energy", "Food", "Beverage", "Hospitality", "Travel",
    "Education", "Finance", "Insurance", "Realty", "Legal", "Law",
    "Entreprise", "Société", "Établissements", "Compagnie", "Bureau",
    "Cabinet", "Agence", "Atelier", "et Cie", "& Cie", "& Co",
    "Limited", "Incorporated",
]

_TITLE_PREFIXES = [
    "manager", "head", "director", "directeur", "ceo", "president", "pdg",
    "consultant", "expert", "associate", "founder", "co-founder", "chairman",
    "chairperson", "vice president", "vp", "chief", "lead", "senior",
    "specialist", "engineer", "developer", "analyst", "coordinator", "officer",
    "agent", "representative", "sales", "marketing", "support", "administrator",
    "supervisor", "executive", "partner", "owner", "proprietor", "gérant",
    "gérante", "responsable", "chef de", "chef", "commercial", "technicien",
    "ingénieur", "conseiller", "chargé", "assistant", "assistante",
    "secrétaire", "مدير", "مهندس", "محاسب", "موظف", "directeur général",
    "directrice", "administrateur", "professeur", "docteur",
]

_TEXT_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "a", "strong", "em", "b", "i", "label",
    "dt", "dd", "figcaption", "blockquote", "section", "article",
    "header", "footer", "address", "nav", "pre", "code", "small",
    "caption", "option", "summary",
}

_COMPANY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _COMPANY_SUFFIXES) + r")\b",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _TITLE_PREFIXES) + r")\b",
    re.IGNORECASE,
)


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
 

def _regex_extract_names(html: str) -> Tuple[Optional[str], Optional[str]]:
    """Fallback extraction using regex patterns for company suffixes and title prefixes.
    Searches HTML elements for matching patterns and returns the full text content
    of the containing tag.
    """
    if not html:
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.find_all(_TEXT_TAGS)
    person_name: Optional[str] = None
    company_name: Optional[str] = None

    for el in elements:
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if person_name is None and _TITLE_RE.search(text):
            person_name = text
        if company_name is None and _COMPANY_RE.search(text):
            company_name = text
        if person_name and company_name:
            break

    return person_name, company_name


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

    if spacy_person and spacy_org:
        return spacy_person, spacy_org

    regex_person, regex_org = _regex_extract_names(html)
    return spacy_person or regex_person, spacy_org or regex_org


if __name__ == "__main__":
    sample_text = """
    Dr. Jane Doe is the CEO of Acme Corp, a leading company in the industry. 
    Contact her at jane.doe@acme.com or visit https://www.acme.com for more info.
    """

    person, company = extract_name_company(sample_text)
    print(f"Extracted Person: {person}")
    print(f"Extracted Company: {company}")