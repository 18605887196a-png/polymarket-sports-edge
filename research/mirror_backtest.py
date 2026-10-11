#!/usr/bin/env python3
"""忠实复刻故事里的跟单机器人 v2:大佬买我就买,大佬卖我就按比例卖。
$200本金,官方手续费公式,不同延迟。零花钱,纯公开数据。
优化:价格历史窗口收窄到交易前后数小时,跨延迟共享缓存。"""
import urllib.request, json, time
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0 (research)"}
DATA, CLOB = "https://data-api.polymarket.com", "https://clob.polymarket.com"

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def classify(title):
    t = (title or "").lower()
    if any(k in t for k in ["bitcoin","btc","ethereum","eth ","solana","sol ","crypto","xrp","doge"]): return "crypto"
    if any(k in t for k in ["nba","nfl","mlb","nhl","super bowl"," vs."," vs "]): return "sports"
    if any(k in t for k in ["trump","election","senate","president","xi jinping","putin"]): return "politics"
    return "other"

FEE_RATE = {"crypto":0.07,"sports":0.05,"politics":0.04,"other":0.05}
def fee(shares, price, title):
    r = FEE_RATE[classify(title)]
    return shares*r*price*(1-price)

def price_at(hist, ts):
    for pt in hist:
        if pt["t"] >= ts: return pt["p"]
    return None

def build_cache(wallet, days=14):
    now = int(time.time())
    trades = get(f"{DATA}/trades?user={wallet}&limit=1000")
    trades = [t for t in trades if now - t["timestamp"] < days*86400]
    trades.sort(key=lambda t: t["timestamp"])
    cache, seen = {}, 0
    for t in trades:
        a = t["asset"]
        if a in cache: continue
        ts = t["timestamp"]
        try:
            h = get(f"{CLOB}/prices-history?market={a}&startTs={ts-1800}&endTs={ts+7200}&fidelity=5")
            cache[a] = sorted(h.get("history",[]), key=lambda x: x["t"])
        except Exception:
            cache[a] = []
        seen += 1
        if seen % 40 == 0: print(f"    价格历史 {seen} tokens...", flush=True)
        time.sleep(0.15)
    return trades, cache, now

def mirror(trades, cache, delay):
    cap = 200.0
    leader_net = defaultdict(float)
    bot_pos = defaultdict(lambda: [0.0, 0.0])
    n_mirror = n_skip = 0; fees_paid = 0.0; realized = 0.0
    for t in trades:
        a, ts = t["asset"], t["timestamp"]
        h = cache.get(a) or []
        if not h: n_skip += 1; continue
        px = price_at(h, ts+delay)
        if not px or px <= 0 or px >= 1: n_skip += 1; continue
        title = t.get("title","")
        if t["side"] == "BUY":
            leader_net[a] += t["size"]
            usd = min(25.0, cap*0.25)
            if usd < 1 or cap <= 0: continue
            sh = usd/px
            f = fee(sh, px, title); fees_paid += f
            cap -= usd + f
            s, c = bot_pos[a]; bot_pos[a] = [s+sh, c+usd]
            n_mirror += 1
        else:
            before = leader_net[a]
            if before <= 1e-9: n_skip += 1; continue
            frac = min(t["size"]/before, 1.0)
            leader_net[a] = max(before - t["size"], 0)
            s, c = bot_pos[a]
            if s <= 1e-9: n_skip += 1; continue
            sell_sh = s*frac
            proceeds = sell_sh*px
            f = fee(sell_sh, px, title); fees_paid += f
            cost_part = c*frac
            realized += (proceeds - f) - cost_part
            cap += proceeds - f
            bot_pos[a] = [s-sell_sh, c-cost_part]
            n_mirror += 1
    mtm_val = 0.0
    for a,(s,c) in bot_pos.items():
        if s > 1e-9:
            h = cache.get(a) or []
            last = h[-1]["p"] if h else 0
            mtm_val += s*last
    total = cap + mtm_val
    return {"mirror": n_mirror, "skip": n_skip, "fees": round(fees_paid,2),
            "realized": round(realized,2), "final": round(total,2),
            "multiple": round(total/200,2)}

def main():
    board = get(f"{DATA}/v1/leaderboard?limit=50")
    top2 = sorted(board, key=lambda w: w.get("pnl",0), reverse=True)[:2]
    out = {}
    for w in top2:
        addr, name = w["proxyWallet"], w.get("userName") or w["proxyWallet"][:12]
        print(f"=== 镜像跟单 {name} (榜单pnl=${w.get('pnl',0):,.0f}) ===", flush=True)
        trades, cache, now = build_cache(addr)
        print(f"  近14天 {len(trades)} 笔, {len(cache)} tokens", flush=True)
        res = {}
        for d in [0, 60, 300]:
            r = mirror(trades, cache, d)
            res[d] = r
            print(f"  延迟{d:>3}s: 镜像{r['mirror']}笔 跳过{r['skip']}笔 手续费${r['fees']:.0f} "
                  f"已实现${r['realized']:+.0f} $200→${r['final']:.0f} ({r['multiple']}x)", flush=True)
        out[name] = res
    json.dump(out, open("/home/hatch/workspace/polymarket-copy-research/mirror_backtest.json","w"), indent=1)
    print("存 mirror_backtest.json", flush=True)

main()
