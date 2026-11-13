#!/usr/bin/env python3
"""Build data/latest.json from what the sweep and the marketplace crawl collected (stdlib only).

  python3 tools/assemble.py                 merge + dedupe + benchmark -> data/latest.json, data/recheck.txt
  python3 tools/assemble.py --checks pc.log apply page-check results first (politefetch output), then rebuild

It replaces the hand-written merge of earlier runs, so every run applies the same rules:
 - every listing must satisfy every saved limit (marketplace.in_focus, plus distance when set)
 - one car listed in two places is published once (same make, model, year and exact odometer);
   the dealer's own page wins over the marketplace copy, which is kept in `also_on`
 - benchmark: same make + model + fuel, within 2 model years and similar km (30,000 km or 35 %, whichever
   is larger), median asking price, at least 3 comparables, drawn from EVERYTHING collected
   (not only the in-scope cars) so the median is not squeezed by the saved price limits
 - availability "verified" = seen live today (dealer page opened today, or on a marketplace results
   page fetched in the last 6 h, or its own page opened by a page-check); otherwise "listed"
data/recheck.txt lists the listing pages worth opening: marketplace rows that were NOT re-seen today.
Run tools/finalize.py afterwards, as before.
"""
import argparse, datetime, json, math, re, statistics, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marketplace

ROOT = Path(__file__).resolve().parent.parent
BNE = datetime.timezone(datetime.timedelta(hours=10))
KEEP = ('id', 'make', 'model', 'variant', 'year', 'km', 'price_aud', 'body', 'state', 'suburb', 'postcode', 'source', 'seller_type',
        'transmission', 'fuel', 'colour', 'url', 'image_url', 'status', 'availability', 'first_seen_at')
MAX_RECHECK = 25


def norm_make(raw, focus):
    m = marketplace.MAKE_NAME.get(raw, raw) or ''
    for fm in focus.get('makes') or []:
        if m.lower() == fm.lower():
            return fm
    return m.title() if m.isupper() and len(m) > 3 else m


def norm(d, focus):
    d = dict(d)
    raw = d.get('make') or ''
    d['make'] = norm_make(raw, focus)
    if raw.lower() == 'haval' and d.get('model') and not str(d['model']).lower().startswith('haval'):
        d['model'] = 'Haval ' + str(d['model'])       # CarsGuide files these as GWM Haval / "Haval H6"
    d['postcode'] = d.get('postcode') or None
    return d


def model_key(d):
    return re.sub(r'[^a-z0-9]', '', str(d.get('model') or '').lower())


def car_key(d):
    return (d.get('make'), model_key(d), d.get('year'), d.get('km')) if d.get('km') and d.get('year') else None


def dedupe(rows):
    """Same id once; then the same car across sources once, preferring the seller's own page."""
    by_id = {}
    for d in rows:
        by_id.setdefault(d['id'], d)
    out, seen = [], {}
    for d in sorted(by_id.values(), key=lambda d: (d.get('source') == 'CarsGuide', d['id'])):
        k = car_key(d)
        if k and k in seen:
            first = seen[k]
            if d.get('source') != first.get('source'):
                first.setdefault('also_on', []).append({'source': d.get('source'), 'url': d.get('url')})
                if not first.get('image_url'):
                    first['image_url'] = d.get('image_url')
            continue
        if k:
            seen[k] = d
        out.append(d)
    return out


