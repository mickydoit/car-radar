import datetime, json, unittest
from pathlib import Path
import marketplace as mp

FX = Path(__file__).resolve().parent / 'fixtures'
RESULTS = (FX / 'carsguide_results.html').read_text(encoding='utf-8')
DETAIL = (FX / 'carsguide_detail.html').read_text(encoding='utf-8')
NOW = datetime.datetime(2026, 11, 6, 4, 30, tzinfo=datetime.timezone.utc)


class Parse(unittest.TestCase):
    def test_results_page(self):
        meta, rows = mp.parse_results(RESULTS)
        self.assertEqual((meta['total'], meta['current_page'], meta['last_page']), (58, 1, 3))
        self.assertEqual(len(rows), 26)
        L = mp.to_listing(rows[0], '2026-11-06')
        self.assertEqual(L['id'], 'cg-15364993'); self.assertEqual(L['make'], 'Toyota'); self.assertEqual(L['model'], 'Yaris')
        self.assertEqual(L['year'], 2025); self.assertEqual(L['km'], 44096); self.assertEqual(L['price_aud'], 25190)
        self.assertEqual((L['body'], L['fuel'], L['transmission']), ('hatch', 'Hybrid', 'Auto'))
        self.assertEqual((L['state'], L['suburb'], L['seller_type'], L['status']), ('QLD', 'Yeerongpilly', 'dealer', 'active'))
        self.assertTrue(L['url'].startswith('https://www.carsguide.com.au/car/15364993/'))
        self.assertTrue(L['image_url'].endswith('_1.jpg'))

    def test_detail_page(self):
        d = mp.parse_detail(DETAIL)
        self.assertEqual(d['status'], 'Live'); self.assertIsNone(d['deleted_at']); self.assertEqual(d['km'], 44096)
        self.assertEqual(d['price_aud'], 25190); self.assertEqual(d['fuel'], 'Hybrid')

    def test_make_name_follows_focus_names(self):
        row = dict(mp.parse_results(RESULTS)[1][0]); row['make'] = 'GWM'
        self.assertEqual(mp.to_listing(row, '2026-11-06')['make'], 'GWM Haval')
        cache = {'urls': {}, 'listings': {'x': {'make': 'GWM', 'status': 'active', 'last_seen': '2026-11-06', 'fuel': 'Hybrid'}}}
        self.assertEqual(len(mp.export({'makes': ['GWM Haval']}, cache, now=NOW)), 1)

    def test_non_results_page(self):
        self.assertEqual(mp.parse_results('<html>challenge</html>'), (None, []))
        self.assertIsNone(mp.parse_detail(RESULTS))


class Plan(unittest.TestCase):
    F = {'makes': ['Toyota', 'GWM Haval'], 'fuel': ['Hybrid'], 'price_max_aud': 30000}

    def test_base_urls_broad_first(self):
        self.assertEqual(mp.base_urls(self.F), [mp.BASE + 'toyota/hybrid/under-$30000', mp.BASE + 'gwm/hybrid/under-$30000'])

    def test_models_and_state(self):
        f = dict(self.F, models={'Toyota': ['Yaris Cross']}, state='QLD')
        self.assertIn(mp.BASE + 'toyota/yaris-cross/qld/hybrid/under-$30000', mp.base_urls(f))

    def test_pages_follow_known_last_page_and_split_when_huge(self):
        u = mp.BASE + 'toyota/hybrid/under-$30000'
        cache = {'urls': {u: {'last_page': 3}}, 'listings': {}}
        self.assertEqual(mp.plan(self.F, cache)[:3], [u, u + '?page=2', u + '?page=3'])
        cache['urls'][u]['last_page'] = 40
        p = mp.plan(self.F, cache)
        self.assertIn(mp.BASE + 'toyota/qld/hybrid/under-$30000', p); self.assertNotIn(u, p)


    def test_tight_paths_use_the_sites_own_year_and_price_facets(self):
        f = dict(self.F, price_min_aud=20000, year_min=2019)
        u = mp.BASE + 'toyota/over-2018/hybrid/$20000-$30000'
        self.assertEqual(mp.base_urls(f)[0], u)
        self.assertEqual(mp.base_urls(dict(f, price_min_aud=18000))[0], mp.BASE + 'toyota/over-2018/hybrid/under-$30000')   # not a site bucket
        cache = {'urls': {u: {'last_page': 22}}, 'listings': {}}
        self.assertIn(mp.BASE + 'toyota/qld/over-2018/hybrid/$20000-$30000', mp.plan(f, cache))


