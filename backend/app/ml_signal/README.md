# ML 信号诊断系统 (`app.ml_signal`)

从历史 K 线学习买卖信号并诊断策略有效性的 ML 训练管线。

## 快速开始

```python
from app.data import fetcher
from app.ml_signal import run_ml_pipeline, PipelineConfig

# 1. 取数（复用 FinanceCrew 数据层）
df = fetcher.get_history(fetcher._norm_symbol("600519"), days=400)

# 2. 跑诊断
result = run_ml_pipeline(df, PipelineConfig(model="rf"))

# 3. 看报告
print(result.summary())
```

输出示例：
```
ML 信号诊断报告
==================================================
样本数: 332  切分: {'train': 207, 'val': 29, 'test': 56}
后端: sklearn_rf

分类指标:
  准确率:     0.3571
  买入精度:   0.3409  (最关键: 预测买的有多少真涨)
  买入召回:   0.6818

策略表现:
  策略收益:   -3.47%
  基准收益:   -3.82%
  超额收益:   0.35%
  夏普比率:   -0.723

Top-5 重要特征:
  ma_bias_60: 0.135
  ret_20d: 0.1096
  ...
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `features.py` | 特征工程：18 个特征（收益率/动量/波动率/量能/结构） |
| `labels.py` | 标签生成：三重壁垒 + 简易二分类 |
| `split.py` | 时间序列切分：单次 / walk-forward / purge 防泄露 |
| `train.py` | 模型训练：sklearn（rf/gb/logit）优先，numpy 兜底 |
| `evaluate.py` | 评估：分类指标 + 策略回测（收益/夏普/胜率） |
| `pipeline.py` | 编排：`run_ml_pipeline` 串联全流程 |

## 设计要点

### 1. 自适应后端
- **sklearn 可用**（已安装）：RandomForest / GradientBoosting / LogisticRegression
- **sklearn 不可用**：退化为纯 numpy 的 softmax 逻辑回归（梯度下降 + L2）
- 通过 `model="auto"` 自动选择，或显式指定 `model="numpy"` 强制兜底

### 2. 防止数据泄露
- 所有特征在 t 时只用 t 及之前信息（无未来函数）
- 切分按时间顺序，边界两侧删除 `purge_window` 个样本
- 标签窗口（`max_holding_days`）不跨训练/测试边界

### 3. 三重壁垒标签（López de Prado）
对每个时点设置止盈/止损/时间三道壁垒，标签 ∈ {-1, 0, +1}：
- `+1`：先触及止盈线（看对）
- `-1`：先触及止损线（看错）
- `0`：超时未触及，按到期方向判定

### 4. 诊断而非预测
系统核心价值是**诊断**——回答「ML 信号到底有没有 alpha」：
- `buy_precision`：预测买入的信号里有多少真的涨了（最关键）
- `excess_return`：ML 策略 vs 买入持有的超额收益
- `feature_importance`：哪些特征真正有预测力

如果 `buy_precision` 接近随机（~0.33）且超额收益为负，说明当前特征集不足以产生 alpha。

## 配置参数

```python
PipelineConfig(
    feature_cols=[...],          # 默认 18 个特征
    take_profit_pct=0.05,        # 止盈 5%
    stop_loss_pct=0.05,          # 止损 5%
    max_holding_days=10,         # 最大持有 10 天
    test_size=0.2,               # 测试集 20%
    val_size=0.15,               # 验证集 15%
    purge_window=10,             # 防泄露窗口
    model="auto",                # auto/rf/gb/logit/numpy
    transaction_cost_pct=0.001,  # 单边千一手续费
)
```

## 批量诊断多只股票

```python
from app.ml_signal import diagnose_symbols
results = diagnose_symbols(["600519", "000858", "601318"])
for sym, res in results.items():
    print(f"\n=== {sym} ===")
    print(res.summary())
```

## 测试

```bash
cd backend
.venv/Scripts/python.exe -m tests.test_ml_signal
```

## 与 backtest.py 的关系

| 维度 | `backtest.py` | `ml_signal` |
|------|----------------|-------------|
| 目标 | 模拟策略交易 | 训练 ML 模型诊断信号 |
| 数据 | 同一 OHLCV 格式 | 同一 OHLCV 格式 |
| 决策 | 规则驱动（MA交叉/网格/AI） | 数据驱动（学出来的模型） |
| 输出 | 收益/回撤/交易记录 | 分类指标 + 策略表现 + 特征重要性 |

两者互补：`backtest.py` 验证人工策略，`ml_signal` 自动发现特征组合的预测力。
