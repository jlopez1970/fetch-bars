"""
massive_capture.py
──────────────────
Version: see __version__ below (also: python3 massive_capture.py --version)

Changelog
  01.08  --from auto: each ticker starts from the day of the last bar already
         in --out (implies --merge). Tickers with no files yet start
         AUTO_NEW_DAYS (730) days back. --to still defaults to today, so a
         top-up is just: --tickers-file X.txt --from auto --out <folder>
  01.07  RSI + MACD are added automatically after every capture/merge (same
         math as add_indicators.py: RSI 14, MACD 12/26/9, regular session for
         intraday, all bars for daily; filtered files copy from the full file).
         Before, a --merge top-up left new bars without indicators and rebuilt
         30min/1day without them. --no-indicators skips this step.
  01.06  TradingView TVC: symbols only map to indexes when Polygon has them
         (TVC:VIX -> I:VIX); TVC-only data such as TVC:US10Y, TVC:UKOIL,
         TVC:DXY is skipped with a warning instead of failing later.
  01.05  TradingView watchlist exports work with --tickers-file (and --tickers):
         exchange prefixes stripped (NASDAQ:AAPL -> AAPL), ###Section headers
         skipped, FX:EURUSD -> C:EURUSD, BINANCE:BTCUSD -> X:BTCUSD,
         SP:SPX / TVC:VIX -> I:SPX / I:VIX; futures/economic symbols skipped
         with a warning. --merge-only banner no longer shows download dates.
  01.04  single file name (massive_capture.py) + --version; version recorded
         in capture_log.json and in every JSON file written ("toolVersion")
  01.03  --merge / --merge-only: one continuous file per ticker, old files
         moved to <out>/_replaced/ (nothing is deleted)
  01.02  forex / crypto / index tickers (EUR/USD -> C:EURUSD, files C_EURUSD_...)
  01.01  SSL fix for corporate networks (truststore, strict flag relaxed, --insecure)
  01.00  first version: fetch + filter + aggregate for many tickers, resumable, log

One-shot "capture everything" tool for Polygon.io (now Massive.com) bars.
Combines fetch_spy_bars.py + filter_bars.py + aggregate_bars_v01.02.py +
aggregate_to_daily_v01.01.py into a single batch run over many tickers,
and writes the results in the same folder layout already used in Used/2026:

    <out>/
      2min/                  raw source bars           TICKER_2min_<first>__<last>.json
      2min/filtered/         open-hour + power-hour    ..._filtered.json
      30min/                 aggregated                TICKER_30min_<first>__<last>.json
      1day/                  aggregated                TICKER_1day_<first>__<last>.json
      capture_log.json       summary of the run (per ticker: status, bar counts, files)

All output files keep the same JSON schema as the original scripts, so they
load in the chart app unchanged.

Tickers
  --tickers AAPL MSFT ...        explicit list
  --tickers-file tickers.txt     one per line (# comments allowed)
  --tickers-from Used/2026/2min  reuse the tickers found in an existing folder
  (none given)                   the default watchlist below (same set as Used/2026)

Resume
  A ticker whose source file for the same timeframe + --from/--to already
  exists in <out>/<tf>min is skipped (its derived files are rebuilt only if
  missing). Use --force to re-download.

API key
  --key KEY, or env var MASSIVE_API_KEY / POLYGON_API_KEY.

Usage:
    python3 massive_capture.py --key KEY --from 2026-01-01 --to 2026-09-25 --out Used/2026b
    python3 massive_capture.py --tickers SPY QQQ --from 2024-06-25 --to 2026-06-24
    python3 massive_capture.py --tickers-from Used/2026/2min --agg 10 30 60 1440
    python3 massive_capture.py --from 2025-01-01 --to 2025-12-31 --out Used/2026_full --merge
    python3 massive_capture.py --tickers-from Used/2026_full/2min --out Used/2026_full --merge-only
    python3 massive_capture.py --tickers-file macro56.txt --from auto --out Used/2026_macro
    python3 massive_capture.py --calls-per-min 0      # paid plan: no throttling
    python3 massive_capture.py --no-filter --agg      # raw bars only
"""

