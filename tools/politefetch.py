#!/usr/bin/env python3
"""Polite fetcher with retry, back-off and a per-host circuit breaker (stdlib only).

Rules it NEVER breaks: one honest User-Agent, no header tricks, no proxies, no UA/IP rotation,
no attempt to defeat a bot check. It only does what a considerate crawler does when a site says
"not right now": wait longer, try again a few times, cool the host down, then give up and report.

CLI:  python3 tools/politefetch.py --out DIR [--rounds 3 --round-wait 300] URL [URL ...]
      python3 tools/politefetch.py --out DIR --list urls.txt
Prints one JSON line per URL: {"u","code","attempts","f","round"} and a final {"stats":...} line.
"""
import argparse, hashlib, json, random, sys, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlparse

UA = "car-radar research bot (contact: site owner)"
RETRY_CODES = {403, 408, 425, 429, 500, 502, 503, 504, 0}
GONE_CODES = {404, 410}


class Result:
    def __init__(self, url, code, body, attempts, error=None):
        self.url, self.code, self.body, self.attempts, self.error = url, code, body, attempts, error

    @property
    def ok(self):
        return self.code == 200 and self.body is not None


class Fetcher:
    def __init__(self, ua=UA, min_interval=1.5, backoffs=(20, 60, 180), breaker_after=8,
                 cooldown=300, max_cooldowns=3, timeout=40, sleep=time.sleep, clock=time.monotonic,
                 opener=None, jitter=0.25):
        self.ua, self.min_interval, self.backoffs = ua, min_interval, tuple(backoffs)
        self.breaker_after, self.cooldown, self.max_cooldowns = breaker_after, cooldown, max_cooldowns
        self.timeout, self.sleep, self.clock, self.jitter = timeout, sleep, clock, jitter
        self.opener = opener or self._open
        self.last = {}          # host -> time of last request
        self.streak = {}        # host -> consecutive failed attempts
        self.cooldowns = {}     # host -> times cooled down
        self.dead = set()       # hosts given up for this run
        self.stats = {}         # host -> counters

    def _open(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.headers.get("Retry-After")
        except urllib.error.HTTPError as e:
            return e.code, None, e.headers.get("Retry-After") if e.headers else None
        except Exception:
            return 0, None, None

    def _st(self, host):
        return self.stats.setdefault(host, dict(requests=0, ok=0, failed_urls=0, retried_ok=0, cooldowns=0, gave_up=False))

    def _pace(self, host):
        wait = self.last.get(host, -1e9) + self.min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last[host] = self.clock()

    def get(self, url):
        host = urlparse(url).netloc
        st = self._st(host)
        if host in self.dead:
            st["failed_urls"] += 1
            return Result(url, 0, None, 0, "host given up for this run")
        attempts = 0
        for i in range(len(self.backoffs) + 1):
            self._pace(host)
            attempts += 1
            st["requests"] += 1
            code, body, retry_after = self.opener(url)
            if code == 200:
                self.streak[host] = 0
                st["ok"] += 1
                if attempts > 1:
                    st["retried_ok"] += 1
                return Result(url, 200, body, attempts)
            if code in GONE_CODES:
                self.streak[host] = 0
                return Result(url, code, None, attempts, "gone")
            self.streak[host] = self.streak.get(host, 0) + 1
            if self.streak[host] >= self.breaker_after:
                if self.cooldowns.get(host, 0) >= self.max_cooldowns:
                    self.dead.add(host); st["gave_up"] = True; st["failed_urls"] += 1
                    return Result(url, code, None, attempts, "circuit breaker: host given up")
                self.cooldowns[host] = self.cooldowns.get(host, 0) + 1
                st["cooldowns"] += 1
                self.sleep(self.cooldown)
                self.streak[host] = 0
            if code not in RETRY_CODES or i == len(self.backoffs):
                break
            wait = self.backoffs[i]
            if retry_after and str(retry_after).isdigit():
                wait = max(wait, min(int(retry_after), 300))
            self.sleep(wait * (1 + random.uniform(0, self.jitter)))
        st["failed_urls"] += 1
        return Result(url, code, None, attempts, "blocked or failing")


def run(urls, out, rounds=1, round_wait=300, fetcher=None, log=print):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    fx = fetcher or Fetcher()
    pending, done = list(dict.fromkeys(urls)), {}
    for rnd in range(1, rounds + 1):
        failed = []
        for u in pending:
            r = fx.get(u)
            f = None
            if r.ok:
                f = str(out / (hashlib.sha1(u.encode()).hexdigest()[:16] + ".html"))
                Path(f).write_text(r.body)
            rec = {"u": u, "code": r.code, "attempts": r.attempts, "f": f, "round": rnd}
            done[u] = rec
            log(json.dumps(rec))
            if not r.ok and r.code not in GONE_CODES:
                failed.append(u)
        pending = failed
        if not pending or rnd == rounds:
            break
        fx.sleep(round_wait)
    log(json.dumps({"stats": fx.stats, "planned": len(done), "ok": sum(1 for d in done.values() if d["f"])}))
    return done, fx.stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--list")
    ap.add_argument("--rounds", type=int, default=3); ap.add_argument("--round-wait", type=int, default=300)
    ap.add_argument("urls", nargs="*")
    a = ap.parse_args()
    urls = a.urls + (Path(a.list).read_text().split() if a.list else [])
    run(urls, a.out, a.rounds, a.round_wait)
