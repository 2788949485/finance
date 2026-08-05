# ML 信号诊断系统 — 实现路线图

| 版本 | 日期 |
|------|------|
| v0.2 | 2026-08-05 |

> 本文档逐模块标注 **已完成 / 待完善**，状态来自实际代码核查（2026-08-05）。
> 配合 [ML_SIGNAL_DIAGNOSIS.md](./ML_SIGNAL_DIAGNOSIS.md) 阅读。

---

## 0. 当前状态速览

**全部核心模块均已实现。** 信号诊断系统端到端可跑通。

| 模块 | 路径 | 状态 | 行数 |
|------|------|------|------|
| 事件级特征 + CSV + 标签 | `app/signal_features.py` | ✅ 完成 | 383 |
| 向量化特征 | `app/ml_signal/features.py` | ✅ 完成 | 131 |
| 三重壁垒 / 二分类标签 | `app/ml_signal/labels.py` | ✅ 完成 | 110 |
| 时间序列切分 + purge | `app/ml_signal/split.py` | ✅ 完成 | 134 |
| 模型训练（sklearn/numpy 自适应） | `app/ml_signal/train.py` | ✅ 完成 | 272 |
| 评估（分类 + 策略层） | `app/ml_signal/evaluate.py` | ✅ 完成 | 247 |
| 全流程编排 | `app/ml_signal/pipeline.py` | ✅ 完成 | 206 |
| 回测采集钩子 | `app/backtest.py` | ✅ 已接入（ma_cross / ai） |

---

## 1. 已实现模块清单（含设计要点）

### 1.1 事件级特征快照 — `signal_features.py` ✅

- `SIGNAL_CSV_COLUMNS` 固定 65 列表头
- `build_signal_features()` 50+ 维特征快照
- `save_signals_to_csv()` 写 UTF-8 CSV 到 `data/ml_signals/`
- `fill_labels()` 固定窗口 5/10/20 日收益 + ATR 标准化 + 二分类标签
- 辅助函数 `_ema / _atr / _adx_di`

### 1.2 向量化特征 — `ml_signal/features.py` ✅

- `add_features()` 18 维通用特征（收益率/动量RSI+MACD/波动/量能OBV/结构分位）
- `DEFAULT_FEATURE_COLUMNS` 默认特征列序
- 无未来函数，inf 清洗，dropna 头部

### 1.3 标签 — `ml_signal/labels.py` ✅

- `triple_barrier_labels()` 三重壁垒（止盈/止损/时间），支持方向元标签，输出 `{-1,0,+1,NaN}`
- `binary_labels()` 简易二分类（阈值 ±2%）

### 1.4 切分 — `ml_signal/split.py` ✅

- `Split` dataclass + `sizes()`
- `time_series_split()` 单次切分 + purge_window 防泄露
- `walk_forward_split()` 滚动生成器
- `purge_overlap()` 标签窗口跨边界去污

### 1.5 训练 — `ml_signal/train.py` ✅

**自适应后端，亮点设计：**

| 后端 | 触发条件 | 实现 |
|------|----------|------|
| `sklearn_rf` | sklearn 可用 + model=auto/rf | RandomForest(200树, depth=6, class_weight=balanced) |
| `sklearn_gb` | sklearn 可用 + model=gb | GradientBoosting(150树) |
| `sklearn_logit` | sklearn 可用 + model=logit | LogisticRegression(class_weight=balanced) |
| `numpy_logit` | sklearn 不可用 / model=numpy | 纯 numpy softmax 多分类逻辑回归（L2 正则，梯度下降） |
| `constant` | 样本不足 / 单一类别 | 兜底常量预测器 |

- `ModelTrainer` 统一接口：`fit / predict / predict_proba / feature_importance / backend`
- `predict_proba` 返回固定 `(n,3)`，列对应 `[-1,0,1]`
- 特征重要性：RF 用 `feature_importances_`，LR 用 `coef_`，numpy LR 用 `W`
- 容错：NaN 行过滤、样本不足退化为常量预测器

### 1.6 评估 — `ml_signal/evaluate.py` ✅

