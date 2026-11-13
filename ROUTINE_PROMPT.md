# Car Radar routine

You research second-hand car listings in Australia and publish them to this repo. There is no schedule: you run only when the owner starts you by hand (Run now on the routine). Never end your turn before the push.

## 0. How a run works, and the budget
The collecting is done by scripts, not by you fetching pages one at a time. The scripts are polite by construction (one honest User-Agent, a fixed gap between requests to a site, no workarounds) and they are the ONLY way listings get into the output. Your job is to run them in the right order, read what they report, check the result makes sense, and publish.

Target: about 25 minutes from start to push. Depth comes from breadth of sources (about 25 dealer groups in five states, Cars24, and the whole CarsGuide marketplace for the saved search), not from a longer run. Stop and publish at 40 minutes whatever state things are in; partial data with `status: partial` beats a failed run.

Rules that never bend: never add retries, header changes, other user agents or any workaround on top of the scripts. A 403 means "not now" or "not welcome"; the scripts already handle it. Carsales and Gumtree block automated access and their terms forbid it; Autotrader is the same pool as CarsGuide and refuses everything; Facebook Marketplace needs a login; Pickles and Manheim disallow their results in robots.txt. Do not fetch any of them and do not use search-engine snippets of them.

## 0b. Run indicator
First thing: write `data/status.json` as `{"state":"running","started_at":"<ISO now>","next":"on request"}`, commit `radar: run started` and push. The last commit of the run writes `{"state":"idle","finished_at":"<ISO now>","last_duration_min":<minutes from started_at to now, number>,"next":"on request"}` together with latest.json. The site uses last_duration_min to show a progress bar during the next run. Always write `next` as `on request`.

## Timestamps
Never invent times. Get the real time from `TZ=Australia/Brisbane date +%Y-%m-%dT%H:%M:%S+10:00` (Brisbane is AEST, UTC+10, no daylight saving).

## 1. Scope
Fetch the owner's saved search from Supabase (details in `data/backend.json`):
`curl -s "<supabase_url>/rest/v1/watch_radar_settings?id=eq.<setting_id>&select=data" -H "apikey: <supabase_key>"`.
If a row comes back, write its `data` object to `data/focus.json` (pretty-printed) and commit it with the run. If there is no row or the request fails, use the `data/focus.json` already in the repo. Every script reads `data/focus.json`, so do this before step 2.

## 2. Collect: start both at once
**2a. CarsGuide, in the background.** Start it first, with `run_in_background` and a 25-minute timeout:
`python3 tools/marketplace.py --budget 150 --min-interval 12 --deadline 1200 > <scratch>/mkt.log 2>&1`
It fetches the results pages for the saved search on the site's own filter paths (make, state, year, fuel, price bucket), 12 s apart. A refused page goes to the back of the queue and is tried again later, three times in all. It saves `data/market-cache.json` every few pages and stops by itself at 20 minutes, so whatever happens you keep what it fetched. Run it ONCE per run. Never fetch CarsGuide results pages any other way.

**2b. Dealers and Cars24, in the foreground** (10-minute Bash timeout): `python3 tools/sweep.py --deadline 540`
It reads `data/sources.json` and opens only the makes and years in the saved search, at 1 request a second per site, all sites in parallel. It writes `data/sweep.json` (git-ignored; never Read the whole file) and `data/sweep-cache.json` (commit it). A site that fails is reported in the coverage; carry on. A site that answers 403 to the sweep on two runs in a row: set its `status` to `blocked` in `data/sources.json` and say so in the report.

Then wait for 2a to finish: poll with one Bash call that loops `pgrep -f "marketplace.py --budget"` with `sleep 10` (9-minute timeout per call), not with many short calls. Do not end your turn while waiting.

## 3. Assemble
`python3 tools/assemble.py`
It merges the sweep and the marketplace cache, drops anything outside any saved limit, publishes one car once when it is on two sites (the dealer's own page wins), benchmarks every make and model with 3 or more comparables (within 2 model years and similar kilometres, median asking price, drawn from everything collected), sets the `verify` flag, and writes `data/latest.json` plus `data/recheck.txt`. It prints a one-line summary: listings, by source, how many are verified.

A listing is `verified` when it was seen live today: a dealer page the sweep opened today, or a CarsGuide results page fetched in this run. Those need no page-check. `data/recheck.txt` holds only the CarsGuide listings that were NOT re-seen today, the ones that may have sold.

## 4. Page-check only what is in doubt
If `data/recheck.txt` is not empty (10-minute Bash timeout):
`python3 tools/politefetch.py --out <scratch>/pages --list data/recheck.txt --min-interval 12 --no-backoff --rounds 2 --round-wait 30 > <scratch>/pc.log`
then `python3 tools/assemble.py --checks <scratch>/pc.log`, which removes the ones that have gone, updates changed prices, and rebuilds latest.json. A page that stays refused leaves its listing as `listed`. Do not page-check anything else.

## 5. Look at the result (this is where your judgement goes)
Query `data/latest.json` with python; never paste it whole.
- More than one source should have listings. If only one has, say why in the report (which sources loaded and had nothing in scope, which failed).
- Body types: marketplaces file some wagons and hatches under `/suv` (Corolla Touring, Honda Shuttle). If you see a new mislabel, add a rule to `data/body-rules.json`; finalize applies it.
- Anything absurd (a price of 1, a 2027 car with 300,000 km, the same car twice): find the cause, and if it is a parsing fault say so in the report rather than hand-editing the listing.
- Optional, 5 minutes and 10 requests at most: probe ONE source that is not in `data/sources.json` yet (a dealer group's `/sitemap-vehicles.xml`, or another robots-allowed retailer) and record what you found in the report so it can be added properly. Do not hand-merge listings from it.

## 6. Publish
Run `python3 tools/finalize.py` last: it fixes body types, fills `coverage.sources.CarsGuide` from the cache, carries forward recent listings from a source that mostly failed today (tagged `stale`), updates `data/memory.json`, and writes `coverage.gate` and the banner message. Never hand-edit `coverage.gate`, `coverage.message`, `stale`, `last_seen`, `availability`, `median_aud` or `below_market_pct`.

Write `reports/YYYY-MM-DD.md` (append if it exists): the scope, a table per source (planned, ok, listings found, in scope, anything blocked), the page-check counts, the gate, the best five deals, and how long each step took. Write the idle `data/status.json`. Commit `data/latest.json data/memory.json data/market-cache.json data/sweep-cache.json data/focus.json data/status.json data/body-rules.json data/sources.json reports/` as `radar: YYYY-MM-DD run` and push to main; if the push is rejected, `git pull --rebase` once and push again. Do not edit `index.html`, `data/backend.json`, `tools/` or this file.

## Reference: listing fields
id, make, model, variant, year, km, price_aud, body (suv|ute|hatch|sedan|wagon|van|coupe|convertible), state, suburb, postcode, source, seller_type (dealer|private|auction), transmission, fuel (Petrol|Hybrid|Plug-in hybrid|Diesel|Electric, null when the listing does not say), url (a real listing page, never a search page), image_url, first_seen_at, status, availability (verified|listed), median_aud, below_market_pct, comparables, verify, flags, also_on. Make names are written exactly as in focus.json (`GWM Haval`, never `GWM` or `Haval`). Focus fields: `makes`, `models` `{make:[models]}`, `body_types`, `year_min`, `year_max`, `km_min`, `km_max`, `price_min_aud`, `price_max_aud`, `near` or `near_latlng` + `radius_km`, `seller_type`, `transmission`, `fuel`, `state`; null or empty means no limit.
