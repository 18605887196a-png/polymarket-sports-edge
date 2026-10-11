#!/usr/bin/env python3
"""Lighter 顶级交易员画像: PnL榜Top30 -> 逐账户持仓/杠杆/多空/品种偏好。零花钱,公开API。"""
import urllib.request, json, time
from collections import Counter

UA = {"User-Agent": "Mozilla/5.0 (research)"}
BASE = "https://mainnet.zklighter.elliot.ai/api/v1"

def get(path, timeout=30):
    req = urllib.request.Request(BASE + path, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def main():
    print("== 拉 PnL 榜 ==")
    lb = get("/pnlLeaderboard?time_window=all&sort_by=pnl&sort_dir=desc&limit=50&offset=0")
    rows = lb.get("entries", [])
    print(f"榜单共 {lb.get('total')} 个, 取 Top{len(rows)}")
    profiles = []
    agg_coin_vol = Counter(); agg_dir = Counter()
    for r in rows:
        addr = r.get("l1_address")
        try:
            d = get(f"/account?by=l1_address&value={addr}")
        except Exception as e:
            print("  skip", addr[:10], str(e)[:50]); continue
        accs = d.get("accounts", [])
        if not accs: continue
        a = accs[0]
        poss = a.get("positions", []) or []
        # 聚合该地址所有子账户? 先取主账户;positions 结构: sign(1/-1), position, avg entry, value, uPnl, rPnl
        tot_val = sum(abs(float(p.get("position_value") or p.get("value") or 0)) for p in poss)
        coll = float(a.get("collateral") or 0)
        lev = tot_val / coll if coll > 0 else 0
        longs = sum(1 for p in poss if str(p.get("sign")) == "1")
        shorts = sum(1 for p in poss if str(p.get("sign")) == "-1")
        coins = [str(p.get("market") or p.get("symbol") or p.get("coin") or "?") for p in poss]
        for p in poss:
            c = str(p.get("market") or p.get("symbol") or p.get("coin") or "?")
            agg_coin_vol[c] += abs(float(p.get("position_value") or p.get("value") or 0))
            agg_dir["long" if str(p.get("sign")) == "1" else "short"] += 1
        rpnl = sum(float(p.get("realized_pnl") or p.get("realizedPnl") or 0) for p in poss)
        profiles.append({
            "addr": addr[:12], "lb_pnl": round(float(r.get("pnl") or 0)),
            "lb_roi": round(float(r.get("roi") or 0), 2),
            "lb_vol": round(float(r.get("volume") or 0)),
            "acct_val": round(float(r.get("account_value") or 0)),
            "collateral": round(float(a.get("collateral") or 0)),
            "n_pos": len(poss), "longs": longs, "shorts": shorts,
            "lev": round(lev, 1), "coins": coins[:6],
            "orders": a.get("total_order_count"),
        })
        time.sleep(0.3)
    print(f"\n== Top{len(profiles)} 画像 ==")
    for p in profiles[:20]:
        print(f"{p['addr']} pnl=${p['lb_pnl']:>10,.0f} roi={p['lb_roi']:>7.0%} vol=${p['lb_vol']:>11,.0f} "
              f"抵押${p['collateral']:>9,.0f} 持仓{p['n_pos']:>2} 多{p['longs']}/空{p['shorts']} 杠杆~{p['lev']}x "
              f"币:{','.join(p['coins'][:4])}")
    print("\n== 共性 ==")
    print("持仓品种(按名义价值):", dict(agg_coin_vol.most_common(8)))
    print("多空比:", dict(agg_dir))
    levs = [p["lev"] for p in profiles if p["lev"] < 100]
    print(f"平均杠杆(中位数): {sorted(levs)[len(levs)//2] if levs else 0}x")
    print(f"有持仓vs空仓: {sum(1 for p in profiles if p['n_pos']>0)}/{len(profiles)}")
    json.dump(profiles, open("/home/hatch/workspace/polymarket-copy-research/lighter_profiles.json", "w"), indent=1)
    print("存 lighter_profiles.json")

main()
