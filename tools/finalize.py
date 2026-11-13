#!/usr/bin/env python3
"""Last step of every run: python3 tools/finalize.py
 1. fixes known body-type mislabels (data/body-rules.json)
 2. stamps last_seen on listings, keeps data/memory.json (id -> listing)
 3. CARRY-FORWARD: for any source whose pages mostly failed today, re-publishes that source's
    listings seen in the last MAX_AGE days (still in focus), tagged stale:true with their last_seen date,
    so a bad-403 day does not make the site one-sided or shrink it
 4. COVERAGE GATE: writes coverage.gate (complete|degraded|thin) + a plain-English message for the site

Agent supplies coverage.sources = {"CarsGuide": {"planned": 130, "ok": 98}, ...} (from politefetch stats).
"""
import json, sys, datetime
from pathlib import Path
import fix_body
import marketplace

ROOT = Path(__file__).resolve().parent.parent
MAX_AGE_DAYS = 3
OK_RATE, THIN_RATE = 0.85, 0.5


def in_scope(d, f):
    if f.get('makes') and d.get('make') not in f['makes']: return False
    if f.get('year_min') and (d.get('year') or 0) < f['year_min']: return False
    if f.get('year_max') and (d.get('year') or 9999) > f['year_max']: return False
    if f.get('km_min') and (d.get('km') or 0) < f['km_min']: return False
    if f.get('km_max') and (d.get('km') if d.get('km') is not None else 10**9) > f['km_max']: return False
    if f.get('price_min_aud') and (d.get('price_aud') or 0) < f['price_min_aud']: return False
    if f.get('price_max_aud') and (d.get('price_aud') or 10**9) > f['price_max_aud']: return False
    if f.get('body_types') and d.get('body') not in f['body_types']: return False
    if f.get('fuel') and d.get('fuel') not in f['fuel']: return False
    return True


SMALL = 'Dealers and small sources'


def gate(sources):
    """One dealer site being down must not paint the whole run thin: single-page sources are judged
    together, as one pooled source; anything with several pages (the marketplace) is judged on its own."""
    rated = {s: v for s, v in sources.items() if v.get('planned')}
    for v in rated.values():
        v['rate'] = round(v.get('ok', 0) / v['planned'], 2)
    big = {s: v for s, v in rated.items() if v['planned'] > 2}
    small = {s: v for s, v in rated.items() if v['planned'] <= 2}
    judged = dict(big)
    if small:
        ok = sum(1 for v in small.values() if v.get('ok', 0) >= v['planned'])
        judged[SMALL] = {'planned': len(small), 'ok': ok, 'rate': round(ok / len(small), 2)}
    bad = [s for s, v in judged.items() if v['rate'] < OK_RATE]
    thin = [s for s, v in judged.items() if v['rate'] < THIN_RATE]
    g = 'thin' if thin else 'degraded' if bad else 'complete'
    if SMALL in bad:      # carry-forward works per listing source, so name the ones that failed
        bad = [s for s in bad if s != SMALL] + [s for s, v in small.items() if v.get('ok', 0) < v['planned']]
        judged.update(small)
    return g, bad, judged


def message(g, bad, rated, carried):
    if g == 'complete': return ''
    parts = [f"{s}: {rated[s].get('ok', 0)} of {rated[s]['planned']} pages loaded" if rated[s]['planned'] > 2 else f"{s}: did not load" for s in bad]
    m = 'Some sources refused part of today\'s search (' + '; '.join(parts) + ').'
    if carried: m += f' {carried} listings are carried over from a recent run and marked "last seen".'
    return m + ' Results may under-represent those sources.'


def main(latest=ROOT / 'data/latest.json', memory=ROOT / 'data/memory.json', focus=ROOT / 'data/focus.json',
         today=None, market_cache=marketplace.CACHE):
    today = today or datetime.date.today().isoformat()
    latest, memory, focus = Path(latest), Path(memory), Path(focus)
    fix_body.main(latest, ROOT / 'data/body-rules.json')
    j = json.loads(latest.read_text())
    mem = json.loads(memory.read_text()) if memory.exists() else {}
    f = json.loads(focus.read_text()) if focus.exists() else {}
    cov = j.setdefault('coverage', {})
    if Path(market_cache).exists():   # CarsGuide coverage is measured from the crawl cache, never hand-written
        mc = marketplace.coverage(f, marketplace.load_cache(market_cache))
        cov.setdefault('sources', {})['CarsGuide'] = {'planned': mc['planned'], 'ok': mc['ok']}
        cov['market_cache_last_run'] = mc['last_run']
    g, bad, rated = gate(cov.setdefault('sources', {}))
    down = sorted(n for n, v in cov['sources'].items() if v.get('planned') and v.get('ok', 0) < v['planned'] and v['planned'] <= 2)
    cov['sources_down'] = down
    cur = {d['id'] for d in j['listings']}
    for d in j['listings']:
        d['last_seen'] = today; d.pop('stale', None)
    carried = 0
    if g != 'complete':
        cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=MAX_AGE_DAYS)).isoformat()
        for i, d in mem.items():
            if i in cur or d.get('source') not in bad and not (g == 'thin' and d.get('source') in rated): continue
            if d.get('last_seen', '') < cutoff or not in_scope(d, f): continue
            d = dict(d); d['stale'] = True; d['availability'] = 'listed'
            j['listings'].append(d); carried += 1
    for d in j['listings']:
        mem[d['id']] = {k: v for k, v in d.items() if k != 'stale'}
    mem = {i: d for i, d in mem.items() if d.get('last_seen', '') >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=14)).isoformat()}
    cov.update(gate=g, carried_over=carried, message=message(g, bad, rated, carried))
    if g != 'complete' and j.get('status') == 'ok': j['status'] = 'partial'
    latest.write_text(json.dumps(j, ensure_ascii=False, indent=1))
    memory.write_text(json.dumps(mem, ensure_ascii=False))
    print(f'finalize: gate={g} carried={carried} total={len(j["listings"])} memory={len(mem)}')
    return g


if __name__ == '__main__':
    main()
