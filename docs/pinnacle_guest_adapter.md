# Pinnacle Guest API 适配器（免 key 数据源）

> 状态：研究中，待实测验证（2026-10-11）
> 替代 `sports_odds_scanner.py` 里 the-odds-api 的 key 依赖。用户无需再注册 the-odds-api 免费 key。

## 发现

Pinnacle 官方 API 已于 2025-07-23 关闭公开访问，但其网站前端读取的 Arcadia "guest" API
仍是匿名开放的（非官方文档，2026-06 实测可用；多个公开项目在用）。

- Host: `https://guest.api.arcadia.pinnacle.com/0.1`
- 必需 headers（网站前端自带的公开 web-client key，非用户密钥）：
  - `x-api-key: CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R`
  - `x-device-uuid: <任意稳定 UUID>`
  - `accept: application/json`
- 无 key 时 `/sports/{id}/matchups` 等返回 401；带 key 即通。

## 端点

| 路径 | 用途 |
|---|---|
| `/sports` | sportId 列表（3=棒球，4=篮球，29=足球，其余待实测） |
| `/sports/{sportId}/matchups` | 全量对阵（非 highlight 版） |
| `/matchups/{matchupId}/markets/related/straight` | moneyline 等直接盘赔率 |
| `/sports/{sportId}/matchups/live` | 滚球盘（如需 in-play） |

## 数据约定

- 返回多为顶层 JSON 数组；只有 `hasMarkets=true` 的 matchup 才有价格。
- `price` 是美式赔率（-128 / +105）；转隐含概率：price>0 → 100/(price+100)；price<0 → |price|/(|price|+100)。
- devig：各边隐含概率除以总和。
- moneyline market 的每条 price 有 `designation`（home/away/draw），按它映射，不要按价格顺序猜。

## 限制与注意

- 美国 IP 会被 403 `{"reason":"location"}`（监管性 geo-block，不是反爬）；本 VM 非美国出口，待实测。
- 请求保持低频串行（~300ms 间隔），只读。
- the-odds-api 的 Pinnacle 数据是爬这个源的聚合，有延迟/缺口；直连更新鲜。
- soccer 三向盘：draw 单独处理；与 Polymarket 双边盘对比时只用 home/away devig 概率。

## 接入 scanner 的改法

在 `sports_odds_scanner.py` 里新增 `pinnacle_guest_games()` 替代 `pinnacle_games()`：
`sports → matchups → markets/related/straight → 美式转概率 → devig → {(home,away): prob}`，
其余匹配/阈值逻辑不变。`--odds-key` 参数保留作备用。
