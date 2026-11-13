#!/usr/bin/env python3
"""CarsGuide marketplace crawler: deterministic, polite, cache-based (stdlib only).

Usage
  python3 tools/marketplace.py --budget 150 [--min-interval 12] [--deadline 1200] [--fresh-hours 6]
      Plans the result-page URLs the focus needs (as tightly filtered as the site's clean paths
      allow: make / state / over-YEAR / fuel / price bucket), fetches them through politefetch
      (honest UA, one request per interval, circuit breaker), parses the page's embedded JSON and
      merges listings into data/market-cache.json. A refused page goes to the back of the queue
      and is tried again later (3 tries), so the crawl keeps moving instead of sleeping.
      The cache is saved every few pages, so a stopped run keeps what it fetched.
  python3 tools/marketplace.py --stats          -> coverage summary for latest.json
  python3 tools/marketplace.py --export         -> in-focus listings as JSON on stdout; rows not
      re-seen in the last 6 h carry "recheck": true (open those pages to see if they sold)
  python3 tools/marketplace.py --mark-removed cg-1,cg-2   -> record listings a page-check found gone

Robots: only clean paths plus ?page=N (CarsGuide disallows specific query params, not page).
Same inventory as Autotrader, so one door is enough; ids are the shared listing ids.
"""
import argparse, datetime, json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import politefetch
from sweep import norm_body, norm_fuel, norm_trans

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'data/market-cache.json'
BASE = 'https://www.carsguide.com.au/buy-a-car/used/'
IMG = 'https://images.autotrader.com.au/t_listing_grid_cg/'
STATES = ['qld', 'nsw', 'vic', 'sa', 'wa', 'tas', 'act', 'nt']
MAKE_NAME = {'GWM': 'GWM Haval', 'Haval': 'GWM Haval', 'Great Wall': 'GWM Haval', 'Mercedes': 'Mercedes-Benz', 'VW': 'Volkswagen'}  # site name -> focus name
MAKE_SLUG = {'GWM Haval': 'gwm', 'Mercedes-Benz': 'mercedes-benz', 'Land Rover': 'land-rover', 'Alfa Romeo': 'alfa-romeo'}
FUEL_SLUG = {'Hybrid': 'hybrid', 'Petrol': 'petrol', 'Diesel': 'diesel', 'Electric': 'electric-vehicle', 'Plug-in hybrid': 'hybrid'}   # the site has no separate plug-in facet
PRICE_BUCKETS = {(5000, 10000), (10000, 15000), (15000, 20000), (20000, 30000), (30000, 40000), (50000, 60000), (60000, 70000)}  # the site's own price facets
SEEN_FRESH_HOURS = 6   # a listing on a results page fetched this recently is live: no page-check needed
PAGE_CAP = 10          # pages followed per URL before splitting by state
PER_PAGE = 26
TAGS = {'Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'Set', 'Map', 'Date', 'RegExp', 'Error', 'BigInt', 'NuxtError', 'EmptyShallowRef', 'EmptyRef'}


# ---------- parsing ----------
def nuxt_data(html):
    m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    arr = json.loads(m.group(1)); seen = {}

    def res(i):
        if not isinstance(i, int) or i < 0:
            return None
        if i in seen:
            return seen[i]
        v = arr[i]
        if isinstance(v, dict):
            out = {}; seen[i] = out
            for k, x in v.items():
                out[k] = res(x)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in TAGS:
                if v[0] in ('Reactive', 'ShallowReactive', 'Ref', 'ShallowRef'):
                    return res(v[1])
                return v[1] if len(v) > 1 else None
            out = []; seen[i] = out
            out.extend(res(x) for x in v)
            return out
        return v
    root = res(0)
    return root.get('data', {}) if isinstance(root, dict) else {}


def parse_results(html):
    """-> (meta, [raw _source dicts]) or (None, []) when the page is not a results page."""
    d = nuxt_data(html) or {}
    sr = next((v for k, v in d.items() if k.startswith('searchResults') and isinstance(v, dict)), None)
    if not sr or not isinstance(sr.get('data'), list):
        return None, []
    meta = {'total': sr.get('total'), 'current_page': sr.get('current_page'), 'last_page': sr.get('last_page'), 'per_page': sr.get('per_page')}
    return meta, [x.get('_source', x) for x in sr['data'] if isinstance(x, dict)]


def parse_detail(html):
    """Listing page -> {'status','deleted_at','km','price_aud','fuel','transmission'} or None."""
    d = nuxt_data(html) or {}
    ld = next((v for k, v in d.items() if k.startswith('listingData') and isinstance(v, dict)), None)
    if not ld or not isinstance(ld.get('data'), dict):
        return None
    x = ld['data']; veh = x.get('vehicle') or {}; spec = veh.get('specification') or {}
    price = None
    for p in x.get('prices') or []:
        price = p.get('advertised_price') or p.get('driveaway_price') or price
    return {'status': x.get('status'), 'deleted_at': x.get('deleted_at'), 'km': x.get('odometer'), 'price_aud': price,
            'fuel': norm_fuel(spec.get('fuel_type_abbreviated') or spec.get('fuel_type') or veh.get('fuel_type')),
            'transmission': norm_trans(spec.get('transmission_type_abbreviated') or spec.get('transmission_type') or veh.get('transmission_type'))}


def to_listing(src, today):
    veh = src.get('vehicle') or {}
    p = src.get('price') or {}
    price = p.get('driveaway_price') if src.get('is_driveaway') and p.get('driveaway_price') else p.get('advertised_price')
    photos = src.get('photos') or []
    img = next((IMG + ph['image_path'] for ph in photos if ph.get('image_path')), None)
    url = src.get('url_cg') or src.get('url')
    alive = (src.get('status') in (None, 'Live')) and not src.get('deleted_at')
    make = src.get('make') or veh.get('make')
    return {
        'id': f"cg-{src.get('id')}", 'make': MAKE_NAME.get(make, make), 'model': src.get('model') or veh.get('model'),
        'variant': src.get('variant') or veh.get('variant'), 'year': src.get('manu_year'), 'km': src.get('odometer'),
        'price_aud': price or None, 'body': norm_body(veh.get('body_type_group') or veh.get('body_type')),
        'state': src.get('location_state'), 'suburb': src.get('location_city'), 'postcode': None,
        'source': 'CarsGuide', 'seller_type': 'auction' if src.get('is_auction') else 'private' if src.get('is_private') else 'dealer',
        'transmission': norm_trans(veh.get('transmission_type')), 'fuel': norm_fuel(veh.get('fuel_type')),
        'url': ('https://www.carsguide.com.au/' + url.lstrip('/')) if url else None, 'image_url': img,
        'status': 'active' if alive else 'removed', 'availability': 'listed', 'last_seen': today,
    }


# ---------- planning ----------
def slug(s):
    return MAKE_SLUG.get(s) or re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def base_urls(focus):
    makes = focus.get('makes') or ['Toyota', 'Mazda', 'Hyundai', 'Ford', 'Subaru', 'Volkswagen', 'Kia', 'Mitsubishi']
    fuels = sorted({FUEL_SLUG.get(f) for f in (focus.get('fuel') or []) if FUEL_SLUG.get(f)}) or [None]
    pmin, pmax = focus.get('price_min_aud'), focus.get('price_max_aud')
    price = f'${int(pmin)}-${int(pmax)}' if pmin and pmax and (int(pmin), int(pmax)) in PRICE_BUCKETS else f'under-${int(pmax)}' if pmax else None
    year = f"over-{int(focus['year_min']) - 1}" if focus.get('year_min') else None   # over-2018 lists 2018 and newer
    state = (focus.get('state') or '').lower() or None
    out = []
    for m in makes:
        models = (focus.get('models') or {}).get(m) or [None]
        for mo in models:
            for fu in fuels:
                parts = [slug(m)] + ([slug(mo)] if mo else []) + ([state] if state else []) + ([year] if year else []) + ([fu] if fu else []) + ([price] if price else [])
                out.append(BASE + '/'.join(parts))
    return out


def with_states(url):
    """Split a too-big URL by state: insert the state after make[/model]."""
    tail = url[len(BASE):].split('/')
    fixed = [t for t in tail if t in STATES or t.startswith(('under-', 'over-')) or '$' in t or t in FUEL_SLUG.values()]
    head = [t for t in tail if t not in fixed]
    if any(t in STATES for t in tail):
        return []
    return [BASE + '/'.join(head + [s] + fixed) for s in STATES]


def plan(focus, cache):
    """Every URL (with ?page=N) the focus needs, using totals the cache already knows."""
    urls = []
    for u in base_urls(focus):
        urls += expand(u, cache)
    return list(dict.fromkeys(urls))


def expand(u, cache):
    info = cache['urls'].get(u, {})
    lp = info.get('last_page')
    if lp and lp > PAGE_CAP:
        split = with_states(u)
        if split:
            out = []
            for s in split:
                out += expand(s, cache)
            return out
    pages = min(lp or 1, PAGE_CAP)
    return [u] + [f'{u}?page={n}' for n in range(2, pages + 1)]


# ---------- cache ----------
def load_cache(path=CACHE):
    if Path(path).exists():
        c = json.loads(Path(path).read_text())
        c.setdefault('urls', {}); c.setdefault('listings', {})
        return c
    return {'urls': {}, 'listings': {}}


def save_cache(c, path=CACHE, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now.date() - datetime.timedelta(days=30)).isoformat()
    c['listings'] = {i: d for i, d in c['listings'].items() if d.get('last_seen', '9999') >= cutoff}
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=0))
    os.replace(tmp, path)        # atomic: a killed run never leaves half a file


