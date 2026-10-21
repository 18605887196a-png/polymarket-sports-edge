# Polymarket 加密预测

一句话：**找 Polymarket 加密价格盘（BTC/ETH/SOL）的定价错位，纸面验证 + 手动下单。**

## 整体架构

```
                    ┌─ Polymarket gamma API（原生，按 endDate 过滤，干净数据）
数据源 ─────────────┼─ Deribit 期权链（get_book_summary_by_currency → 各到期 IV）
                    └─ Gate 现货 K 线（现价 + realized vol 降级备用）
                              │
                              ▼
┌─ 信号层（scanners/，只读，不下单）──────────────────────────────┐
│ crypto_scanner.py        主力：BTC/ETH/SOL "above $X on [date]"      │
│   Tier1 ≤7天 / Tier2 >7天；Deribit IV 定价；|模型-市场| ≥5pp 才信号   │
│   + 稳健检查：波动率 ±30% 两端都成立                                │
│ crypto_band_scanner.py   每日整条行权价阶梯扫描 + 单调性校验（3% 线）  │
│ btc_band_scanner.py      BTC 日内价格区间盘（lo < S_T ≤ hi）错价       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─ 验证层（纸面，$0）──────────────────────────────────────────────┐
│ crypto_signals_log.csv   每个信号：模型概率 / 价格 / 方向 / 仓位      │
│ settle_check.py          到期后用 K 线算真实结算价 → WIN/LOSS + pnl  │
│ 50 个信号后：模型概率 vs 实际发生率，偏差 >±10% 修模型              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                      人手在 Gate App 下单
              （扫描器永远不碰真钱，这是铁律）
```

**原理**：Polymarket 价格 vs Deribit 期权隐含波动率 → 对数正态理论概率 → 错价 = 机会

## 方法论（五层判断）
1. **期权隐含分布**（核心）：Deribit IV → 理论概率，**不猜波动率**（2026-10-12 血泪修复：ETH 曾静默用 realized vol，导致两次"稳"单被推翻；现 BTC/ETH 统一走 Deribit IV，结果带 `vol_src` 标注来源）
2. **聪明钱验证**（反向）：Gate Whale 标签/链上大额持仓，大户反向则跳过
3. **订单簿深度**：价差 >10% 跳过，<5% 才算 edge
4. **期限结构一致性**：相邻到期日 IV 应平滑，突兀的 edge 优先怀疑数据
5. **历史校准**：50 个信号后看模型概率 vs 实际发生率，偏差 >±10% 修模型

详细见 [docs/crypto-prediction-framework.md](docs/crypto-prediction-framework.md)。

## NBA 滚球监控（nba_live_monitor/）

2026-10-21 揭幕战实盘跑通后封装的滚球监控能力，完整规则见 [nba_live_monitor/SKILL.md](nba_live_monitor/SKILL.md)。

- **数据源**：ESPN scoreboard/summary（比分、时钟、球员 box score）+ Polymarket gamma（bestBid/bestAsk，只认真实盘口，不用滞后的成交价）
- **胜率模型**：WP = Φ((lead + M·t/48) / (13·√(t/48)))，M 为赛前预期分差；误差约 ±4pp
- **双轨制（2026-10-21 改版）**：球员轨全场监控，只推达成概率约九成、卖价比概率低 ≥5pp、盘口真有人交易的球员线（打热推大、失常推小），已成定局（≥97¢）和死盘不关注；队伍轨（胜负/让分/大小分）前三节静默，第四节开打 3 分钟出第一份"稳不稳"报告，剩 5 分钟、3 分钟各固定推一次，达标线约 95%（大小分看推算总分离线 ≥10 分）
- **执行**：Gate 的 Polymarket 板块与 Polymarket 同一订单簿，价格同源；Gate 另收单边 0.8% 手续费，算账用费后口径；监控只报，下单永远人手

## 目录
| 文件 | 说明 |
|---|---|
| `scanners/crypto_scanner.py` | 主力扫描器 v2（above 盘，Deribit IV，5pp 信号线） |
| `scanners/crypto_band_scanner.py` | 行权价阶梯扫描器 v1（单调性校验） |
| `scanners/btc_band_scanner.py` | BTC 区间盘扫描器 v1 |
| `settle_check.py` | 信号结算器（纸面 WIN/LOSS + pnl） |
| `gate/prediction.py` | Gate News MCP 只读封装（查盘口/异动排行） |
| `nba_live_monitor/` | NBA 滚球监控：SKILL.md（双轨规则）+ scripts/monitor.py（胜负盘轮询） |
| `docs/crypto-prediction-framework.md` | 方法论框架 |
| `results/` | 扫描结果样例 |

## 仓位（$50 本金，1/4 Kelly + 硬顶）
| 情况 | 金额 |
|---|---|
| 1/4 Kelly 算出 >$1.50 | $1.50（3% 硬顶） |
| 模型概率 <20% 的彩票 | 跳过（不叫稳定，叫彩票） |
| 博轨（波动率赌注） | $0.50（1% cap），最多 1–2 注 |

铁律：每天最多 3 注；同一标的同一到期只选一注；**只买正向**（看多买 Yes，看不懂不下）。

## 快速开始
```bash
pip install -r requirements.txt
python3 scanners/crypto_scanner.py          # 主力：above 盘
python3 scanners/crypto_band_scanner.py     # 阶梯盘
python3 scanners/btc_band_scanner.py        # BTC 区间盘
python3 settle_check.py                      # 结算已到期信号
```

## 边界
- **不自动下真钱单**：扫描自动化，下单人手操作
- **$0 验证心态**：50 个结算信号完成校准前，小注试探
- 验证标准：模型概率 vs 实际发生率偏差 ±10% 内
- API key 全部走环境变量/本地 env 文件，**永不进仓库**（见 `.env.example`）