class Crawl(unittest.TestCase):
    class FakeFetcher:
        def __init__(self, codes): self.codes, self.calls, self.stats = codes, [], {}
        def get(self, u):
            self.calls.append(u); code = self.codes.get(u, 200)
            return politefetch_result(u, code, RESULTS if code == 200 else None)

    def test_budget_and_cache_merge(self):
        f = {'makes': ['Toyota'], 'fuel': ['Hybrid'], 'price_max_aud': 30000}
        cache = mp.load_cache('/nonexistent')
        fx = self.FakeFetcher({})
        st = mp.crawl(f, cache, budget=5, fetcher=fx, now=NOW, log=lambda s: None)
        u = mp.BASE + 'toyota/hybrid/under-$30000'
        self.assertEqual(fx.calls, [u, u + '?page=2', u + '?page=3'])     # learns last_page=3 from page 1, stops at 3
        self.assertEqual(st['ok'], 3); self.assertEqual(len(cache['listings']), 26)
        self.assertEqual(cache['listings']['cg-15364993']['first_seen_at'], '2026-11-06')
        cov = mp.coverage(f, cache, now=NOW); self.assertEqual((cov['planned'], cov['ok']), (3, 3))
        # second run within fresh window fetches nothing
        fx2 = self.FakeFetcher({}); mp.crawl(f, cache, 5, fx2, now=NOW + datetime.timedelta(hours=1), log=lambda s: None)
        self.assertEqual(fx2.calls, [])
        # a day later: stalest first, a 403 leaves last_ok untouched
        fx3 = self.FakeFetcher({u + '?page=2': 403}); mp.crawl(f, cache, 5, fx3, now=NOW + datetime.timedelta(hours=30), log=lambda s: None)
        self.assertEqual(cache['urls'][u + '?page=2']['last_code'], 403); self.assertEqual(cache['urls'][u + '?page=2']['last_ok'], NOW.isoformat())

    def test_export_filters_focus_and_age(self):
        f = {'makes': ['Toyota'], 'fuel': ['Hybrid'], 'price_max_aud': 30000, 'km_max': 50000}
        cache = mp.load_cache('/nonexistent'); mp.crawl(f, cache, 5, self.FakeFetcher({}), now=NOW, log=lambda s: None)
        out = mp.export(f, cache, now=NOW)
        self.assertTrue(out); self.assertTrue(all(d['price_aud'] <= 30000 and d['km'] <= 50000 for d in out))
        self.assertEqual(mp.export(f, cache, now=NOW + datetime.timedelta(days=5)), [])


class Queue(unittest.TestCase):
    F = {'makes': ['Toyota'], 'fuel': ['Hybrid'], 'price_max_aud': 30000}
    U = mp.BASE + 'toyota/hybrid/under-$30000'

    class Flaky:
        """Refuses each URL the first `n` times it is asked."""
        def __init__(self, n, dead_after=None): self.n, self.seen, self.calls, self.dead_after = n, {}, [], dead_after
        def get(self, u):
            self.calls.append(u); self.seen[u] = self.seen.get(u, 0) + 1
            if self.dead_after is not None and len(self.calls) > self.dead_after:
                return politefetch_result(u, 0, None, attempts=0)
            return politefetch_result(u, 403, None) if self.seen[u] <= self.n else politefetch_result(u, 200, RESULTS)

    def test_refused_page_is_retried_after_the_others(self):
        cache = mp.load_cache('/nonexistent'); cache['urls'][self.U] = {'last_page': 3}
        fx = self.Flaky(1)
        st = mp.crawl(self.F, cache, 20, fx, now=NOW, log=lambda s: None)
        self.assertEqual(fx.calls[:3], [self.U, self.U + '?page=2', self.U + '?page=3'])      # every page once before any retry
        self.assertEqual((st['ok'], st['failed'], st['retried_ok'], st['fetched']), (3, 0, 3, 6))

    def test_gives_up_on_a_page_after_three_tries(self):
        cache = mp.load_cache('/nonexistent')
        fx = self.Flaky(99); st = mp.crawl(self.F, cache, 20, fx, now=NOW, log=lambda s: None)
        self.assertEqual((len(fx.calls), st['ok'], st['failed']), (3, 0, 1))

    def test_stops_when_host_gives_up_and_on_deadline_and_saves_as_it_goes(self):
        cache = mp.load_cache('/nonexistent'); cache['urls'][self.U] = {'last_page': 3}
        st = mp.crawl(self.F, cache, 20, self.Flaky(0, dead_after=1), now=NOW, log=lambda s: None)
        self.assertEqual((st['stopped'], st['ok']), ('host gave up', 1))
        cache = mp.load_cache('/nonexistent'); cache['urls'][self.U] = {'last_page': 3}; saves = []
        ticks = iter([0, 0, 100, 100, 100])
        st = mp.crawl(self.F, cache, 20, self.Flaky(0), now=NOW, log=lambda s: None, deadline=50, clock=lambda: next(ticks), save=lambda: saves.append(1), save_every=1)
        self.assertEqual((st['stopped'], st['ok'], len(saves)), ('deadline', 2, 2))

    def test_export_marks_what_was_not_reseen_and_mark_removed(self):
        cache = mp.load_cache('/nonexistent'); mp.crawl(self.F, cache, 5, self.Flaky(0), now=NOW, log=lambda s: None)
        out = mp.export(self.F, cache, now=NOW + datetime.timedelta(hours=1))
        self.assertTrue(all(d['availability'] == 'verified' and 'recheck' not in d and 'seen_at' not in d for d in out))
        later = mp.export(self.F, cache, now=NOW + datetime.timedelta(hours=30))
        self.assertTrue(later and all(d['availability'] == 'listed' and d['recheck'] for d in later))
        self.assertEqual(mp.mark_removed(cache, [out[0]['id'], 'cg-nope']), 1)
        self.assertEqual(len(mp.export(self.F, cache, now=NOW)), len(out) - 1)


def politefetch_result(u, code, body, attempts=1):
    import politefetch
    return politefetch.Result(u, code, body, attempts)


def _unused(u, code, body):
    import politefetch
    return politefetch.Result(u, code, body, 1)


if __name__ == '__main__':
    unittest.main()
