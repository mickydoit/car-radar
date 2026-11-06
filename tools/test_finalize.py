import json, tempfile, unittest
from pathlib import Path
import finalize

FOCUS = {'makes': ['Toyota'], 'year_min': 2019, 'km_max': 50000, 'price_max_aud': 30000, 'fuel': ['Hybrid']}


def car(i, src='CarsGuide', **k):
    d = dict(id=i, make='Toyota', model='Yaris', year=2022, km=20000, price_aud=25000, fuel='Hybrid', source=src, body='hatch')
    d.update(k); return d


def run(listings, sources, memory=None, status='ok'):
    t = Path(tempfile.mkdtemp())
    (t / 'l.json').write_text(json.dumps({'status': status, 'listings': listings, 'coverage': {'sources': sources}}))
    (t / 'm.json').write_text(json.dumps(memory or {})); (t / 'f.json').write_text(json.dumps(FOCUS))
    g = finalize.main(t / 'l.json', t / 'm.json', t / 'f.json', today='2026-11-07')
    return g, json.loads((t / 'l.json').read_text()), json.loads((t / 'm.json').read_text())


class T(unittest.TestCase):
    def test_complete_day_no_carry_and_status_kept(self):
        g, j, m = run([car('a')], {'CarsGuide': {'planned': 10, 'ok': 10}}, {'old': dict(car('old'), last_seen='2026-11-06')})
        self.assertEqual(g, 'complete'); self.assertEqual(len(j['listings']), 1); self.assertEqual(j['status'], 'ok')
        self.assertEqual(j['coverage']['message'], '')

    def test_degraded_carries_recent_in_scope_only(self):
        mem = {'recent': dict(car('recent'), last_seen='2026-11-06'),
               'ancient': dict(car('ancient'), last_seen='2026-10-01'),
               'outscope': dict(car('outscope', km=90000), last_seen='2026-11-06'),
               'dealer': dict(car('dealer', src='Madill'), last_seen='2026-11-06')}
        g, j, m = run([car('a')], {'CarsGuide': {'planned': 100, 'ok': 55}, 'Madill': {'planned': 5, 'ok': 5}}, mem)
        ids = {d['id']: d for d in j['listings']}
        self.assertEqual(g, 'degraded'); self.assertEqual(set(ids), {'a', 'recent'})
        self.assertTrue(ids['recent']['stale']); self.assertNotIn('stale', ids['a'])
        self.assertEqual(j['status'], 'partial'); self.assertIn('55', j['coverage']['message'])
        self.assertEqual(j['coverage']['carried_over'], 1)

    def test_thin_flagged(self):
        g, j, m = run([car('a')], {'CarsGuide': {'planned': 100, 'ok': 30}})
        self.assertEqual(g, 'thin')

    def test_memory_updated_with_last_seen(self):
        g, j, m = run([car('a')], {'CarsGuide': {'planned': 4, 'ok': 4}})
        self.assertEqual(m['a']['last_seen'], '2026-11-07')

if __name__ == '__main__': unittest.main()
