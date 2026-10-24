"""ISBN-only online title enrichment. Book files and local paths stay local."""
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .metadata import valid_isbn
from .paths import STATE_DIR

CACHE = STATE_DIR / '.online-titles.json'
LOCK = threading.Lock()
LAST_REQUEST = 0.0


def canonical_isbn(value):
    value = re.sub(r'[^0-9Xx]','',str(value)).upper()
    if not valid_isbn(value):
        return None
    if len(value) == 10:
        prefix = '978'+value[:9]
        value = prefix+str((-sum(int(c)*(1 if i%2==0 else 3) for i,c in enumerate(prefix)))%10)
    return value


def full_title(title,subtitle=''):
    title = ' '.join(str(title or '').split()).strip(' :;')
    subtitle = ' '.join(str(subtitle or '').split()).strip(' :;')
    normalize = lambda s: re.sub(r'\W+','',s.casefold())
    if not title:
        return ''
    if subtitle and normalize(subtitle) not in normalize(title):
        return title+': '+subtitle
    return title


def fetch_json(url):
    global LAST_REQUEST
    # Sequential requests respect Open Library's unauthenticated 1 request/sec.
    delay = 1.05-(time.monotonic()-LAST_REQUEST)
    if delay > 0:
        time.sleep(delay)
    LAST_REQUEST = time.monotonic()
    request = Request(url,headers={'User-Agent':'EPDFInventory/1.0 (local ISBN title lookup)','Accept':'application/json'})
    with urlopen(request,timeout=8) as response:
        return json.loads(response.read(2_000_000).decode('utf-8'))


def lookup_title(isbn):
    key = canonical_isbn(isbn)
    if not key:
        return {'title':None,'reason':'No valid ISBN for online title lookup'}
    with LOCK:
        try:
            cache = json.loads(CACHE.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            cache = {}
        if key in cache and time.time()-cache[key].get('fetched_at',0) < 30*86400:
            return dict(cache[key])
        errors = []
        providers = [
            ('Google Books','https://www.googleapis.com/books/v1/volumes?'+urlencode({'q':'isbn:'+key,'maxResults':5})),
            ('Open Library',f'https://openlibrary.org/isbn/{key}.json')]
        for provider,url in providers:
            try:
                data = fetch_json(url)
                records = [item.get('volumeInfo',{}) for item in data.get('items',[])] if provider == 'Google Books' else [data]
                for record in records:
                    identifiers = ([item.get('identifier','') for item in record.get('industryIdentifiers',[])]
                                   if provider == 'Google Books' else record.get('isbn_10',[])+record.get('isbn_13',[]))
                    if key not in [canonical_isbn(value) for value in identifiers]:
                        continue
                    title = full_title(record.get('title'),record.get('subtitle'))
                    if not title:
                        continue
                    result = {'title':title,'provider':provider,'url':url,'matched_isbn':key,'fetched_at':time.time()}
                    cache[key] = result
                    try:
                        CACHE.parent.mkdir(parents=True,exist_ok=True)
                        temporary = CACHE.with_suffix('.tmp')
                        temporary.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
                        temporary.replace(CACHE)
                    except OSError:
                        pass
                    return result
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                errors.append(f'{provider}: {type(exc).__name__}')
        return {'title':None,'reason':'Online title unavailable; PDF title retained'+(' ('+'; '.join(errors)+')' if errors else '')}


def enrich_title(result,enabled=True):
    if not enabled:
        return result
    updated = dict(result)
    local_title = result.get('local_title',result['title'])
    lookup = lookup_title(result['isbn'])
    updated.update(local_title=local_title,title_lookup=lookup)
    updated['title'] = lookup.get('title') or local_title
    notes = [note for note in result.get('notes',[]) if not note.startswith(('Online title:','Online title unavailable','No valid ISBN for online'))]
    notes.append('Online title: '+lookup['provider']+'; exact ISBN matched; title and subtitle combined' if lookup.get('title') else lookup['reason'])
    updated['notes'] = sorted(set(notes))
    return updated
