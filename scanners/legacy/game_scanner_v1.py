#!/usr/bin/env python3
"""
跨市场赔率扫描器 v1 (纸面模式, $0)
Polymarket 体育盘价格 vs Pinnacle 基准价,找定价偏差。
- Polymarket 侧: 免费公开 API,已验证。
- Pinnacle 侧: 经 the-odds-api.com(免费 500 req/月,需 key,不传 key 则只跑 poly-only 自检)。
用法: python3 sports_odds_scanner.py [--odds-key KEY] [--threshold 0.04]
"""
import urllib.request, json, time, csv, sys, os, re
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (research paper)"}
ODDS_KEY = None
THRESH = 0.04
VENUE = "poly"  # poly | gate
# 各 venue 往返成本(限价单口径): gate = 0.8%平台费 x2(进出),poly 直连 ~0(只计协议费,限价 maker 为 0)
VENUE_COST_RT = {"poly": 0.0, "gate": 0.016}
OUT = "/home/hatch/workspace/polymarket-copy-research/scanner_log.csv"

for i, a in enumerate(sys.argv):
    if a == "--odds-key" and i + 1 < len(sys.argv):
        ODDS_KEY = sys.argv[i + 1]
    if a == "--threshold" and i + 1 < len(sys.argv):
        THRESH = float(sys.argv[i + 1])
    if a == "--venue" and i + 1 < len(sys.argv):
        VENUE = sys.argv[i + 1]
if not ODDS_KEY:
    # 从 ~/.the-odds-api.env 读 key
    try:
        with open(os.path.expanduser("~/.the-odds-api.env")) as f:
            for line in f:
                if line.startswith("THE_ODDS_API_KEY="):
                    ODDS_KEY = line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

# ---------- Polymarket 侧 ----------
def poly_sports_markets(limit=200):
    out, off = [], 0
    while len(out) < limit:
        d = get(f"https://gamma-api.polymarket.com/events?tag_slug=sports&limit=100&offset={off}&active=true&closed=false")
        if not d:
            break
        out.extend(d)
        off += 100
        time.sleep(0.3)
    mkts = []
    for e in out:
        for m in e.get("markets", []):
            try:
                px = json.loads(m.get("outcomePrices", "[]"))
                ids = json.loads(m.get("clobTokenIds", "[]"))
            except Exception:
                continue
            if len(px) != 2 or len(ids) != 2:
                continue
            try:
                py, pn = float(px[0]), float(px[1])
            except Exception:
                continue
            if not (0 < py < 1):
                continue
            mkts.append({
                "event": e.get("title", "")[:80],
                "question": m.get("question", "")[:100],
                "yes_px": py, "no_px": pn,
                "volume": float(m.get("volume") or 0),
                "slug": e.get("slug", ""),
            })
    return mkts

def norm(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_teams(question):
    # "X vs Y" / "X v Y" 结构才可能是比赛盘
    m = re.search(r"(.+?)\s+vs?\.?\s+(.+?)(\?|:|$)", question, re.I)
    if not m:
        return None
    a, b = norm(m.group(1)), norm(m.group(2))
    # 去掉常见的比赛后缀词
    a = re.sub(r"\b(game|match|winner|win)\b", "", a).strip()
    b = re.sub(r"\b(game|match|winner|win)\b", "", b).strip()
    return (a, b) if a and b else None

# ---------- Pinnacle 侧 (the-odds-api) ----------
ODDS_SPORTS = ["americanfootball_nfl", "basketball_nba", "soccer_epl",
               "soccer_spain_la_liga", "icehockey_nhl", "baseball_mlb",
               "tennis_atp_singles", "mma_mixed_martial_arts"]

def pinnacle_games():
    games = {}  # (norm_home, norm_away) -> pinnacle prob(home)
    for sp in ODDS_SPORTS:
        try:
            d = get(f"https://api.the-odds-api.com/v4/sports/{sp}/odds?apiKey={ODDS_KEY}"
                    f"&regions=us&markets=h2h&bookmakers=pinnacle&oddsFormat=decimal")
        except Exception as e:
            print(f"  odds-api {sp}: {str(e)[:60]}")
            continue
        for g in d:
            bm = [b for b in g.get("bookmakers", []) if b.get("key") == "pinnacle"]
            if not bm:
                continue
            outs = bm[0]["markets"][0]["outcomes"]
            if len(outs) != 2:
                continue
            # devig: 按比例归一
            p = [1 / o["price"] for o in outs]
            tot = sum(p)
            probs = [x / tot for x in p]
            # the-odds-api 给 home_team; outs[0] 通常对应 home
            home = norm(g.get("home_team", ""))
            away = norm(g.get("away_team", ""))
            # outcomes 里找哪边是 home
            for o, pr in zip(outs, probs):
                if norm(o["name"]) == home:
                    games[(home, away)] = pr
        time.sleep(0.5)
    return games

def match_score(poly_teams, pin_key):
    (pa, pb), (ha, aw) = poly_teams, pin_key
    def overlap(x, y):
        xs, ys = set(x.split()), set(y.split())
        return len(xs & ys) / max(len(xs | ys), 1)
    s1 = overlap(pa, ha) + overlap(pb, aw)
    s2 = overlap(pa, aw) + overlap(pb, ha)
    return max(s1, s2)

# ---------- 主流程 ----------
def main():
    print("== 拉 Polymarket 体育盘 ==")
    mkts = poly_sports_markets()
    print(f"双边市场: {len(mkts)} 个")
    matchables = [(m, extract_teams(m["question"])) for m in mkts]
    matchables = [(m, t) for m, t in matchables if t]
    print(f"其中比赛盘结构: {len(matchables)} 个")

    pin = {}
    if ODDS_KEY:
        print("== 拉 Pinnacle 赔率 ==")
        pin = pinnacle_games()
        print(f"Pinnacle 比赛: {len(pin)} 场")
    else:
        print("!! 未传 --odds-key,只跑 poly-only 自检模式")

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cost = VENUE_COST_RT.get(VENUE, 0.0)
    eff_thresh = THRESH + cost
    print(f"venue={VENUE} 往返成本={cost:.1%} 有效阈值={eff_thresh:.1%}")
    flags = []
    for m, teams in matchables:
        py = m["yes_px"]
        if not ODDS_KEY:
            # 自检: Yes+No 偏离 1 超过 2% 记一笔(理论上应≈1)
            if abs(py + m["no_px"] - 1) > 0.02:
                flags.append((ts, m["event"], m["question"], py, "", "", "INTERNAL_SKEW"))
            continue
        best, bk = 0, None
        for k in pin:
            s = match_score(teams, k)
            if s > best:
                best, bk = s, k
        if best < 1.0 or bk is None:
            continue
        pp = pin[bk]
        edge = py - pp  # poly 定价高于 pinnacle
        if abs(edge) >= eff_thresh:
            flags.append((ts, m["event"], m["question"], round(py, 4), round(pp, 4),
                          round(edge, 4), "POLY_HIGH" if edge > 0 else "POLY_LOW"))

    new = os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if not new:
            w.writerow(["ts", "event", "question", "poly_yes", "pinnacle", "edge", "signal"])
        w.writerows(flags)
    print(f"\n标记 {len(flags)} 条 (阈值 {THRESH:.0%}), 写入 scanner_log.csv")
    for r in flags[:15]:
        print(f"  [{r[6]}] edge={r[5]:+.3f} poly={r[3]:.3f} pin={r[4] or '-'} | {str(r[2])[:55]}")

main()
