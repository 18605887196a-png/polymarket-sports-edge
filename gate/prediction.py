"""
Gate 预测市场只读能力封装 (2026-10-11 落地)

两条路都通:
  1. gate-cli (推荐): gate-cli news prediction search-events --query "xxx" --format json
  2. 原始 MCP HTTP: https://api.gatemcp.ai/mcp/news (需 x-api-key: MCP_AK_8W2N7Q)

5 个只读工具 (无交易、无账户、无资金操作):
  - search_events: 按 query/category/venue 搜预测事件
  - get_event_signal: 单个事件信号 (概率、资金流)
  - get_market_orderbook: 实时盘口 (venue=polymarket|predict_fun)
  - get_volume_delta_ranking: 按成交量变化排行
  - get_fastest_rising_ranking: 按概率上涨排行

venue 取值: polymarket (主池), predict_fun (BNB链池子,币安预测同源)
category 取值: sports, crypto_price, elections, ...

注意: 交易执行类 MCP (mcp/exchange) 需要 Gate OAuth2,未配置 —— 按用户边界不配。
"""
import json, subprocess, os

GATE_CLI = os.path.expanduser("~/.local/bin/gate-cli")

def _run(*args):
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
    out = subprocess.run([GATE_CLI, *args, "--format", "json"],
                         capture_output=True, text=True, timeout=60, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"gate-cli failed: {out.stderr[:300]}")
    return json.loads(out.stdout)

def search_events(query=None, category=None, venue=None, status="active", limit=20):
    args = ["news", "prediction", "search-events", "--limit", str(limit)]
    if query: args += ["--query", query]
    if category: args += ["--category", category]
    if venue: args += ["--venue", venue]
    if status: args += ["--status", status]
    return _run(*args).get("events", [])

def get_event_signal(event_ref, window="24h"):
    return _run("news", "prediction", "get-event-signal",
                "--event-ref", event_ref, "--window", window)

def get_orderbook(venue, market_id, depth=10):
    return _run("news", "prediction", "get-market-orderbook",
                "--venue", venue, "--market-id", str(market_id),
                "--depth", str(depth))

def volume_delta_ranking(category=None, venue=None, date_utc=None, limit=20):
    args = ["news", "prediction", "get-volume-delta-ranking", "--limit", str(limit)]
    if category: args += ["--category", category]
    if venue: args += ["--venue", ",".join(venue) if isinstance(venue, list) else venue]
    if date_utc: args += ["--date-utc", date_utc]
    return _run(*args)

def fastest_rising_ranking(category=None, venue=None, date_utc=None, limit=20):
    args = ["news", "prediction", "get-fastest-rising-ranking", "--limit", str(limit)]
    if category: args += ["--category", category]
    if venue: args += ["--venue", ",".join(venue) if isinstance(venue, list) else venue]
    if date_utc: args += ["--date-utc", date_utc]
    return _run(*args)

if __name__ == "__main__":
    # 自检: 体育 + 电竞各搜 3 条
    for cat, q in [("sports", None), (None, "esports")]:
        evs = search_events(query=q, category=cat, limit=3)
        print(f"--- category={cat} query={q}: {len(evs)}")
        for e in evs:
            print(f"  [{e.get('venue')}] {e.get('venue_event_title','')[:60]}")
