#!/usr/bin/env python3
"""结算更新器: 读取 crypto_signals_log.csv, 对已过期的信号用 Gate K线算真实结算价,
按 condition_id 精确匹配更新 outcome/pnl。幂等, 可重复跑。

outcome: WIN / LOSS (按 direction vs 实际价格)
pnl_usd: 以 alloc_usd 为本金, WIN=+alloc*(1-pay)/pay, LOSS=-alloc
注意: 结算价取 end_time 时刻的 Gate 现货价, 与 Polymarket 官方结算源可能有微小差异,
纸面验证用, 不做逐 tick 仲裁。
"""
import csv, os, json, urllib.request, datetime

LOG = os.path.expanduser("~/workspace/polymarket-copy-research/crypto_signals_log.csv")
SYM = {"BTC": "BTC_USDT", "ETH": "ETH_USDT", "SOL": "SOL_USDT"}

def gate_price_at(symbol, dt):
    """取 dt 时刻前后最近的 1h K线收盘价"""
    ts = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    url = (f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={symbol}"
           f"&interval=1h&from={ts-7200}&to={ts+7200}&limit=10")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if not data:
        return None
    # 取时间最接近 dt 的那根
    best = min(data, key=lambda c: abs(int(c[0]) - ts))
    return float(best[2])  # close

def main():
    if not os.path.exists(LOG):
        print("log 不存在"); return
    rows = list(csv.DictReader(open(LOG)))
    now = datetime.datetime.now()
    updated = 0
    for r in rows:
        if r.get("outcome"):  # 已结算, 跳过
            continue
        try:
            end = datetime.datetime.strptime(r["end_time"], "%m-%d %H:%M").replace(year=now.year)
        except Exception:
            continue
        if end > now:
            continue  # 未到期
        # 结算
        sym = SYM.get(r["coin"])
        if not sym or not r.get("strike"):
            r["notes"] = "缺strike/币种, 手动确认"
            continue
        try:
            px = gate_price_at(sym, end)
        except Exception as e:
            r["notes"] = f"价格获取失败: {e}"
            continue
        if px is None:
            r["notes"] = "无K线数据"
            continue
        strike = float(r["strike"])
        direction = r["direction"]
        won = (px > strike) if direction == "买Yes" else (px <= strike)
        r["outcome"] = "WIN" if won else "LOSS"
        try:
            alloc = float(r.get("alloc_usd") or 0)
            pay = float(r["pay_price"])
            r["pnl_usd"] = round(alloc * (1 - pay) / pay, 2) if won else round(-alloc, 2)
        except Exception:
            r["pnl_usd"] = ""
        r["notes"] = f"结算价{px}, strike{strike}"
        updated += 1
        print(f"  [{r['coin']}] {r['question'][:35]} → {r['outcome']} (结算价 {px})")

    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    print(f"\n更新 {updated} 条, log 共 {len(rows)} 条")

if __name__ == "__main__":
    main()
