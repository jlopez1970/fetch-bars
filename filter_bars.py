"""
filter_bars.py
──────────────
Reads a bars JSON file (produced by fetch_spy_bars.py or the charting app)
and keeps only bars that fall within two daily windows (America/New_York):
  - Open hour  : 09:30 – 10:30
  - Power hour : 15:00 – 16:00

Output: <original_name>_filtered.json  (same schema, loadable in the chart app)

Usage:
    python filter_bars.py SPY_2min_20260101_0930__20260611_1558.json
    python filter_bars.py myfile.json --open-start 09:30 --open-end 10:30 --close-start 15:00 --close-end 16:00
"""

import argparse
import json
import os
from datetime import datetime, timezone, timedelta

# ── ET offset helpers ─────────────────────────────────────────────────────────
# America/New_York: UTC-5 (EST) or UTC-4 (EDT)
# Python's zoneinfo (3.9+) or dateutil handle DST automatically.
# We try zoneinfo first, fall back to dateutil, then manual EST-only fallback.

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
    # Manual fallback: fixed EST (UTC-5). DST days will be off by 1h —
    # acceptable for filtering purposes (windows are wide enough).
    print("  [warn] zoneinfo/dateutil not found — using fixed UTC-5 (EST, no DST)")
    return timezone(timedelta(hours=-5))


ET = get_et_zone()


def to_et(ts_ms: int) -> datetime:
    """Unix milliseconds → datetime in ET."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def hhmm(dt: datetime) -> int:
    """datetime → integer minutes since midnight, e.g. 09:30 → 570."""
    return dt.hour * 60 + dt.minute


def parse_hhmm(s: str) -> int:
    """'HH:MM' string → minutes since midnight."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_window(ts_ms: int, windows: list[tuple[int, int]]) -> bool:
    """Return True if the bar's ET time falls inside any (start_min, end_min) window."""
    mins = hhmm(to_et(ts_ms))
    return any(start <= mins < end for start, end in windows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Filter bars JSON to open + power-hour windows.")
    parser.add_argument("file",                              help="Input .json file")
    parser.add_argument("--open-start",  default="09:30",   help="Open window start  (default 09:30 ET)")
    parser.add_argument("--open-end",    default="10:30",   help="Open window end    (default 10:30 ET)")
    parser.add_argument("--close-start", default="15:00",   help="Close window start (default 15:00 ET)")
    parser.add_argument("--close-end",   default="16:00",   help="Close window end   (default 16:00 ET)")
    args = parser.parse_args()

    windows = [
        (parse_hhmm(args.open_start),  parse_hhmm(args.open_end)),
        (parse_hhmm(args.close_start), parse_hhmm(args.close_end)),
    ]

    # ── load ─────────────────────────────────────────────────────────────────
    print(f"Reading {args.file} ...")
    with open(args.file, "r") as f:
        payload = json.load(f)

    bars   = payload["bars"] if isinstance(payload, dict) else payload
    meta   = payload if isinstance(payload, dict) else {}
    ticker = meta.get("ticker", "DATA")
    tf     = meta.get("timeframe", "?")

    print(f"  {len(bars)} bars loaded  ({ticker} {tf}-min)")

    # ── filter ───────────────────────────────────────────────────────────────
    filtered = [b for b in bars if in_window(b["t"], windows)]

    print(f"  {len(filtered)} bars kept after filtering")

    # break down by window for info
    open_bars  = [b for b in filtered if hhmm(to_et(b["t"])) < parse_hhmm(args.close_start)]
    close_bars = [b for b in filtered if hhmm(to_et(b["t"])) >= parse_hhmm(args.close_start)]
    print(f"    open  ({args.open_start}–{args.open_end}):  {len(open_bars)} bars")
    print(f"    close ({args.close_start}–{args.close_end}): {len(close_bars)} bars")

    if not filtered:
        print("Nothing to save.")
        return

    # ── build output payload ─────────────────────────────────────────────────
    first_ts = filtered[0]["t"]
    last_ts  = filtered[-1]["t"]

    out_meta = {
        **meta,
        "firstBar": datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat(),
        "lastBar":  datetime.fromtimestamp(last_ts  / 1000, tz=timezone.utc).isoformat(),
        "savedAt":  datetime.now(tz=timezone.utc).isoformat(),
        "filterWindows": [
            f"{args.open_start}–{args.open_end} ET",
            f"{args.close_start}–{args.close_end} ET",
        ],
        "bars": filtered,
    }

    base, ext = os.path.splitext(args.file)
    out_path  = base + "_filtered" + ext
    with open(out_path, "w") as f:
        json.dump(out_meta, f, separators=(",", ":"))

    print(f"\n✓  saved → {out_path}")


if __name__ == "__main__":
    main()
