#!/usr/bin/env python3
"""Dealer stock sweep for car-radar. Stdlib only.

Reads data/sources.json, fetches every active dealer's used stock, and writes
data/sweep.json with listings in the site's schema. Honest single User-Agent,
1 request/second per host, no retries past a 403, hard deadline.

Usage: python3 tools/sweep.py [--deadline 540] [--out data/sweep.json] [--only id,id]
"""
import argparse, json, re, sys, time, threading, datetime, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BODY_MAP = {
    'suv': 'suv', 'hatch': 'hatch', 'hatchback': 'hatch', 'sedan': 'sedan', 'wagon': 'wagon',
    'ute': 'ute', 'utility': 'ute', 'cab chassis': 'ute', 'dual cab': 'ute', 'van': 'van',
    'coupe': 'coupe', 'convertible': 'convertible', 'people mover': 'van', 'bus': 'van',
}


def norm_body(s):
    s = (s or '').lower()
    for k, v in BODY_MAP.items():
        if k in s:
            return v
    return None


def norm_fuel(s):
    """Raw dealer fuel text -> Petrol | Hybrid | Plug-in hybrid | Diesel | Electric | None."""
    t = (s or '').lower()
    if not t.strip():
        return None
    if 'phev' in t or 'plug' in t:
        return 'Plug-in hybrid'
    petrolish = any(w in t for w in ('petrol', 'unleaded', 'ulp'))
    if 'hybrid' in t or ('electric' in t and (petrolish or 'diesel' in t)):
        return 'Hybrid'
    if 'electric' in t or t.strip() == 'ev':
        return 'Electric'
    if 'diesel' in t:
        return 'Diesel'
    if petrolish:
        return 'Petrol'
    return None


def norm_trans(s):
    s = (s or '').lower()
    if 'man' in s:
        return 'Manual'
    if s:
        return 'Auto'
    return None


def num(v):
    try:
        if v is None or v == '':
            return None
        return int(float(str(v).replace(',', '')))
    except ValueError:
        return None


# ---------- parsers (pure; unit-tested on saved pages) ----------

def parse_dealerstudio_sitemap(xml):
    return [u for u in re.findall(r'<loc>([^<]+)</loc>', xml) if '/cars/used-' in u]


def extract_carssr(html):
    """Pull the carSSR object out of the escaped Next.js RSC payload."""
    i = html.find('carSSR\\":{')
    if i < 0:
        return None
    seg = html[i + len('carSSR\\":'):i + 200000]
    seg = seg.replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\')
    depth = 0
    for k, ch in enumerate(seg):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(seg[:k + 1])
                except json.JSONDecodeError:
                    return None
    return None


def listing_from_carssr(d, src, url):
    if not d:
        return None
    loc = d.get('location') or {}
    addr = loc.get('address') or {}
    price = num(d.get('dapprice')) or num(d.get('price')) or num(d.get('egcprice'))
    if not price:
        return None
    imgs = d.get('images') or []
    img = None
    if imgs and isinstance(imgs[0], dict):
        img = (imgs[0].get('image') or {}).get('url')
    variant = ' '.join(x for x in [d.get('badge'), d.get('series')] if x) or None
    return {
        'id': f"{src['id']}-{d.get('stocknum') or d.get('id')}",
        'make': d.get('make'), 'model': d.get('model'), 'variant': variant,
        'year': num(d.get('model_year') or d.get('year')), 'km': num(d.get('km') or d.get('odometer_reading')),
        'price_aud': price, 'price_type': d.get('price_type'),
        'body': norm_body(d.get('simple_body') or d.get('body')),
        'transmission': norm_trans(d.get('simple_transmission')),
        'fuel': norm_fuel(d.get('simple_fuel') or d.get('fuel')), 'colour': d.get('colour'),
        'state': addr.get('state') or src.get('state'), 'suburb': addr.get('city') or src.get('town'),
        'postcode': str(addr.get('postcode') or src.get('postcode') or ''),
        'source': src['name'], 'seller_type': 'dealer', 'url': url, 'image_url': img,
        'status': 'removed' if (d.get('status') not in (None, 'in_stock') or d.get('sold_date')) else 'active',
        'days_old': d.get('days_old'), 'stock_no': d.get('stocknum'),
    }


