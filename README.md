# Polymarket 体育预测套利研究

一句话：**找 Polymarket（及 Gate 预测板块）上相对 Pinnacle 定价错的体育盘，纸面验证，不自动下真钱单。**

## 架构：五层

```
1. 数据层  Polymarket 价格 + Pinnacle 基准赔率 + Gate 费用模型
2. 模型层  scanner-first（跨市场比价）；Elo 自建概率降为 P2 备用
3. 信号层  只输出「扣掉 Gate 1.6% 往返成本后净 edge > 3%」的信号
4. 资金层  1/4 Kelly、单注上限 2%、回撤 20% 熔断（真钱阶段才用）
5. 验证层  纸面记录 CLV，攒够 200 个信号、平均 CLV ≥ +2%、beat 收盘 ≥ 55% 才谈真钱
```

详细方法论见 [docs/sports-edge-framework.md](docs/sports-edge-framework.md)。

## 目录

| 目录 | 内容 | 状态 |
|---|---|---|
| `scanners/outrights_scanner.py` | **冠军盘扫描器**：Polymarket 冠军盘 vs Pinnacle 冠军盘 | ✅ 主力，2026-10-11 首跑 22 个纸面信号 |
| `scanners/game_scanner_v2.py` | 单场盘扫描器：Polymarket 单场 vs Pinnacle 单场 | ⚠️ 能跑但结构性 0 信号（见下） |
| `scanners/legacy/` | v1 存档（匹配器有 bug，仅留作教训） | ❌ 不用 |
| `gate/prediction.py` | Gate News MCP 预测市场只读封装（搜事件/查盘口/异动排行） | ✅ |
| `research/` | 顶级钱包跟单可行性研究 | ❌ 已证伪：回测负期望，存档备查 |
| `results/` | 扫描结果 CSV 样例 | — |
| `docs/` | 框架文档、Pinnacle 适配笔记 | — |

## 三条路线的结论（省流版）

1. **跟单顶级钱包** ❌ —— 回测 14 天：即使 0 延迟完美跟单也只有 1.06x–1.26x，真实延迟下亏钱。大 V 是 buy-and-hold 到结算，没有可复制的信号。
2. **单场盘跨市场扫描** ⚠️ —— 技术上跑通了（55 个联赛、410 场 Pinnacle、50 个有效 Polymarket 盘），但 **0 有效信号**。原因是结构性的：Polymarket 的单场盘集中在哥伦比亚、斯洛伐克、J2 等 Pinnacle 不覆盖的小联赛，两边没交集。不是扫得不够勤，是仪器不对。
3. **冠军盘/futures 扫描** ✅ —— 两边都有数据（NBA/NFL/NHL/MLB 冠军盘），没有结构性错配。首跑 59 个高置信匹配 → **22 个净 edge > 3% 纸面信号**（全是 1–9¢ 长冷门，见 `results/`）。

## 费用模型（Gate）

- Gate 平台费 maker/taker 各 0.8%，叠加 Polymarket 协议费
- 扫描器按**限价单进出各 0.8% = 1.6% 往返成本**估算
- 只有净 edge > 3% 才算信号（费用吃掉薄利是常态）

## 快速开始

```bash
pip install -r requirements.txt

# 1. 配置 key（去 https://the-odds-api.com 免费注册，每 key 每月 500 credits）
cp .env.example ~/.the-odds-api.env
# 填入 THE_ODDS_API_KEY 和 THE_ODDS_API_KEY_BACKUP

# 2. 跑冠军盘扫描（约 4 credits/次）
python3 scanners/outrights_scanner.py

# 3. 跑单场盘扫描（约 60 credits/次，预期 0 信号）
python3 scanners/game_scanner_v2.py --venue gate --threshold 0.04

# 4. 查 Gate 预测市场有什么盘（只读，无需 key）
python3 gate/prediction.py
```

## 当前状态（2026-10-11）

- 22 个冠军盘纸面信号，**0 个经过验证** —— 按框架要攒 200 个信号 + CLV 验证才谈真钱
- 已知 caveats：长冷门买卖价差可能吃掉纸面 edge；Pinnacle NBA 冠军盘 overround 异常（1.498，数据或 stale）；欧洲足球冠军盘在 the-odds-api 无 Pinnacle 基准
- 定时任务：冠军盘每 3 天自动重扫（只在有新信号时推送）
- Gate 预测 = Polymarket 主池 + predict_fun 双 venue；交易执行无程序化入口，真钱单必须手动下

## 边界

- **不自动下真钱单**：扫描、信号、纸面跟踪可以自动化；下单必须人手操作
- **不伪造身份**：不碰 KYC 相关任何灰色操作
- **$0 验证**：没过验证标准前不谈真钱
