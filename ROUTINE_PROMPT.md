# Car Radar daily routine

You research second-hand car listings in Australia and publish them to this repo. Run once a day. Never end your turn before the push.

## 0. Depth and budget
The owner wants a DEEP pass, not a quick one. Earlier runs finished in about 3 minutes, which was too shallow. Budget per run: the one marketplace top-up call, up to 40 listing page-checks, the dealer sweep, and about 20 minutes of Claude time. Depth now comes from the cache accumulating all day, not from fetching more in one go; a run that ends with the coverage gate `complete` is a good run whatever its length. Spread effort across every make AND every model in focus, and every body type. Still obey the polite-crawling rules (1 s between requests to one host, honest UA, no workarounds). Stop at the budget and publish; partial data with `status: partial` beats a failed run.

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
### 2b. Marketplace listings come from the CACHE, not from a burst of fetches
CarsGuide (same inventory as Autotrader; same listing ids) answers bursts with Peakhour JavaScript-challenge pages (HTTP 403). We never try to pass or dodge that challenge. Instead a separate small cloud routine ("car-radar market top-up", Sonnet, 12:00 / 20:00 / 02:00 Brisbane) runs `tools/marketplace.py` with a small budget and ≥12 s between requests, and keeps `data/market-cache.json` (listings + per-URL last_ok). (GitHub Actions was tried and is challenged on 100% of requests; its workflow is manual-only now.) Your job:
1. `python3 tools/marketplace.py --stats` → planned/ok page counts for the last 24 h. Then `python3 tools/marketplace.py --budget 40 --min-interval 15` once (Bash timeout 15 min) to top up the stalest pages yourself; do NOT fetch CarsGuide/Autotrader result pages any other way, and never more than that one call.
2. `python3 tools/marketplace.py --export` → every active, in-focus CarsGuide listing seen in the last 3 days, already in the latest.json schema (fuel, body, image_url, first_seen_at included). Take them all.
3. Page-checks (sold/price/fuel) of individual listing pages go through `tools/politefetch.py` (≥15 s apart, max 40 per run) and are parsed with `marketplace.parse_detail(html)`; `status != "Live"` or `deleted_at` set means exclude. A persistent 403 means leave it as `listed`.
4. Carsales and Gumtree BLOCK automated access and their terms forbid it: do not fetch them, do not use search-engine snippets of them. Manheim 403s even robots.txt: ruled out. Facebook Marketplace needs a login: skip. Autotrader is the same pool as CarsGuide: skip it.
5. `coverage.sources.CarsGuide` is filled in by `tools/finalize.py` from the cache — never hand-write it. Add the sweep's per-dealer planned/ok and any other source you probe.
### 2c. Depth steps (do all of them)
1. Dealers (sweep) + marketplace cache + any extra source are all merged and deduped by listing id / stock number before benchmarking.
2. Fuel and details: for any in-scope listing whose fuel, transmission or suburb is missing, open its listing page (through politefetch, within the §2b.3 limit) and fill them in (fuel as Petrol|Hybrid|Plug-in hybrid|Diesel|Electric, never guessed).
3. Page-check: re-fetch EVERY listing that is 3% or more below market, plus every listing newer than the previous run's `updated`, and at least 20 others. Sold or 404 means exclude; a persistent 403 means leave it as `listed`.
4. Other sources: probe Pickles (`https://www.pickles.com.au`, public search pages only, respect its robots.txt which disallows `/cars/vehicle/*/itemid-*`, auction detail and `?filter=` paths) and any other robots-allowed source relevant to the focus (e.g. the makes' own approved-used sites). Record each probe in `coverage.sources` and the report, success or failure. Never leave a source "not tried".
5. Benchmark every make/model with 3+ comparables under the §3 rule; with fewer, leave the benchmark null rather than guess.
6. In `reports/YYYY-MM-DD.md` list per source: pages planned, ok, retried-ok, listings found, in-scope count, anything blocked, and the gate result.

## 3. Rules
- Every published listing needs a real listing URL. Search-result or brand overview pages are not listing URLs. Snippets without one stay out of the output.
- Record: id, make, model, variant, year, km (number), price_aud (number, drive-away if stated, else asking), body (suv|ute|hatch|sedan|wagon|van|coupe|convertible), state, suburb, postcode (4 digits when known), source, seller_type (dealer|private|auction), transmission, fuel (Petrol|Hybrid|Plug-in hybrid|Diesel|Electric; null if the listing does not say, never guess), url, first_seen_at (ISO date), status (active|sold|removed).
- BODY TYPE: set `body` from the car itself, never from the marketplace URL or category (CarsGuide/Autotrader file some wagons and hatches such as the Corolla Touring and Honda Shuttle under `/suv`). If unsure, leave it null. After writing latest.json the finalize step below corrects known mislabels from `data/body-rules.json`; add a rule there whenever you spot a new one.
- JSON numbers must be numbers, dates ISO.
- Page-check as set out in §2c. Listings you did not open are published from the results pages with `availability: "listed"`; page-checked ones get `availability: "verified"`.
- Use the make names exactly as they appear in focus.json (e.g. `GWM Haval`, never `GWM` or `Haval`), so the site groups them correctly.
- Every published listing must satisfy every saved limit (year, km, price, body type, distance). Check each one before writing it; drop anything outside.
- Benchmark: per make + model, comparables within ±2 model years AND similar kilometres (within 30,000 km or 35%, whichever is larger). A 300,000 km car is not comparable to a 60,000 km one. Median asking price of the comparables. `median_aud` and `below_market_pct = (median - price) / median * 100`. Need at least 3 comparables. Otherwise leave both null.
- Flag `verify` when 25% or more under market, private seller with no rego or no photos, or an odometer that looks implausible for the year.

## 4. Output
Write `data/latest.json` as `{updated, status: ok|partial|idle, coverage: {...}, listings: [...]}` (no `demo` key). THEN ALWAYS run `python3 tools/finalize.py` as the last step before committing: it fixes body types, carries forward recent listings from any source that mostly failed today (tagged `stale`, so the site never goes one-sided or shrinks on a bad day), stamps `last_seen`, updates `data/memory.json`, and writes `coverage.gate` + the banner message the site shows. Commit `data/memory.json` with the run. Never hand-edit `coverage.gate`, `coverage.message`, `stale` or `last_seen`. Append a one-line summary to `reports/YYYY-MM-DD.md`. Commit `radar: YYYY-MM-DD daily` and push to main. Do not edit `index.html`, `data/backend.json` or this file.