import argparse
import glob
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta, timezone

__version__ = "01.08"

# ── config ────────────────────────────────────────────────────────────────────
DEFAULT_TICKERS = [
    "AAPL", "AMD", "AMZN", "BABA", "BITC", "BITX", "COIN", "DELL", "DIA", "DPST",
    "FUTU", "GLD", "GOOGL", "INTC", "IWM", "LABU", "LUNR", "META", "MRNA", "MSFT",
    "MSFU", "MU", "MUU", "NFLX", "NVAX", "NVDA", "NVO", "ORCL", "ORCU", "PDD",
    "QQQ", "SLV", "SOXL", "SOXS", "SPY", "SQQQ", "TSLA", "USO", "VIXY", "YANG", "YINN",
]
DEFAULT_FROM   = f"{date.today().year}-01-01"
DEFAULT_TO     = date.today().isoformat()
DEFAULT_TF     = 2                     # minutes
DEFAULT_AGG    = [30, 1440]            # 30-min + daily, like Used/2026
DEFAULT_OUT    = "Captures"
LIMIT          = 50_000                # max bars per call
CALLS_PER_MIN  = 5                     # free-tier limit
AUTO_NEW_DAYS  = 730                   # --from auto, ticker with no files yet
BASE_URL       = "https://api.polygon.io"   # api.massive.com also works after the rebrand
MAX_RETRIES    = 5


# ── ET zone helper (same approach as the other scripts) ───────────────────────
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


def to_et(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def day_key(ts_ms):
    return to_et(ts_ms).strftime("%Y-%m-%d")


def iso_utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def fmt_ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d_%H%M")


def now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def parse_hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def label_timeframe(t):
    if t >= 1440 and t % 1440 == 0:
        return f"{t // 1440}day"
    if t >= 60 and t % 60 == 0:
        return f"{t // 60}h"
    return f"{t}min"


def write_json(path, payload):
    if isinstance(payload, dict) and "bars" in payload:
        payload = {**payload, "toolVersion": __version__}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)          # atomic: never leaves a half-written file


# TradingView exchange prefixes -> what they mean for Polygon/Massive
TV_FX_PREFIXES = {"FX", "FX_IDC", "OANDA", "FOREXCOM", "FXCM", "SAXO", "PEPPERSTONE",
                  "ICMARKETS", "CAPITALCOM", "EIGHTCAP", "BLACKBULL", "CURRENCYCOM"}
TV_CRYPTO_PREFIXES = {"BINANCE", "BINANCEUS", "COINBASE", "KRAKEN", "BITSTAMP", "BITFINEX",
                      "GEMINI", "BYBIT", "OKX", "KUCOIN", "CRYPTO", "CRYPTOCAP", "BITGET", "MEXC"}
TV_INDEX_PREFIXES = {"SP", "DJ", "INDEX", "CBOE_INDEX"}
TV_SKIP_PREFIXES = {"ECONOMICS", "FRED", "QUANDL", "USI", "CME", "CME_MINI", "CBOT", "CBOT_MINI",
                    "NYMEX", "COMEX", "ICEUS", "ICEEUR", "EUREX"}
KNOWN_INDEXES = {"SPX", "NDX", "DJI", "VIX", "RUT", "VXN", "OEX", "COMP", "VVIX"}
SKIPPED = []   # (original symbol, reason) — reported once at start-up


