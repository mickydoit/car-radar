# Car Radar daily routine

You research second-hand car listings in Australia and publish them to this repo. Run once a day. Never end your turn before the push.

## 0. Depth and budget
The owner wants a DEEP pass, not a quick one. Earlier runs finished in about 3 minutes, which was too shallow. Budget per run: up to about 300 fetches, 80 searches and 45 minutes. A good run takes 15–40 minutes; if you are done in under 10 minutes you skipped work, so go back and do the depth steps in §2c. Spread effort across every make AND every model in focus, and every body type. Still obey the polite-crawling rules (1 s between requests to one host, honest UA, no workarounds). Stop at the budget and publish; partial data with `status: partial` beats a failed run.

## 0b. Run indicator
First thing: write `data/status.json` as `{"state":"running","started_at":"<ISO now>","next":"about 08:00 Brisbane"}`, commit `radar: run started` and push. The last commit of the run writes `{"state":"idle","finished_at":"<ISO now>","last_duration_min":<minutes from started_at to now, number>,"next":"about 08:00 Brisbane"}` together with latest.json. The site uses last_duration_min to show a progress bar during the next run. Keep the `next` value exactly as you found it if it says `paused`. The site shows a live dot while state is running (and under 75 minutes old).

## Timestamps
Never invent times. Get the real time from `TZ=Australia/Brisbane date +%Y-%m-%dT%H:%M:%S+10:00` (Brisbane is AEST, UTC+10, no daylight saving) and use it for `updated`, `started_at` and `finished_at`.

