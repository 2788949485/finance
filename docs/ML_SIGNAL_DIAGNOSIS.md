# FinanceCrew — ML 信号诊断系统设计文档

| 版本 | 日期 | 状态 | 作者 |
|------|------|------|------|
| v0.1 | 2026-08-05 | 设计 + 部分实现 | FinanceCrew 团队 |

---

## 0. 文档导航

本设计文档共分四份，按阅读顺序：

| 文档 | 内容 | 读者 |
|------|------|------|
| **本文 (`ML_SIGNAL_DIAGNOSIS.md`)** | 系统总览、架构、数据流、设计决策 | 所有人（先读本文） |
| [`csv_feature_format.md`](./csv_feature_format.md) | 65 列 CSV 特征字段逐列规范 | 特征工程 / 数据工程 |
| [`sample_signals.csv`](./sample_signals.csv) | 带表头与示例行的 CSV 样本 | 数据工程 / 调试用 |
| [`implementation_roadmap.md`](./implementation_roadmap.md) | 模块级实现路线图（含已完成 / 待完善清单） | 后端开发 |

> **状态**：截至 2026-08-05，全部 7 个核心模块（事件特征/向量化特征/标签/切分/训练/评估/管线）均已实现，端到端可跑通。详见 [implementation_roadmap.md](./implementation_roadmap.md)。

---

## 1. 背景与目标

### 1.1 为什么需要这套系统

FinanceCrew 当前已具备完整的「多智能体投研 → 回测」链路：

- `backend/app/backtest.py` 提供 `ma_cross` / `grid` / `hold` / `ai` 四种策略回测
- `backend/app/data/fetcher.py` 提供 A 股 OHLCV 数据与 `compute_tech_signals()` 技术指标
- `backend/app/graph/nodes.py` 做多智能体辩论并产出 `consensus_score`

但**所有策略都是规则驱动或 LLM 驱动**，缺少两样东西：

1. **对历史信号的量化诊断**：这个金叉信号过去打出来，到底有多少比例真正赚钱？
2. **学习型信号质量判别器**：用 ML 从历史信号里学到「什么样的金叉更可靠」，在实盘/回测中过滤低质量信号。

本系统就是补齐这两环：**把每一次信号当成一条带标签的样本，沉淀成 CSV，训练模型，反哺策略**。

### 1.2 设计目标

| # | 目标 | 验收标准 |
|---|------|----------|
| G1 | 信号即样本：回测每产生一次买卖信号，落一条 50+ 维特征快照 | `record_signals=True` 时回测结果含 `signal_csv_path` |
| G2 | 标签可追溯：固定窗口 + 周期收益双标签，ATR 标准化 | `fill_labels()` 填满 11 个标签/收益列 |
| G3 | 防前视泄露：切分用 walk-forward / purge，不用随机切 | `ml_signal/split.py` 全部基于时间顺序 |
| G4 | 可解释：模型输出概率 + 特征重要性，不黑盒 | `evaluate` 输出 top 特征 |
| G5 | 零硬依赖：仅 pandas + numpy 必备，sklearn 可选降级 | venv 默认依赖即可跑通 |
| G6 | 接入现有链路：不动 fetcher / 前端，只在 backtest 旁路挂载 | 已完成，见 §4.2 |

### 1.3 不做什么（Non-goals）

- ❌ 不做高频 tick 级建模（本系统日频 D1）
- ❌ 不替换 LLM 决策，只做**信号质量过滤**（二分类：采纳 / 丢弃）
- ❌ 不做实盘自动下单
- ❌ 不引入 PyTorch / TensorFlow（保持轻量，sklearn 够用）

---

## 2. 系统架构

### 2.1 在 FinanceCrew 中的位置

```
┌─────────────────────────────────────────────────────────────────┐
│                      FinanceCrew 整体链路                         │
│                                                                  │
│  fetcher.get_history()  ──►  backtest.run_backtest()            │
│         (OHLCV)                 (ma_cross/ai/...)                │
│                                       │                          │
│                            ┌──────────┴──────────┐               │
│                            │                     │               │
│                       正常回测路径            【本系统】          │
│                       (equity/胜率)          信号诊断路径         │
│                                              │                   │
│                          record_signals=True │                   │
│                                              ▼                   │
│                              ┌───────────────────────────┐      │
│                              │ signal_features.py        │      │
│                              │ build_signal_features()   │      │
│                              │ fill_labels()             │      │
│                              │ save_signals_to_csv()     │      │
│                              └───────────┬───────────────┘      │
│                                          │ 65 列 CSV             │
│                                          ▼                       │
│                              ┌───────────────────────────┐      │
│                              │ data/ml_signals/*.csv     │      │
│                              └───────────┬───────────────┘      │
│                                          │                       │
│                              ┌───────────▼───────────────┐      │
│                              │ ml_signal/ (训练管线)      │      │
│                              │  features labels split    │      │
│                              │  train evaluate pipeline  │      │
│                              └───────────┬───────────────┘      │
│                                          │                       │
│                                          ▼                       │
│                              信号质量判别器 (概率)                │
│                              → 回灌回测做过滤                     │
└─────────────────────────────────────────────────────────────────┘
```

