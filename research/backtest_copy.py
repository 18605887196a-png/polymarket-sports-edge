#!/usr/bin/env python3
"""零花钱回测:用公开链上数据回测"跟单顶级钱包"策略。
流程:扫榜 -> 按已实现胜率筛交易员 -> 锁定2个 -> 模拟不同延迟跟单 -> 24h持有退出,扣2%双边taker费。
只读公开API,不下单不花钱。"""
import urllib.request, json, time, sys, datetime

UA = {"User-Agent": "Mozilla/5.0 (research backtest)"}
DATA, CLOB = "https://data-api.polymarket.com", "https://clob.polymarket.com"
FEE = 0.02
DELAYS = [0, 60, 300, 1800]
HOLD = 86400

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def price_at(history, ts):
    """history按t升序,取首个>=ts的点"""
    for pt in history:
        if pt["t"] >= ts:
            return pt["p"]
    return None

def main():
    now = int(time.time())
    print("== 1. 扫榜(150钱包) ==")
    board = []
    for off in (0, 50, 100):
        board += get(f"{DATA}/v1/leaderboard?limit=50&offset={off}")
        time.sleep(0.3)
    print(f"拿到 {len(board)} 个")

    print("== 2. 筛高胜率交易员(已实现胜率,positions口径) ==")
    screened = []
    for w in sorted(board, key=lambda x: x.get("pnl", 0), reverse=True)[:30]:
        addr = w["proxyWallet"]
        try:
            poss = get(f"{DATA}/positions?user={addr}&limit=300")
        except Exception as e:
            print(f"  {addr[:10]} positions失败 {e}"); continue
        closed = [p for p in poss if abs(p.get("realizedPnl") or 0) > 1e-9]
        if len(closed) < 15:
            continue
        wins = sum(1 for p in closed if p["realizedPnl"] > 0)
        wr = wins / len(closed)
        rpnl = sum(p["realizedPnl"] for p in closed)
        try:
            tr = get(f"{DATA}/trades?user={addr}&limit=5")
            last_ts = max(t.get("timestamp", 0) for t in tr) if tr else 0
        except Exception:
            last_ts = 0
        if wr >= 0.55 and rpnl > 0 and now - last_ts < 7 * 86400:
            screened.append({"wallet": addr, "name": w.get("userName") or addr[:12],
                             "win_rate": round(wr, 3), "closed": len(closed),
                             "realized": round(rpnl, 1), "lb_pnl": round(w.get("pnl", 0), 1)})
        time.sleep(0.35)
    screened.sort(key=lambda s: (s["win_rate"], s["realized"]), reverse=True)
    print(f"通过筛选: {len(screened)} 个")
    for s in screened[:8]:
        print(f"  {s['name']}: 胜率{s['win_rate']:.1%} 已平仓{s['closed']} 已实现${s['realized']:,.0f}")
    top2 = screened[:2]
    if len(top2) < 2:
        print("通过筛选不足2个,放宽到胜率>=0.5"); 
        # fallback: 已在上面过滤,此处直接报错
        sys.exit(1)

    print("\n== 3. 回测跟单(近14天BUY,延迟d跟进,持有24h退出) ==")
    results = {}
    for s in top2:
        addr = s["wallet"]
        trades = get(f"{DATA}/trades?user={addr}&limit=1000")
        buys = [t for t in trades if t.get("side") == "BUY"
                and now - t.get("timestamp", 0) < 14 * 86400][:60]
        print(f"\n--- {s['name']} 近14天BUY {len(buys)}笔,胜率{s['win_rate']:.1%} ---")
        # 缓存每个token的价格历史(覆盖回测窗口)
        hist_cache, sigs = {}, []
        for t in buys:
            tok = t.get("asset"); ts = t.get("timestamp")
            if tok not in hist_cache:
                try:
                    h = get(f"{CLOB}/prices-history?market={tok}&startTs={ts-3600}&endTs={now}&fidelity=15")
                    hist_cache[tok] = sorted(h.get("history", []), key=lambda x: x["t"])
                except Exception:
                    hist_cache[tok] = []
                time.sleep(0.25)
            hist = hist_cache[tok]
            if not hist:
                continue
            exits = price_at(hist, ts + HOLD) or hist[-1]["p"]
            row = {"ts": ts, "exits": exits, "entries": {}}
            ok = True
            for d in DELAYS:
                e = price_at(hist, ts + d)
                if not e or e <= 0:
                    ok = False; break
                row["entries"][d] = e
            if ok:
                sigs.append(row)
        print(f"  有效信号 {len(sigs)}/{len(buys)}")
        # 组合模拟: $200本金,每笔固定$20,按时间顺序
        per_delay = {}
        for d in DELAYS:
            cap, rets, wins = 200.0, [], 0
            for sg in sorted(sigs, key=lambda x: x["ts"]):
                e, x = sg["entries"][d], sg["exits"]
                net = (x * (1 - FEE)) / (e * (1 + FEE)) - 1
                size = min(20.0, cap * 0.25)
                cap += size * net
                rets.append(net)
                wins += 1 if net > 0 else 0
                if cap <= 0: break
            per_delay[d] = {"n": len(rets),
                            "win_rate": round(wins / len(rets), 3) if rets else 0,
                            "avg_net": round(sum(rets) / len(rets), 4) if rets else 0,
                            "final_cap": round(cap, 2),
                            "multiple": round(cap / 200, 2)}
            r = per_delay[d]
            print(f"  延迟{d:>4}s: n={r['n']} 胜率{r['win_rate']:.1%} 笔均{r['avg_net']:+.2%} "
                  f"$200→${r['final_cap']:.0f} ({r['multiple']}x)")
        results[s["name"]] = {"profile": s, "per_delay": per_delay}

    out = {"ts": datetime.datetime.now().isoformat(), "fee": FEE, "hold_h": 24,
           "delays": DELAYS, "wallets": results}
    with open("/home/hatch/workspace/polymarket-copy-research/backtest.json", "w") as f:
        json.dump(out, f, indent=1)
    print("\n回测存 backtest.json")

main()
