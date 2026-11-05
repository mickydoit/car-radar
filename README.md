# Car Radar

Second-hand car finder for agents. Filter by make, model, year, kilometres and price bracket, with below-market deals ranked first. Filters live in the URL hash, so a filtered view can be shared as a link.

Static site on GitHub Pages. Data is `data/latest.json`, written by a daily Claude routine that follows `ROUTINE_PROMPT.md`. The routine is not enabled yet. Until it runs, the site shows labelled demo data.

Preview locally: `python3 -m http.server 8000`
