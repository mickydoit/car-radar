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
