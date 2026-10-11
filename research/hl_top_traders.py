#!/usr/bin/env python3
"""Hyperliquid 顶级交易员画像:盈利榜Top30 -> 持仓方向/杠杆/品种/近期成交活跃度。零花钱,公开API。"""
import urllib.request, json, time, datetime
from collections import Counter

UA = {"User-Agent": "Mozilla/5.0 (research)"}

def get_lb():
    req = urllib.request.Request("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
        headers={"User-Agent": "Mozilla/5.0 research"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())["leaderboardRows"]

def post(p, timeout=60):
    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def alltime(r):
    for w, m in r["windowPerformances"]:
        if w == "allTime":
            return float(m["pnl"]), float(m["roi"]), float(m["vlm"])
    return 0, 0, 0

def main():
    rows = get_lb()
    top = sorted(rows, key=lambda r: alltime(r)[0], reverse=True)[:30]
    print(f"榜单 {len(rows)} 人, 取 Top30")
    profiles = []
    agg_coin = Counter(); agg_side = Counter()
    for r in top:
        addr = r["ethAddress"]
        pnl, roi, vlm = alltime(r)
        try:
            cs = post({"type": "clearinghouseState", "user": addr})
            fills = post({"type": "userFills", "user": addr})
        except Exception as e:
            print("  skip", addr[:10], str(e)[:60]); continue
        poss = cs.get("assetPositions", [])
        ms = cs.get("marginSummary", {})
        acct = float(ms.get("accountValue") or 0)
        tot_ntl = sum(abs(float(p["position"]["positionValue"])) for p in poss)
        lev = tot_ntl / acct if acct > 0 else 0
        longs = sum(1 for p in poss if float(p["position"]["szi"]) > 0)
        shorts = sum(1 for p in poss if float(p["position"]["szi"]) < 0)
        coins = [p["position"]["coin"] for p in poss]
        for p in poss:
            q = p["position"]
            agg_coin[q["coin"]] += abs(float(q["positionValue"]))
            agg_side["long" if float(q["szi"]) > 0 else "short"] += 1
        # 近期成交:方向与品种
        now = time.time() * 1000
        recent = [f for f in fills if now - f.get("time", 0) < 30*86400*1000]
        fcoins = Counter(f.get("coin") for f in recent)
        profiles.append({
            "addr": addr[:12], "pnl": round(pnl), "roi": round(roi, 2),
            "vlm": round(vlm), "acct": round(acct),
            "n_pos": len(poss), "longs": longs, "shorts": shorts,
            "lev": round(lev, 1), "coins": coins[:6],
            "fills_30d": len(recent), "top_fcoin": fcoins.most_common(1)[0][0] if fcoins else "-",
        })
        time.sleep(0.4)
    print(f"\n== Top{len(profiles)} 画像 ==")
    for p in profiles[:20]:
        print(f"{p['addr']} pnl=${p['pnl']:>11,.0f} roi={p['roi']:>6.0%} acct=${p['acct']:>11,.0f} "
              f"持仓{p['n_pos']:>2} 多{p['longs']}/空{p['shorts']} 杠杆{p['lev']}x 30天成交{p['fills_30d']:>4} "
              f"币:{','.join(p['coins'][:4])}")
    print("\n== 共性 ==")
    print("持仓品种:", dict(agg_coin.most_common(8)))
    print("多空比:", dict(agg_side))
    lv = sorted(p["lev"] for p in profiles if p["lev"] > 0)
    print(f"杠杆中位数: {lv[len(lv)//2] if lv else 0}x")
    print(f"有持仓: {sum(1 for p in profiles if p['n_pos']>0)}/{len(profiles)}")
    json.dump(profiles, open("/home/hatch/workspace/polymarket-copy-research/hl_profiles.json", "w"), indent=1)
    print("存 hl_profiles.json")

main()
