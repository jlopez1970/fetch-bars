"""
aggregate_bars_v01.02.py
─────────────────────────
Reads a 2-min bars JSON file (produced by fetch_spy_bars.py) and aggregates
it into bars of a new timeframe T (in minutes), where T must be a multiple
of 2 (since the source bars are 2-min).

For each output bar:
    open   = open of the first source bar in the bucket
    high   = max high across the bucket
    low    = min low across the bucket
    close  = close of the last source bar in the bucket
    volume = sum of volume across the bucket
    vwap   = volume-weighted average price across the bucket (if vw present)
    n      = sum of trade counts (if n present)
    t      = timestamp of the first bar in the bucket

Buckets are aligned to the start of each ET trading day — e.g. with T=10,
the open-hour day 09:30 → 10:30 becomes 6 bars of 10 minutes each
(09:30, 09:40, 09:50, 10:00, 10:10, 10:20). Buckets never span across two
different trading days, even if T is large (e.g. T=1440 for daily bars
collapses each day into a single bar).

Works on either the raw fetch output or the filtered (open-hour/power-hour)
output — if given a filtered file, each day's buckets simply restart at
the first bar of that day (e.g. 09:30 and 15:00), which is called out in
a warning since bucket boundaries will follow the filtered windows, not
the full session clock.

Output: <original_name with timeframe token swapped to T>.json
        (same schema, loadable in the chart app)

Usage:
    python3 aggregate_bars_v01.02.py SPY_2min_20260102_0900__20260611_2358.json --t 10
    python3 aggregate_bars_v01.02.py SPY_2min_20260102_0900__20260611_2358.json --t 1440   # daily bars
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
    return to_et(ts_ms).strftime("%Y-%m-%d")


def label_timeframe(t: int) -> str:
    """Human-readable label for the new timeframe, used in filenames/metadata."""
    if t % 1440 == 0 and t >= 1440:
        days = t // 1440
        return f"{days}day" if days > 1 else "1day"
    if t % 60 == 0 and t >= 60:
        hours = t // 60
        return f"{hours}h"
    return f"{t}min"


def aggregate_bucket(bucket_bars):
    """Collapse a list of source bars into one OHLCV bar."""
    o = bucket_bars[0]["o"]
    h = max(b["h"] for b in bucket_bars)
    l = min(b["l"] for b in bucket_bars)
    c = bucket_bars[-1]["c"]
    v = sum(b.get("v", 0) for b in bucket_bars)
    t = bucket_bars[0]["t"]   # timestamp of first bar in the bucket

    bar = {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

    if all("vw" in b for b in bucket_bars) and v > 0:
        bar["vw"] = sum(b["vw"] * b.get("v", 0) for b in bucket_bars) / v

    if all("n" in b for b in bucket_bars):
        bar["n"] = sum(b["n"] for b in bucket_bars)

    return bar


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Aggregate 2-min bars into bars of a new timeframe T (minutes).")
    parser.add_argument("file", help="Input 2-min bars .json file")
    parser.add_argument("--t", required=True, type=int,
                         help="Target timeframe in minutes. Must be a multiple of 2 (source bars are 2-min). "
                              "Use 1440 for daily bars.")
    args = parser.parse_args()

    target_t = args.t
    if target_t <= 0 or target_t % 2 != 0:
        print(f"Error: --t must be a positive multiple of 2 (got {target_t}).")
        return

    print(f"Reading {args.file} ...")
    with open(args.file, "r") as f:
        payload = json.load(f)

    bars       = payload["bars"] if isinstance(payload, dict) else payload
    meta       = payload if isinstance(payload, dict) else {}
    ticker     = meta.get("ticker", "DATA")
    source_tf  = meta.get("timeframe", 2)

    print(f"  {len(bars)} bars loaded  ({ticker} {source_tf}-min)")

    if source_tf and int(source_tf) != 2:
        print(f"  [warn] source timeframe metadata says {source_tf}-min, not 2-min — "
              f"bucket sizes below assume 2-min source bars.")

    if target_t < int(source_tf or 2):
        print(f"Error: target timeframe ({target_t}min) must be >= source timeframe ({source_tf}min).")
        return

    bars_per_bucket = target_t // int(source_tf or 2)

    if meta.get("filterWindows"):
        print(f"  [note] source file is already windowed: {meta['filterWindows']}")
        print("         buckets restart at the first bar of each window, not the full session clock.")

    if not bars:
        print("No bars to aggregate.")
        return

    # ── group by ET trading day, preserving chronological order ──────────────
    bars_sorted = sorted(bars, key=lambda b: b["t"])

    days  = {}    # day_key -> list of bars
    order = []    # day_key insertion order
    for b in bars_sorted:
        k = day_key(b["t"])
        if k not in days:
            days[k] = []
            order.append(k)
        days[k].append(b)

    print(f"  grouped into {len(order)} trading days")
    print(f"  bucketing every {bars_per_bucket} source bar(s) → 1 output bar of {target_t}min")

    # ── bucket within each day (buckets never span across days) ──────────────
    out_bars = []
    for k in order:
        day_bars = days[k]
        for i in range(0, len(day_bars), bars_per_bucket):
            bucket = day_bars[i:i + bars_per_bucket]
            out_bars.append(aggregate_bucket(bucket))

    if not out_bars:
        print("Nothing to save.")
        return

    # ── build output payload (same schema, loadable in chart app) ────────────
    first_ts = out_bars[0]["t"]
    last_ts  = out_bars[-1]["t"]
    new_tf_label = label_timeframe(target_t)

    out_meta = {
        **meta,
        "timeframe": target_t,
        "firstBar":  datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat(),
        "lastBar":   datetime.fromtimestamp(last_ts  / 1000, tz=timezone.utc).isoformat(),
        "savedAt":   datetime.now(tz=timezone.utc).isoformat(),
        "aggregatedFrom": f"{source_tf}-min bars",
        "bars": out_bars,
    }

    base, ext = os.path.splitext(args.file)
    old_tf_token = f"{source_tf}min"
    if old_tf_token in base:
        new_base = base.replace(old_tf_token, new_tf_label)
    else:
        new_base = base + f"_{new_tf_label}"
    out_path = new_base + ext
    with open(out_path, "w") as f:
        json.dump(out_meta, f, separators=(",", ":"))

    print(f"\n✓  {len(out_bars)} bars of {target_t}min saved → {out_path}")


if __name__ == "__main__":
    main()
