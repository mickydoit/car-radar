import json, unittest
from pathlib import Path
import sweep

FX = Path(__file__).parent / 'fixtures'
SRC = {'id': 'cricks-maroochydore', 'name': 'Cricks Sunshine Coast', 'town': 'Maroochydore', 'state': 'QLD', 'postcode': '4558', 'url': 'https://www.cricks.com.au'}
KM = {'id': 'ken-mills-toyota', 'name': 'Ken Mills Toyota', 'town': 'Maroochydore', 'state': 'QLD', 'postcode': '4558', 'url': 'https://www.maroochydore.kenmillstoyota.com.au', 'list_path': '/pre-owned/'}


class DealerStudio(unittest.TestCase):
    def test_sitemap_keeps_only_used(self):
        urls = sweep.parse_dealerstudio_sitemap((FX / 'dealerstudio_sitemap.xml').read_text())
        self.assertEqual(len(urls), 113)
        self.assertTrue(all('/cars/used-' in u for u in urls))

    def test_detail_page_to_listing(self):
        html = (FX / 'dealerstudio_detail.html').read_text(encoding='utf-8', errors='replace')
        d = sweep.extract_carssr(html)
        self.assertIsNotNone(d)
        L = sweep.listing_from_carssr(d, SRC, 'https://www.cricks.com.au/cars/used-grey-2021-subaru-outback-u63615')
        self.assertEqual((L['make'], L['model'], L['year'], L['km'], L['price_aud']), ('Subaru', 'Outback', 2021, 135986, 25990))
        self.assertEqual(L['body'], 'suv')
        self.assertEqual(L['transmission'], 'Auto')
        self.assertEqual((L['suburb'], L['postcode'], L['state']), ('Maroochydore', '4558', 'QLD'))
        self.assertEqual(L['status'], 'active')
        self.assertEqual(L['id'], 'cricks-maroochydore-U63615')
        self.assertTrue(L['image_url'].startswith('https://edge.dealerstudio.com.au/'))
        self.assertEqual(L['variant'], 'AWD 6GEN')

    def test_missing_carssr_is_none(self):
        self.assertIsNone(sweep.extract_carssr('<html>no json here</html>'))
        self.assertIsNone(sweep.listing_from_carssr(None, SRC, 'x'))


class IMotor(unittest.TestCase):
    def test_stock_page(self):
        hits, pages = sweep.parse_imotor_page((FX / 'imotor_stock.html').read_text(encoding='utf-8', errors='replace'))
        self.assertEqual(len(hits), 12)
        self.assertGreaterEqual(pages, 2)
        Ls = [sweep.listing_from_imotor(h, KM) for h in hits]
        Ls = [L for L in Ls if L]
        self.assertTrue(Ls)
        L = Ls[0]
        self.assertEqual((L['make'], L['year']), ('Toyota', 2022))
        self.assertIsInstance(L['km'], int)
        self.assertIsInstance(L['price_aud'], int)
        self.assertEqual(L['seller_type'], 'dealer')
        self.assertTrue(L['url'].startswith('https://www.maroochydore.kenmillstoyota.com.au'))

    def test_non_used_dropped(self):
        self.assertIsNone(sweep.listing_from_imotor({'vehType': 'New', 'price': 1}, KM))


class Normalisers(unittest.TestCase):
    def test_body(self):
        self.assertEqual(sweep.norm_body('Cab Chassis - Dual Cab'), 'ute')
        self.assertEqual(sweep.norm_body('SUV'), 'suv')
        self.assertIsNone(sweep.norm_body(''))

    def test_fuel(self):
        for raw, want in [('Petrol', 'Petrol'), ('Petrol - Unleaded ULP', 'Petrol'), ('Hybrid with Petrol - Premium ULP', 'Hybrid'),
                          ('Hybrid', 'Hybrid'), ('PHEV', 'Plug-in hybrid'), ('PREMIUM UNLEADED/ELECTRIC', 'Hybrid'),
                          ('Electric', 'Electric'), ('Diesel', 'Diesel'), ('', None), (None, None)]:
            self.assertEqual(sweep.norm_fuel(raw), want, raw)

    def test_trans(self):
        self.assertEqual(sweep.norm_trans('6 Sp Manual'), 'Manual')
        self.assertEqual(sweep.norm_trans('Automatic'), 'Auto')


if __name__ == '__main__':
    unittest.main()


class Cars24(unittest.TestCase):
    SRC = {'id': 'cars24', 'name': 'Cars24', 'state': 'QLD', 'town': 'Brisbane'}

    def test_results_page_cards(self):
        cards, total = sweep.parse_cars24((FX / 'cars24_results.html').read_text(encoding='utf-8'))
        self.assertEqual((len(cards), total), (20, 98))
        L = {x['id']: x for x in (sweep.listing_from_cars24(c, self.SRC) for c in cards)}
        d = L['cars24-23775817040704']
        self.assertEqual((d['make'], d['model'], d['year'], d['km'], d['price_aud']), ('Toyota', 'RAV4', 2021, 119721, 29490))
        self.assertEqual((d['fuel'], d['body'], d['transmission'], d['state'], d['status']), ('Hybrid', 'suv', 'Auto', 'QLD', 'active'))
        self.assertEqual(d['url'], 'https://www.cars24.com.au/buy-used-toyota-rav4-car-23775817040704/')
        self.assertTrue(d['image_url'].startswith('https://'))

    def test_not_a_results_page(self):
        self.assertEqual(sweep.parse_cars24('<html>nope</html>'), ([], None))


class FocusFilter(unittest.TestCase):
    F = {'makes': ['Toyota', 'GWM Haval'], 'year_min': 2019, 'year_max': 2027}

    def test_only_focus_makes_and_years_are_opened(self):
        w = lambda slug: sweep.wanted_url('https://d.example/cars/' + slug, self.F)
        self.assertTrue(w('used-white-2021-toyota-rav4-u1')); self.assertTrue(w('used-grey-2023-haval-jolion-u2')); self.assertTrue(w('used-grey-2023-gwm-haval-h6-u3'))
        self.assertFalse(w('used-white-2024-ford-everest-u226303')); self.assertFalse(w('used-white-2017-toyota-rav4-u4'))

    def test_no_focus_keeps_everything(self):
        self.assertTrue(sweep.wanted_url('https://d.example/cars/used-white-2010-ford-falcon-u5', {}))


class SkipForever(unittest.TestCase):
    F = {'fuel': ['Hybrid'], 'km_max': 50000, 'year_min': 2019}

    def test_only_permanent_mismatches_are_skipped(self):
        n = lambda **k: sweep.never_in_focus(dict({'fuel': 'Hybrid', 'km': 30000, 'year': 2022, 'price_aud': 99000}, **k), self.F)
        self.assertFalse(n())                                  # price is over any limit, but prices move
        self.assertTrue(n(fuel='Diesel')); self.assertTrue(n(km=80000)); self.assertTrue(n(year=2015))
        self.assertFalse(n(fuel=None)); self.assertFalse(sweep.never_in_focus({'fuel': 'Diesel'}, {}))
