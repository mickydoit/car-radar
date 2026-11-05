# Car Radar daily routine

You research second-hand car listings in Australia and publish them to this repo. Run once a day. Never end your turn before the push.

## 1. Scope
Read `data/focus.json`: `makes` (list), `models` (optional `{make: [models]}`), `year_min`, `price_max_aud`, `km_max`. If empty, research the most-listed makes: Toyota, Mazda, Hyundai, Ford, Subaru, Volkswagen, Kia, Mitsubishi.

## 2. Sources
Carsales, Autotrader, Gumtree, Facebook Marketplace (snippets only), Pickles and Manheim auctions, plus any dealer sites with open feeds. Some sites return 403 to the cloud fetcher. Use search snippets for those and mark `availability: "unverified"`. Do not rotate user agents or work around bot checks.

## 3. Rules
- Every published listing needs a real listing URL. Search-result or brand overview pages are not listing URLs. Snippets without one stay out of the output.
- Record: id, make, model, variant, year, km (number), price_aud (number, drive-away if stated, else asking), state, suburb, source, seller_type (dealer|private|auction), transmission, url, first_seen_at (ISO date), status (active|sold|removed).
- JSON numbers must be numbers, dates ISO.
- Re-fetch each listing page before publishing. Sold or 404 means exclude it.
- Benchmark: per make + model, listings within ±2 model years, median asking price. `median_aud` and `below_market_pct = (median - price) / median * 100`. Need at least 3 comparables. Otherwise leave both null.
- Flag `verify` when 25% or more under market, private seller with no rego or no photos, or an odometer that looks implausible for the year.

## 4. Output
Write `data/latest.json` as `{updated, status: ok|partial|idle, listings: [...]}` (no `demo` key). Append a one-line summary to `reports/YYYY-MM-DD.md`. Commit `radar: YYYY-MM-DD daily` and push to main. Do not edit `index.html` or this file.
