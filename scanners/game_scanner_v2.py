#!/usr/bin/env python3
"""
跨市场赔率扫描器 v2 (纸面模式, $0)
专注两类无歧义市场:
  DRAW   "Will X vs Y end in a draw?"  Yes=平局  vs Pinnacle 3-way 平局概率
  TOTALS "X vs Y: O/U N.5"             Yes=大球  vs Pinnacle 同盘口大球概率
用法: python3 sports_odds_scanner_v2.py [--venue gate] [--threshold 0.04]
key 从 ~/.the-odds-api.env 读 (THE_ODDS_API_KEY, 备用的 _BACKUP)。
"""
import urllib.request, json, time, csv, sys, os, re
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (research paper)"}
THRESH = 0.04
VENUE = "poly"
VENUE_COST_RT = {"poly": 0.0, "gate": 0.016}
OUT = "/home/hatch/workspace/polymarket-copy-research/scanner_log.csv"
KEYS = []

for i, a in enumerate(sys.argv):
    if a == "--threshold" and i + 1 < len(sys.argv):
        THRESH = float(sys.argv[i + 1])
    if a == "--venue" and i + 1 < len(sys.argv):
        VENUE = sys.argv[i + 1]
try:
    with open(os.path.expanduser("~/.the-odds-api.env")) as f:
        for line in f:
            line = line.strip()
            if line.startswith("THE_ODDS_API_KEY"):
                KEYS.append(line.split("=", 1)[1])
except FileNotFoundError:
    pass
KEYI = [0]

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def odds_get(path):
    # key 轮换(主备)
    for _ in range(len(KEYS)):
        k = KEYS[KEYI[0] % len(KEYS)]
        try:
            return get(f"https://api.the-odds-api.com/v4/{path}apiKey={k}")
        except Exception as e:
            if "401" in str(e):
                KEYI[0] += 1
                continue
            raise
    raise RuntimeError("odds keys exhausted")

# ---------- Polymarket ----------
def poly_markets(limit=1500):
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
            except Exception:
                continue
            if len(px) != 2:
                continue
            try:
                py = float(px[0])
            except Exception:
                continue
            if not (0.02 < py < 0.98):
                continue
            q = m.get("question", "")
            kind, info = classify(q)
            if not kind:
                continue
            # 过滤已开赛: gameStartTime 已过的不做(赔率不可比)
            try:
                gst = m.get("gameStartTime") or ""
                if gst and gst[:16] < datetime.now(timezone.utc).isoformat(timespec="minutes")[:16]:
                    continue
            except Exception:
                pass
            mkts.append({"event": e.get("title", "")[:80], "question": q[:100],
                         "yes_px": py, "volume": float(m.get("volume") or 0),
                         "kind": kind, "info": info})
    return mkts

def norm(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

PROPS_WORDS = ["corner", "card", "shot", "foul", "offside", "throw", "possession",
               "goal kick", "penalt", "hat-trick", "hat trick", "assist", "save",
               "tackle", "pass", " VAR ".lower().strip()]

def classify(q):
    ql = q.lower()
    # 排除道具盘:角球/牌/射门/犯规等
    if any(w in ql for w in PROPS_WORDS):
        return None, None
    # DRAW
    m = re.search(r"will\s+(.+?)\s+vs\.?\s+(.+?)\s+end in a draw\??", q, re.I)
    if m:
        return "DRAW", {"a": norm(m.group(1)), "b": norm(m.group(2))}
    # TOTALS: 只接受 "X vs Y: O/U N.5" (冒号后直接 O/U)——排除 "X vs Y: TeamName O/U" 球队大小球
    m = re.search(r"(.+?)\s+vs\.?\s+(.+?):\s*O/U\s+(\d+\.5)", q, re.I)
    if m:
        return "TOTALS", {"a": norm(m.group(1)), "b": norm(m.group(2)), "line": float(m.group(3))}
    return None, None

# ---------- Pinnacle ----------
SOCCER = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
          "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_usa_mls",
          "soccer_uefa_champs_league", "soccer_brazil_campeonato",
          "soccer_mexico_ligamx", "soccer_japan_j_league",
          "soccer_argentina_primera_division", "soccer_australia_aleague",
          "soccer_austria_bundesliga", "soccer_belgium_first_div",
          "soccer_brazil_serie_b", "soccer_chile_campeonato",
          "soccer_china_superleague", "soccer_conmebol_copa_libertadores",
          "soccer_conmebol_copa_sudamericana", "soccer_denmark_superliga",
          "soccer_efl_champ", "soccer_england_efl_cup", "soccer_england_league1",
          "soccer_england_league2", "soccer_finland_veikkausliiga",
          "soccer_france_ligue_two", "soccer_germany_bundesliga2",
          "soccer_germany_dfb_pokal", "soccer_germany_liga3",
          "soccer_greece_super_league", "soccer_italy_serie_b",
          "soccer_korea_kleague1", "soccer_league_of_ireland",
          "soccer_netherlands_eredivisie", "soccer_norway_eliteserien",
          "soccer_poland_ekstraklasa", "soccer_portugal_primeira_liga",
          "soccer_russia_premier_league", "soccer_saudi_arabia_pro_league",
          "soccer_spain_segunda_division", "soccer_spl",
          "soccer_sweden_allsvenskan", "soccer_sweden_superettan",
          "soccer_switzerland_superleague", "soccer_turkey_super_league",
          "soccer_uefa_europa_conference_league", "soccer_uefa_europa_league",
          "soccer_uefa_nations_league",
          "cricket_international_t20", "cricket_odi",
          "americanfootball_nfl", "basketball_nba", "icehockey_nhl",
          "baseball_mlb", "mma_mixed_martial_arts"]

