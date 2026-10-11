# Polymarket 加密预测

一句话：**找 Polymarket 加密价格盘（BTC/ETH/SOL）的定价错位，纸面验证 + 手动下单。**

## 架构

```
Tier 1 短期 (≤7天结算): 每日价格盘，快周转 ← 主力
Tier 2 长期 (>7天结算): 季度/年度价格目标 ← 待补充
```

**原理**：Polymarket 价格 vs Deribit 期权隐含波动率 → 对数正态理论概率 → 错价 = 机会

## 方法论（五层判断）
1. **期权隐含分布**（核心）：Deribit IV → 理论概率，不猜波动率
2. **聪明钱验证**（反向）：Gate Whale 标签/链上大额持仓，大户反向则跳过
3. **订单簿深度**：价差 >10% 跳过，<5% 才算 edge
4. **期限结构一致性**：相邻到期日 IV 应平滑，突兀的 edge 优先怀疑数据
5. **历史校准**：50 个信号后看模型概率 vs 实际发生率，偏差 >±10% 修模型

详细见 [docs/crypto-prediction-framework.md](docs/crypto-prediction-framework.md)。

## 目录
| 文件 | 说明 |
|---|---|
| `scanners/crypto_scanner.py` | 加密预测扫描器 v1 |
| `gate/prediction.py` | Gate News MCP 只读封装（查盘口/异动排行） |
| `docs/crypto-prediction-framework.md` | 方法论框架 |
| `results/` | 扫描结果样例 |

## 稳定性评分 → $50 仓位
| 评分 | 金额 |
|---|---|
| 80+ | $1.00 |
| 60–80 | $0.75 |
| 40–60 | $0.50 |
| <40 | 跳过 |

铁律：每天最多 3 注；同一标的同一到期只选一注；模型概率 <20% 的彩票跳过。

## 快速开始
```bash
pip install -r requirements.txt
python3 scanners/crypto_scanner.py
```

## 边界
- **不自动下真钱单**：扫描自动化，下单人手操作
- **$0 验证心态**：50 个结算信号完成校准前，小注试探
- 验证标准：模型概率 vs 实际发生率偏差 ±10% 内
