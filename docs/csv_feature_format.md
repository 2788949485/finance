# 信号 CSV 特征格式规范

| 版本 | 日期 | 对应代码 |
|------|------|----------|
| v1.0 | 2026-08-05 | `backend/app/signal_features.py` (`SIGNAL_CSV_COLUMNS`) |

> ⚠️ 本文档列顺序、列名与 `signal_features.py` 中的 `SIGNAL_CSV_COLUMNS` **完全一致**，是该列表的权威说明。任何改动需同步更新代码与本文。

---

## 1. 总览

- **文件位置**：`backend/data/ml_signals/signals_<YYYYMMDD_HHMMSS>.csv`
- **编码**：UTF-8
- **分隔符**：逗号 `,`
- **表头**：第一行为列名，顺序固定，**不允许变动**
- **总列数**：65
- **每行含义**：回测中产生的一次买卖信号（金叉 / 死叉 / AI 决策买入等），含该时刻的完整特征快照与回测后填充的标签
- **缺失值**：空字符串（标签列在样本距结尾太近、无法计算未来收益时为空）

### 1.1 列分组（6 大组）

| 组 | 列范围 | 列数 | 生成时机 | 是否用于训练 |
|----|--------|------|----------|-------------|
| ① 标识 | `signal_id` … `timeframe` + `direction` | 5 | 信号产生时 | 否（元信息） |
| ② 基础行情 | K线 / 收益率 / 振幅 / 连续形态 | 21 | 信号产生时 | ✅ |
| ③ 趋势特征 | EMA / ADX / DI | 14 | 信号产生时 | ✅ |
| ④ 波动率特征 | ATR / 布林带 | 6 | 信号产生时 | ✅ |
| ⑤ 交易环境 | 量能 / 时间 | 4 | 信号产生时 | ✅ |
| ⑥ 原策略状态 + 标签 | strategy / filter / future_ret / label | 15 | 部分信号时 / 部分回测末尾 | 标签为 y，其余可做特征 |

---

## 2. 逐列规范

> 单位列说明：价格类列以**元**为单位；收益率/偏移类列以**百分比**为单位（如 3.5 表示 3.5%）；ATR 标准化收益为**无量纲比值**。

### ① 标识组（5 列）

| # | 列名 | 类型 | 说明 | 示例 |
|---|------|------|------|------|
| 1 | `signal_id` | str | 信号唯一 ID。格式：`{symbol}_D_{date}_{index}_{BUY\|SELL}` | `600519_D_20260115_42_BUY` |
| 2 | `signal_time` | str | 信号当日，`YYYYMMDD` | `20260115` |
| 3 | `symbol` | str | 标准化股票代码（A 股 6 位 / 港股 hk+5 / 美股 us+） | `600519` |
| 4 | `timeframe` | str | K 线周期，固定 `D1`（日线） | `D1` |
| 5 | `direction` | int | 信号方向：`1`=做多/买入，`-1`=做空/卖出 | `1` |

### ② 基础行情组（21 列）

#### ②a. K 线 OHLC（信号根 + 前一根）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 6 | `open_1` | float | 信号根（第 i 根）开盘价 |
| 7 | `high_1` | float | 信号根最高价 |
| 8 | `low_1` | float | 信号根最低价 |
| 9 | `close_1` | float | 信号根收盘价（即入场参考价） |
| 10 | `open_2` | float | 前一根（i-1）开盘价 |
| 11 | `high_2` | float | 前一根最高价 |
| 12 | `low_2` | float | 前一根最低价 |
| 13 | `close_2` | float | 前一根收盘价 |

#### ②b. K 线形态

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 14 | `body_size_1` | float | 信号根实体大小 `|close_1 - open_1|` |
| 15 | `upper_shadow_1` | float | 信号根上影 `high_1 - max(close_1, open_1)` |
| 16 | `lower_shadow_1` | float | 信号根下影 `min(close_1, open_1) - low_1` |

#### ②c. 历史收益率（%）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 17 | `ret_1d` | float | `(close_1/close[i-1]-1)*100`，近 1 日收益% |
| 18 | `ret_3d` | float | 近 3 日收益% |
| 19 | `ret_5d` | float | 近 5 日收益% |
| 20 | `ret_10d` | float | 近 10 日收益% |
| 21 | `ret_20d` | float | 近 20 日收益% |

#### ②d. 振幅（%）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 22 | `amplitude_5d` | float | 过去 5 日 `(max_high/min_low - 1)*100` |
| 23 | `amplitude_10d` | float | 过去 10 日振幅% |
| 24 | `amplitude_20d` | float | 过去 20 日振幅% |

#### ②e. 连续形态

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 25 | `consecutive_up` | int | 信号根及之前**连续收红**（close↑）的根数 |
| 26 | `consecutive_down` | int | 连续收绿的根数 |

> 数据不足时收益/振幅记 `0.0`（见 `_ret` / `_amplitude` 实现），非空。

### ③ 趋势特征组（14 列）

