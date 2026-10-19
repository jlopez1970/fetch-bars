"""
add_indicators.py
─────────────────
Version: see __version__ below (also: python3 add_indicators.py --version)

Changelog
  01.01  folders starting with "_" (_replaced, _backup...) are skipped when a
         whole folder is given; process() can be called from massive_capture.py
  01.00  first version: RSI + MACD added to every bar of the capture JSON files

Adds RSI and MACD to the bars of the JSON files written by massive_capture.py
(and by the chart app's "Save data"). Every bar gets four extra fields:

    "rsi"          RSI (Wilder), default period 14            2 decimals
    "macd"         MACD line  = EMA(12) - EMA(26)
    "macd_signal"  signal     = EMA(9) of the MACD line
    "macd_hist"    histogram  = macd - macd_signal

The formulas are the same as chart_v02 (calcRSI / calcEMA / calcMACD), so the
values match what the chart shows. Two differences on purpose:
  • MACD is kept at full precision (rounded to 6 decimals, 8 for prices < 10).
    The chart rounds MACD to 4 decimals, which turns EUR/USD MACD into 0.
  • The series runs continuously over the regular session (09:30–16:00 ET) of
    all days in the file. The chart restarts every day with only the previous
    day as warm-up; on 2-min bars the difference is negligible (see README).
    Use --chart-mode to reproduce the chart's per-day warm-up exactly.

Which bars
  2-min / 30-min files   regular-session bars only; pre/post-market bars get null
                         (use --all-hours to include them in the calculation)
  1-day files            all bars
  _filtered files        values copied from the matching full 2-min file (a
                         filtered file alone has gaps, so RSI/MACD on it would be wrong)

The rest of each file is left untouched (same schema, extra fields only), so
the chart app and massive_capture.py --merge keep working. After a new capture
or merge, just run this script again (it recalculates and overwrites).

Usage:
    python3 add_indicators.py Used/2026_macro                 # whole folder, in place
    python3 add_indicators.py Used/2026_macro/2min/SPY_2min_*.json
    python3 add_indicators.py Used/2026_macro --rsi 9 --macd 8 21 5
    python3 add_indicators.py Used/2026_macro --out Used/2026_macro_ind   # write copies instead
    python3 add_indicators.py Used/2026_macro --chart-mode    # exact chart_v02 per-day warm-up
"""

__version__ = "01.01"

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone


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
    print("  [warn] zoneinfo/dateutil not found — using fixed UTC-5 (EST, no DST)")
    return timezone(timedelta(hours=-5))


ET = get_et_zone()
FIELDS = ("rsi", "macd", "macd_signal", "macd_hist")


