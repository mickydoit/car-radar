# Car Radar

Second-hand car finder for agents. Filter by make, model, year, kilometres and price bracket, with below-market deals ranked first. Filters live in the URL hash, so a filtered view can be shared as a link.

Static site on GitHub Pages. Data is `data/latest.json`, written by a daily Claude routine (08:00 Brisbane) that follows `ROUTINE_PROMPT.md`. The search scope is saved from the site itself ("Save these filters as the daily search", password-protected, stored in Supabase and mirrored to `data/focus.json`).

Local dealer stock comes from `tools/sweep.py` (registry in `data/sources.json`, tests `cd tools && python3 -m unittest test_sweep`). Carsales and Gumtree block automated access and are not used.

Preview locally: `python3 -m http.server 8000`
