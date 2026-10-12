"""
加密预测扫描器 (2026-10-11 建, 2026-10-12 修: BTC/ETH 波动率改走 Deribit IV —
此前 ETH 静默用 realized vol, 是 10-11/10-12 两次"稳"单被推翻的根因)

架构 (按用户要求):
  Tier 1 短期: 现在可买, 7天内结算 (每日 BTC/ETH 价格盘为主)
  Tier 2 长期: 7天以上结算 (季度/年度价格目标)

原理: Polymarket 加密价格盘 vs 期权隐含概率分布 (对数正态)
  - 现价: Gate 现货
  - 波动率: BTC/ETH 用 Deribit 期权链 IV (打法规则 #1: 期权市场为准; 每到期单独取最接近结算日的平均 IV);
           SOL 用 realized vol 降级 (Deribit 无链), CRYPTO_IV_OVERRIDE 环境变量可覆盖 (小数, 如 0.6 = 60%);
           每次结果带 vol_src 字段标注来源
  - 模型: P(above X) = 1 - N((ln(X/S)+0.5σ²T)/(σ√T))
  - edge = (model - market)/market; 净 edge 扣 Gate 1.6% 往返成本
  - 只报净 edge > 5% (加密盘滑点大, 阈值比体育高)

数据源: Polymarket 原生 gamma-api (endDate 干净), 不用 Gate MCP 索引 (掺死数据)

纸面研究, 不下单.
"""
import urllib.request, json, os, math, csv, re, datetime, statistics

UA = {"User-Agent": "Mozilla/5.0 research"}
GATE_FEE_ROUNDTRIP = 0.016
EDGE_THRESHOLD = 0.05
SHORT_DAYS = 7

def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def spot_price(symbol):
    t = get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol}_USDT")
    return float(t[0]["last"])