def normalize_ticker(t):
    """
    Turn user / TradingView symbols into Polygon tickers. Returns None to skip.
      AAPL, NASDAQ:AAPL, NYSE:BRK.B     -> AAPL, AAPL, BRK.B
      EUR/USD, EURUSD=FX, FX:EURUSD     -> C:EURUSD
      BINANCE:BTCUSD                    -> X:BTCUSD
      SP:SPX, TVC:VIX, CBOE:VIX         -> I:SPX, I:VIX
      C:EURUSD / X:BTCUSD / I:SPX       -> unchanged
      CME_MINI:ES1!, ECONOMICS:USCPI    -> skipped (not available on Polygon stocks plan)
    """
    orig = t
    t = t.strip().strip('"').upper()
    if not t or t.startswith("#"):
        return None
    if re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", t):
        return "C:" + t.replace("/", "")
    if t.endswith("=FX") and len(t) == 9:
        return "C:" + t[:6]
    if "!" in t:
        SKIPPED.append((orig.strip(), "futures contract"))
        return None
    if ":" in t:
        prefix, sym = t.split(":", 1)
        if prefix in ("C", "X", "I"):                      # already Polygon style
            return t
        if prefix in TV_SKIP_PREFIXES:
            SKIPPED.append((orig.strip(), "futures/economic data"))
            return None
        if prefix in TV_FX_PREFIXES:
            if re.fullmatch(r"[A-Z]{6}", sym):
                return "C:" + sym
            SKIPPED.append((orig.strip(), "not a currency pair"))
            return None
        if prefix in TV_CRYPTO_PREFIXES:
            return "X:" + sym
        if prefix == "TVC" and sym not in KNOWN_INDEXES:
            SKIPPED.append((orig.strip(), "TradingView-only data, not on Polygon"))
            return None
        if prefix in TV_INDEX_PREFIXES or sym in KNOWN_INDEXES:
            return "I:" + sym
        t = sym                                             # NASDAQ:, NYSE:, AMEX:, ARCA:, BATS: ...
    if t in KNOWN_INDEXES and t != "COMP":
        return t                                            # plain 'VIX' etc. left as typed
    return t


def safe_name(ticker):
    """Filesystem-safe ticker for file names: 'C:EURUSD' -> 'C_EURUSD'."""
    return re.sub(r"[^A-Z0-9.\-]", "_", ticker)


# ── rate limiter (sliding 60-s window) ────────────────────────────────────────
class RateLimiter:
    def __init__(self, cap):
        self.cap = cap
        self.calls = deque()

    def wait(self):
        if self.cap <= 0:
            return
        now = time.monotonic()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.cap:
            sleep_for = 60 - (now - self.calls[0]) + 0.5
            print(f"    [rate-limit] {self.cap} calls/min used — sleeping {sleep_for:.1f}s ...")
            time.sleep(sleep_for)
            self.calls.popleft()
        self.calls.append(time.monotonic())


# ── SSL context ───────────────────────────────────────────────────────────────
SSL_CTX = None