def hours_since(iso, now):
    if not iso:
        return 1e9
    return (now - datetime.datetime.fromisoformat(iso)).total_seconds() / 3600


def crawl(focus, cache, budget, fetcher, now=None, fresh_hours=6, log=print, deadline=None, clock=time.monotonic,
          save=None, save_every=5, max_tries=3):
    """Fetch every planned page that is not fresh. `budget` counts requests. A refused page is queued
    again behind the pages not tried yet, up to max_tries; the fetcher's circuit breaker ends the run
    if the host refuses everything. `deadline` is a clock() value; `save()` is called every few pages."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    stats = {'planned': 0, 'fetched': 0, 'ok': 0, 'failed': 0, 'retried_ok': 0, 'new_listings': 0, 'updated': 0, 'stopped': None}
    done, tries, unsaved = set(), {}, 0
    while True:
        urls = plan(focus, cache)
        stats['planned'] = len(urls)
        todo = [u for u in urls if u not in done and hours_since(cache['urls'].get(u, {}).get('last_ok'), now) > fresh_hours]
        if not todo:
            break
        if budget <= 0:
            stats['stopped'] = 'budget'; break
        if deadline is not None and clock() > deadline:
            stats['stopped'] = 'deadline'; break
        todo.sort(key=lambda u: (tries.get(u, 0), -hours_since(cache['urls'].get(u, {}).get('last_ok'), now), u))
        u = todo[0]; budget -= 1
        r = fetcher.get(u)
        tries[u] = tries.get(u, 0) + 1
        rec = cache['urls'].setdefault(u, {})
        rec['last_try'] = now.isoformat(); rec['last_code'] = r.code
        stats['fetched'] += 1
        if not r.ok:
            log(f'{r.code} {u} (try {tries[u]})')
            if r.attempts == 0:                       # circuit breaker: the host is refusing everything
                stats['stopped'] = 'host gave up'; break
            if tries[u] >= max_tries or r.code in politefetch.GONE_CODES:
                done.add(u)
            continue
        meta, rows = parse_results(r.body)
        if meta is None:
            rec['last_code'] = 'noparse'; done.add(u); log(f'noparse {u}'); continue
        done.add(u)
        want = u[len(BASE):].split('?')[0].split('/')[0]
        if rows and not any(slug(to_listing(src, today).get('make') or '') == want for src in rows):
            # the site ignores a path token it does not know and answers with every used car
            rec['last_code'] = 'filter ignored'; log(f'filter ignored {u} total={meta.get("total")}'); continue
        rec['last_ok'] = now.isoformat(); rec.update({k: meta[k] for k in ('total', 'last_page') if meta.get(k) is not None})
        stats['ok'] += 1
        if tries[u] > 1:
            stats['retried_ok'] += 1
        for src in rows:
            L = to_listing(src, today)
            L['seen_at'] = now.isoformat()
            old = cache['listings'].get(L['id'])
            if old:
                L['first_seen_at'] = old.get('first_seen_at', today); stats['updated'] += 1
            else:
                L['first_seen_at'] = today; stats['new_listings'] += 1
            cache['listings'][L['id']] = L
        log(f"200 {u} total={meta['total']} page={meta['current_page']}/{meta['last_page']} rows={len(rows)}")
        unsaved += 1
        if save and unsaved >= save_every:
            save(); unsaved = 0
    stats['failed'] = sum(1 for u in plan(focus, cache) if hours_since(cache['urls'].get(u, {}).get('last_ok'), now) > fresh_hours)
    cache['last_run'] = now.isoformat(); cache['last_stats'] = stats
    return stats


def coverage(focus, cache, now=None, window_hours=24):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    urls = plan(focus, cache)
    ok = sum(1 for u in urls if hours_since(cache['urls'].get(u, {}).get('last_ok'), now) <= window_hours)
    return {'planned': len(urls), 'ok': ok, 'window_hours': window_hours, 'last_run': cache.get('last_run')}


def in_focus(d, f):
    if f.get('makes') and d.get('make') not in f['makes']: return False
    mods = (f.get('models') or {}).get(d.get('make'))
    if mods and d.get('model') not in mods: return False
    if f.get('year_min') and (d.get('year') or 0) < f['year_min']: return False
    if f.get('year_max') and (d.get('year') or 9999) > f['year_max']: return False
    if f.get('km_min') and (d.get('km') or 0) < f['km_min']: return False
    if f.get('km_max') and (d.get('km') if d.get('km') is not None else 10**9) > f['km_max']: return False
    if f.get('price_min_aud') and (d.get('price_aud') or 0) < f['price_min_aud']: return False
    if f.get('price_max_aud') and (d.get('price_aud') or 10**9) > f['price_max_aud']: return False
    if f.get('body_types') and d.get('body') not in f['body_types']: return False
    if f.get('fuel') and d.get('fuel') not in f['fuel']: return False
    if f.get('state') and d.get('state') != f['state']: return False
    if f.get('transmission') and d.get('transmission') != f['transmission']: return False
    if f.get('seller_type') and d.get('seller_type') != f['seller_type']: return False
    return True


def normalise(cache):
    """Re-apply name rules to cached rows (rules can change after a crawl)."""
    for d in cache['listings'].values():
        d['make'] = MAKE_NAME.get(d.get('make'), d.get('make'))


def export(focus, cache, max_age_days=3, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    normalise(cache)
    cutoff = (now.date() - datetime.timedelta(days=max_age_days)).isoformat()
    out = []
    for d in cache['listings'].values():
        if d.get('status') != 'active' or d.get('last_seen', '') < cutoff or not in_focus(d, focus):
            continue
        d = dict(d)
        if hours_since(d.pop('seen_at', None), now) <= SEEN_FRESH_HOURS:
            d['availability'] = 'verified'       # on a results page fetched this run: live, price current
        else:
            d['availability'] = 'listed'; d['recheck'] = True
        out.append(d)
    return out


def mark_removed(cache, ids):
    n = 0
    for i in ids:
        if i in cache['listings'] and cache['listings'][i].get('status') != 'removed':
            cache['listings'][i]['status'] = 'removed'; n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', type=int, default=0); ap.add_argument('--min-interval', type=float, default=12)
    ap.add_argument('--fresh-hours', type=float, default=6); ap.add_argument('--focus', default=str(ROOT / 'data/focus.json'))
    ap.add_argument('--cache', default=str(CACHE)); ap.add_argument('--stats', action='store_true'); ap.add_argument('--export', action='store_true')
    ap.add_argument('--deadline', type=float, default=0, help='stop after this many seconds and save'); ap.add_argument('--mark-removed', default='')
    a = ap.parse_args(argv)
    focus = json.loads(Path(a.focus).read_text()) if Path(a.focus).exists() else {}
    cache = load_cache(a.cache)
    if a.budget:
        # one try per request (retries are queued by crawl); 8 refusals in a row = cool down 3 min, twice, then stop
        fx = politefetch.Fetcher(min_interval=a.min_interval, backoffs=(), breaker_after=8, cooldown=180, max_cooldowns=2)
        st = crawl(focus, cache, a.budget, fx, fresh_hours=a.fresh_hours, log=lambda m: print(m, flush=True),
                   deadline=(time.monotonic() + a.deadline) if a.deadline else None, save=lambda: save_cache(cache, a.cache))
        save_cache(cache, a.cache)
        print(json.dumps({'run': st, 'coverage': coverage(focus, cache), 'hosts': fx.stats}))
    if a.mark_removed:
        n = mark_removed(cache, [x for x in a.mark_removed.split(',') if x])
        save_cache(cache, a.cache); print(json.dumps({'marked_removed': n}))
    if a.stats:
        print(json.dumps(coverage(focus, cache)))
    if a.export:
        print(json.dumps(export(focus, cache), ensure_ascii=False))


if __name__ == '__main__':
    main()