关键点：**本系统是旁路（sidecar）**，不改变主回测路径，只在 `record_signals=True` 时挂载采集。

### 2.2 两套特征机制（重要）

系统当前并存两套特征工程，服务于不同场景，**不是重复，是分层**：

| 机制 | 文件 | 触发时机 | 粒度 | 用途 |
|------|------|----------|------|------|
| **A. 事件级快照** | `signal_features.py` | 回测中每次产生买卖信号 | 单条信号（50+ 维） | 采集训练样本 → CSV |
| **B. 向量化特征** | `ml_signal/features.py` | 批量训练前对整段 K 线加列 | 每根 K 线一行（18 维） | 离线训练输入矩阵 |

- 机制 A 解决「**在哪一刻产生了信号，那一刻长什么样**」
- 机制 B 解决「**整段历史每根 K 线的通用特征矩阵**，供模型批量学习」

两者共享相同的底层指标族（收益率/动量/波动/量能/结构），但 A 是事件触发、含原策略状态；B 是逐 bar、通用。最终训练用 B（行多、标签规整），A 的 CSV 作为**信号级诊断数据集**（看「打出的信号质量如何」）。

> 决策：CSV 格式以机制 A 的 `SIGNAL_CSV_COLUMNS` 为准（65 列），见 [csv_feature_format.md](./csv_feature_format.md)。

---

## 3. 数据流

### 3.1 信号采集流（已实现）

```
run_backtest(symbol, strategy="ma_cross", record_signals=True)
   │
   ├─ 1. fetcher.get_history()  → df (含 open/high/low/close/volume/date)
   │
   ├─ 2. 策略循环逐 bar 判断
   │      └─ 产生金叉/死叉时:
   │           build_signal_features(df, i, symbol, direction, strategy)
   │           → 50+ 维特征 dict（标签列先留 None）
   │           → append 到 signal_log
   │
   ├─ 3. 回测结束:
   │      fill_labels(signal_log, df)
   │      → 按 signal_time 定位回 df，向前取 5/10/20 日收益，ATR 标准化
   │      → 填 future_ret_*d, label_fixed_*d 等列
   │
   └─ 4. save_signals_to_csv(signal_log)
          → 写入 backend/data/ml_signals/signals_<timestamp>.csv
          → 返回 result["signal_csv_path"]
```

### 3.2 训练流（已实现）

```
CSV / DataFrame (机制 B)
   │
   ├─ add_features(df)               # 加 18 维通用特征
   ├─ triple_barrier_labels(df)      # 三重壁垒打标 {+1,0,-1}
   ├─ walk_forward_split(n)          # 时间序列切分，防泄露
   ├─ train_model(X_train, y_train)  # sklearn 优先，numpy 降级
   └─ evaluate_predictions(...)      # 精度/召回/夏普/胜率
```

全部 7 步由 `pipeline.run_ml_pipeline()` 串联，支持单股诊断与 `diagnose_symbols()` 多股批量。详见 [implementation_roadmap.md](./implementation_roadmap.md) §1。

---

## 4. 关键设计决策

### 4.1 标签设计：双标签体系

为什么不只用一个标签？因为单一标签在不同持仓周期 / 波动环境下的含义不同。

| 标签族 | 列名前缀 | 含义 | 生成时机 |
|--------|----------|------|----------|
| **固定窗口** | `label_fixed_5d` / `label_fixed_10d` / `label_fixed_20d` | 信号后第 N 天收益（按 direction 方向）是否 > 0.3×ATR | `fill_labels()` 回测末尾 |
| **固定窗口收益** | `future_ret_*d` / `future_ret_*d_atr` | 绝对收益 + ATR 标准化收益 | 同上 |
| **周期收益**（预留） | `label_cycle_profit` / `cycle_realized_profit` | 从本信号到反向信号的完整持仓周期收益 | 待实现（需配对买卖信号） |

ATR 标准化的意义：**一个涨 3% 的信号，在高波动股上可能是噪音，在低波动股上才是真信号**。用 ATR 归一后才能跨股票可比。

### 4.2 接入策略：旁路挂载，零侵入

```python
# backtest.py 已有的接入方式（机制 A）
if record_signals and signal_log is not None:
    from .signal_features import build_signal_features
    feat = build_signal_features(df, i, symbol, 1, "ma_cross")
    if feat:
        signal_log.append(feat)
```

- 默认 `record_signals=False`，**零开销**，不影响正常回测性能
- `True` 时才采集，采集完在 `run_backtest` 末尾统一 `fill_labels` + 落 CSV
- `signal_features` 用**惰性 import**（函数内 `from .signal_features import ...`），避免循环依赖和启动开销

### 4.3 防前视泄露：三层防护

| 层 | 手段 | 位置 |
|----|------|------|
| 特征层 | 所有特征在 t 时只用 ≤t 的数据 | `build_signal_features` / `add_features` 均遵守 |
| 标签层 | `fill_labels` 只向前取 future_ret | `signal_features.py` |
| 切分层 | `time_series_split` / `walk_forward_split` + `purge_window` | `ml_signal/split.py` |