EMA 周期默认：fast=5, slow=20（可在 `build_signal_features` 传入）。

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 27 | `ema_fast` | float | EMA(5) 当前值 |
| 28 | `ema_slow` | float | EMA(20) 当前值 |
| 29 | `ema_distance` | float | `(ema_fast-ema_slow)/price*100`，均线偏离% |
| 30 | `ema_distance_atr` | float | `(ema_fast-ema_slow)/ATR`，ATR 标准化的均线偏离 |
| 31 | `ema_fast_slope` | float | EMA(5) 斜率 `(ema_fast-prev)/price*100` |
| 32 | `ema_slow_slope` | float | EMA(20) 斜率 |
| 33 | `price_vs_ema_fast` | float | `(price-ema_fast)/price*100` |
| 34 | `price_vs_ema_slow` | float | `(price-ema_slow)/price*100` |
| 35 | `adx` | float | ADX 趋势强度（简化版：用单周期 DX 近似），0-100 |
| 36 | `adx_slope` | float | 前一根 ADX 值（近似斜率） |
| 37 | `di_plus` | float | +DI 多头方向指标 |
| 38 | `di_minus` | float | -DI 空头方向指标 |
| 39 | `di_diff` | float | `di_plus - di_minus` |
| 40 | `di_diff_adx_ratio` | float | `di_diff / adx`，方向强度归一化 |

> ⚠️ 当 `idx < period*2` 时，ADX/DI 返回默认值 `(25, 20, 20)`（见 `_adx_di`），非真实计算。

### ④ 波动率特征组（6 列）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 41 | `atr` | float | ATR(14) 真实波幅均值 |
| 42 | `atr_price_ratio` | float | `atr/price*100`，波动占价比 |
| 43 | `bb_width` | float | 布林带宽 `(upper-lower)/mid*100`，mid=EMA20 |
| 44 | `bb_width_change` | float | 当前带宽 - 前一根带宽 |
| 45 | `bb_position` | float | 价格在布林带中的位置 `[0,1]`，0=触下轨 1=触上轨 |
| 46 | `volatility_regime` | int | 波动率分位：`0`低波动 / `1`正常 / `2`高波动（基于 ATR 60 日分位） |

### ⑤ 交易环境组（4 列）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 47 | `volume` | int | 信号根成交量（手） |
| 48 | `volume_ma5_ratio` | float | `volume / 5日均量`，量比 |
| 49 | `day_of_week` | int | 星期几，0=周一 … 6=周日 |
| 50 | `month` | int | 月份 1-12 |

### ⑥ 原策略状态 + 标签组（15 列）

#### ⑥a. 原策略状态

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 51 | `strategy` | str | 产生信号的策略名：`ma_cross` / `ai` / … |
| 52 | `filter_ema_pass` | int | 0/1：信号方向是否与 EMA 排列一致（趋势过滤） |
| 53 | `filter_adx_pass` | int | 0/1：ADX 是否 > 20（趋势强度过滤） |
| 54 | `filter_bb_pass` | int | 0/1：价格是否在布林带边缘（`bb_position<0.3` 或 `>0.7`） |

#### ⑥b. 标签 —— 未来固定窗口收益（回测末尾填充）

> `direction` 已纳入计算：做多方向收益 = `(future-close_1)*direction_sign`。这些列在信号采集时为**空**，由 `fill_labels()` 在回测结束时回填。

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 55 | `future_ret_5d` | float | 第 5 日方向化绝对收益（元） |
| 56 | `future_ret_10d` | float | 第 10 日方向化绝对收益 |
| 57 | `future_ret_20d` | float | 第 20 日方向化绝对收益 |
| 58 | `future_ret_5d_atr` | float | 第 5 日收益 / ATR（无量纲） |
| 59 | `future_ret_10d_atr` | float | 第 10 日 ATR 标准化收益 |
| 60 | `future_ret_20d_atr` | float | 第 20 日 ATR 标准化收益 |

#### ⑥c. 标签 —— 二分类与周期收益

| # | 列名 | 类型 | 说明 | 状态 |
|---|------|------|------|------|
| 61 | `label_cycle_profit` | int | 完整持仓周期（本信号→反向信号）是否盈利 0/1 | ⚠️ 待实现，暂 None |
| 62 | `label_fixed_5d` | int | `future_ret_5d_atr > 0.3` ? 1 : 0 | ✅ |
| 63 | `label_fixed_10d` | int | `future_ret_10d_atr > 0.3` ? 1 : 0 | ✅ |
| 64 | `label_fixed_20d` | int | `future_ret_20d_atr > 0.3` ? 1 : 0 | ✅ |
| 65 | `cycle_realized_profit` | float | 周期实际收益率% | ⚠️ 待实现，暂 None |

**默认标签阈值** `threshold_atr=0.3`（可在 `fill_labels` 调用时覆盖）：即「未来收益超过 0.3 倍 ATR 才算有效信号」。该阈值的意义是过滤掉「波动噪音内的小幅漂移」。

---

## 3. 训练时如何取列

```python
import pandas as pd

df = pd.read_csv("signals_xxx.csv")

# 特征列：去掉标识、标签、strategy 等非数值/元信息列
EXCLUDE = {"signal_id","signal_time","symbol","timeframe","strategy",
           "label_cycle_profit","label_fixed_5d","label_fixed_10d",
           "label_fixed_20d","cycle_realized_profit",
           "future_ret_5d","future_ret_10d","future_ret_20d"}
feature_cols = [c for c in df.columns if c not in EXCLUDE]

X = df[feature_cols].apply(pd.to_numeric, errors="coerce")
y = df["label_fixed_10d"].astype(int)   # 推荐 10 日标签做主标签
```

**推荐主标签**：`label_fixed_10d`（10 日窗口平衡了「反应够快」与「不被短期噪音主导」）。

---

## 4. 版本演进规则

1. **只允许加列，不允许删列 / 改列名 / 调顺序**（向后兼容）。
2. 新增列统一追加到 `SIGNAL_CSV_COLUMNS` 末尾（标签区之后）。
3. 新增列需同步更新本文 §2 表格和列计数。
4. 任何语义变更（如改阈值默认值）需 bump 版本号并在本表记录。