def pinnacle_soccer():
    games = []
    for sp in SOCCER:
        try:
            d = odds_get(f"sports/{sp}/odds?regions=us&markets=h2h,totals&bookmakers=pinnacle&oddsFormat=decimal&")
        except Exception as e:
            print(f"  {sp}: {str(e)[:50]}")
            continue
        for g in d:
            bm = [b for b in g.get("bookmakers", []) if b.get("key") == "pinnacle"]
            if not bm:
                continue
            rec = {"home": norm(g.get("home_team", "")), "away": norm(g.get("away_team", "")),
                   "draw": None, "totals": {}}
            for mk in bm[0]["markets"]:
                if mk["key"] == "h2h" and len(mk["outcomes"]) == 3:
                    ps = [1 / o["price"] for o in mk["outcomes"]]
                    tot = sum(ps)
                    for o, p in zip(mk["outcomes"], ps):
                        if o["name"].lower() == "draw":
                            rec["draw"] = p / tot
                elif mk["key"] == "totals":
                    for o in mk["outcomes"]:
                        if o["name"].lower() == "over":
                            # devig 双边
                            break
                    ov = [o for o in mk["outcomes"] if o["name"].lower() == "over"]
                    un = [o for o in mk["outcomes"] if o["name"].lower() == "under"]
                    if ov and un and ov[0].get("point") == un[0].get("point"):
                        po, pu = 1 / ov[0]["price"], 1 / un[0]["price"]
                        rec["totals"][float(ov[0]["point"])] = po / (po + pu)
            games.append(rec)
        time.sleep(0.4)
    return games

def team_overlap(x, y):
    xs, ys = set(x.split()), set(y.split())
    if not xs or not ys:
        return 0
    return len(xs & ys) / max(len(xs), len(ys))

def match_game(info, games):
    best, bg = 0, None
    for g in games:
        # 两个队必须各自都配上(防"Copenhagen vs Braga"配上"Braga vs Gil Vicente"这类)
        s_home = (team_overlap(info["a"], g["home"]), team_overlap(info["b"], g["away"]))
        s_away = (team_overlap(info["a"], g["away"]), team_overlap(info["b"], g["home"]))
        for s1, s2 in (s_home, s_away):
            tot = s1 + s2
            if tot >= 1.2 and min(s1, s2) >= 0.5 and tot > best:
                best, bg = tot, g
    return bg

# ---------- 主流程 ----------
def main():
    print("== Polymarket 体育盘 ==")
    mkts = poly_markets()
    draws = [m for m in mkts if m["kind"] == "DRAW"]
    tots = [m for m in mkts if m["kind"] == "TOTALS"]
    print(f"双边市场 {len(mkts)}: DRAW {len(draws)}, TOTALS {len(tots)}")
    print("== Pinnacle 足球 ==")
    games = pinnacle_soccer()
    print(f"比赛 {len(games)} 场")
    cost = VENUE_COST_RT.get(VENUE, 0.0)
    eff = THRESH + cost
    print(f"venue={VENUE} 往返成本={cost:.1%} 有效阈值={eff:.1%}")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    flags = []
    matched = 0
    for m in draws + tots:
        g = match_game(m["info"], games)
        if not g:
            continue
        matched += 1
        if m["kind"] == "DRAW":
            if g["draw"] is None:
                continue
            ref, side = g["draw"], "DRAW"
        else:
            line = m["info"]["line"]
            if line not in g["totals"]:
                continue
            ref, side = g["totals"][line], f"O{line}"
        edge = m["yes_px"] - ref
        if abs(edge) >= eff:
            flags.append((ts, m["event"], m["question"], round(m["yes_px"], 4),
                          round(ref, 4), round(edge, 4),
                          "POLY_HIGH" if edge > 0 else "POLY_LOW", side,
                          round(m["volume"])))
    print(f"配对成功 {matched} 条, 标记 {len(flags)} 条")
    new = os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if not new:
            w.writerow(["ts", "event", "question", "poly_yes", "pinnacle", "edge", "signal", "side", "volume"])
        w.writerows(flags)
    for r in flags[:20]:
        print(f"  [{r[6]}] edge={r[5]:+.3f} poly={r[3]:.3f} pin={r[4]:.3f} vol={r[8]:,.0f} | {str(r[2])[:50]}")

main()
