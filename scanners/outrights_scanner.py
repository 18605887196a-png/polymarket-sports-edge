#!/usr/bin/env python3
"""
冠军盘跨市场扫描器 v1 (纸面模式, $0)
Polymarket 冠军 outrights vs Pinnacle outrights, Gate 费率口径。

原理:
  Polymarket 冠军盘 (多市场事件,每个 outcome 一个 yes 价格)
  vs Pinnacle outrights (the-odds-api 的 outright 专用 sport key),overround 归一后比较。
  edge = (pinnacle_prob - poly_price) / poly_price
  net_edge = edge - 往返成本; 信号阈值 net_edge > 3%。

用法:
  python3 outrights_scanner.py [--threshold 0.03]
key 从 ~/.the-odds-api.env 读 (主备两个 key,自动选有余额的)。
每次运行约消耗 4 credits (4 个 outright sport)。

注意:
  - the-odds-api 的 outrights 只对部分 sport key 开放 (美职四大联盟+高尔夫),
    以 /v4/sports 返回的 has_outrights 为准;EPL/UCL/西甲/德甲没有 outright key。
  - Polymarket 上赛季/已结算事件必须排除 (closed=true 或价格全 0/1)。
  - 匹配只接受高置信度;歧义进人工复核,绝不硬配。
"""
import urllib.request, json, time, csv, sys, os, re, unicodedata
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (research paper)"}
THRESH = 0.03
GATE_ROUNDTRIP = 0.016  # Gate 平台费 0.8% x 2 (限价往返)
OUTDIR = "/home/hatch/workspace/polymarket-copy-research"

for i, a in enumerate(sys.argv):
    if a == "--threshold" and i + 1 < len(sys.argv):
        THRESH = float(sys.argv[i + 1])

# ---------- key ----------
KEYS = []
try:
    with open(os.path.expanduser("~/.the-odds-api.env")) as f:
        for line in f:
            line = line.strip()
            if line.startswith("THE_ODDS_API_KEY"):
                KEYS.append(line.split("=", 1)[1])
except FileNotFoundError:
    pass

def pick_key():
    """选 remaining 最多的 key"""
    best, best_rem = None, -1
    for k in KEYS:
        try:
            req = urllib.request.Request(
                f"https://api.the-odds-api.com/v4/sports?apiKey={k}", headers=UA)
            r = urllib.request.urlopen(req, timeout=20)
            rem = int(r.headers.get("x-requests-remaining") or 0)
            if rem > best_rem:
                best, best_rem = k, rem
        except Exception:
            continue
    if best is None:
        raise RuntimeError("no usable odds key")
    print(f"using key with remaining={best_rem}")
    return best, best_rem

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def odds_get(key, path):
    req = urllib.request.Request(
        f"https://api.the-odds-api.com/v4/{path}apiKey={key}", headers=UA)
    r = urllib.request.urlopen(req, timeout=30)
    return json.loads(r.read()), r.headers.get("x-requests-remaining")

# ---------- 配置: Polymarket 冠军盘事件 -> Pinnacle outright sport key ----------
# event id 来自 gamma-api 实地勘察 (2026-10-11);closed=false 的当前赛季事件。
# pinnacle=None 表示 the-odds-api 没有对应 outright key,只能记录、无法比价。
POLY_OUTRIGHTS = {
    478277: ("NBA: 2027 Champion", "basketball_nba_championship_winner"),
    202857: ("Pro Football: 2027 Champion (Super Bowl LXI)", "americanfootball_nfl_super_bowl_winner"),
    179312: ("MLB World Series Champion 2026", "baseball_mlb_world_series_winner"),
    588160: ("NHL: 2027 Champion", "icehockey_nhl_championship_winner"),
    659518: ("EPL: 2027 Champion", None),          # the-odds-api 无 EPL outright
    659671: ("UEFA Champions League: 2027 Champion", None),
    659548: ("LALIGA: 2027 Champion", None),
    681261: ("Bundesliga: 2027 Champion", None),
}

PLACEHOLDER = re.compile(r"team [a-z]$|another team|^other$", re.I)

def extract_team(question):
    m = re.search(r"will\s+(?:the\s+)?(.+?)\s+(win|be named|lift|claim)", question, re.I)
    if not m:
        return None
    return m.group(1).strip()

# ---------- 队名标准化 ----------
VARIANTS = {
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "bayern": "bayern munich",
    "dortmund": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "atletico": "atletico madrid",
    "sporting": "sporting cp",
}
SUFFIXES = [" fc", " sc", " cf", " afc", " ac"]

def norm_team(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suf in SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)].strip()
    if s.startswith("fc ") and len(s) > 5:
        s = s[3:].strip()
    return VARIANTS.get(s, s)

