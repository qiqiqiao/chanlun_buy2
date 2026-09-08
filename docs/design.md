# Buy-2 Radar — 设计文档（v2，对应 P0/P1 修订）

本文把每条“程序化、可回测、可解释、无未来函数”的规则定位到代码与公式。
业务阈值全部在 `data/config.json`（`python -m buy2radar.main config` 生成），代码不硬编码。

术语约定：内部对象一律叫 **Buy2Candidate（二买候选）**，不宣称是“绝对正确/严格缠论二买”。
缠论实现是有明确定义的简化近似（分型→笔→中枢→背驰），不做线段/走势类型递归。
完整演进路线见文末 §12。

## 1. 架构

```
OKX public REST（限速/重试/并发）  okx.py
SQLite 缓存与状态                store.py   (candles / events / pool_state / scans)
universe 筛选 + 编排               scan.py
单币流水线（无未来函数）          analysis.py
   chan/core.py    包含→分型→笔→中枢（极值/确认双时间戳）
   chan/detect.py  一买/二买锚点状态机
   chan/status.py  结构状态（只用已收盘K）
   scoring.py      Buy2Score / 交易价值状态
hysteresis 进出池 + 综合排名      pool.py
终端看板                          dashboard.py  (TOP10 / READY / WATCH)
回测                              backtest.py   (含 MFE/MAE)
```
仅标准库。实时 = REST 轮询（watch: 60s ticker 轻刷 + 15min 全量；日线策略足够，不上 WebSocket）。
轻刷只重算成员 + 按 final_score 取前 `monitor.candidateRefresh` 个候选（新信号最晚下个全量周期补上）；
每个全量周期先重建 universe（新币/成交额变化即时纳入）。K线增量分页回补到与本地重叠，
停机多天也不留缺口；单币同步失败只记日志不中断整批。

## 2. 无未来函数（严格化）

- 分析只用**已收盘**日K：`ts < 当日00:00 UTC`。当天K不参与任何判定。
- **分型/笔结构状态 只允许使用已收盘K。** 实时 ticker 价格只进入“交易价值/距离/涨幅”，
  绝不进入 `StructureStatus`（P0-①）。盘中插破 H1 ≠ CONFIRMED。
- 每个分型同时记录两个时间（P0-③）：
  - `raw_idx`（extreme_raw_idx）：极值发生的K（顶=最高价那根，底=最低价那根）；
  - `confirm_raw_idx`：该分型被右邻K确认的那根（=右邻合并K的最后一根原始K）。
  锚点判定要求 `confirm_raw_idx` 已存在且其后仍有 `minAnchorBarsAfter` 根收盘K，否则视为“待确认”。
- 回测：第 d 根收盘处决策用 `candles[:d+1]`，`今日=candles[d].ts+1天`，前向收益取 d+K 收盘；
  MFE/MAE 用 d 到 d+K 的 high/low，杜绝前视。

## 3. 缠论简化引擎

| 层 | 规则 | 参数 |
|---|---|---|
| `merge_bars` | 包含关系处理，记录每组极值原始K `high_raw/low_raw` | — |
| `find_fractals` | 合并K上严格顶/底分型；确认组本身必须已收盘（`i+1≤n-2`，见下） | chan.fractalConfirmerClosed=true |
| `build_bis` | 顶底交替成笔，同类取更极端者，分型合并索引差≥`biMinMergedGap`；异类过近时若新分型比上一个同类更极端则剔除中间毛刺 | chan.biMinMergedGap=2 |
| `build_pivots` | 连续3笔重叠 `[max(low),min(high)]`，仅统计参考 | — |
| MACD/ATR | EMA 12/26/9（SMA seed）；Wilder ATR(14)（SMA seed）；warm-up 建议 `3×slow+signal` | chan.* |

> 确认语义：追加新K只可能改变最后一个合并组；若用开放的末尾组做确认，
> 后续K并入该组可能改写极值使分型事后消失（隐性未来函数）。因此默认要求
> 确认组收盘，多一组合并组的延迟是保稳定的代价。`fractalConfirmerClosed=false`
> 可退化为“下一组出现即确认”（仅研究对比，实盘/回测禁用）。

## 4. 一买/二买锚点（detect.py，显式状态机）

在“已确认分型”的端点序列上跑状态机，只保留**当前这一波最新且未失效**的候选：

```
底 a（一买低点候选，含起跌顶 a_top）
  新底 < a.low  → 刷新 a（下跌延续，旧候选作废）
  新底 > a.low  → 若 a→现高点反弹 H1 超过
                     a.low + max(waveReboundFrac×leg, minBounceAtr×ATR)
                  则 H1 之后的第一个更高低点 b = Buy2Candidate（此后本波不再重复锚定）
```

