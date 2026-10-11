#!/usr/bin/env python3
"""顶级交易员共性分析:Top25钱包的交易行为画像(品类/频率/持仓/盈亏),零花钱纯公开数据。"""
import urllib.request, json, time, datetime
from collections import defaultdict, Counter

UA = {"User-Agent": "Mozilla/5.0 (research)"}
DATA = "https://data-api.polymarket.com"

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def classify(title):
    t = (title or "").lower()
    if any(k in t for k in ["dota", "counter-strike", "league of legends", "lol:", "valorant",
                            "call of duty", "overwatch", "rocket league", "bo3", "bo5",
                            "map 2 winner", "game 2 winner", "esports"]):
        return "esports"
    if any(k in t for k in ["bitcoin", "btc", "ethereum", "eth ", "solana", "sol ", "crypto", "xrp", "doge", "bnb "]):
        return "crypto"
    if (" vs." in t or " vs " in t or "o/u" in t or "over/under" in t or
        any(k in t for k in ["nba", "nfl", "mlb", "nhl", "super bowl", "world series",
                             "premier league", "champions league", "world cup"])):
        return "sports"
    if any(k in t for k in ["trump", "election", "senate", "congress", "president", "white house",
                            "xi jinping", "putin", "fed ", "powell", "tariff"]):
        return "politics"
    if any(k in t for k in ["cpi", "gdp", "unemployment", "recession", "s&p", "nasdaq", "stock market",
                            "interest rate", "inflation"]):
        return "econ"
    return "other"

def med(xs):
    xs = sorted(xs)
    return xs[len(xs)//2] if xs else 0

def main():
    board = []
    for off in (0, 50, 100):
        board += get(f"{DATA}/v1/leaderboard?limit=50&offset={off}")
        time.sleep(0.3)
    top = sorted(board, key=lambda w: w.get("pnl", 0), reverse=True)[:25]
    print(f"分析 Top{len(top)} 钱包(按leaderboard pnl)")

    profiles = []
    agg_cat_vol = Counter(); agg_cat_trades = Counter()
    for w in top:
        addr = w["proxyWallet"]
        try:
            trades = get(f"{DATA}/trades?user={addr}&limit=1000")
        except Exception as e:
            print("skip", addr[:10], e); continue
        if not trades: continue
        trades.sort(key=lambda t: t["timestamp"])
        vols = [t["size"]*t["price"] for t in trades]
        days = (trades[-1]["timestamp"]-trades[0]["timestamp"])/86400 or 1
        buys = sum(1 for t in trades if t["side"]=="BUY")
        # FIFO配对算持仓时长与已实现
        lots = defaultdict(list)  # asset -> [(buy_ts, shares, price)]
        holds, realized, wins, n_closed = [], 0.0, 0, 0
        for t in trades:
            sh = t["size"]; px = t["price"]
            if t["side"] == "BUY":
                lots[t["asset"]].append([t["timestamp"], sh, px])
            else:
                need = sh
                while need > 1e-9 and lots[t["asset"]]:
                    bts, bsh, bpx = lots[t["asset"]][0]
                    take = min(need, bsh)
                    pnl = take*(px-bpx)
                    realized += pnl; n_closed += 1; wins += pnl > 0
                    holds.append((t["timestamp"]-bts)/3600)
                    bsh -= take; need -= take
                    if bsh <= 1e-9: lots[t["asset"]].pop(0)
                    else: lots[t["asset"]][0][1] = bsh
        cat_vol = Counter(); cat_n = Counter()
        for t in trades:
            c = classify(t.get("title"))
            cat_vol[c] += t["size"]*t["price"]; cat_n[c] += 1
            agg_cat_vol[c] += t["size"]*t["price"]; agg_cat_trades[c] += 1
        profiles.append({
            "name": w.get("userName") or addr[:12], "pnl": round(w.get("pnl",0)),
            "n": len(trades), "per_day": round(len(trades)/days,1),
            "med_usdc": round(med(vols),1), "buy_ratio": round(buys/len(trades),2),
            "med_hold_h": round(med(holds),1), "closed": n_closed,
            "wr": round(wins/n_closed,3) if n_closed else 0,
            "top_cat": cat_vol.most_common(1)[0][0] if cat_vol else "?",
            "cat_vol": {k: round(v) for k,v in cat_vol.most_common(3)},
        })
        time.sleep(0.35)

    print("\n=== 个体画像 ===")
    for p in profiles:
        print(f"{p['name'][:22]:22} pnl=${p['pnl']:>9,.0f} 笔数{p['n']:>4} {p['per_day']:>5}/天 "
              f"中位${p['med_usdc']:>7,.0f} 买比{p['buy_ratio']:.2f} 中位持仓{p['med_hold_h']:>7.1f}h "
              f"已平仓{p['closed']:>4} 胜率{p['wr']:.0%} 主力:{p['top_cat']}")

    print("\n=== 共性汇总(Top25加总) ===")
    tot_vol = sum(agg_cat_vol.values()); tot_n = sum(agg_cat_trades.values())
    for c in ["sports","esports","crypto","politics","econ","other"]:
        print(f"  {c:8} 成交额占比{agg_cat_vol[c]/tot_vol:6.1%} 笔数占比{agg_cat_trades[c]/tot_n:6.1%}")
    holds_all = [p["med_hold_h"] for p in profiles]
    print(f"  中位持仓时长(中位数): {med(holds_all):.1f}h | 日均笔数(中位数): {med([p['per_day'] for p in profiles]):.1f}")
    print(f"  单笔中位金额(中位数): ${med([p['med_usdc'] for p in profiles]):,.0f}")
    mm_like = sum(1 for p in profiles if 0.45 <= p["buy_ratio"] <= 0.55 and p["per_day"] > 20)
    print(f"  像做市商的(买卖比~1:1且>20笔/天): {mm_like}/{len(profiles)}")
    json.dump(profiles, open("/home/hatch/workspace/polymarket-copy-research/trader_profiles.json","w"), indent=1)
    print("存 trader_profiles.json")

main()
