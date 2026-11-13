import datetime, json, tempfile, unittest
from pathlib import Path
import assemble

NOW = datetime.datetime(2026, 11, 13, 3, 0, tzinfo=datetime.timezone.utc)      # 13:00 Brisbane
F = {'makes': ['Toyota', 'GWM Haval'], 'fuel': ['Hybrid'], 'year_min': 2019, 'km_max': 50000, 'price_min_aud': 20000, 'price_max_aud': 30000}
REG = {'sources': [{'id': 'd1', 'name': 'Dealer One'}, {'id': 'd2', 'name': 'Dealer Two'}]}


def car(i, source='CarsGuide', **k):
    d = dict(id=i, make='Toyota', model='Corolla', variant='Ascent Sport Hybrid', year=2022, km=30000, price_aud=26000, body='hatch', state='QLD',
             suburb='Kedron', postcode=None, source=source, seller_type='dealer', transmission='Auto', fuel='Hybrid', url='https://x/' + i,
             image_url='https://img/' + i, status='active', last_seen='2026-11-13', first_seen_at='2026-11-10', seen_at=NOW.isoformat())
    d.update(k); return d


def build(market, dealer=(), cov=None, focus=F, places=None, checks=None):
    cache = {'urls': {}, 'listings': {d['id']: d for d in market}}
    sweep = {'swept_at': '2026-11-13T12:50:00+10:00', 'coverage': cov or {'d1': {'ok': True}}, 'listings': list(dealer)}
    return assemble.build(focus, sweep, cache, REG, places or {}, NOW, checks)


class T(unittest.TestCase):
    def test_merges_sources_and_enforces_every_limit(self):
        latest, _ = build([car('cg-1'), car('cg-2', km=90000), car('cg-3', price_aud=19000), car('cg-4', fuel='Petrol')],
                          [car('d1-9', 'Dealer One', km=41000, checked='2026-11-13'), car('d1-8', 'Dealer One', make='Ford', checked='2026-11-13')])
        self.assertEqual({d['id'] for d in latest['listings']}, {'cg-1', 'd1-9'})
        self.assertEqual(latest['coverage']['by_source'], {'CarsGuide': 1, 'Dealer One': 1})
        self.assertEqual(latest['coverage']['sweep']['Dealer One'], {'planned': 1, 'ok': 1, 'listings': 2, 'in_scope': 1})
        self.assertEqual(latest['status'], 'ok'); self.assertTrue(latest['updated'].endswith('+10:00'))

    def test_same_car_on_two_sites_is_published_once_from_the_dealer(self):
        latest, _ = build([car('cg-1', km=41234)], [car('d1-9', 'Dealer One', km=41234, checked='2026-11-13')])
        self.assertEqual([d['id'] for d in latest['listings']], ['d1-9'])
        self.assertEqual(latest['listings'][0]['also_on'][0]['source'], 'CarsGuide')

    def test_benchmark_uses_everything_collected_not_only_in_scope(self):
        pool = [car(f'cg-p{i}', price_aud=p, km=30000 + i) for i, p in enumerate((31000, 32000, 33000))]      # over the price limit, still comparables
        far = [car('cg-far', km=200000, price_aud=15000), car('cg-old', year=2015, price_aud=12000)]         # not comparable
        latest, _ = build([car('cg-1', price_aud=24000, km=31000)] + pool + far)
        d = latest['listings'][0]
        self.assertEqual((d['id'], d['median_aud'], d['comparables']), ('cg-1', 32000, 3))
        self.assertEqual(d['below_market_pct'], 25.0); self.assertTrue(d['verify']); self.assertEqual(d['flags'], ['verify'])

    def test_other_fuels_are_not_comparables(self):
        petrol = [car(f'cg-p{i}', price_aud=22000, km=30000 + i, fuel='Petrol') for i in range(3)]
        self.assertIsNone(build([car('cg-1')] + petrol)[0]['listings'][0]['median_aud'])

    def test_fewer_than_three_comparables_means_no_benchmark(self):
        d = build([car('cg-1'), car('cg-2', km=30001)])[0]['listings'][0]
        self.assertIsNone(d['median_aud']); self.assertIsNone(d['below_market_pct']); self.assertFalse(d['verify'])

    def test_recheck_list_is_only_marketplace_rows_not_reseen_today(self):
        old = (NOW - datetime.timedelta(hours=30)).isoformat()
        latest, recheck = build([car('cg-1'), car('cg-2', km=30500, seen_at=old, last_seen='2026-11-12')])
        av = {d['id']: d['availability'] for d in latest['listings']}
        self.assertEqual(av, {'cg-1': 'verified', 'cg-2': 'listed'}); self.assertEqual(recheck, ['https://x/cg-2'])

    def test_dealer_page_not_opened_today_is_listed_not_verified(self):
        latest, _ = build([], [car('d1-9', 'Dealer One', checked='2026-11-12')])
        self.assertEqual(latest['listings'][0]['availability'], 'listed')

    def test_make_names_follow_the_focus(self):
        latest, _ = build([], [car('d1-1', 'Dealer One', make='Haval', model='H6', checked='2026-11-13'), car('d1-2', 'Dealer One', make='TOYOTA', km=31000, checked='2026-11-13')])
        self.assertEqual({(d['make'], d['model']) for d in latest['listings']}, {('GWM Haval', 'Haval H6'), ('Toyota', 'Corolla')})

    def test_distance_drops_only_what_can_be_placed_outside_the_radius(self):
        places = {'postcodes': {'4560': [-26.63, 152.96], '6000': [-31.95, 115.86]}, 'places': {}}
        f = dict(F, near_latlng=[-26.65, 153.09], radius_km=100)
        latest, _ = build([car('cg-1', postcode='4560'), car('cg-2', km=30500, postcode='6000'), car('cg-3', km=30900, suburb='Nowhere')], focus=f, places=places)
        self.assertEqual({d['id'] for d in latest['listings']}, {'cg-1', 'cg-3'})

    def test_page_checks_update_the_cache(self):
        t = Path(tempfile.mkdtemp()); fx = Path(__file__).parent / 'fixtures' / 'carsguide_detail.html'
        log = t / 'pc.log'
        log.write_text('\n'.join(json.dumps(r) for r in [
            {'u': 'https://www.carsguide.com.au/car/15364993/toyota/yaris/qld/x/hatch', 'code': 200, 'f': str(fx)},
            {'u': 'https://www.carsguide.com.au/car/222/toyota/yaris/qld/x/hatch', 'code': 404, 'f': None},
            {'u': 'https://www.carsguide.com.au/car/333/toyota/yaris/qld/x/hatch', 'code': 403, 'f': None}, {'stats': {}}]))
        old = (NOW - datetime.timedelta(hours=30)).isoformat()
        cache = {'urls': {}, 'listings': {'cg-15364993': car('cg-15364993', price_aud=26000, seen_at=old), 'cg-222': car('cg-222'), 'cg-333': car('cg-333', seen_at=old)}}
        st = assemble.apply_checks(log, cache, NOW)
        self.assertEqual(st, {'attempted': 3, 'opened': 1, 'refused': 1, 'removed': 1, 'price_changes': 1})
        self.assertEqual(cache['listings']['cg-15364993']['price_aud'], 25190); self.assertEqual(cache['listings']['cg-15364993']['seen_at'], NOW.isoformat())
        self.assertEqual(cache['listings']['cg-222']['status'], 'removed'); self.assertEqual(cache['listings']['cg-333']['seen_at'], old)


if __name__ == '__main__':
    unittest.main()
