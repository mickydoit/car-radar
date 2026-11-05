# Car Radar daily routine

You research second-hand car listings in Australia and publish them to this repo. Run once a day. Never end your turn before the push.

## 0. Budget (usage-conscious)
The owner is cost-sensitive. Aim for at most about 60 fetches and 30 searches per run and 20 minutes. Cover up to 8 makes, roughly 8 fetches per make, favouring sources that return readable pages. Stop and publish when the budget is reached. Partial data with `status: partial` beats running long.

## 0b. Run indicator
First thing: write `data/status.json` as `{"state":"running","started_at":"<ISO now>","next":"about 08:00 Brisbane"}`, commit `radar: run started` and push. The last commit of the run writes `{"state":"idle","finished_at":"<ISO now>","next":"about 08:00 Brisbane"}` together with latest.json. Keep the `next` value exactly as you found it if it says `paused`. The site shows a live dot while state is running (and under 75 minutes old).

## Timestamps
Never invent times. Get the real time from `TZ=Australia/Brisbane date +%Y-%m-%dT%H:%M:%S+10:00` (Brisbane is AEST, UTC+10, no daylight saving) and use it for `updated`, `started_at` and `finished_at`.

## 1. Scope
Read `data/focus.json`: `makes` (list), `models` (optional `{make: [models]}`), `year_min`, `price_max_aud`, `km_max`. If empty, research the most-listed makes: Toyota, Mazda, Hyundai, Ford, Subaru, Volkswagen, Kia, Mitsubishi.

## 2. Sources
Carsales, Autotrader, Gumtree, Facebook Marketplace (snippets only), Pickles and Manheim auctions, plus any dealer sites with open feeds. Some sites return 403 to the cloud fetcher. Use search snippets for those and mark `availability: "unverified"`. Do not rotate user agents or work around bot checks.

## 3. Rules
- Every published listing needs a real listing URL. Search-result or brand overview pages are not listing URLs. Snippets without one stay out of the output.
- Record: id, make, model, variant, year, km (number), price_aud (number, drive-away if stated, else asking), state, suburb, postcode (4 digits when known), source, seller_type (dealer|private|auction), transmission, url, first_seen_at (ISO date), status (active|sold|removed).
- JSON numbers must be numbers, dates ISO.
- Re-fetch each listing page before publishing. Sold or 404 means exclude it.
- Benchmark: per make + model, comparables within ±2 model years AND similar kilometres (within 30,000 km or 35%, whichever is larger). A 300,000 km car is not comparable to a 60,000 km one. Median asking price of the comparables. `median_aud` and `below_market_pct = (median - price) / median * 100`. Need at least 3 comparables. Otherwise leave both null.
- Flag `verify` when 25% or more under market, private seller with no rego or no photos, or an odometer that looks implausible for the year.

## 4. Output
Write `data/latest.json` as `{updated, status: ok|partial|idle, listings: [...]}` (no `demo` key). Append a one-line summary to `reports/YYYY-MM-DD.md`. Commit `radar: YYYY-MM-DD daily` and push to main. Do not edit `index.html` or this file.