def et(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def in_session(ts_ms):
    d = et(ts_ms)
    m = d.hour * 60 + d.minute
    return d.weekday() < 5 and 9 * 60 + 30 <= m < 16 * 60


# ── indicator math (same as chart_v02) ──────────────────────────────────────
def ema(values, period):
    out = [None] * len(values)
    k = 2 / (period + 1)
    s = period - 1
    while s < len(values) and values[s] is None:
        s += 1
    if s >= len(values):
        return out
    seed = [v for v in values[:s + 1] if v is not None]
    out[s] = sum(seed) / len(seed)
    for i in range(s + 1, len(values)):
        if values[i] is None:
            continue
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(closes, period):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0)) / period
        al = (al * (period - 1) + max(-ch, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(closes, fast, slow, signal):
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def compute(closes, args):
    r = rsi(closes, args.rsi)
    l, s, h = macd(closes, *args.macd)
    return r, l, s, h


# ── per-file work ───────────────────────────────────────────────────────────
def indicator_values(bars, is_daily, args):
    """Return {t: (rsi, macd, signal, hist)} for the bars that get values."""
    use = [b for b in bars if is_daily or args.all_hours or in_session(b["t"])]
    use.sort(key=lambda b: b["t"])
    res = {}
    if args.chart_mode and not is_daily:
        days = {}
        for b in use:
            days.setdefault(et(b["t"]).date(), []).append(b)
        keys = sorted(days)
        for i, d in enumerate(keys):
            prev = days[keys[i - 1]] if i > 0 else []
            seq = prev + days[d]
            r, l, s, h = compute([b["c"] for b in seq], args)
            off = len(prev)
            for j, b in enumerate(days[d]):
                res[b["t"]] = (r[off + j], l[off + j], s[off + j], h[off + j])
    else:
        r, l, s, h = compute([b["c"] for b in use], args)
        for j, b in enumerate(use):
            res[b["t"]] = (r[j], l[j], s[j], h[j])
    return res


def apply(bars, vals):
    px = next((b["c"] for b in bars if b.get("c")), 100)
    dec = 8 if px < 10 else 6
    for b in bars:
        v = vals.get(b["t"])
        if v is None:
            for f in FIELDS:
                b[f] = None
            continue
        rv, lv, sv, hv = v
        b["rsi"] = round(rv, 2) if rv is not None else None
        b["macd"] = round(lv, dec) if lv is not None else None
        b["macd_signal"] = round(sv, dec) if sv is not None else None
        b["macd_hist"] = round(hv, dec) if hv is not None else None


def parent_of_filtered(path):
    d = os.path.dirname(path)
    name = os.path.basename(path).replace("_filtered.json", ".json")
    p = os.path.join(os.path.dirname(d), name)
    return p if os.path.exists(p) else None


def write_json(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)          # atomic: never leaves a half-written file


def meta(args, basis):
    return {
        "rsi": {"period": args.rsi},
        "macd": {"fast": args.macd[0], "slow": args.macd[1], "signal": args.macd[2]},
        "basis": basis,
        "addedBy": f"add_indicators.py v{__version__}",
        "addedAt": datetime.now(tz=timezone.utc).isoformat(),
    }


def process(path, args, cache):
    with open(path) as f:
        payload = json.load(f)
    if isinstance(payload, list):
        payload = {"bars": payload}
        bare = True
    else:
        bare = False
    bars = payload.get("bars")
    if not bars or "c" not in bars[0]:
        return "skip (no bars)"
    is_daily = str(payload.get("timeframe", "")).upper() in ("1D", "1440") or "_1day_" in os.path.basename(path)

    if payload.get("filterWindows") or path.endswith("_filtered.json"):
        parent = parent_of_filtered(path)
        if not parent:
            return "skip (filtered file without its full 2-min file)"
        if parent not in cache:
            with open(parent) as f:
                pb = json.load(f)
            cache[parent] = indicator_values(pb["bars"] if isinstance(pb, dict) else pb, False, args)
        vals = cache[parent]
        basis = "copied from " + os.path.basename(parent)
    else:
        vals = indicator_values(bars, is_daily, args)
        if not is_daily:
            cache[path] = vals
        basis = ("all bars" if is_daily or args.all_hours else "regular session 09:30-16:00 ET") + \
                (", per-day warm-up = previous day (chart_v02 mode)" if args.chart_mode and not is_daily
                 else ", continuous across days")

    apply(bars, vals)
    payload["indicators"] = meta(args, basis)
    out = path
    if args.out:
        rel = os.path.relpath(path, args.base) if args.base else os.path.basename(path)
        out = os.path.join(args.out, rel)
    write_json(out, bars if bare else payload)
    n = sum(1 for b in bars if b.get("rsi") is not None)
    return f"ok  {len(bars):>7} bars, {n:>7} with RSI"


def collect(inputs):
    files = []
    for p in inputs:
        if os.path.isdir(p):
            for q in glob.glob(os.path.join(p, "**", "*.json"), recursive=True):
                rel = os.path.relpath(q, p).split(os.sep)
                if any(part.startswith("_") for part in rel[:-1]) or os.path.basename(q) == "capture_log.json":
                    continue
                files.append(q)
        else:
            files.extend(glob.glob(p))
    # full files first, filtered ones after (they reuse the full file's values)
    files = sorted(set(files), key=lambda q: (q.endswith("_filtered.json"), q))
    return files


def main():
    p = argparse.ArgumentParser(description="Add RSI and MACD to every bar of capture JSON files.")
    p.add_argument("--version", action="version", version=f"add_indicators.py v{__version__}")
    p.add_argument("paths", nargs="+", help="Files, globs or folders (folders are searched recursively)")
    p.add_argument("--rsi", type=int, default=14, help="RSI period (default 14)")
    p.add_argument("--macd", type=int, nargs=3, default=[12, 26, 9], metavar=("FAST", "SLOW", "SIGNAL"),
                   help="MACD periods (default 12 26 9)")
    p.add_argument("--all-hours", action="store_true",
                   help="Include pre/post-market bars in the calculation (default: regular session only)")
    p.add_argument("--chart-mode", action="store_true",
                   help="Restart every day with the previous day as warm-up, exactly like chart_v02")
    p.add_argument("--out", help="Write copies under this folder instead of updating files in place")
    args = p.parse_args()
    args.base = args.paths[0] if (args.out and os.path.isdir(args.paths[0])) else None

    files = collect(args.paths)
    if not files:
        sys.exit("No JSON files found.")
    print(f"add_indicators.py v{__version__}  ·  RSI {args.rsi}  ·  MACD {args.macd[0]}/{args.macd[1]}/{args.macd[2]}"
          f"  ·  {len(files)} file(s)" + ("  ·  chart mode" if args.chart_mode else ""))
    cache, t0 = {}, time.time()
    for i, f in enumerate(files, 1):
        try:
            msg = process(f, args, cache)
        except Exception as e:           # keep going with the other files
            msg = f"ERROR {e}"
        print(f"[{i:>3}/{len(files)}] {os.path.relpath(f)}  {msg}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