def deribit_iv(currency, hours_ahead):
    """取指定币种、到期日最接近目标 horizon 的 Deribit 期权平均 IV (小数). 失败返回 None"""
    try:
        d = get(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option", timeout=25)
        import datetime, re
        now = datetime.datetime.now(datetime.timezone.utc)
        from collections import defaultdict
        g = defaultdict(list)
        for b in d.get("result", []):
            parts = b.get("instrument_name", "").split("-")
            iv = b.get("mark_iv")
            if len(parts) >= 2 and iv and 5 < iv < 200:
                # 解析到期日: 12OCT26
                m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", parts[1])
                if not m: continue
                day, mon_s, yr = m.groups()
                mon = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                       "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}[mon_s]
                exp = datetime.datetime(2000+int(yr), mon, int(day), 8, 0, tzinfo=datetime.timezone.utc)
                exp_h = (exp - now).total_seconds()/3600
                if exp_h > hours_ahead * 0.5:  # 到期不能比目标早太多
                    g[parts[1]].append((iv/100, exp_h))
        if not g: return None
        # 选到期最接近 (且不早于) 目标的
        best_key = min(g.keys(), key=lambda k: abs(sum(x[1] for x in g[k])/len(g[k]) - hours_ahead))
        ivs = [x[0] for x in g[best_key]]
        return sum(ivs)/len(ivs)
    except Exception:
        return None

_IV_CACHE = {}
_DERIBIT_COINS = {"BTC": "BTC", "ETH": "ETH"}  # 有 Deribit 期权链的币种; SOL 等用 realized 降级

def get_vol(symbol, horizon_hours):
    """波动率: BTC/ETH 用 Deribit IV (打法规则 #1, 期权市场为准); 其他用 realized;
       环境变量 CRYPTO_IV_OVERRIDE 可覆盖 (小数, 如 0.6 = 60%).
       返回 (hourly_vol, vol_src, iv_pct_or_None)"""
    iv_override = os.environ.get("CRYPTO_IV_OVERRIDE")
    if iv_override:
        return float(iv_override) / math.sqrt(24 * 365), "override", float(iv_override)
    cur = _DERIBIT_COINS.get(symbol)
    if cur:
        if cur not in _IV_CACHE:
            _IV_CACHE[cur] = deribit_iv(cur, horizon_hours)
        iv = _IV_CACHE[cur]
        if iv:
            return iv / math.sqrt(24 * 365), f"deribit-{cur}", iv
        return realized_vol(symbol, horizon_hours), f"realized(downgraded:{cur}-IV-fail)", None
    return realized_vol(symbol, horizon_hours), "realized", None

def realized_vol(symbol, horizon_hours):
    # 自适应窗口: 2x 到期时间,  clamp 24h..720h
    window_h = max(24, min(720, int(horizon_hours * 2)))
    n = min(window_h, 720)
    kl = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={symbol}_USDT&interval=1h&limit={n}")
    closes = [float(k[2]) for k in kl]
    if len(closes) < 10:
        return 0.6 / math.sqrt(24 * 365)  # fallback 60% 年化
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    hv = statistics.stdev(rets)
    return hv  # hourly vol

def prob_above(spot, strike, hourly_vol, hours):
    if hours <= 0:
        return 1.0 if spot > strike else 0.0
    T = hours / 24 / 365
    sig = hourly_vol * math.sqrt(24 * 365)  # 年化
    if sig <= 0:
        return 1.0 if spot > strike else 0.0
    d = (math.log(strike / spot) + 0.5 * sig ** 2 * T) / (sig * math.sqrt(T))
    return 1 - ncdf(d)

COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

def scan():
    now = datetime.datetime.now(datetime.timezone.utc)
    results = []
    for cname, sym in COINS.items():
        try:
            spot = spot_price(sym)
        except Exception as e:
            print(f"{sym} 现价获取失败: {e}")
            continue
        queries = [f"{cname} price on", f"{cname} above",
                   f"what price will {cname} hit", f"when will {cname} hit",
                   f"{cname} all-time high", f"{cname} by December",
                   f"{cname} by end of", f"{sym} $"]
        for q in queries:
            try:
                d = get(f"https://gamma-api.polymarket.com/public-search?q={q.replace(' ', '+')}&limit=25")
            except Exception:
                continue
            for e in d.get("events", []):
                title = e.get("title", "")
                for m in e.get("markets", []):
                    qtext = m.get("question", "")
                    end = m.get("endDate", "")
                    try:
                        edt = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    hours = (edt - now).total_seconds() / 3600
                    if hours <= 0.5:
                        continue  # 已结算/马上结算
                    px = json.loads(m.get("outcomePrices", "[\"0\",\"1\"]"))
                    try:
                        market_yes = float(px[0])
                    except Exception:
                        continue
                    if market_yes <= 0.005 or market_yes >= 0.995:
                        continue  # 已定价, 无意义
                    # 只处理 above $X 结构
                    mm = re.search(r"above \$([\d,]+)", qtext, re.I)
                    if not mm:
                        continue
                    strike = float(mm.group(1).replace(",", ""))
                    hv, vol_src, iv_ann = get_vol(sym, hours)
                    model_p = prob_above(spot, strike, hv, hours)
                    # v2 方向与信号: 绝对百分点差 >= 5pp 才算信号
                    # 买Yes: model_p - market_yes >= 5pp; 买No: market_yes - model_p >= 5pp
                    pp_diff = model_p - market_yes  # 正=Yes被低估, 负=No被低估
                    direction = "买Yes" if pp_diff > 0 else "买No"
                    # 稳健性检查 v3: vol 上下浮动 30%, 绝对5pp差在两端都成立且方向不变
                    robust = True
                    for mult in [0.7, 1.3]:
                        hv2 = hv * mult
                        mp2 = prob_above(spot, strike, hv2, hours)
                        pp2 = mp2 - market_yes
                        d2 = "买Yes" if pp2 > 0 else "买No"
                        if d2 != direction or abs(pp2) < EDGE_THRESHOLD:
                            robust = False
                            break
                    edge = (model_p - market_yes) / market_yes if market_yes > 0 else 0
                    net = edge - GATE_FEE_ROUNDTRIP
                    tier = "short" if hours <= SHORT_DAYS * 24 else "long"
                    # 买No 的 edge 按 No 价格算
                    pay_price = market_yes
                    if direction == "买No":
                        no_price = 1 - market_yes
                        no_model = 1 - model_p
                        edge = (no_model - no_price) / no_price if no_price > 0 else 0
                        net = edge - GATE_FEE_ROUNDTRIP
                        pay_price = no_price
                    results.append({
                        "condition_id": m.get("conditionId", ""),
                        "market_id": m.get("id", ""),
                        "tier": tier, "coin": sym, "event": title[:60],
                        "question": qtext, "strike": strike,
                        "spot": round(spot, 1), "hours_left": round(hours, 1),
                        "pay_price": round(pay_price, 4),
                        "market_yes": round(market_yes, 4), "model_p": round(model_p, 4),
                        "direction": direction, "net_edge": round(net, 4),
                        "pp_diff": round(pp_diff, 4),
                        "signal": "YES" if (abs(pp_diff) >= EDGE_THRESHOLD and robust) else "",
                        "robust": robust,
                        "vol_src": vol_src,
                        "iv_ann": round(iv_ann, 4) if iv_ann else "",
                        "end": edt.strftime("%m-%d %H:%M"),
                    })
    # 去重 (同一 condition_id 可能被多个 query 搜到)
    seen, uniq = set(), []
    for r in results:
        k = r["condition_id"] or (r["question"], r["end"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    uniq.sort(key=lambda x: x["net_edge"], reverse=True)
    return uniq

def main():
    rows = scan()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.expanduser(f"~/workspace/polymarket-copy-research/crypto_scan_{ts}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader(); w.writerows(rows)
    print(f"CSV: {path}\n")
    for tier, label in [("short", "Tier1 短期 (≤7天)"), ("long", "Tier2 长期 (>7天)")]:
        tr = [r for r in rows if r["tier"] == tier]
        sig = [r for r in tr if r["signal"]]
        print(f"== {label}: {len(tr)} 盘口, {len(sig)} 信号 (净edge>5%) ==")
        for r in sig[:10]:
            print(f"  [{r['coin']}] {r['question'][:45]} | {r['direction']} @ {r['pay_price']:.2%} "
                  f"| 模型 {r['model_p']:.2%}(Yes) | 净edge {r['net_edge']:+.1%} | {r['hours_left']:.0f}h后结算")
        if not sig:
            print("  (无信号)")
        print()

if __name__ == "__main__":
    main()
