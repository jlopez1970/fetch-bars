"""
aggregate_to_daily_v01.01.py
─────────────────────────────
Reads a 2-min bars JSON file (produced by fetch_spy_bars.py) and aggregates
all bars within each trading day (America/New_York) into a single daily
OHLCV bar:
    open   = open of the first bar of the day
    high   = max high across the day
    low    = min low across the day
    close  = close of the last bar of the day
    volume = sum of volume across the day
    vwap   = volume-weighted average price across the day (if vw present)
    n      = sum of trade counts (if n present)
    t      = timestamp of the first bar of the day (start of day, ms)

Works on either the raw fetch output or the filtered (open-hour/power-hour)
output — if given a filtered file, the daily bar will only reflect those
windows, which is called out in a warning.

Output: <original_name with timeframe token swapped to 1day>.json
        (same schema, loadable in the chart app)

Usage:
    python3 aggregate_to_daily_v01.01.py SPY_2min_20260102_0900__20260611_2358.json
"""

import argparse
import json
import os
from datetime import datetime, timezone

# ── ET zone helper (same approach as filter_bars.py) ──────────────────────────
def get_et_zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except ImportError:
        pass
    try:
        from dateutil.tz import gettz
        tz = gettz("America/New_York")
        if tz:
            return tz
    except ImportError:
        pass
    from datetime import timezone as tzmod, timedelta
    print("  [warn] zoneinfo/dateutil not found — using fixed UTC-5 (EST, no DST)")
    return tzmod(timedelta(hours=-5))


ET = get_et_zone()


def to_et(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def day_key(ts_ms: int) -> str:
    """ET calendar date as 'YYYY-MM-DD' — used to group bars by trading day."""
    d = to_et(ts_ms)
    return d.strftime("%Y-%m-%d")


def fmt_ts(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return d.strftime("%Y%m%d_%H%M")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Aggregate 2-min bars into 1-day OHLCV bars.")
    parser.add_argument("file", help="Input 2-min bars .json file")
    args = parser.parse_args()

    print(f"Reading {args.file} ...")
    with open(args.file, "r") as f:
        payload = json.load(f)

    bars   = payload["bars"] if isinstance(payload, dict) else payload
    meta   = payload if isinstance(payload, dict) else {}
    ticker = meta.get("ticker", "DATA")
    tf     = meta.get("timeframe", "?")

    print(f"  {len(bars)} bars loaded  ({ticker} {tf}-min)")

    if meta.get("filterWindows"):
        print(f"  [note] source file is already windowed: {meta['filterWindows']}")
        print("         daily OHLC will only reflect those windows, not the full session.")

    if not bars:
        print("No bars to aggregate.")
        return

    # ── group by ET trading day, preserving chronological order ──────────────
    bars_sorted = sorted(bars, key=lambda b: b["t"])

    groups = {}      # day_key -> list of bars
    order  = []       # day_key insertion order
    for b in bars_sorted:
        k = day_key(b["t"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(b)

    print(f"  grouped into {len(order)} trading days")

    # ── aggregate each day ─────────────────────────────────────────────────────
    daily_bars = []
    for k in order:
        day_bars = groups[k]
        o  = day_bars[0]["o"]
        h  = max(b["h"] for b in day_bars)
        l  = min(b["l"] for b in day_bars)
        c  = day_bars[-1]["c"]
        v  = sum(b.get("v", 0) for b in day_bars)
        t  = day_bars[0]["t"]      # timestamp of first bar = start of day

        bar = {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

        # volume-weighted vwap across the day, if source bars have 'vw'
        if all("vw" in b for b in day_bars) and v > 0:
            bar["vw"] = sum(b["vw"] * b.get("v", 0) for b in day_bars) / v

        # sum trade counts, if present
        if all("n" in b for b in day_bars):
            bar["n"] = sum(b["n"] for b in day_bars)

        daily_bars.append(bar)

    # ── build output payload (same schema, loadable in chart app) ────────────
    first_ts = daily_bars[0]["t"]
    last_ts  = daily_bars[-1]["t"]

    out_meta = {
        **meta,
        "timeframe": "1D",
        "firstBar":  datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat(),
        "lastBar":   datetime.fromtimestamp(last_ts  / 1000, tz=timezone.utc).isoformat(),
        "savedAt":   datetime.now(tz=timezone.utc).isoformat(),
        "aggregatedFrom": f"{tf}-min bars",
        "bars": daily_bars,
    }

    base, ext = os.path.splitext(args.file)
    # Replace the original timeframe token (e.g. "2min") with "1day" in the filename,
    # so the output name reflects the new timeframe instead of the source one.
    old_tf_token = f"{tf}min"
    if old_tf_token in base:
        new_base = base.replace(old_tf_token, "1day")
    else:
        new_base = base + "_1day"
    out_path = new_base + ext
    with open(out_path, "w") as f:
        json.dump(out_meta, f, separators=(",", ":"))

    print(f"\n✓  {len(daily_bars)} daily bars saved → {out_path}")


if __name__ == "__main__":
    main()
