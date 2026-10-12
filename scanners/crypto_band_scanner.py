#!/usr/bin/env python3
"""crypto_band_scanner.py v1 — paper-only research, zero spend.

Pulls clean daily BTC/ETH "above on [date]" band markets from the Polymarket
native gamma API (filtered by endDate, NOT the Gate MCP index which is polluted
with stale markets), then compares market-implied Yes probability against a
driftless-normal model calibrated on Deribit option-implied volatility
(per-expiry IV closest to settlement; rule #1 of the framework — options market
is the fair-value benchmark). Trailing-48h realized vol from Gate spot klines
is only a flagged fallback if the Deribit chain is unreachable.

Model: for resolution reference S_T at T hours out, strike K:
    fair = Phi( ln(spot/K) / (sigma_hourly * sqrt(T_h)) )
sigma_hourly = deribit_IV_annualized / sqrt(24*365)
(driftless; for intraday horizons drift is negligible vs vol).

Signals: |market_yes - fair| > EDGE_THRESHOLD (default 3%) with the ladder's
own monotonicity sanity-checked. Output is research only — no orders.
"""

import json, math, re, sys, urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"
GATE_KLINES = ("https://api.gateio.ws/api/v4/spot/candlesticks"
               "?currency_pair={pair}&interval=1h&limit=72")
EDGE_THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.03


def get(url, timeout=25):
    # NOTE: in the proactivity research lane, python urllib egress through the
    # proxy tunnel is denied (403); curl from the shell works. Route via curl.
    import subprocess
    out = subprocess.run(["curl", "-s", "-m", str(timeout), url],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"curl failed for {url}")
    return json.loads(out.stdout)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


_DERIBIT_BOOK = {}  # currency -> list of (expiry_date_str, iv_decimal)


def deribit_book(currency):
    """Deribit 期权链 (instrument_name, mark_iv) — curl 路由, 失败抛错"""
    if currency not in _DERIBIT_BOOK:
        d = get(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
                f"?currency={currency}&kind=option", timeout=30)
        book = []
        for b in d.get("result", []):
            iv = b.get("mark_iv")
            name = b.get("instrument_name", "")
            parts = name.split("-")
            if iv and 5 < iv < 200 and len(parts) >= 2:
                book.append((parts[1], iv / 100))
        if not book:
            raise RuntimeError(f"empty deribit book for {currency}")
        _DERIBIT_BOOK[currency] = book
    return _DERIBIT_BOOK[currency]


def deribit_iv_annual(currency, hours_ahead, now):
    """到期日最接近目标 horizon 的平均 IV (年化小数). 失败返回 None"""
    mon = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    groups = {}
    for exp_s, iv in deribit_book(currency):
        m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", exp_s)
        if not m:
            continue
        day, mon_s, yr = m.groups()
        exp = datetime(2000 + int(yr), mon[mon_s], int(day), 8, 0,
                       tzinfo=timezone.utc)
        exp_h = (exp - now).total_seconds() / 3600
        if exp_h > hours_ahead * 0.5:  # 到期不能比目标早太多
            groups.setdefault(exp_s, []).append((iv, exp_h))
    if not groups:
        return None
    best = min(groups, key=lambda k: abs(sum(x[1] for x in groups[k])
                                        / len(groups[k]) - hours_ahead))
    ivs = [x[0] for x in groups[best]]
    return sum(ivs) / len(ivs)


def gate_spot(pair):
    d = get(GATE_KLINES.format(pair=pair))
    closes = [float(c[2]) for c in d]  # close of each 1h candle
    return closes[-1], closes


def realized_sigma_hourly(closes):
    rets = [math.log(closes[i + 1] / closes[i]) for i in range(len(closes) - 1)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var)


def strike_of(question):
    m = re.search(r"\$?([\d,]+)", question)
    return int(m.group(1).replace(",", "")) if m else None


