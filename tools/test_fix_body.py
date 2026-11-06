import json, unittest
from pathlib import Path
import fix_body

RULES = json.loads((Path(__file__).resolve().parent.parent / 'data/body-rules.json').read_text())['rules']


def f(make, model, body, variant=''):
    return fix_body.fix_one({'make': make, 'model': model, 'body': body, 'variant': variant}, RULES)


class T(unittest.TestCase):
    def test_shuttle_is_wagon(self):
        self.assertEqual(f('Honda', 'Shuttle', 'suv'), 'wagon')
    def test_corolla_touring_wagon_else_hatch(self):
        self.assertEqual(f('Toyota', 'Corolla', 'suv', 'TOURING (HYBRID)'), 'wagon')
        self.assertEqual(f('Toyota', 'Corolla', 'suv', 'WXB'), 'hatch')
    def test_real_suvs_untouched(self):
        for m, mo in [('Toyota', 'Yaris Cross'), ('Toyota', 'C-HR'), ('Mazda', 'MX-30'), ('Hyundai', 'Kona'), ('Toyota', 'Corolla Cross')]:
            self.assertEqual(f(m, mo, 'suv'), 'suv')
    def test_non_suv_corolla_untouched(self):
        self.assertEqual(f('Toyota', 'Corolla', 'sedan'), 'sedan')

if __name__ == '__main__':
    unittest.main()