`purge_window` 默认 10：训练集末尾 10 行的标签可能「偷看」到验证集开头价格，必须剔除。

### 4.4 依赖与降级

| 依赖 | 是否必备 | 用途 | 降级方案 |
|------|----------|------|----------|
| pandas | ✅ 必备 | DataFrame（fetcher 已依赖） | — |
| numpy | ✅ 必备 | 向量计算 | — |
| scikit-learn | ⚪ 可选 | `train_model` 用 LR/RF | `train.py` 需用 numpy 手写逻辑回归降级 |

`requirements.txt` 当前**不含 sklearn**，故 `train.py` 必须支持 numpy-only 降级路径。

---

## 5. 目录与文件结构

```
backend/
├── app/
│   ├── signal_features.py      ✅ 已实现：事件级特征快照 + CSV + 标签（383 行）
│   ├── backtest.py             ✅ 已接入 record_signals 钩子（ma_cross / ai）
│   ├── data/fetcher.py         ✅ 数据源（不改动）
│   └── ml_signal/              ✅ 训练管线包（全部实现）
│       ├── __init__.py         ✅ 包导出
│       ├── features.py         ✅ 向量化特征（18 维）
│       ├── labels.py           ✅ 三重壁垒 / 二分类标签
│       ├── split.py            ✅ 时间序列切分 + purge
│       ├── train.py            ✅ sklearn/numpy 自适应训练（272 行）
│       ├── evaluate.py         ✅ 分类 + 策略双层评估（247 行）
│       └── pipeline.py         ✅ 全流程编排 + 多股批量（206 行）
└── data/
    └── ml_signals/             ✅ CSV 输出目录（运行时自动创建）
        └── signals_*.csv

docs/                           ✅ 本文档集
├── ML_SIGNAL_DIAGNOSIS.md      ← 你在这里
├── csv_feature_format.md       CSV 字段规范
├── sample_signals.csv          样本 CSV
└── implementation_roadmap.md   实现路线图
```

---

## 6. 与现有模块的契约

### 6.1 signal_features ↔ backtest

`backtest.py` 调用 `signal_features` 的三个函数：

| 函数 | 签名 | 作用 |
|------|------|------|
| `build_signal_features` | `(df, i, symbol, direction, strategy) → dict\|None` | 信号产生时建特征 |
| `fill_labels` | `(signals, df, threshold_atr=0.3) → signals` | 回测末尾填标签 |
| `save_signals_to_csv` | `(signals, filename=None) → path` | 落 CSV |

回测结果 dict 新增字段（`record_signals=True` 时）：
- `signal_log_count`: 信号数
- `signal_csv_path`: CSV 绝对路径
- `signal_sample`: 前 3 条信号预览

### 6.2 ml_signal ↔ 数据格式

`ml_signal/features.add_features()` 的输入要求与 `fetcher.get_history()` 输出**完全一致**：

```
列: date, open, high, low, close, volume, ma5, ma20, ma60
类型: date 为 datetime，其余为 float
```

无需任何适配层，直接 `df = fetcher.get_history(sym); df_feat = add_features(df)`。

---

## 7. 验证方法

```bash
# 在 backend 目录用项目 venv 验证采集流
cd D:/top/finance/backend
./.venv/Scripts/python.exe -c "
from app.backtest import run_backtest
r = run_backtest('600519', strategy='ma_cross', days=180, record_signals=True)
print('signal_csv_path:', r.get('signal_csv_path'))
print('count:', r.get('signal_log_count'))
print('sample:', r.get('signal_sample', [{}])[0] if r.get('signal_sample') else 'N/A')
"
```

预期：生成 `backend/data/ml_signals/signals_<时间戳>.csv`，含表头和若干信号行，标签列已被 `fill_labels` 填充。

> 若 `600519` 数据不可用，换任意 A 股代码。若 akshare 网络不通，回测返回 None 属正常（fetcher 容错设计）。

---

## 8. 风险与开放问题

| 风险 | 影响 | 缓解 |
|------|------|------|
| 单股票信号样本太少（180 天 MA 策略可能只有 ~10 次信号） | 事件级 CSV 训练欠拟合 | 用 `pipeline.diagnose_symbols()` 走向量化路径（逐 bar 样本量足够）；或多股 CSV 聚合 |
| ATR/DI 简化实现（ADX 用单周期 DX 近似） | 趋势特征精度略低 | 可接受；后续可换 Wilder 平滑 |
| 标签 `label_cycle_profit` 未实现 | 周期收益类标签为 None | 需配对买卖信号，待 roadmap §3 |
| `signal_features.py:93` 行 `lows = df["close"]` 应为 `df["low"]` | 振幅计算略偏 | 轻微 bug，见 roadmap §3 P1 |

---

## 9. 后续阅读

- 字段逐列含义 → [csv_feature_format.md](./csv_feature_format.md)
- 看真实 CSV 长什么样 → [sample_signals.csv](./sample_signals.csv)
- 接下来写哪些代码 → [implementation_roadmap.md](./implementation_roadmap.md)