# ---------- Polymarket ----------
def poly_outright(eid):
    e = get(f"https://gamma-api.polymarket.com/events/{eid}")
    if e.get("closed"):
        return None, "closed"
    outcomes = []
    for m in e.get("markets", []):
        try:
            px = json.loads(m.get("outcomePrices", "[]"))
        except Exception:
            continue
        if len(px) != 2:
            continue
        try:
            py = float(px[0])
        except Exception:
            continue
        q = m.get("question", "")
        team = extract_team(q)
        vol = float(m.get("volume") or 0)
        if not team:
            continue
        # 排除占位市场 (Team A/B/C, Another Team: 无量、价格 0.5)
        if PLACEHOLDER.search(team) and vol == 0:
            continue
        if not (0.01 < py < 0.99):
            continue
        outcomes.append({"team": team, "norm": norm_team(team),
                         "px": py, "vol": vol, "q": q[:60]})
    return outcomes, "ok"

# ---------- Pinnacle ----------
def pinnacle_outright(key, sport_key):
    d, rem = odds_get(key, f"sports/{sport_key}/odds?regions=us&oddsFormat=decimal&bookmakers=pinnacle&")
    outcomes = []
    for e in d:
        for b in e.get("bookmakers", []):
            if b.get("key") != "pinnacle":
                continue
            for m in b.get("markets", []):
                if m["key"] not in ("outrights", "outrights_lay"):
                    continue
                for o in m.get("outcomes", []):
                    try:
                        price = float(o["price"])
                    except Exception:
                        continue
                    if price <= 1:
                        continue
                    outcomes.append({"team": o["name"], "norm": norm_team(o["name"]),
                                     "price": price})
    return outcomes, rem

# ---------- 主流程 ----------
def main():
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    print("== Polymarket 冠军盘 ==")
    poly = {}
    for eid, (title, pkey) in POLY_OUTRIGHTS.items():
        outcomes, status = poly_outright(eid)
        if outcomes is None:
            print(f"  [{status}] {title}")
            continue
        poly[eid] = {"title": title, "pkey": pkey, "outcomes": outcomes}
        print(f"  {title}: {len(outcomes)} 个 live outcomes")
        time.sleep(0.3)

    print("== Pinnacle outrights ==")
    key, rem0 = pick_key()
    pinn = {}
    for eid, info in poly.items():
        pkey = info["pkey"]
        if not pkey:
            print(f"  {info['title']}: the-odds-api 无 outright key,跳过")
            continue
        outcomes, rem = pinnacle_outright(key, pkey)
        if not outcomes:
            print(f"  {pkey}: Pinnacle 无报价 (无覆盖)")
            pinn[pkey] = []
            continue
        # devig: 去 overround
        inv = [1 / o["price"] for o in outcomes]
        tot = sum(inv)
        for o, iv in zip(outcomes, inv):
            o["prob"] = iv / tot
        pinn[pkey] = outcomes
        print(f"  {pkey}: {len(outcomes)} outcomes, overround={tot:.3f}, rem={rem}")
        time.sleep(0.5)

    print("== 匹配 + 边际计算 ==")
    rows, review = [], []
    for eid, info in poly.items():
        pkey = info["pkey"]
        if not pkey or not pinn.get(pkey):
            continue
        pmap = {o["norm"]: o for o in pinn[pkey]}
        for po in info["outcomes"]:
            mo = pmap.get(po["norm"])
            if not mo:
                review.append((info["title"], po["team"], po["px"]))
                continue
            raw_edge = (mo["prob"] - po["px"]) / po["px"]
            net_edge = raw_edge - GATE_ROUNDTRIP
            rows.append({
                "event": info["title"], "outcome": po["team"],
                "poly_price": round(po["px"], 4),
                "pinnacle_prob": round(mo["prob"], 4),
                "raw_edge": round(raw_edge, 4), "net_edge": round(net_edge, 4),
                "poly_volume": round(po["vol"]),
                "match_confidence": "high",
                "signal": "YES" if net_edge > THRESH else "",
            })
    rows.sort(key=lambda r: -r["net_edge"])

    out_csv = f"{OUTDIR}/outrights_scan_{ts}.csv"
    new = not os.path.exists(out_csv)
    with open(out_csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event", "outcome", "poly_price",
                                          "pinnacle_prob", "raw_edge", "net_edge",
                                          "poly_volume", "match_confidence", "signal"])
        if new:
            w.writeheader()
        w.writerows(rows)

    sigs = [r for r in rows if r["signal"]]
    print(f"\n配对 {len(rows)} 条, 未配对待人工复核 {len(review)} 条, "
          f"net>{THRESH:.0%} 信号 {len(sigs)} 条")
    print(f"CSV: {out_csv}")
    print("\n--- Top 净边际 ---")
    for r in rows[:15]:
        flag = " << SIGNAL" if r["signal"] else ""
        print(f"  net={r['net_edge']:+.3f} raw={r['raw_edge']:+.3f} "
              f"poly={r['poly_price']:.3f} pin={r['pinnacle_prob']:.3f} "
              f"vol={r['poly_volume']:,.0f} | {r['outcome']}{flag}")
    if review:
        print("\n--- 待人工复核 (未配对) ---")
        for t, team, px in review[:15]:
            print(f"  {team} px={px:.3f} | {t[:40]}")

main()
