"""
event_seconds_capture.py
────────────────────────
Version: see __version__ below (also: python3 event_seconds_capture.py --version)

Changelog
  01.01  --res 1m: 1-minute bars instead of 1-second (for plans without second
         data); stop after the first "not entitled" (HTTP 403) per run; README
         examples without inline comments (zsh)
  01.00  first version: 1-second bars only around volume-burst events

Downloads 1-SECOND bars from Polygon.io / Massive, but only in short windows
around "volume-burst" events (a 2-min bar with unusually high volume AND a big
price move), for a small set of tickers. This is the data needed to see which
ticker moves first inside those 2 minutes, without downloading years of
1-second history.

Companion to massive_capture.py: it re-uses its API key handling, rate limiter,
SSL fix and ticker names, so it must sit in the same folder.

Input: an events CSV (default Used/2026_macro/analysis/volume_burst_events.csv),
one row per event: leader, time_et, ts_ms, direction, rel_vol, z, ...
Every selected event opens a window [event - before, event + 2 min + after];
overlapping windows on the same day are merged, and each merged window is
downloaded once for every ticker in --tickers.

Output (--out, default Used/2026_seconds):
    <out>/1sec/<TICKER>/<TICKER>_1sec_<YYYYMMDD>.json   all windows of that day
    <out>/events_used.csv                              the events selected
    <out>/capture_log.json                              summary of the run
Each JSON has the usual fields (ticker, timeframe "1s", bars[...]) plus
"windows": [[from_ms, to_ms], ...]. Existing day files with the same windows
are skipped, so an interrupted run can simply be started again.

Usage:
    python3 event_seconds_capture.py --dry-run                         # count windows / API calls only
    python3 event_seconds_capture.py --leaders QQQ --top 50            # quick test: 50 biggest QQQ bursts
    python3 event_seconds_capture.py                                   # defaults below
    python3 event_seconds_capture.py --leaders QQQ SPY C:EURUSD YINN --tickers QQQ SPY SOXL VIXY \
        --from 2026-01-01 --calls-per-min 0

Defaults
    --leaders   QQQ SPY SOXL VIXY C:EURUSD YINN SOXX      (whose bursts define the windows)
    --tickers   QQQ SPY SOXL VIXY C:EURUSD YINN SOXX SLV GLD   (whose 1-sec bars are downloaded)
    --before 120  --after 180   seconds around the 2-minute event bar
    --top 0 (all events; otherwise the N largest rel_vol per leader)

Note: second-level aggregates may need a paid Polygon/Massive plan. If yours
does not include them, the first call reports an error or no data.
"""

__version__ = "01.01"

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone

try:
    import massive_capture as mc
except ImportError:
    sys.exit("event_seconds_capture.py must be in the same folder as massive_capture.py")

DEFAULT_EVENTS = os.path.join("Used", "2026_macro", "analysis", "volume_burst_events.csv")
DEFAULT_LEADERS = ["QQQ", "SPY", "SOXL", "VIXY", "C:EURUSD", "YINN", "SOXX"]
DEFAULT_TICKERS = ["QQQ", "SPY", "SOXL", "VIXY", "C:EURUSD", "YINN", "SOXX", "SLV", "GLD"]


def load_events(path, leaders, date_from, date_to, top):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    lead = set(leaders)
    sel = [r for r in rows if mc.normalize_ticker(r["leader"]) in lead
           and (not date_from or r["time_et"][:10] >= date_from)
           and (not date_to or r["time_et"][:10] <= date_to)]
    if top and top > 0:
        by = {}
        for r in sel:
            by.setdefault(r["leader"], []).append(r)
        sel = []
        for rs in by.values():
            sel += sorted(rs, key=lambda r: -float(r["rel_vol"]))[:top]
    return sorted(sel, key=lambda r: int(r["ts_ms"]))