def haversine(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def locate(d, places):
    pc = str(d.get('postcode') or '')
    if pc in places.get('postcodes', {}):
        return places['postcodes'][pc]
    return places.get('places', {}).get(f"{str(d.get('suburb') or '').lower()}|{d.get('state')}")


def within(d, focus, places):
    """False only when the listing can be placed and is outside the saved radius."""
    r = focus.get('radius_km')
    centre = focus.get('near_latlng') or (locate({'postcode': focus.get('near'), 'suburb': focus.get('near'), 'state': focus.get('state')}, places) if focus.get('near') else None)
    if not r or not centre:
        return True
    at = locate(d, places)
    return True if not at else haversine(centre, at) <= r


def benchmark(d, pool):
    if not d.get('km') or not d.get('year') or not d.get('price_aud'):
        return None, 0
    span = max(30000, 0.35 * d['km'])
    comps = [c['price_aud'] for c in pool.get((d['make'], model_key(d)), [])
             if c['id'] != d['id'] and c.get('price_aud') and c.get('year') and c.get('km') is not None
             and abs(c['year'] - d['year']) <= 2 and abs(c['km'] - d['km']) <= span
             and (not c.get('fuel') or not d.get('fuel') or c['fuel'] == d['fuel'])]      # a petrol Corolla is not a hybrid's comparable
    return (statistics.median(comps), len(comps)) if len(comps) >= 3 else (None, len(comps))


def odd_odometer(d, year_now):
    if not d.get('km') or not d.get('year'):
        return False
    age = max(0, year_now - d['year'])
    return d['km'] > 45000 * (age + 1) or (age >= 3 and d['km'] < 2000)


def apply_checks(log_path, cache, now):
    """politefetch output -> update the marketplace cache. Returns counters for coverage.page_checks."""
    st = {'attempted': 0, 'opened': 0, 'refused': 0, 'removed': 0, 'price_changes': 0}
    for line in Path(log_path).read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = re.search(r'/car/(\d+)/', r.get('u', ''))
        if not m:
            continue
        st['attempted'] += 1
        L = cache['listings'].get('cg-' + m.group(1))
        if r.get('code') in (404, 410):
            st['removed'] += 1
            if L: L['status'] = 'removed'
            continue
        if not r.get('f'):
            st['refused'] += 1
            continue
        d = marketplace.parse_detail(Path(r['f']).read_text())
        if d is None:
            st['refused'] += 1
            continue
        st['opened'] += 1
        if not L:
            continue
        if d.get('status') not in (None, 'Live') or d.get('deleted_at'):
            L['status'] = 'removed'; st['removed'] += 1
            continue
        if d.get('price_aud') and d['price_aud'] != L.get('price_aud'):
            L['price_aud'] = d['price_aud']; st['price_changes'] += 1
        for k in ('km', 'fuel', 'transmission'):
            if d.get(k) and not L.get(k):
                L[k] = d[k]
        L['seen_at'] = now.isoformat(); L['last_seen'] = now.date().isoformat()
    return st


def build(focus, sweep, cache, registry, places, now, checks=None):
    today = now.astimezone(BNE).date().isoformat()
    names = {s['id']: s['name'] for s in registry.get('sources', [])}
    sweep_day = (sweep.get('swept_at') or '')[:10]
    dealer = []
    for d in sweep.get('listings', []):
        if d.get('status') != 'active':
            continue
        d = norm(d, focus)
        d['availability'] = 'verified' if (d.get('checked') or sweep_day) == today else 'listed'
        dealer.append(d)
    market_all = [norm(d, focus) for d in cache['listings'].values()
                  if d.get('status') == 'active' and d.get('last_seen', '') >= (now.date() - datetime.timedelta(days=3)).isoformat()]
    market = [norm(d, focus) for d in marketplace.export(focus, cache, now=now)]
    pool = {}
    for d in dedupe([dict(x) for x in dealer + market_all]):
        pool.setdefault((d['make'], model_key(d)), []).append(d)
    scoped = [d for d in dealer if marketplace.in_focus(d, focus)] + market
    scoped = [d for d in dedupe(scoped) if within(d, focus, places) and d.get('url')]
    out, recheck = [], []
    for d in scoped:
        med, n = benchmark(d, pool)
        row = {k: d.get(k) for k in KEEP}
        row['median_aud'] = round(med) if med else None
        row['below_market_pct'] = round((med - d['price_aud']) / med * 100, 1) if med else None
        row['comparables'] = n
        flags = []
        if (row['below_market_pct'] or 0) >= 25 or odd_odometer(d, now.astimezone(BNE).year) or (d.get('seller_type') == 'private' and not d.get('image_url')):
            flags.append('verify')
        row['verify'] = bool(flags); row['flags'] = flags
        if d.get('also_on'):
            row['also_on'] = d['also_on']
        out.append(row)
        if d.get('recheck'):
            recheck.append(row)
    out.sort(key=lambda r: -(r['below_market_pct'] or -999))
    recheck.sort(key=lambda r: -(r['below_market_pct'] or -999))
    sw_cov, sources, per = {}, {}, {}
    for r in out:
        per[r['source']] = per.get(r['source'], 0) + 1
    for sid, c in (sweep.get('coverage') or {}).items():
        name = names.get(sid, sid)
        got = [d for d in dealer if d.get('source') == name]
        sw_cov[name] = {'planned': 1, 'ok': 1 if c.get('ok') else 0, 'listings': len(got), 'in_scope': per.get(name, 0)}
        if c.get('error'):
            sw_cov[name]['error'] = c['error']
        sources[name] = {'planned': 1, 'ok': 1 if c.get('ok') else 0}
    cov = {'sources_total': len(sources) + 1, 'sources_with_listings': len(per), 'by_source': dict(sorted(per.items(), key=lambda kv: -kv[1])),
           'sweep': sw_cov, 'sources': sources,
           'not_fetched': ['Carsales (blocks automated access)', 'Gumtree (blocks automated access)', 'Autotrader (same pool as CarsGuide)',
                           'Facebook Marketplace (needs a login)', 'Pickles and Manheim (robots.txt disallows the results)']}
    if checks:
        cov['page_checks'] = checks
    status = 'idle' if not (focus.get('makes') or out) else 'ok'
    latest = {'updated': now.astimezone(BNE).isoformat(timespec='seconds'), 'status': status, 'coverage': cov, 'listings': out}
    return latest, [r['url'] for r in recheck[:MAX_RECHECK]]


def load(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--checks', help='politefetch output of the page-checks to apply first')
    ap.add_argument('--out', default=str(ROOT / 'data/latest.json')); ap.add_argument('--recheck-out', default=str(ROOT / 'data/recheck.txt'))
    a = ap.parse_args(argv)
    now = datetime.datetime.now(datetime.timezone.utc)
    focus = load(ROOT / 'data/focus.json', {})
    cache = marketplace.load_cache()
    checks = None
    if a.checks:
        checks = apply_checks(a.checks, cache, now)
        marketplace.save_cache(cache)
    latest, recheck = build(focus, load(ROOT / 'data/sweep.json', {}), cache, load(ROOT / 'data/sources.json', {}), load(ROOT / 'data/places.json', {}), now, checks)
    Path(a.out).write_text(json.dumps(latest, ensure_ascii=False, indent=1))
    Path(a.recheck_out).write_text('\n'.join(recheck) + ('\n' if recheck else ''))
    L = latest['listings']
    print(json.dumps({'listings': len(L), 'by_source': latest['coverage']['by_source'], 'benchmarked': sum(1 for r in L if r['median_aud']),
                      'deals_3pct': sum(1 for r in L if (r['below_market_pct'] or 0) >= 3), 'verified': sum(1 for r in L if r['availability'] == 'verified'),
                      'to_recheck': len(recheck), 'page_checks': checks}))


if __name__ == '__main__':
    main()