def build_ssl_context(insecure=False):
    """
    Corporate HTTPS inspection (Zscaler etc.) re-signs traffic with an internal
    CA. Python 3.13+ turns on VERIFY_X509_STRICT, which rejects many of those
    certs ("Missing Authority Key Identifier"). Order of preference:
      1. truststore  -> verify with the macOS keychain (where IT installs its CA)
      2. certifi / system certs with the strict flag cleared
      3. --insecure  -> no verification (last resort)
    """
    if insecure:
        print("  [ssl] --insecure: certificate verification DISABLED")
        return ssl._create_unverified_context()
    try:
        import truststore
        print("  [ssl] using macOS keychain (truststore)")
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"massive_capture/{__version__}"})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def fetch_bars(ticker, tf, date_from, date_to, api_key, limiter, base_url):
    """Return (bars, api_calls). Raises RuntimeError on API errors."""
    url = (f"{base_url}/v2/aggs/ticker/{ticker}/range/{tf}/minute/{date_from}/{date_to}"
           f"?adjusted=true&sort=asc&limit={LIMIT}")
    bars, calls = [], 0
    while url:
        attempt = 0
        while True:
            limiter.wait()
            calls += 1
            try:
                data = fetch_json(url + f"&apiKey={api_key}")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:300]
                if e.code == 429 or e.code >= 500:
                    attempt += 1
                    if attempt > MAX_RETRIES:
                        raise RuntimeError(f"HTTP {e.code} after {MAX_RETRIES} retries: {body}")
                    wait = 60 if e.code == 429 else 5 * attempt
                    print(f"    [HTTP {e.code}] retry {attempt}/{MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"HTTP {e.code}: {body}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    raise RuntimeError(f"SSL verification failed: {e}  "
                                       "→ try: pip3 install truststore   (or --insecure)")
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise RuntimeError(f"network error after {MAX_RETRIES} retries: {e}")
                print(f"    [network] {e} — retry {attempt}/{MAX_RETRIES} in {5 * attempt}s")
                time.sleep(5 * attempt)

        if data.get("status") == "ERROR" or data.get("error"):
            raise RuntimeError(data.get("error") or data.get("message") or "API error")

        results = data.get("results") or []
        bars.extend(results)
        print(f"    call #{calls}: +{len(results)} bars (total {len(bars)})")
        url = data.get("next_url") if results else None

    # de-dup + sort (pages can overlap on the boundary)
    uniq = {b["t"]: b for b in bars}
    return [uniq[t] for t in sorted(uniq)], calls


# ── transforms (same logic as filter_bars.py / aggregate_bars_v01.02.py) ─────
def filter_windows(bars, windows):
    def inside(b):
        d = to_et(b["t"])
        m = d.hour * 60 + d.minute
        return any(s <= m < e for s, e in windows)
    return [b for b in bars if inside(b)]


def aggregate_bucket(bucket):
    v = sum(b.get("v", 0) for b in bucket)
    bar = {"t": bucket[0]["t"], "o": bucket[0]["o"],
           "h": max(b["h"] for b in bucket), "l": min(b["l"] for b in bucket),
           "c": bucket[-1]["c"], "v": v}
    if v > 0 and all("vw" in b for b in bucket):
        bar["vw"] = sum(b["vw"] * b.get("v", 0) for b in bucket) / v
    if all("n" in b for b in bucket):
        bar["n"] = sum(b["n"] for b in bucket)
    return bar


def aggregate(bars, source_tf, target_t):
    """Bucket per ET trading day; buckets never span days. 1440 → one bar per day."""
    days, order = {}, []
    for b in sorted(bars, key=lambda x: x["t"]):
        k = day_key(b["t"])
        if k not in days:
            days[k] = []
            order.append(k)
        days[k].append(b)
    out = []
    for k in order:
        day_bars = days[k]
        if target_t >= 1440:
            out.append(aggregate_bucket(day_bars))
        else:
            n = target_t // source_tf
            for i in range(0, len(day_bars), n):
                out.append(aggregate_bucket(day_bars[i:i + n]))
    return out


# ── per-ticker pipeline ───────────────────────────────────────────────────────
def find_existing_source(src_dir, ticker, tf, date_from, date_to):
    for p in glob.glob(os.path.join(src_dir, f"{safe_name(ticker)}_{tf}min_*.json")):
        if p.endswith("_filtered.json"):
            continue
        try:
            with open(p) as f:
                meta = json.load(f)
            if meta.get("from") == date_from and meta.get("to") == date_to and meta.get("bars"):
                return p, meta
        except (OSError, ValueError):
            pass
    return None, None


def source_path(src_dir, ticker, tf, bars):
    return os.path.join(src_dir, f"{safe_name(ticker)}_{tf}min_{fmt_ts(bars[0]['t'])}__{fmt_ts(bars[-1]['t'])}.json")


def list_sources(src_dir, ticker, tf):
    """All (path, payload) source files for this ticker in src_dir (not filtered)."""
    out = []
    for p in sorted(glob.glob(os.path.join(src_dir, f"{safe_name(ticker)}_{tf}min_*.json"))):
        if p.endswith("_filtered.json"):
            continue
        try:
            with open(p) as f:
                meta = json.load(f)
            if isinstance(meta, dict) and meta.get("bars"):
                out.append((p, meta))
        except (OSError, ValueError):
            print(f"  [warn] could not read {os.path.basename(p)} — left untouched")
    return out


def related_files(out_root, src_path, ticker, tf):
    """Source file + its filtered and aggregated siblings (any timeframe folder)."""
    base = os.path.splitext(os.path.basename(src_path))[0]
    prefix = f"{safe_name(ticker)}_{tf}min"
    suffix = base[len(prefix):]                       # e.g. _20260102_0900__20260611_2358
    files = {src_path,
             os.path.join(os.path.dirname(src_path), "filtered", base + "_filtered.json")}
    for p in glob.glob(os.path.join(out_root, "*", f"{safe_name(ticker)}_*{suffix}.json")):
        files.add(p)
    return [p for p in files if os.path.exists(p)]


def move_to_replaced(out_root, paths):
    moved = []
    for p in paths:
        rel = os.path.relpath(p, out_root)
        dest = os.path.join(out_root, "_replaced", rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(dest)
            dest = f"{stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
        os.replace(p, dest)
        moved.append(rel)
    return moved


def merge_source(ticker, args, limiter, api_key, src_dir, entry):
    """
    Merge mode: combine existing source files for this ticker with a fresh
    download of --from/--to into ONE file. Returns (path, payload) or None.
    """
    tf = args.tf
    existing = list_sources(src_dir, ticker, tf)
    new_bars = []

    if args.merge_only:
        if not existing:
            entry["status"] = "no-data"
            print("  ! nothing on disk to merge")
            return None
    else:
        covered = [(p, m) for p, m in existing
                   if m.get("from", "9999") <= args.date_from and m.get("to", "") >= args.date_to]
        if covered and not args.force:
            print(f"  = range already covered by {os.path.basename(covered[0][0])} — no download")
            entry["status"] = "cached"
        else:
            new_bars, calls = fetch_bars(ticker, tf, args.date_from, args.date_to,
                                         api_key, limiter, args.base_url)
            entry["apiCalls"] = calls
            print(f"  ↓ {len(new_bars)} new bars ({args.date_from} → {args.date_to})")

    if not existing and not new_bars:
        entry["status"] = "no-data"
        print("  ! no bars returned")
        return None

    # single file on disk and nothing new → already consolidated
    if len(existing) == 1 and not new_bars:
        return existing[0]

    merged = {}
    for _, m in existing:
        for b in m["bars"]:
            merged[b["t"]] = b
    for b in new_bars:                      # fresh download wins on overlap
        merged[b["t"]] = b
    bars = [merged[t] for t in sorted(merged)]

    froms = [m.get("from") for _, m in existing if m.get("from")]
    tos   = [m.get("to")   for _, m in existing if m.get("to")]
    if new_bars:
        froms.append(args.date_from)
        tos.append(args.date_to)
    payload = {
        "ticker": ticker, "timeframe": tf,
        "from": min(froms) if froms else day_key(bars[0]["t"]),
        "to":   max(tos)   if tos   else day_key(bars[-1]["t"]),
        "firstBar": iso_utc(bars[0]["t"]), "lastBar": iso_utc(bars[-1]["t"]),
        "savedAt": now_iso(),
        "mergedFrom": [os.path.basename(p) for p, _ in existing] +
                      ([f"download {args.date_from} → {args.date_to}"] if new_bars else []),
        "bars": bars,
    }

    # move old source files (and their filtered/aggregated siblings) out of the way first
    old = []
    for p, _ in existing:
        old += related_files(args.out, p, ticker, tf)
    moved = move_to_replaced(args.out, sorted(set(old)))
    if moved:
        entry["replaced"] = moved
        print(f"  → {len(moved)} old file(s) moved to _replaced/")

    path = source_path(src_dir, ticker, tf, bars)
    write_json(path, payload)
    before = sum(len(m["bars"]) for _, m in existing) + len(new_bars)
    print(f"  ✓ merged {len(existing)} file(s)" + (" + download" if new_bars else "") +
          f": {len(bars)} bars ({before - len(bars)} duplicates removed) → {os.path.relpath(path, args.out)}")
    entry["status"] = "merged"
    return path, payload


def auto_from(ticker, args, src_dir):
    """--from auto: day (ET) of the last bar already on disk for this ticker."""
    last = None
    for _, m in list_sources(src_dir, ticker, args.tf):
        t = m["bars"][-1]["t"]
        last = t if last is None or t > last else last
    if last is None:
        d = (date.today() - timedelta(days=AUTO_NEW_DAYS)).isoformat()
        print(f"  · no files yet → from {d}")
    else:
        d = day_key(last)
        print(f"  · last bar on disk {d} → from {d}")
    return argparse.Namespace(**{**vars(args), "date_from": d})


def capture_ticker(ticker, args, limiter, api_key):
    tf = args.tf
    src_dir = os.path.join(args.out, f"{tf}min")
    entry = {"ticker": ticker, "status": "ok", "files": [], "apiCalls": 0}
    if args.date_from == "auto" and not args.merge_only:
        args = auto_from(ticker, args, src_dir)
        entry["from"] = args.date_from

    if args.merge or args.merge_only:
        result = merge_source(ticker, args, limiter, api_key, src_dir, entry)
        if result is None:
            return entry
        path, payload = result
        bars = payload["bars"]
    else:
        path, payload = (None, None) if args.force else \
            find_existing_source(src_dir, ticker, tf, args.date_from, args.date_to)

        if payload:
            print(f"  = source exists, skipping download: {os.path.basename(path)}")
            entry["status"] = "cached"
            bars = payload["bars"]
        else:
            bars, calls = fetch_bars(ticker, tf, args.date_from, args.date_to,
                                     api_key, limiter, args.base_url)
            entry["apiCalls"] = calls
            if not bars:
                entry["status"] = "no-data"
                print("  ! no bars returned")
                return entry
            payload = {
                "ticker": ticker, "timeframe": tf,
                "from": args.date_from, "to": args.date_to,
                "firstBar": iso_utc(bars[0]["t"]), "lastBar": iso_utc(bars[-1]["t"]),
                "savedAt": now_iso(), "bars": bars,
            }
            path = source_path(src_dir, ticker, tf, bars)
            write_json(path, payload)
            print(f"  ✓ {len(bars)} bars → {os.path.relpath(path, args.out)}")

    entry["bars"] = len(bars)
    entry["files"].append(os.path.relpath(path, args.out))
    base_name = os.path.splitext(os.path.basename(path))[0]
    tf_token = f"{tf}min"

    def derived(out_path, build):
        if os.path.exists(out_path) and not args.force:
            entry["files"].append(os.path.relpath(out_path, args.out))
            return
        out_payload = build()
        if out_payload:
            write_json(out_path, out_payload)
            entry["files"].append(os.path.relpath(out_path, args.out))
            print(f"  ✓ {len(out_payload['bars'])} bars → {os.path.relpath(out_path, args.out)}")

    # filtered (open hour + power hour)
    if not args.no_filter:
        windows = [(parse_hhmm(args.open_start), parse_hhmm(args.open_end)),
                   (parse_hhmm(args.close_start), parse_hhmm(args.close_end))]

        def build_filtered():
            fb = filter_windows(bars, windows)
            if not fb:
                return None
            return {**payload, "firstBar": iso_utc(fb[0]["t"]), "lastBar": iso_utc(fb[-1]["t"]),
                    "savedAt": now_iso(),
                    "filterWindows": [f"{args.open_start}–{args.open_end} ET",
                                      f"{args.close_start}–{args.close_end} ET"],
                    "bars": fb}
        derived(os.path.join(src_dir, "filtered", base_name + "_filtered.json"), build_filtered)

    # aggregates (from the full, unfiltered source)
    for t in args.agg:
        label = label_timeframe(t)

        def build_agg(t=t):
            ab = aggregate(bars, tf, t)
            return {**payload, "timeframe": "1D" if t == 1440 else t,
                    "firstBar": iso_utc(ab[0]["t"]), "lastBar": iso_utc(ab[-1]["t"]),
                    "savedAt": now_iso(), "aggregatedFrom": f"{tf}-min bars", "bars": ab}
        name = base_name.replace(tf_token, label, 1) if tf_token in base_name else f"{base_name}_{label}"
        derived(os.path.join(args.out, label, name + ".json"), build_agg)

    return entry


# ── indicators (v01.07) ───────────────────────────────────────────────────────
def add_indicators_to(entry, args):
    """Recalculate RSI/MACD on every file this ticker run produced or reused."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import add_indicators as ai
    except ImportError:
        print("  ! add_indicators.py not found next to this script — indicators skipped")
        entry["indicators"] = "skipped (add_indicators.py missing)"
        return
    ns = argparse.Namespace(rsi=14, macd=[12, 26, 9], all_hours=False, chart_mode=False,
                            out=None, base=None)
    paths = [os.path.join(args.out, f) for f in entry.get("files", [])]
    paths = sorted(set(p for p in paths if os.path.exists(p)), key=lambda q: (q.endswith("_filtered.json"), q))
    cache = {}
    for p in paths:
        msg = ai.process(p, ns, cache)
        if not msg.startswith("ok"):
            print(f"  ! indicators {os.path.basename(p)}: {msg}")
    entry["indicators"] = f"RSI 14 · MACD 12/26/9 (add_indicators.py v{ai.__version__})"
    print(f"  ✓ RSI/MACD added to {len(paths)} file(s)")


# ── ticker list ───────────────────────────────────────────────────────────────
def resolve_tickers(args):
    tickers = []
    if args.tickers:
        tickers += args.tickers
    if args.tickers_file:
        with open(args.tickers_file, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or (line.startswith("#") and not line.startswith("###")):
                    continue                                # comment line
                line = re.sub(r"\s#(?!##).*$", "", line)    # trailing '# comment'
                for chunk in line.split(","):               # TradingView: comma-separated
                    chunk = chunk.strip()
                    if not chunk or chunk.startswith("###"):
                        continue                            # TradingView ###Section header
                    tickers += chunk.split()
    if args.tickers_from:
        for p in glob.glob(os.path.join(args.tickers_from, "*.json")):
            m = re.match(r"(?:([CXI])_)?([A-Z0-9.\-]+)_\d+(min|day|h|D)_", os.path.basename(p))
            if m:
                tickers.append(f"{m.group(1)}:{m.group(2)}" if m.group(1) else m.group(2))
    if not tickers:
        tickers = DEFAULT_TICKERS
    seen, out = set(), []
    for t in tickers:
        t = normalize_ticker(t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Batch-capture Polygon/Massive bars for many tickers, "
                                            "then filter + aggregate into the Used/<year> layout.")
    p.add_argument("--version", action="version", version=f"massive_capture.py v{__version__}")
    p.add_argument("--key", default=os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY"),
                   help="API key (or set MASSIVE_API_KEY / POLYGON_API_KEY)")
    p.add_argument("--tickers", nargs="+", help="Ticker list")
    p.add_argument("--tickers-file", help="Text file with tickers")
    p.add_argument("--tickers-from", help="Folder of existing bar files to take tickers from")
    p.add_argument("--from", dest="date_from", default=DEFAULT_FROM, help="Start date YYYY-MM-DD, or 'auto' = last bar on disk per ticker (implies --merge)")
    p.add_argument("--to", dest="date_to", default=DEFAULT_TO, help="End date YYYY-MM-DD")
    p.add_argument("--tf", type=int, default=DEFAULT_TF, help="Source timeframe in minutes (default 2)")
    p.add_argument("--agg", type=int, nargs="*", default=DEFAULT_AGG,
                   help="Aggregate timeframes in minutes (default: 30 1440). Pass --agg alone for none.")
    p.add_argument("--no-filter", action="store_true", help="Skip open/power-hour filtered files")
    p.add_argument("--open-start", default="09:30")
    p.add_argument("--open-end", default="10:30")
    p.add_argument("--close-start", default="15:00")
    p.add_argument("--close-end", default="16:00")
    p.add_argument("--out", default=DEFAULT_OUT, help=f"Output root folder (default {DEFAULT_OUT})")
    p.add_argument("--calls-per-min", type=int, default=CALLS_PER_MIN,
                   help="API calls per minute (default 5 = free tier; 0 = unlimited)")
    p.add_argument("--base-url", default=BASE_URL, help="API base URL (e.g. https://api.massive.com)")
    p.add_argument("--insecure", action="store_true",
                   help="Disable SSL verification (last resort on corporate networks)")
    p.add_argument("--merge", action="store_true",
                   help="Merge the download with existing files for the same ticker in --out into ONE file")
    p.add_argument("--merge-only", action="store_true",
                   help="No download: just consolidate existing files per ticker in --out")
    p.add_argument("--force", action="store_true", help="Re-download and rebuild even if files exist")
    p.add_argument("--no-indicators", action="store_true",
                   help="Do not add RSI/MACD to the files (default: added after each ticker)")
    args = p.parse_args()

    if not args.key and not args.merge_only:
        sys.exit("Error: no API key. Use --key or set MASSIVE_API_KEY / POLYGON_API_KEY.")
    bad = [t for t in args.agg if t < args.tf or (t < 1440 and t % args.tf)]
    if bad:
        sys.exit(f"Error: --agg values must be multiples of --tf ({args.tf}) or 1440; bad: {bad}")

    if args.date_from == "auto":
        args.merge = True                   # auto only makes sense when topping up existing files
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date_from):
        sys.exit("Error: --from must be YYYY-MM-DD or 'auto'")

    global SSL_CTX
    SSL_CTX = build_ssl_context(args.insecure)

    tickers = resolve_tickers(args)
    if args.merge_only and not (args.tickers or args.tickers_file or args.tickers_from):
        tickers = resolve_tickers(argparse.Namespace(
            tickers=None, tickers_file=None, tickers_from=os.path.join(args.out, f"{args.tf}min")))
    if args.merge or args.merge_only:
        print("MERGE MODE: existing files for each ticker in --out are combined into one; "
              "old files go to _replaced/")
    print(f"massive_capture.py v{__version__}")
    if SKIPPED:
        print(f"Skipped {len(SKIPPED)} symbol(s) not available here: " +
              ", ".join(f"{o} ({r})" for o, r in SKIPPED))
    if args.merge_only:
        print(f"Consolidating {len(tickers)} tickers · {args.tf}-min · no download")
    else:
        print(f"Capturing {len(tickers)} tickers · {args.tf}-min · {args.date_from} → {args.date_to}")
    print(f"Output: {os.path.abspath(args.out)}")
    print(f"Filter: {'off' if args.no_filter else 'open/power hour'} · "
          f"Aggregates: {', '.join(label_timeframe(t) for t in args.agg) or 'none'} · "
          f"Indicators: {'off' if args.no_indicators else 'RSI/MACD'} · "
          f"Rate: {args.calls_per_min or 'unlimited'} calls/min\n")

    limiter = RateLimiter(args.calls_per_min)
    log = {"toolVersion": __version__, "startedAt": now_iso(), "from": args.date_from, "to": args.date_to,
           "timeframe": args.tf, "aggregates": args.agg, "tickers": []}
    started = time.monotonic()

    try:
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {ticker}")
            try:
                entry = capture_ticker(ticker, args, limiter, args.key)
                if not args.no_indicators and entry.get("status") not in ("error", "no-data") \
                        and entry.get("files"):
                    add_indicators_to(entry, args)
            except Exception as e:           # keep going with the next ticker
                print(f"  ✗ {e}")
                if "SSL verification failed" in str(e):
                    log["tickers"].append({"ticker": ticker, "status": "error", "error": str(e)})
                    print("Stopping: SSL problem will affect every ticker.")
                    break
                entry = {"ticker": ticker, "status": "error", "error": str(e)}
            log["tickers"].append(entry)
    except KeyboardInterrupt:
        print("\nInterrupted — writing log for what finished. Re-run to resume.")

    log["finishedAt"] = now_iso()
    write_json(os.path.join(args.out, "capture_log.json"), log)

    counts = {}
    for e in log["tickers"]:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    mins = (time.monotonic() - started) / 60
    print(f"\nDone in {mins:.1f} min · " + " · ".join(f"{k}: {v}" for k, v in counts.items()))
    failed = [e["ticker"] for e in log["tickers"] if e["status"] in ("error", "no-data")]
    if failed:
        print("Needs attention: " + ", ".join(failed))
    print(f"Log → {os.path.join(args.out, 'capture_log.json')}")


if __name__ == "__main__":
    main()