- 事件锚点在 b 的低点，年龄/新鲜度从 **b 的确认日** 起算。
- 可选 `requireBuy1Divergence`：比较进入 a 的下行笔与前一下行笔 MACD 柱极小值，底背驰才放行。
- 跌破 L1(a.low) 整体作废并进入下一波。
- 显式状态：`SEARCH_LOW → AWAIT_BOUNCE → AWAIT_PULLBACK → EMITTED`，
  超时（`buy2.maxBarsB1toB2`，默认0=不限）经 `EXPIRED` 回到 SEARCH_LOW 重找；
  每次转移记入 `state_trace`。可选收紧钩（默认全关=历史行为）：
  `buy2.maxB2GapAtr`（B2-B1 拉开过远视为脱钩跳过）、
  `buy2.requireConfirmCloseAboveB1`（B2确认K收盘须站上B1）。

## 5. 结构状态 StructureStatus（只用收盘K）

锚点冻结 (L1, L2, H1)，H1=a→b 区间反弹高点。`chan/status.py`：

```
1. 任何收盘K低点 ≤ L1            → INVALID
2. 最近收盘 < L2（但未破 L1）     → WEAKENING
3. L2 之后曾跌破 L2（dip）:
     仅在 dip 之后出现的最高价 ≥ H1 才算恢复（顺序修复 P0-②）
     恢复且 dip 深度 ≤ maxDipRecoverAtr×anchorATR → CONFIRMED（洗盘）
     否则 → WEAKENING
4. 最近收盘 ≥ H1（且无 dip）      → CONFIRMED
5. 其余（收盘持稳于 L2 上、未破 H1）→ VALID
```

关键：把“先破 H1 后跌破 L2 再弱反弹”与“先 dip 再真突破”区分开——恢复只看 **dip 之后** 的极值。

结构状态 ≠ 交易价值：可能结构仍 VALID 但已涨远 → 价值 NO_VALUE，不进池。

## 6. 交易价值 / Buy2Score（0~100）

权重默认：structure 30 / distance 25 / gain 15 / freshness 10 / liquidity 10 / volumeChange 10（`score.weights`）。

| 分项 | 输入 | 默认映射（表驱动 score.tables） |
|---|---|---|
| structure | StructureStatus | CONFIRMED 100 / VALID 78 / WEAKENING 40 / INVALID 0（底背驰 +3） |
| distance | `(price-L2)/currentATR` | 0–0.5=100 → 1.0=78 → 1.5=55 → 2.0=32 → 3.0=0 |
| gain | consumed=`(price-L2)/(H1+(H1-L2)-L2)` | ≤0.25=100 → 1.0=50 → 2.5=0 |
| freshness | ageDays（自 b 确认日） | `ageScorePoints`: ≤15d=100 → 30d=70 → 45d=45 → 60d=25 → 80d=0 |
| liquidity | 24h成交额(USDT,估算) | log10(2M..5B) |
| volumeChange | ticker24h额 / 近 baselineDays 日额均值 | 表 volumePoints |

**双 ATR（P1-④/⑤）**
- `anchorATR`=b 低点K当日 ATR；`currentATR`=最近收盘 ATR。
- `distance_atr`（进池/价值档位/看板）= current ATR 距离。
- `distance_anchor_atr`、`runup_pct`、`runup_anchor_atr/current_atr`、`runup_range=(price-L2)/(H1-L2)`、
  `consumed` 全部输出到 breakdown/明细。

**硬过滤（P1，先于评分/排名）**
- 二买后涨幅 `runup_pct > runupValueCapPct`(默认25%) → `TradingValueStatus=NO_VALUE`。
- 池进出附加 `pool.enterMaxRunupPct / exitMaxRunupPct`(默认25/25)：涨幅超限不能进/直接移出池。
- `maxAgeDays`(默认60)：>60 天的二买候选直接淘汰（不再是“当前机会”）。
  freshness 通过 ageScorePoints 降权只是软惩罚，硬年龄线才是兜底。

### TradingValueStatus
`EXCELLENT(≤0.5)/GOOD(≤1.0)/WATCH(≤1.5)/EXTENDED(≤2.0)/NO_VALUE(>2.0、score<40 或 runup>cap)`
（距离按 current ATR，阈值 `zones`；WEAKENING 时顶多 WATCH）。

## 7. 生命周期与 TopN（pool.py，hysteresis）