def build_windows(events, before_s, after_s):
    """Merged windows per ET day: {'YYYY-MM-DD': [[from_ms, to_ms], ...]}"""
    days = {}
    for r in events:
        t = int(r["ts_ms"])
        days.setdefault(r["time_et"][:10], []).append([t - before_s * 1000, t + 120_000 + after_s * 1000])
    out = {}
    for d, ws in days.items():
        ws.sort()
        merged = [ws[0]]
        for a, b in ws[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        out[d] = merged
    return out


def fetch_seconds(ticker, from_ms, to_ms, api_key, limiter, base_url, timespan="second"):
    """1-second bars for [from_ms, to_ms]. Returns (bars, calls). Same retry rules as massive_capture."""
    url = (f"{base_url}/v2/aggs/ticker/{ticker}/range/1/{timespan}/{from_ms}/{to_ms}"
           f"?adjusted=true&sort=asc&limit={mc.LIMIT}")
    bars, calls = [], 0
    while url:
        attempt = 0
        while True:
            limiter.wait()
            calls += 1
            try:
                data = mc.fetch_json(url + f"&apiKey={api_key}")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:300]
                if e.code == 429 or e.code >= 500:
                    attempt += 1
                    if attempt > mc.MAX_RETRIES:
                        raise RuntimeError(f"HTTP {e.code} after {mc.MAX_RETRIES} retries: {body}")
                    wait = 60 if e.code == 429 else 5 * attempt
                    print(f"      [HTTP {e.code}] retry {attempt}/{mc.MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"HTTP {e.code}: {body}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    raise RuntimeError(f"SSL verification failed: {e}  → try: pip3 install truststore   (or --insecure)")
                attempt += 1
                if attempt > mc.MAX_RETRIES:
                    raise RuntimeError(f"network error after {mc.MAX_RETRIES} retries: {e}")
                print(f"      [network] {e} — retry {attempt}/{mc.MAX_RETRIES} in {5 * attempt}s")
                time.sleep(5 * attempt)
        if data.get("status") == "ERROR" or data.get("error"):
            raise RuntimeError(data.get("error") or data.get("message") or "API error")
        results = data.get("results") or []
        bars.extend(results)
        url = data.get("next_url") if results else None
    uniq = {b["t"]: b for b in bars}
    return [uniq[t] for t in sorted(uniq)], calls


def main():
    p = argparse.ArgumentParser(description="Download 1-second bars only around volume-burst events.")
    p.add_argument("--version", action="version", version=f"event_seconds_capture.py v{__version__}")
    p.add_argument("--key", default=os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY"),
                   help="API key (default: env MASSIVE_API_KEY / POLYGON_API_KEY)")
    p.add_argument("--events", default=DEFAULT_EVENTS, help=f"Events CSV (default {DEFAULT_EVENTS})")
    p.add_argument("--leaders", nargs="+", default=DEFAULT_LEADERS, help="Leaders whose bursts define the windows")
    p.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS, help="Tickers to download 1-sec bars for")
    p.add_argument("--from", dest="date_from", help="Only events on/after this date (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", help="Only events on/before this date (YYYY-MM-DD)")
    p.add_argument("--top", type=int, default=0, help="Keep only the N largest-volume events per leader (0 = all)")
    p.add_argument("--before", type=int, default=120, help="Seconds before the event bar (default 120)")
    p.add_argument("--after", type=int, default=180, help="Seconds after the end of the 2-min event bar (default 180)")
    p.add_argument("--out", default=os.path.join("Used", "2026_seconds"), help="Output folder (default Used/2026_seconds)")
    p.add_argument("--calls-per-min", type=int, default=mc.CALLS_PER_MIN, help="5 = free plan, 0 = no limit (paid)")
    p.add_argument("--base-url", default=mc.BASE_URL)
    p.add_argument("--insecure", action="store_true", help="Disable SSL verification (last resort)")
    p.add_argument("--res", choices=["1s", "1m"], default="1s",
                   help="Bar size: 1s (needs second-level data in your plan) or 1m (default 1s)")
    p.add_argument("--dry-run", action="store_true", help="Only count windows and API calls")
    args = p.parse_args()

    leaders = [t for t in (mc.normalize_ticker(x) for x in args.leaders) if t]
    tickers = [t for t in (mc.normalize_ticker(x) for x in args.tickers) if t]
    if not os.path.exists(args.events):
        sys.exit(f"Events file not found: {args.events}")
    events = load_events(args.events, leaders, args.date_from, args.date_to, args.top)
    if not events:
        sys.exit("No events selected.")
    windows = build_windows(events, args.before, args.after)
    n_win = sum(len(w) for w in windows.values())
    secs = sum(b - a for ws in windows.values() for a, b in ws) / 1000
    calls = n_win * len(tickers)
    print(f"event_seconds_capture.py v{__version__}")
    print(f"  events: {len(events)} from {', '.join(leaders)}  ·  {len(windows)} days  ·  {n_win} windows "
          f"(~{secs / 3600:.1f} h of market time)")
    print(f"  tickers: {', '.join(tickers)}  →  about {calls} API calls"
          + (f"  (~{calls / args.calls_per_min:.0f} min at {args.calls_per_min}/min)" if args.calls_per_min > 0 else ""))
    if args.dry_run:
        return
    if not args.key:
        sys.exit("No API key: use --key or set POLYGON_API_KEY")

    timespan, tag = ("second", "1sec") if args.res == "1s" else ("minute", "1min")
    mc.SSL_CTX = mc.build_ssl_context(args.insecure)
    limiter = mc.RateLimiter(args.calls_per_min)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "events_used.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0].keys()))
        w.writeheader()
        w.writerows(events)

    log = {"startedAt": mc.now_iso(), "toolVersion": __version__, "leaders": leaders, "tickers": tickers,
           "before": args.before, "after": args.after, "days": len(windows), "windows": n_win, "results": {}}
    total_calls = 0
    for ti, t in enumerate(tickers, 1):
        folder = os.path.join(args.out, tag, mc.safe_name(t))
        res = log["results"].setdefault(t, {"ok": 0, "skipped": 0, "empty": 0, "errors": []})
        print(f"[{ti}/{len(tickers)}] {t}")
        for d, ws in sorted(windows.items()):
            path = os.path.join(folder, f"{mc.safe_name(t)}_{tag}_{d.replace('-', '')}.json")
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        if json.load(f).get("windows") == ws:
                            res["skipped"] += 1
                            continue
                except Exception:
                    pass
            bars = []
            try:
                for a, b in ws:
                    got, c = fetch_seconds(t, a, b, args.key, limiter, args.base_url, timespan)
                    bars += got
                    total_calls += c
            except RuntimeError as e:
                print(f"    {d}: ERROR {e}")
                if "NOT_AUTHORIZED" in str(e) or "HTTP 403" in str(e):
                    print(f"\n  Your plan does not include {'second' if timespan == 'second' else 'minute'}-level data "
                          f"for this request. Stopping.\n  Try --res 1m (1-minute bars), or check your plan at "
                          f"https://massive.com/pricing")
                    log["results"][t]["errors"].append(f"{d}: {e}")
                    log["finishedAt"] = mc.now_iso()
                    mc.write_json(os.path.join(args.out, "capture_log.json"), log)
                    sys.exit(1)
                res["errors"].append(f"{d}: {e}")
                if len(res["errors"]) >= 3 and res["ok"] == 0:
                    print(f"    3 errors and no data for {t} — skipping this ticker "
                          "(second-level data may not be in your plan)")
                    break
                continue
            if not bars:
                res["empty"] += 1
            mc.write_json(path, {"ticker": t, "timeframe": args.res, "date": d, "windows": ws,
                                 "events": [e for e in events if e["time_et"][:10] == d],
                                 "savedAt": mc.now_iso(), "bars": bars})
            res["ok"] += 1
            print(f"    {d}: {len(ws)} window(s), {len(bars)} bars")
    log["apiCalls"] = total_calls
    log["finishedAt"] = mc.now_iso()
    mc.write_json(os.path.join(args.out, "capture_log.json"), log)
    print(f"done — {total_calls} API calls · log → {os.path.join(args.out, 'capture_log.json')}")


if __name__ == "__main__":
    main()