## Every run is a full pass
Each run researches the whole focus from scratch — fresh results pages for every make/model/body type in scope — whether or not a run already happened today. Re-use `data/seen.json` only to keep `first_seen_at` and to skip re-benchmarking; never skip the search itself. Publish the full in-scope list every time (replace, don't append).

## 1. Scope
First fetch the owner's saved search from Supabase (details in `data/backend.json`):
`curl -s "<supabase_url>/rest/v1/watch_radar_settings?id=eq.<setting_id>&select=data" -H "apikey: <supabase_key>"`.
If a row comes back, write its `data` object to `data/focus.json` (pretty-printed) and use it; commit it with the run. If there is no row or the request fails, use the `data/focus.json` already in the repo.
Focus fields: `makes`, `models` `{make:[models]}`, `body_types`, `year_min`, `year_max`, `km_min`, `km_max`, `price_min_aud`, `price_max_aud`, `near` (suburb or postcode) or `near_latlng` [lat,lng], `radius_km`, `seller_type`, `transmission`, `fuel` (list of Petrol|Hybrid|Plug-in hybrid|Diesel|Electric), `state`. Null or empty means no limit.
Publish EVERY listing that satisfies the limits and whose page you could verify, not only the below-market ones: the site is a browsing tool and ranks deals first itself. Each gets `median_aud` / `below_market_pct` when there are enough comparables (null otherwise). Research ONLY the focus makes/body types; aim the search at the `near` area (its state first) when set.

Read `data/focus.json`: `makes` (list), `models` (optional `{make: [models]}`), `body_types` (list of suv|ute|hatch|sedan|wagon|van|coupe|convertible — empty = all), `year_min`, `price_max_aud`, `km_max`, `near` (suburb or postcode) + `radius_km`. Only research what is in focus. When `near` is set, search listings located in that state first and prefer those within the radius. If makes is empty, use: Toyota, Mazda, Hyundai, Ford, Subaru, Volkswagen, Kia, Mitsubishi.

## 2. Sources
### 2a. Local dealers: run the sweep FIRST (no Claude usage, ~3 min)
`python3 tools/sweep.py --deadline 480` (give the Bash call a 10-minute timeout). It reads `data/sources.json`, fetches every active dealer's used stock with one honest User-Agent at 1 request/second per site, and writes `data/sweep.json` (git-ignored; never Read the whole file — query it with python) plus `data/sweep-cache.json` (commit that; it carries first_seen_at). Take every sweep listing with `status: active` that satisfies the focus limits straight into latest.json — they already have make/model/variant/year/km/price/body/suburb/postcode/url/image_url, seller_type dealer, `availability: "listed"`. If a source fails, note it in `coverage` and carry on. Never add retries or header tricks to the sweep; a 403 means that dealer is off limits (set its status to blocked in sources.json and say so in the report).
### 2b. Marketplaces, with WebFetch
Autotrader (search pages per make/body/state work) and CarsGuide (clean paths only, e.g. `https://www.carsguide.com.au/buy-a-car/used/toyota/rav4/qld` — never add query strings, they are robots-disallowed; 1 s between requests). Carsales and Gumtree BLOCK automated access and their terms forbid it: do not fetch them at all, do not use search-engine snippets of them, do not rotate user agents or work around bot checks. Facebook Marketplace needs a login: skip. Pickles/Manheim auctions: optional if budget remains.
Write `coverage` into latest.json: `{"sources_reached": n, "sources_total": m, "sweep_fetches": k, "failed": [ids]}` from the sweep's coverage plus your own fetches.

### 2c. Depth steps (do all of them)
1. Marketplaces: for EVERY focus make (and each focus model when `models` is set) fetch the Autotrader and CarsGuide results for each state in scope (the `near` state first, then neighbouring states if the radius reaches them), and follow pagination until a page returns no new in-scope listings or 5 pages per make. Do not stop at page one.
2. Make × body type: where body_types is set, also fetch the body-type page for each make, so models the make page buries still show up.
3. Fuel and details: for any in-scope listing whose fuel, transmission or suburb is missing, open its listing page and fill them in (fuel as Petrol|Hybrid|Plug-in hybrid|Diesel|Electric, never guessed). The site has a fuel filter, so fuel coverage matters.
4. Page-check: re-fetch EVERY listing that is 3% or more below market, plus every listing newer than the previous run's `updated`, and a sample of at least 20 others. Sold or 404 means exclude.
5. Auctions and extra sources: also try Pickles and Manheim (public search pages only, no login) and any other robots-allowed source you can find for the focus area (write what you tried and what worked into `coverage`).
6. Benchmark every make/model with 3+ comparables under the §3 rule; with fewer, leave the benchmark null rather than guess.
7. In `reports/YYYY-MM-DD.md` list per source: pages fetched, listings found, in-scope count, and anything blocked.

## 3. Rules
- Every published listing needs a real listing URL. Search-result or brand overview pages are not listing URLs. Snippets without one stay out of the output.
- Record: id, make, model, variant, year, km (number), price_aud (number, drive-away if stated, else asking), body (suv|ute|hatch|sedan|wagon|van|coupe|convertible), state, suburb, postcode (4 digits when known), source, seller_type (dealer|private|auction), transmission, fuel (Petrol|Hybrid|Plug-in hybrid|Diesel|Electric; null if the listing does not say, never guess), url, first_seen_at (ISO date), status (active|sold|removed).
- JSON numbers must be numbers, dates ISO.
- Page-check as set out in §2c. Listings you did not open are published from the results pages with `availability: "listed"`; page-checked ones get `availability: "verified"`.
- Use the make names exactly as they appear in focus.json (e.g. `GWM Haval`, never `GWM` or `Haval`), so the site groups them correctly.
- Every published listing must satisfy every saved limit (year, km, price, body type, distance). Check each one before writing it; drop anything outside.
- Benchmark: per make + model, comparables within ±2 model years AND similar kilometres (within 30,000 km or 35%, whichever is larger). A 300,000 km car is not comparable to a 60,000 km one. Median asking price of the comparables. `median_aud` and `below_market_pct = (median - price) / median * 100`. Need at least 3 comparables. Otherwise leave both null.
- Flag `verify` when 25% or more under market, private seller with no rego or no photos, or an odometer that looks implausible for the year.

## 4. Output
Write `data/latest.json` as `{updated, status: ok|partial|idle, listings: [...]}` (no `demo` key). Append a one-line summary to `reports/YYYY-MM-DD.md`. Commit `radar: YYYY-MM-DD daily` and push to main. Do not edit `index.html`, `data/backend.json` or this file.
