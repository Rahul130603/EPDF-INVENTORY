"""Conservative metadata extraction; unknown values are never invented."""
import re
from functools import lru_cache


def valid_isbn(value: str) -> bool:
    value = re.sub(r"[^0-9Xx]", "", value).upper()
    if len(value) == 13 and value.isdigit() and value.startswith(("978", "979")):
        return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10 == 0
    if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == "X"):
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value)) % 11 == 0
    return False


def find_isbns(text: str) -> list[str]:
    # Fixed digit counts prevent swallowing adjacent dates, page numbers or two ISBNs.
    pattern = r"(?<!\d)(?:97[89](?:[\s\-–]*\d){10}|\d(?:[\s\-–]*\d){8}[\s\-–]*[\dXx])(?!\d)"
    found = []
    for match in re.finditer(pattern, text):
        value = re.sub(r"[\s\-–]", "", match.group()).upper()
        if valid_isbn(value) and value not in found:
            found.append(value)
    return found


def stem_key(stem: str) -> str:
    isbns = find_isbns(stem)
    if isbns:
        return isbns[0]
    stem = re.sub(r"(?:[ _.-]+(?:cover|front|back|scan|page|image|img|text|ocr)(?:[ _.-]*\d+)?)$", "", stem, flags=re.I)
    return re.sub(r"[\W_]+", "", stem.casefold()) or stem.casefold()


@lru_cache(maxsize=1)
def detector():
    from lingua import LanguageDetectorBuilder
    return LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()


def language_content(text: str) -> str:
    """Exclude catalog/publisher pages from book-language evidence only.

    Their text is still available to title/ISBN extraction. English copyright
    notices and bilingual CIP entries do not establish a book's actual language.
    """
    if re.search(r'library of congress|cataloging.in.publication|all rights reserved|'
                 r'cip.einheitsaufnahme|die deutsche bibliothek|copyright\s*©',text,re.I):
        return ''
    if re.search(r'general editors?|herausgegeben',text,re.I) and re.search(r'peter\s+lang|verlag|publishing',text,re.I):
        return ''
    result = []
    for line in text.splitlines():
        if re.search(r'isbn|issn|peter\s+lang|copyright|printed in|all rights|'
                     r'general editors?|^\s*vol\.?\s*\d',line,re.I):
            continue
        cities = re.findall(r'new york|washington|baltimore|san francisco|bern|frankfurt|berlin|vienna|wien|paris',line,re.I)
        if len(cities) >= 2:
            continue
        result.append(line)
    return '\n'.join(result)


def language_evidence(text: str | list[str]) -> dict:
    pages = [text] if isinstance(text,str) else text
    totals = {}
    weight_total, evidence, usable_words = 0, [], 0
    for index,page in enumerate(pages,1):
        cleaned = language_content(page)
        words = re.findall(r'[^\W\d_]{2,}',cleaned,re.UNICODE)
        # Short title pages are valuable after publisher material is removed.
        if len(words) < 4 or sum(map(len,words)) < 18:
            continue
        scores = detector().compute_language_confidence_values(' '.join(words[:1500]))
        weight = min(len(words),80)  # A verbose page must not swamp all other pages.
        for score in scores:
            totals[score.language.name] = totals.get(score.language.name,0.0) + score.value*weight
        usable_words += len(words)
        weight_total += weight
        evidence.append({'page':index,'detected':scores[0].language.name,
                         'confidence':round(scores[0].value,3),'words':len(words)})
    if not weight_total or usable_words < 8:
        return {'language':'NEEDS REVIEW','confidence':0.0,'detected':None,'pages':evidence,
                'reason':'Insufficient book text after excluding publisher/copyright pages'}
    ranked = sorted(((name,value/weight_total) for name,value in totals.items()),key=lambda item:item[1],reverse=True)
    name,confidence = ranked[0]
    label = name if confidence >= .80 and confidence-ranked[1][1] >= .30 else 'NEEDS REVIEW'
    reason = 'Conflicting or uncertain page-language evidence' if label == 'NEEDS REVIEW' else ''
    return {'language':label,'confidence':round(confidence,3),'detected':name,'pages':evidence,'reason':reason}


def language(text: str | list[str]) -> tuple[str, float]:
    evidence = language_evidence(text)
    return evidence['language'],evidence['confidence']


def title_from_text(text: str) -> str:
    explicit = re.search(r"(?im)^\s*(?:title|titel|book title)\s*:\s*(.{3,200})$", text)
    if explicit:
        return explicit.group(1).strip()
    for line in text.splitlines()[:35]:
        line = line.strip()
        if (4 <= len(line) <= 160 and len(re.findall(r"[^\W\d_]", line)) >= 4
                and not re.search(r"isbn|copyright|all rights|www\.|https?://|©", line, re.I)):
            return line
    return ""
