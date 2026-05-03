from typing import Optional, Tuple
import re
from bs4 import BeautifulSoup
import spacy
from langdetect import detect

# uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_trf-3.8.0/en_core_web_trf-3.8.0-py3-none-any.whl
# uv pip install https://github.com/explosion/spacy-models/releases/download/fr_core_news_lg-3.8.0/fr_core_news_lg-3.8.0-py3-none-any.whl
TITLES       = {"mr", "mrs", "ms", "dr", "prof", "m", "mme", "mlle", "pr", "ing"}
LEGAL        = {"inc", "llc", "ltd", "corp", "co", "sa", "sas", "sarl", "se"}
GENERIC_ORGS = {"group", "groupe", "company", "entreprise"}
_nlp_cache = {}


def _get_nlp(lang: str):
    model = "fr_core_news_lg" if lang == "fr" else "en_core_web_trf"
    if model not in _nlp_cache:
        _nlp_cache[model] = spacy.load(model)
    return _nlp_cache[model]

def _clean_person(name: str) -> Optional[str]:
    parts = [p for p in name.split() if p.lower().strip(".") not in TITLES]
    return " ".join(parts).title() if len(parts) >= 2 else None

def _clean_org(name: str) -> Optional[str]:
    parts = [p for p in re.sub(r"[.,]", "", name).split() if p.lower() not in LEGAL]
    if parts and parts[-1].lower() in GENERIC_ORGS:
        parts.pop()
    return " ".join(parts).title() if parts else None

def _score(name: str, context: str, kind: str) -> int:
    score = len(name.split()) >= 2 and 2 or 1
    if kind == "person":
        score += 3 * any(k in context for k in ["by ", "par ", "author", "auteur"])
    else:
        score += 2 * any(k in context for k in ["company", "groupe", "client", "careers", "emploi"])
    return score - (1 if name.isupper() else 0)

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
    context = text.lower()

    persons = [(c, _score(c, context, "person")) for e in doc.ents if e.label_ == "PERSON" and (c := _clean_person(e.text))]
    orgs    = [(c, _score(c, context, "org"))    for e in doc.ents if e.label_ == "ORG"    and (c := _clean_org(e.text))]

    best = lambda lst: max(lst, key=lambda x: x[1])[0] if lst else None
    return best(persons), best(orgs)


if __name__ == "__main__":
    sample_text = """
    Dr. Jane Doe is the CEO of Acme Corp, a leading company in the industry. 
    Contact her at jane.doe@acme.com or visit https://www.acme.com for more info.
    """

    person, company = extract_name_company(sample_text)
    print(f"Extracted Person: {person}")
    print(f"Extracted Company: {company}")