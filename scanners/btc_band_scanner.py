#!/usr/bin/env python3
"""
BTC daily price-band mispricing scanner (paper-only, v1).
Compares Polymarket BTC daily band prices against a lognormal model
parameterized by Deribit options IV interpolated to the exact
settlement time (Binance BTC/USDT 1m close at 12:00 ET = 16:00 UTC).
Edge is computed net of the Gate-app execution stack
(0.8% Gate fee + ~3.3% Polymarket protocol fee per side, taker entry).
No orders are placed; output is CSV + JSON for paper validation.
"""
import json, math, re, sys, urllib.request, datetime

HEADERS = {"User-Agent": "Mozilla/5.0"}
DERIBIT = "https://www.deribit.com/api/v2/public"

def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def band_prob(S, sigma, T, lo, hi):
    """P(lo < S_T <= hi) under lognormal, zero drift."""
    if sigma <= 0 or T <= 0:
        return 0.0
    var = sigma * math.sqrt(T)
    def F(x):
        z = (math.log(x / S) + 0.5 * sigma**2 * T) / var
        return norm_cdf(z)
    hi_p = F(hi) if hi < float("inf") else 1.0
    lo_p = F(lo) if lo > 0 else 0.0
    return max(0.0, hi_p - lo_p)

def deribit_iv_atm(currency="BTC"):
    """ATM IV for the two Deribit daily expiries straddling a settlement."""
    idx = get(f"{DERIBIT}/get_index_price?index_name=btc_usd")["result"]["index_price"]
    strike = int(round(idx / 500.0)) * 500
    # daily expiry code for tomorrow & day after in Deribit format DDMMMYY
    today = datetime.datetime.now(datetime.timezone.utc).date()
    rows = []
    for d in (today + datetime.timedelta(days=1), today + datetime.timedelta(days=2)):
        code = d.strftime("%d%b%y").upper()
        for side in ("C", "P"):
            name = f"{currency}-{code}-{strike}-{side}"
            try:
                t = get(f"{DERIBIT}/ticker?instrument_name={name}")["result"]
                iv = t.get("mark_iv")
                if iv:
                    rows.append((d.isoformat(), code, iv))
            except Exception:
                pass
    return idx, strike, rows

def polymarket_bands(event_id):
    ev = get(f"https://gamma-api.polymarket.com/events?id={event_id}")[0]
    out = []
    for m in ev["markets"]:
        title = m["groupItemTitle"] or ""
        nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", title)]
        lo, hi = (nums[0], nums[1]) if len(nums) == 2 else ((0, nums[0]) if "<" in title else (nums[0], float("inf")))
        yes = float(json.loads(m["outcomePrices"])[0])
        out.append({
            "band": title, "lo": lo, "hi": hi,
            "yes_mid": (m.get("bestBid", 0) + m.get("bestAsk", 0)) / 2 or yes,
            "best_bid": m.get("bestBid"), "best_ask": m.get("bestAsk"),
            "liquidity": float(m.get("liquidityNum", 0) or 0),
        })
    return ev, out

def discover_tomorrow_event():
    """Find the id of the next BTC daily band market by ticker."""
    tomorrow = datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=1)
    ticker = f"bitcoin-price-on-{tomorrow.strftime('%B').lower()}-{tomorrow.day}-2026"
    try:
        res = get(f"https://gamma-api.polymarket.com/public-search?q={ticker.replace('-', '%20')}")
        for e in res.get("events", []):
            if e.get("slug") == ticker and e.get("active") and not e.get("closed"):
                return e["id"], e["title"]
    except Exception:
        pass
    return None, None

def main(event_id="1137831", fee_entry=0.041, signal_thresh=0.03):
    ev, bands = polymarket_bands(event_id)
    settle = datetime.datetime.fromisoformat(ev["endDate"].replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    T = (settle - now).total_seconds() / (365.0 * 24 * 3600)

    spot, strike, iv_rows = deribit_iv_atm()
    # interpolate IV (linear in variance) to settlement time
    def expiry_ts(iso):  # Deribit daily options expire 08:00 UTC
        return datetime.datetime.fromisoformat(iso).replace(
            hour=8, minute=0, tzinfo=datetime.timezone.utc).timestamp()
    now_ts = now.timestamp()
    iv_by_exp = {}
    for iso, code, iv in iv_rows:
        iv_by_exp.setdefault(iso, []).append(iv)
    exps = sorted((iso, sum(v) / len(v)) for iso, v in iv_by_exp.items())
    settle_ts = settle.timestamp()
    if len(exps) >= 2:
        (t1, s1), (t2, s2) = exps[0], exps[1]
        t1s, t2s = expiry_ts(t1), expiry_ts(t2)
        w = min(max((settle_ts - t1s) / (t2s - t1s), 0), 1)
        # Deribit mark_iv comes in percentage points; convert to decimals
        s1, s2 = s1 / 100.0, s2 / 100.0
        sigma = math.sqrt((1 - w) * s1**2 + w * s2**2)
        iv_note = f"var-interp {exps[0][0]} {s1:.3f} / {exps[1][0]} {s2:.3f}, w={w:.2f}"
    elif exps:
        sigma = exps[0][1] / 100.0; iv_note = f"single {exps[0][0]} {sigma:.3f}"
    else:
        print("no deribit iv", file=sys.stderr); return

    results = []
    for b in bands:
        p = band_prob(spot, sigma, T, b["lo"], b["hi"])
        mkt = b["yes_mid"]
        gross = (p / mkt - 1) if mkt > 0 else 0
        net = (p / (mkt * (1 + fee_entry)) - 1) if mkt > 0 else 0
        results.append({**b, "model_prob": p, "gross_edge": gross, "net_edge": net,
                        "signal": net > signal_thresh and b["best_ask"]})
    results.sort(key=lambda r: -r["net_edge"])

    ts = now.strftime("%Y%m%d_%H%M")
    with open(f"btc_band_scan_{ts}.csv", "w") as f:
        f.write("band,lo,hi,best_bid,best_ask,mid,model_prob,gross_edge,net_edge,signal\n")
        for r in results:
            f.write(f"{r['band']},{r['lo']},{r['hi']},{r['best_bid']},{r['best_ask']},"
                    f"{r['yes_mid']:.4f},{r['model_prob']:.4f},{r['gross_edge']:+.3f},"
                    f"{r['net_edge']:+.3f},{r['signal']}\n")
    summary = {
        "checked_at_utc": now.isoformat(), "event": ev["title"],
        "settle_utc": ev["endDate"], "hours_to_settle": round((settle - now).total_seconds() / 3600, 2),
        "deribit_spot": spot, "atm_strike": strike, "sigma": round(sigma, 4),
        "iv_note": iv_note, "fee_entry": fee_entry,
        "model_prob_sum": round(sum(r["model_prob"] for r in results), 4),
        "signals": [r["band"] for r in results if r["signal"]],
        "top3": [{k: (round(v, 4) if isinstance(v, float) else v)
                  for k, v in r.items() if k in ("band", "yes_mid", "model_prob", "gross_edge", "net_edge")}
                 for r in results[:3]],
    }
    with open(f"btc_band_scan_{ts}.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    return summary

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "tomorrow":
        eid, title = discover_tomorrow_event()
        if eid:
            print(f"discovered event {eid}: {title}", file=sys.stderr)
            args = [eid] + args[1:]
        else:
            print("could not discover tomorrow's BTC band event", file=sys.stderr)
            sys.exit(2)
    main(*args)