```
进入池 : 非成员 & score≥enterScore(70) & dist≤enterDistance(1.5 currentATR)
         & runup≤enterMaxRunupPct & 结构∈{VALID,CONFIRMED} & 成交额≥minTurnoverPool & 年龄≤maxAgeDays
保留   : 成员 & score≥exitScore(55) & dist≤exitDistance(2.0) & runup≤exitMaxRunupPct & 结构有效
移除(任一): 结构INVALID/WEAKENING | score<exitScore | dist>exitDistance | runup超限 | 被更优者顶替 | 本期无结果
冷却   : removeCooldownHours(12h) 防抖动；成员粘性 rank.membershipBonus(3)
排名   : FinalScore=(1-liquidityWeight)×Buy2Score + liquidityWeight×LiquidityScore + (成员? bonus)
perBase: best = 同 base(现货/永续)只保留评分高者
```
看板明确三段：**TOP10（池）/ READY（过入场线、池位空出即补位）/ WATCH（有二买但未达入场线，
含 EXT/weak/low-* 原因标注）**。NO_VALUE/超龄/失效不在任何列。

> 第11名补位：只有 `READY`（stage=CANDIDATE）才是候选补位项；WATCH 只是观察，不补位。

## 8. 数据口径与流动性（okx.py）

- K线第 8 字段 `volCcyQuote` 即报价币成交额（SPOT/SWAP 同构），作为成交额基准。
- ticker 24h：SPOT 用 `volCcy24h`（精确报价币额）；SWAP 该字段是**基础币**数量，
  成交额为 `volCcy24h×last` → **估算值**，UI 标注 “est”。（更准口径可用当日日K quote turnover。）
- Universe：live + quote=USDT + 线性永续(settle=USDT)，剔除稳定币，按成交额下限+`maxInstruments` 裁剪。

## 9. 回测（backtest.py，两层）

walk-forward 逐日重建，输出 (score≥S × dist≤D) 网格的：n / 前向收益均值 / 胜率 / PF / **MFE / MAE**。
MFE/MAE 揭示入场后的“先浮亏多少、能走多远”，用于后续止损/目标/加仓设计。
walk 阶段按币多进程并行（`--workers`，0=自动；回测 SWAP 量能用 `vol×收盘` 模拟实盘 ticker 估算口径；
`turnover_24h_at()` 按 bar 周期累计 24h，切 4H/12H 不埋雷）。
第二层 Pool/Portfolio 回测（`--pool/--no-pool`，默认开）：逐日横截面跑**真实 `plan_pool`**
（去重/TopN/冷却/排名全生效），持仓=当日 in_top，等权每日收盘再平衡，
输出 entries/ever_top/round_trips/平均持仓/单笔净收益·胜率·PF·MFE·MAE/组合总收益·最大回撤·成本拖累
（`backtest.feeBpsPerSide`，默认10bps/边；缺数日按最后价离场）。

## 10. 持久化（store.py）

`candles`（增量缓存，按 `state.candleLimit` 裁剪）、`pool_state`（成员 entered_ms/removed_ms/评分 → 冷却依据）、
`events`（每次全量扫描的最新锚点快照，批量单事务写入）、`scans`（最近 20 次看板 payload）。

## 11. CLI

```
python -m buy2radar.main config [path]
python -m buy2radar.main scan
python -m buy2radar.main watch [--once]
python -m buy2radar.main inspect BTC-USDT-SWAP
python -m buy2radar.main backtest --limit 80 --fwd 5 10 20 [--workers 4] [--no-pool]
```
工程要点：并发数取 `data.threads`（不再写死）；OKX 业务限流码 **50011/50010** 与 HTTP 429
统一走指数退避重试。

## 12. 已知简化 / 路线（诚实声明）

1. 缠论 = 分型/笔/中枢（简化、有定义），未做线段/特征序列/走势类型递归；
   因此输出是“Buy2Candidate”，二买定义准确性上限受此限制。
2. 结构完整性的“新中枢/线段/背驰再分类”等深层次反推未做；现有 L1/L2/H1 冻结锚点 + 破位/插破规则是其一阶近似。
3. SWAP ticker 成交额为估算；实时=REST 轮询。

路线：v3 严格化笔（分型→笔的确认规则细化）→ 线段/中枢/走势类型 → 用其重定义一/二买；
v4 剩余：滑点模型 + 阈值自动调优（一期 Pool/组合回测已上线：席位/去重/排名/成本/资金曲线/回撤）；
v5 再考虑 WebUI/推送。
