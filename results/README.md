# 扫描结果

- `outrights_scan_20261011.csv` —— 冠军盘扫描器首跑结果（2026-10-11）
  - 59 个高置信匹配（NBA/NFL/NHL 冠军盘；MLB 无 Pinnacle 基准；欧洲足球无基准）
  - 22 个 `signal=YES`：扣掉 Gate 1.6% 往返成本后净 edge > 3% 的纸面信号
  - 列：event, outcome, poly_price, pinnacle_prob（去水后）, raw_edge, net_edge, poly_volume, match_confidence, signal

注意：这些是**时点价格**（2026-10-11 ~16:20 CST）+ **纸面信号**，不是下单建议。
长冷门（1–9¢）的买卖价差可能吃掉纸面 edge；Pinnacle NBA 数据疑似 stale。
