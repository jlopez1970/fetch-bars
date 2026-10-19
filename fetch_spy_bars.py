"""
fetch_spy_bars.py
─────────────────
Fetches all SPY 2-min bars from 2026-01-01 to today from Polygon.io.
Respects the free-tier limit of 5 calls/minute:
  - fires up to 5 requests, then sleeps until the top of the next minute.

Output: SPY_2min_<from>__<to>.json   (same format as the charting app expects)

Usage:
    python fetch_spy_bars.py --key YOUR_API_KEY
    python fetch_spy_bars.py --key YOUR_API_KEY --from 2026-01-01 --to 2026-06-11
    python fetch_spy_bars.py --key YOUR_API_KEY --ticker QQQ
"""

import argparse
import json
import math
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timezone

# ── config ────────────────────────────────────────────────────────────────────
TICKER        = "SPY"
DATE_FROM     = "2026-01-01"
DATE_TO       = date.today().isoformat()   # today
TIMEFRAME     = 2                          # minutes
LIMIT         = 50_000                     # max bars per call (Polygon max)
CALLS_PER_MIN = 5                          # free-tier hard limit
BASE_URL      = "https://api.polygon.io/v2/aggs/ticker"

# ── helpers ───────────────────────────────────────────────────────────────────
def fmt_ts(ms: int) -> str:
    """Unix-ms → 'YYYYMMDD_HHMM'"""
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return d.strftime("%Y%m%d_%H%M")


def build_url(ticker, tf, date_from, date_to, api_key, cursor=None):
    if cursor:
        # Polygon next_url already contains all params; just append key
        return cursor + f"&apiKey={api_key}"
    return (
        f"{BASE_URL}/{ticker}/range/{tf}/minute/{date_from}/{date_to}"
        f"?adjusted=true&sort=asc&limit={LIMIT}&apiKey={api_key}"
    )


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_spy_bars/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ── rate-limit bucket ─────────────────────────────────────────────────────────
class RateLimiter:
    """
    Allows up to `cap` calls per 60-second window.
    After the cap-th call, sleeps until the top of the next minute.
    """
    def __init__(self, cap: int = CALLS_PER_MIN):
        self.cap       = cap
        self.window_start = time.monotonic()
        self.calls_in_window = 0

    def wait(self):
        self.calls_in_window += 1
        if self.calls_in_window >= self.cap:
            elapsed = time.monotonic() - self.window_start
            sleep_for = 60.0 - elapsed
            if sleep_for > 0:
                print(f"  [rate-limit] {self.cap} calls used — sleeping {sleep_for:.1f}s ...")
                time.sleep(sleep_for)
            # reset window
            self.window_start    = time.monotonic()
            self.calls_in_window = 0


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch Polygon 2-min bars with rate-limiting.")
    parser.add_argument("--key",    required=True,         help="Polygon.io API key")
    parser.add_argument("--ticker", default=TICKER,        help="Ticker symbol (default: SPY)")
    parser.add_argument("--from",   default=DATE_FROM,     dest="date_from", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",     default=DATE_TO,       dest="date_to",   help="End date   YYYY-MM-DD")
    parser.add_argument("--tf",     default=TIMEFRAME, type=int,             help="Timeframe in minutes (default: 2)")
    args = parser.parse_args()

    ticker    = args.ticker.upper()
    api_key   = args.key
    date_from = args.date_from
    date_to   = args.date_to
    tf        = args.tf

    print(f"Fetching {ticker} {tf}-min bars  {date_from} → {date_to}")
    print(f"Rate limit: {CALLS_PER_MIN} calls/min (free tier)\n")

    all_bars   = []
    call_count = 0
    cursor     = None          # Polygon pagination cursor (next_url)
    limiter    = RateLimiter(CALLS_PER_MIN)

    while True:
        url = build_url(ticker, tf, date_from, date_to, api_key, cursor)

        # honour rate limit BEFORE the call
        limiter.wait()

        call_count += 1
        print(f"  call #{call_count}  bars so far: {len(all_bars)}", end="  ")

        try:
            data = fetch_json(url)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"\n[HTTP {e.code}] {body}")
            if e.code == 429:
                print("  429 Too Many Requests — sleeping 60s ...")
                time.sleep(60)
                continue          # retry same page
            raise

        status = data.get("status", "")
        if status == "ERROR" or data.get("error"):
            print(f"\n[API error] {data.get('error') or data.get('message')}")
            break

        results = data.get("results") or []
        all_bars.extend(results)
        print(f"→ got {len(results)} bars  (total {len(all_bars)})")

        # check for next page
        next_url = data.get("next_url")
        if next_url and results:
            cursor = next_url
        else:
            break   # done

    if not all_bars:
        print("\nNo bars returned. Check ticker, dates, and API key.")
        return

    # ── build output payload (same schema as charting app) ───────────────────
    first_ts = all_bars[0]["t"]
    last_ts  = all_bars[-1]["t"]
    ts_from  = fmt_ts(first_ts)
    ts_to    = fmt_ts(last_ts)

    payload = {
        "ticker":    ticker,
        "timeframe": tf,
        "from":      date_from,
        "to":        date_to,
        "firstBar":  datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat(),
        "lastBar":   datetime.fromtimestamp(last_ts  / 1000, tz=timezone.utc).isoformat(),
        "savedAt":   datetime.now(tz=timezone.utc).isoformat(),
        "bars":      all_bars,
    }

    filename = f"{ticker}_{tf}min_{ts_from}__{ts_to}.json"
    with open(filename, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    print(f"\n✓  {len(all_bars)} bars saved → {filename}")
    print(f"   API calls made: {call_count}")


if __name__ == "__main__":
    main()
