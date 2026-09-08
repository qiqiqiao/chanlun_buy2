# Buy-2 Radar (OKX · 缠论日线二买)

一个针对 OKX（现货 USDT + USDT 本位永续）的“**当前仍有效的二买雷达**”：
不是找历史上出现过二买的币，而是持续回答——

> 现在市场上，哪些币的日线走势正处于一个**仍然有效、距离不远、具有交易价值**的缠论二买附近？

## 快速开始

需要 Python 3.10+（仅标准库，无需 pip 安装任何东西）。

```bash
# 1) 生成默认配置（可选，不生成则用内置默认）
python -m buy2radar.main config data/config.json

# 2) 一次性扫描并打印看板（首次会自动拉取约300根日K缓存）
python -m buy2radar.main scan

# 3) 持续监控（终端实时刷新看板）
python -m buy2radar.main watch

# 4) 单币明细（结构/子分/锚点全展开）
python -m buy2radar.main inspect BTC-USDT-SWAP

# 5) 阈值回测（walk-forward，用于调 score/dist/权重；默认含 Pool/组合第二层）
python -m buy2radar.main backtest --limit 80 --fwd 5 10 20 --workers 4
```

## 看板读法

```
 #  inst              price      B2low      distATR  pct   age d struct  value     score  liq%  volR
 1  WLFI/USDT S     0.0564     0.0549       0.49    2.7%     4  🟢 VAL   🟢 EXC     80.4  12  0.69
 2  TRUMP/USDT S    2.2740     2.1100       0.59    7.8%    11  🟢 VAL   🟢 GOOD    77.8  19  0.43
```

- `distATR` = 现价距二买低点 L2 的 **当前 ATR** 距离（0~0.5 极佳 / 1.0 内良好 / 1.5 内观察 / 2.0 内边缘 / >2 无价值）。
- `struct`=结构状态（只用已收盘K），`value`=交易价值（用实时价）。二者解耦：结构没坏但已涨远 → value 变红。
- `pct` = 二买后已涨幅；涨幅超过 `runupValueCapPct`(默认25%) 直接判定无交易价值。
- `score`=Buy2Score(0~100)，`liq%`=流动性分，`volR`=成交额/近5日均值。
- 看板分三段：**TOP10（池）** / **READY（过入场线、池位空出自动补位）** / **WATCH（有二买但未达入场线）**。

## 核心设计

- **二买是“交易区域”不是点**：0.5/1.0/1.5/2.0 ATR 分带，全部配置化（`zones`），供回测收敛。
- **Buy2Score 0~100**：结构30% + 距离25% + 涨幅15% + 新鲜度10% + 流动性10% + 量能10%
  （`score.weights` 可改）。≥70 进池、<55 移除，中间带 = hysteresis。
- **双 ATR**：`anchorATR`（锚点）用于结构回溯/插破判定；`currentATR`（当前）用于交易价值距离。
- **硬过滤**：二买涨幅超 25%（`runupValueCapPct`）、或年龄超 `maxAgeDays`(默认60天) → 不再是“当前机会”。
- **新鲜度**：`score.ageScorePoints` 按 15/30/45/60 天逐级降权，60 天后默认淘汰。
- **状态机**：发现→确认→有效观察区→候选池(READY)→Top10→(继续有效 / 远离 / 失效 / 超龄)。
- **无未来函数**：结构只用已收盘日K；盘中插破不确认；实时价只影响交易价值；锚点记录
  “极值K/确认K”两个时间；回测逐日重建，含 MFE/MAE。
- **零第三方依赖**：urllib + sqlite3 + 标准库；REST 轮询实现“实时”。

全部规则、公式、阈值与回测方法见 [docs/design.md](docs/design.md)。
数据落地在 `data/state/buy2.db`（K线缓存 + 池状态 + 历史快照）。

## 目录

```
buy2radar/
  okx.py        REST 客户端（限速/重试）与数据口径
  store.py      SQLite：K线缓存 / pool_state / events / scans
  scan.py       universe 构建、并发同步、池计划编排
  analysis.py   单币分析流水线（核心，无未来函数）
  chan/core.py  包含→分型→笔→中枢
  chan/detect.py  一买/二买锚点状态机
  chan/status.py  结构状态 StructureStatus
  scoring.py    Buy2Score、交易价值状态
  pool.py       hysteresis 进出池 + FinalScore 综合排名
  dashboard.py  终端看板
  backtest.py   阈值回测
  config.py     默认配置与合并
docs/design.md  详细设计与公式
data/config.json 可编辑配置（默认阈值，建议回测后覆盖）
```

> 免责声明：本程序是行情雷达与分析工具，非投资建议，不构成任何交易指令。