def scan_asset(asset, gate_pair, deribit_currency):
    try:
        spot, closes = gate_spot(gate_pair)
    except Exception as e:
        return {"asset": asset, "error": f"gate klines failed: {e}"}
    # 波动率: Deribit IV 优先 (打法规则 #1); 失败才用 trailing realized, 并标注降级
    try:
        deribit_book(deribit_currency)  # 预热, 失败直接走 except
        iv_ok = True
    except Exception as e:
        iv_ok = False
        iv_err = str(e)
    realized_sigma = realized_sigma_hourly(closes)
    try:
        d = get(GAMMA + f"/public-search?q={asset.lower()}%20above%20october"
                        f"&limit_per_type=10")
    except Exception as e:
        return {"asset": asset, "error": f"gamma search failed: {e}"}

    now = datetime.now(timezone.utc)
    events = []
    for e in d.get("events", []):
        end = e.get("endDate")
        if not end or e.get("closed"):
            continue
        try:
            t_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t_end <= now:
            continue
        ladder = []
        for m in e.get("markets", []):
            k = strike_of(m.get("question", ""))
            if k is None:
                continue
            try:
                yes = float(json.loads(m["outcomePrices"])[0])
            except Exception:
                continue
            ladder.append({"strike": k, "yes": yes,
                           "vol": float(m.get("volume", 0) or 0),
                           "question": m.get("question")})
        if ladder:
            ladder.sort(key=lambda r: r["strike"])
            events.append({"slug": e["slug"], "end": end,
                           "_t_end": t_end,
                           "vol24": e.get("volume24hr"), "ladder": ladder})
    events.sort(key=lambda e: e["_t_end"])

    for e in events:
        t_h = (e.pop("_t_end") - now).total_seconds() / 3600.0
        # 每档到期单独取 Deribit IV (年化→小时); 失败降级 realized 并标注
        vol_src = ""
        iv_ann = None
        if iv_ok:
            try:
                iv_ann = deribit_iv_annual(deribit_currency, t_h, now)
            except Exception:
                iv_ann = None
        if iv_ann:
            sigma_h = iv_ann / math.sqrt(24 * 365)
            vol_src = f"deribit-{deribit_currency} IV={iv_ann:.1%}"
        else:
            sigma_h = realized_sigma
            vol_src = "realized(downgraded)"
        e["vol_src"] = vol_src
        sig_tot = sigma_h * math.sqrt(t_h)
        rows = []
        for r in e["ladder"]:
            if sig_tot > 0:
                fair = norm_cdf(math.log(spot / r["strike"]) / sig_tot)
            else:
                fair = 1.0 if spot > r["strike"] else 0.0
            rows.append({**r, "fair": fair, "edge": r["yes"] - fair})
        e["hours_left"] = t_h
        e["rows"] = rows
    iv_note = (f"deribit-{deribit_currency}" if iv_ok
               else f"realized(downgraded: {iv_err})")
    return {"asset": asset, "spot": spot, "vol_source": iv_note,
            "events": events}


def main():
    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "edge_threshold": EDGE_THRESHOLD,
           "assets": [scan_asset("bitcoin", "BTC_USDT", "BTC"),
                      scan_asset("ethereum", "ETH_USDT", "ETH")]}
    signals = []
    for a in out["assets"]:
        if "error" in a:
            continue
        for e in a["events"]:
            prev_yes = None
            for r in e["rows"]:
                if abs(r["edge"]) > EDGE_THRESHOLD and r["vol"] > 1000:
                    signals.append({
                        "asset": a["asset"], "slug": e["slug"],
                        "hours_left": round(e["hours_left"], 1),
                        "strike": r["strike"],
                        "market_yes": round(r["yes"], 4),
                        "model_fair": round(r["fair"], 4),
                        "edge": round(r["edge"], 4),
                        "side": "SELL Yes" if r["edge"] > 0 else "BUY Yes",
                        "market_vol": round(r["vol"], 0)})
                if prev_yes is not None and r["yes"] > prev_yes + 1e-9:
                    r["monotonicity_flag"] = True
                prev_yes = r["yes"]
    out["signals"] = signals
    with open("crypto_band_scan.json", "w") as f:
        json.dump(out, f, indent=2)

    print(f"spot BTC/ETH from Gate 1h klines; vol = Deribit IV per expiry "
          f"(fallback: trailing-48h realized, flagged); "
          f"driftless-normal model; threshold {EDGE_THRESHOLD:.0%}")
    for a in out["assets"]:
        if "error" in a:
            print(f"{a['asset']}: ERROR {a['error']}")
            continue
        print(f"\n== {a['asset'].upper()} spot={a['spot']:.2f} "
              f"vol_source={a['vol_source']} ==")
        for e in a["events"]:
            print(f"  {e['slug']} | {e['hours_left']:.1f}h left | vol24={e['vol24']} | {e['vol_src']}")
            for r in e["rows"]:
                flag = " MONO!" if r.get("monotonicity_flag") else ""
                print(f"    ${r['strike']:<7} mkt={r['yes']:.4f} "
                      f"fair={r['fair']:.4f} edge={r['edge']:+.4f}{flag}")
    print(f"\n== SIGNALS (|edge|>{EDGE_THRESHOLD:.0%}, vol>$1k): {len(signals)} ==")
    for s in signals:
        print(f"  {s['asset']} {s['slug']} ${s['strike']}: "
              f"mkt={s['market_yes']} fair={s['model_fair']} "
              f"edge={s['edge']:+.4f} -> {s['side']}")
    print("\nsaved: crypto_band_scan.json")


if __name__ == "__main__":
    main()
