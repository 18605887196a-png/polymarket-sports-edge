#!/usr/bin/env python3
"""Paper-mode prototype: scan top Polymarket wallets -> screen -> lock top 2 -> simulate copy fills.
No real money, no order placement. Read-only public APIs only."""
import urllib.request, urllib.parse, json, time, sys, datetime

UA = {"User-Agent": "Mozilla/5.0 (research prototype)"}
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def ts_now():
    return int(time.time())

def main():
    n_scan = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print(f"== Step 1: 扫描 leaderboard 前 {n_scan} 个钱包 ==")
    board = get(f"{DATA}/v1/leaderboard?limit={n_scan}")
    print(f"拿到 {len(board)} 个钱包")
    # 初筛: pnl>0 且有成交量
    cands = [w for w in board if (w.get("pnl") or 0) > 0 and (w.get("vol") or 0) > 1000]
    cands.sort(key=lambda w: w["pnl"], reverse=True)
    print(f"初筛 pnl>0 & vol>1000: {len(cands)} 个")

    print("== Step 2: 对 Top 25 做交易行为深查 ==")
    scored = []
    for w in cands[:25]:
        addr = w["proxyWallet"]
        try:
            trades = get(f"{DATA}/trades?user={addr}&limit=200")
        except Exception as e:
            print(f"  {addr[:10]}.. trades 拉取失败: {e}"); continue
        if not trades:
            continue
        now = ts_now()
        last_ts = max(t.get("timestamp", 0) for t in trades)
        days_active = (now - min(t.get("timestamp", now) for t in trades)) / 86400 or 1
        uniq_mkts = len({t.get("conditionId") for t in trades})
        buys = sum(1 for t in trades if t.get("side") == "BUY")
        # 近 7 天交易数(活跃度)
        recent = sum(1 for t in trades if now - t.get("timestamp", 0) < 7 * 86400)
        # 用 leaderboard 的 pnl/vol 做 profit factor 代理
        pf = w["pnl"] / w["vol"] if w["vol"] else 0
        score = pf * 100 + min(recent / 10, 5) + min(len(trades) / 40, 5)
        scored.append({
            "wallet": addr, "name": w.get("userName"), "rank": w.get("rank"),
            "pnl": round(w["pnl"], 2), "vol": round(w["vol"], 2),
            "profit_factor_pct": round(pf * 100, 3),
            "n_trades_200": len(trades), "uniq_markets": uniq_mkts,
            "buy_ratio": round(buys / len(trades), 2),
            "last_trade": datetime.datetime.fromtimestamp(last_ts).strftime("%m-%d %H:%M"),
            "days_active": round(days_active, 1), "recent_7d": recent,
            "score": round(score, 2),
        })
        time.sleep(0.4)
    scored.sort(key=lambda s: s["score"], reverse=True)
    print(f"深查完成 {len(scored)} 个, Top 10:")
    for s in scored[:10]:
        print(f"  #{s['rank']:>3} {s['name'] or s['wallet'][:12]} pnl=${s['pnl']:>10,.0f} "
              f"pf={s['profit_factor_pct']}% trades={s['n_trades_200']} recent7d={s['recent_7d']} "
              f"last={s['last_trade']} score={s['score']}")

    top2 = scored[:2]
    print("\n== Step 3: 锁定 2 个顶级账户,生成跟单信号(纸面模拟) ==")
    TAKER_FEE = 0.02  # 保守估计 2% taker 成本
    for s in top2:
        addr = s["wallet"]
        print(f"\n--- {s['name'] or addr} (pnl=${s['pnl']:,.0f}) ---")
        trades = get(f"{DATA}/trades?user={addr}&limit=30")
        buys = [t for t in trades if t.get("side") == "BUY"][:6]
        for t in buys:
            cid = t.get("conditionId"); asset = t.get("asset")
            price_paid = t.get("price"); size = t.get("size")
            # 市场是否仍活跃
            try:
                mkts = get(f"{GAMMA}/markets?condition_id={cid}")
                active = bool(mkts) and not mkts[0].get("closed", True)
                q = mkts[0].get("question", "")[:60] if mkts else "?"
            except Exception:
                active, q = None, "?"
            # 当前盘口
            cur_ask = None
            if active and asset:
                try:
                    book = get(f"{CLOB}/book?token_id={asset}")
                    asks = book.get("asks") or []
                    cur_ask = float(asks[0]["price"]) if asks else None
                except Exception:
                    pass
            if active and cur_ask:
                slip = (cur_ask - price_paid) / price_paid * 100 if price_paid else 0
                eff_cost = cur_ask * (1 + TAKER_FEE)
                breakeven = eff_cost  # 需要的胜率(以1.0赔付计)
                print(f"  BUY {q} | 大佬成交@{price_paid:.3f} 现价ask@{cur_ask:.3f} "
                      f"滑点{slip:+.1f}% 含费成本{eff_cost:.3f} 需胜率>{breakeven:.1%}")
            else:
                print(f"  BUY {q} | 大佬成交@{price_paid:.3f} 市场已关闭/无盘口(跟不了)")
            time.sleep(0.3)

    with open("/home/hatch/workspace/polymarket-copy-research/screen_result.json", "w") as f:
        json.dump({"scanned": len(board), "screened": scored, "top2": [s["wallet"] for s in top2],
                   "ts": datetime.datetime.now().isoformat()}, f, indent=1)
    print("\n结果已存 screen_result.json")

main()
