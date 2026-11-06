#!/usr/bin/env python3
"""Correct body types in data/latest.json using data/body-rules.json.
Marketplaces file some wagons/hatches under their /suv category; the site's Body filter
trusts `body`, so wrong values leak hatches into SUV results. Idempotent."""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fix_one(d, rules):
    for r in rules:
        if d.get('make') != r['make'] or d.get('model') != r['model']:
            continue
        if r.get('body_in') is not None and d.get('body') not in r['body_in']:
            continue
        if r.get('variant_re') and not re.search(r['variant_re'], d.get('variant') or '', re.I):
            continue
        return r['set']
    return d.get('body')


def main(latest=ROOT / 'data/latest.json', rules=ROOT / 'data/body-rules.json'):
    rules = json.loads(Path(rules).read_text())['rules']
    j = json.loads(Path(latest).read_text())
    n = 0
    for d in j['listings']:
        b = fix_one(d, rules)
        if b != d.get('body'):
            d['body'] = b
            n += 1
    Path(latest).write_text(json.dumps(j, ensure_ascii=False, indent=1))
    print(f'fix_body: corrected {n} of {len(j["listings"])}')


if __name__ == '__main__':
    main(*sys.argv[1:])