**双层评估（核心是「诊断」而非只看 AUC）：**

- `evaluate_predictions()` 分类层：accuracy + 三类 precision/recall/f1 + 混淆矩阵 + Brier + AUC（Mann-Whitney U 近似，**无 sklearn 依赖**）
  - 关键指标：`buy_precision`（+1 类精度）=「预测买的有多少真涨」
- `evaluate_strategy()` 策略层：把预测转成策略（+1满仓/-1空仓/0维持），算收益/超额/夏普(年化√252)/胜率/最大回撤，含千一交易成本，t-1信号→t执行（无未来函数）

### 1.7 全流程编排 — `ml_signal/pipeline.py` ✅

- `PipelineConfig` 超参 dataclass（标签/切分/模型/成本全可配）
- `PipelineResult` 结果 dataclass + `summary()` 人类可读诊断报告
- `run_ml_pipeline(df, config)` 端到端：特征→标签→切分→训练→分类评估→策略评估
- `diagnose_symbols(symbols, fetch_fn, days)` **多股票批量诊断**，单只失败容错不影响其他

---

## 2. 端到端验证方法

```bash
cd D:/top/finance/backend

# 方式1：回测采集信号 CSV（事件级）
./.venv/Scripts/python.exe -c "
from app.backtest import run_backtest
r = run_backtest('600519', strategy='ma_cross', days=250, record_signals=True)
print('CSV:', r.get('signal_csv_path'))
print('信号数:', r.get('signal_log_count'))
"

# 方式2：跑完整 ML 诊断管线（向量化）
./.venv/Scripts/python.exe -c "
from app.data import fetcher
from app.ml_signal.pipeline import run_ml_pipeline
df = fetcher.get_history('600519', days=400)
res = run_ml_pipeline(df)
print(res.summary())
"
```

预期输出 `summary()`：样本数、切分、后端、买入精度/召回、策略收益/超额/夏普/回撤/胜率、Top-5 特征。

---

## 3. 已知问题与待完善（非阻塞）

| 优先级 | 问题 | 位置 | 说明 |
|--------|------|------|------|
| P1 | `lows = df["close"].tolist()` 应为 `df["low"]` | `signal_features.py:93` | 轻微 bug，振幅计算用的是 close 而非 low，影响有限但应修 |
| P2 | `_adx_di` 用单周期 DX 近似 ADX，未做 Wilder 平滑 | `signal_features.py:_adx_di` | 趋势强度精度略低，可接受 |
| P2 | `label_cycle_profit` / `cycle_realized_profit` 未实现 | `signal_features.py` | 需配对买卖信号，暂为 None |
| P3 | grid / hold 策略未接入采集钩子 | `backtest.py` | 可选，扩充样本来源 |
| P3 | 多股票 CSV 聚合工具缺失 | — | 单股信号样本少；`pipeline.diagnose_symbols` 已解决向量化路径，但事件级 CSV 仍需聚合 |

---

## 4. 回灌策略（未来增强）

训练出模型后，在回测中用模型概率过滤低质量信号：

```python
# backtest.py 的未来增强（伪码）
if record_signals and model is not None:
    feat = build_signal_features(df, i, symbol, direction, strategy)
    proba = model.predict_proba([feat_vector])[0][2]  # +1 类概率
    if proba < 0.5:
        continue  # 过滤掉模型判为低质量的信号
```

---

## 5. 测试策略（建议补充）

- **单元测试**：放 `backend/test/test_ml_signal.py`
  - `labels.triple_barrier_labels` 边界用例（触及止盈/止损/时间壁垒）
  - `split.purge_overlap` 跨边界去污正确性
  - `train.ModelTrainer` sklearn 与 numpy 两条路径都能 fit/predict
- **集成测试**：用 `600519` 真实数据跑 `run_ml_pipeline`，断言返回非 None 且 `buy_precision` 字段存在
- **回归**：每次改 `SIGNAL_CSV_COLUMNS` 必须同步改 `csv_feature_format.md` 和 `sample_signals.csv`