def parse_imotor_page(html):
    """Return (hits, nbPages) from an i-Motor Gatsby stock page."""
    m = re.search(r'window\.pageData\s*=\s*', html)
    if not m:
        return [], 0
    j0 = m.end()
    depth = 0
    for k in range(j0, len(html)):
        c = html[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    j = json.loads(html[j0:k + 1])
                except json.JSONDecodeError:
                    return [], 0
                break
    else:
        return [], 0
    try:
        ir = j['result']['serverData']['serverState']['initialResults']
        key = next(x for x in ir if 'stock' in x)
        r = ir[key]['results'][0]
        return r.get('hits', []), r.get('nbPages', 1)
    except (KeyError, StopIteration, IndexError, TypeError):
        return [], 0


def listing_from_imotor(h, src):
    if str(h.get('vehType', '')).lower() != 'used' or h.get('archived'):
        return None
    price = num(h.get('price')) or num(h.get('egcPrice'))
    if not price:
        return None
    loc = h.get('location') or {}
    imgs = h.get('images') or []
    stock = h.get('stockNumber') or h.get('objectID')
    slug = h.get('slug')
    return {
        'id': f"{src['id']}-{stock}",
        'make': h.get('make'), 'model': h.get('model'), 'variant': h.get('series') or None,
        'year': num(h.get('year')), 'km': num(h.get('odometer')), 'price_aud': price,
        'price_type': h.get('priceQualifier'), 'body': norm_body(h.get('body')),
        'transmission': norm_trans(h.get('transmission')), 'fuel': norm_fuel(h.get('fuelType')), 'colour': h.get('colour'),
        'state': src.get('state'), 'suburb': loc.get('suburb') or loc.get('name') or src.get('town'),
        'postcode': str(loc.get('postcode') or src.get('postcode') or ''),
        'source': src['name'], 'seller_type': 'dealer',
        'url': f"{src['url'].rstrip('/')}/{slug.lstrip('/')}" if slug else f"{src['url'].rstrip('/')}{src.get('list_path', '/pre-owned/')}?stock={stock}",
        'image_url': imgs[0] if imgs and isinstance(imgs[0], str) else None,
        'status': 'active', 'stock_no': stock,
    }


# ---------- fetching ----------

class Fetcher:
    def __init__(self, ua, deadline):
        self.ua, self.deadline = ua, deadline
        self.last = {}
        self.lock = threading.Lock()
        self.count = 0

    def get(self, url, timeout=25):
        if time.time() > self.deadline:
            raise TimeoutError('deadline')
        host = url.split('/')[2]
        with self.lock:
            wait = self.last.get(host, 0) + 1.0 - time.time()
        if wait > 0:
            time.sleep(wait)
        with self.lock:
            self.last[host] = time.time()
            self.count += 1
        req = urllib.request.Request(url, headers={'User-Agent': self.ua, 'Accept': 'text/html,application/xml'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')


def sweep_dealerstudio(src, f, cache, today, log):
    base = src['url'].rstrip('/')
    st, xml = f.get(base + '/sitemap-vehicles.xml')
    urls = parse_dealerstudio_sitemap(xml)
    out, fetched = [], 0
    for u in urls:
        c = cache.get(u)
        if c and c.get('checked') == today:
            out.append(c['listing'])
            continue
        try:
            st, html = f.get(u)
        except TimeoutError:
            if c:
                out.append(c['listing'])
            continue
        except urllib.error.HTTPError as e:
            if e.code == 404 and c:
                c['listing']['status'] = 'removed'
                out.append(c['listing'])
            continue
        fetched += 1
        d = extract_carssr(html)
        L = listing_from_carssr(d, src, u)
        if L:
            L['first_seen_at'] = (c or {}).get('listing', {}).get('first_seen_at') or today
            cache[u] = {'checked': today, 'listing': L}
            out.append(L)
    log(f"{src['id']}: {len(urls)} used urls, {fetched} fetched, {len(out)} listings")
    return out


def sweep_imotor(src, f, cache, today, log):
    base = src['url'].rstrip('/')
    path = src.get('list_path', '/pre-owned/')
    out, page, pages = [], 1, 1
    while page <= pages and page <= 40:
        st, html = f.get(f"{base}{path}?page={page}" if page > 1 else base + path)
        hits, pages = parse_imotor_page(html)
        for h in hits:
            L = listing_from_imotor(h, src)
            if L:
                c = cache.get(L['url'])
                L['first_seen_at'] = (c or {}).get('listing', {}).get('first_seen_at') or today
                cache[L['url']] = {'checked': today, 'listing': L}
                out.append(L)
        page += 1
    log(f"{src['id']}: {pages} pages, {len(out)} used listings")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--deadline', type=int, default=540)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'sweep.json'))
    ap.add_argument('--cache', default=str(ROOT / 'data' / 'sweep-cache.json'))
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    reg = json.loads((ROOT / 'data' / 'sources.json').read_text())
    try:
        cache = json.loads(Path(a.cache).read_text())
    except (OSError, json.JSONDecodeError):
        cache = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=10))).date().isoformat()
    f = Fetcher(reg.get('user_agent', 'car-radar research bot'), time.time() + a.deadline)
    only = set(x for x in a.only.split(',') if x)
    srcs = [s for s in reg['sources'] if s.get('status') == 'active' and s.get('platform') in ('dealerstudio', 'imotor') and (not only or s['id'] in only)]
    results, coverage, lock = [], {}, threading.Lock()
    lines = []

    def log(m):
        with lock:
            lines.append(m)
        print(m, file=sys.stderr, flush=True)

    def run(src):
        fn = sweep_dealerstudio if src['platform'] == 'dealerstudio' else sweep_imotor
        try:
            L = fn(src, f, cache, today, log)
            with lock:
                results.extend(L)
                coverage[src['id']] = {'ok': True, 'listings': len(L)}
        except TimeoutError:
            with lock:
                coverage[src['id']] = {'ok': False, 'error': 'deadline'}
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the sweep
            with lock:
                coverage[src['id']] = {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:120]}'}
            log(f"{src['id']}: FAILED {type(e).__name__}: {str(e)[:120]}")

    threads = [threading.Thread(target=run, args=(s,), daemon=True) for s in srcs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=a.deadline + 30)
    # dedupe by stock number across shared pools
    seen, uniq = set(), []
    for L in results:
        k = (L.get('stock_no') or L['id'], L.get('make'), L.get('model'))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(L)
    out = {'swept_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=10))).isoformat(timespec='seconds'),
           'fetches': f.count, 'coverage': coverage, 'listings': uniq}
    Path(a.out).write_text(json.dumps(out, indent=1))
    Path(a.cache).write_text(json.dumps(cache))
    active = sum(1 for L in uniq if L['status'] == 'active')
    print(f"sweep: {len(srcs)} sources, {f.count} fetches, {len(uniq)} listings ({active} active) -> {a.out}", file=sys.stderr)


if __name__ == '__main__':
    main()
