# 专业K线图坐标轴/网格/刻度调研报告

调研来源：TradingView lightweight-charts 源码 + ECharts 源码 + 官方文档。
本报告为修改 KLineChart.tsx 前的依据。

---

## 一、核心发现：专业图表怎么算刻度数量

### TradingView（行业标杆）的算法
源码：`src/model/price-tick-mark-builder.ts` + `price-tick-span-calculator.ts`

**关键点：刻度数量不是写死的固定档数，而是动态计算的。**

```
tickMarkHeight = ceil(fontSize × tickMarkDensity)
maxTickSpan     = (high - low) × tickMarkHeight / scaleHeight
```

- `tickMarkDensity` 默认值 = **2.5**（官方文档确认）
- `fontSize` 默认 = 11
- → 每个 tick 至少占用 `11 × 2.5 = 27.5px` 垂直空间（避免文字重叠）
- 实际 tick 数 = `scaleHeight / 27.5`

**实例**：当前主图 priceH ≈ 232px（(320-14-22)×0.72），按 TradingView 标准：
  `232 / 27.5 ≈ 8~9 档价格刻度` ← 这就是专业图表为什么是 8-10 档而非 5 档。

### "Nice Number"（漂亮间隔）算法
源码 `PriceTickSpanCalculator`：用除数序列 `[2, 2.5, 2]` 循环，把原始区间不断细分，
直到间隔 < maxTickSpan。结果是 1/2/5 系列的"圆整"数字：

| 价格区间(span) | 计算出的间隔 | 大致档数(232px) |
|---|---|---|
| 0.5 | 0.05 | 10 |
| 1.0 | 0.1 | 10 |
| 5.0 | 0.5 | 10 |
| 10 | 1 | 10 |
| 50 | 5 | 10 |
| 100 | 10 | 10 |

→ **专业图表价格刻度间距永远是 1/2/5×10^n 的圆整数**，不是均匀 5 等分。

### ECharts 的算法（佐证）
源码 `src/coord/axisDefault.ts` + `src/util/number.ts nice()`：
- value 轴 `splitNumber: 5`（默认，但这是"目标数"非"精确数"）
- 实际用 nice-number 算法 round 到 1/2/5，**最终段数可能是 4~7，不保证正好 5**
- time 轴 `splitNumber: 6`
- `splitLine.lineStyle.type: 'solid'`，`width: 1`（默认实线）

---

## 二、网格线（TradingView 标准）

源码 `src/model/grid.ts` + `views/pane/grid-pane-view.ts`：

```ts
interface GridLineOptions {
  color: '#D6DCDE'   // 浅灰（亮色主题）
  style: LineStyle.Solid   // 实线（默认）
  visible: true
}
```

**关键设计：网格线 = 刻度位置。**
> "A grid is represented ... as vertical and horizontal lines drawn at the levels
> of visible marks of price and the time scales."

- 水平网格线画在**每个价格 tick** 的 Y 坐标上（与价格标签对齐）
- 竖直网格线画在**每个时间 tick** 的 X 坐标上（与时间标签对齐）
- **网格线 ≠ 独立的 N 等分线**，而是刻度的视觉延伸
- 默认**实线**，颜色低对比（#D6DCDE 浅灰），不抢戏

ECharts 一致：`splitLine` 默认 solid 实线，浅色，category 轴默认不显示 splitLine。

---

## 三、放大缩放时坐标轴怎么响应（最关键）

**TradingView / ECharts / 同花顺/东方财富 全部一致：坐标轴跟随可见数据窗口重新计算。**

TradingView 流程（每次缩放/平移）：
1. `timeScale` 计算当前可见 bar 范围 → 重新生成时间刻度
2. 每个 series 计算**可见范围内**的 min/max（autoscale）→ 重新生成价格刻度
3. `rebuildTickMarks()` 用 nice-number 重新算间隔
4. 网格线随刻度位置重画

**结论：放大后 Y 轴价格范围变小（只看局部），刻度必须重算**，否则：
- 价格刻度消失（因为 `maxV-minV` 变小后，原 5 等分点脱离了主图区域）
- 时间刻度变稀（窗口内 bar 变少，固定 8 个标签会重叠或浪费）

这就是当前实现的核心 bug 来源。

---

## 四、副图（MACD/KDJ）Y 轴处理

TradingView 做法：
- 副图是**独立 pane**，有自己独立的 priceScale
- 副图 autoscale = 可见范围内指标值的 max/min（MACD 用 ±maxAbs 对称）
- 副图刻度数量同样用 `tickMarkDensity` 动态算，但**通常更少**（副图高度小）
- MACD 必须有**零轴**（当前实现已有 ✓）
- KDJ/RSI 这类 0-100 区间指标，刻度固定在关键位（20/50/80 或 30/70）

同花顺/东方财富：副图右侧只标 3-4 个关键值（0、±max 或 20/50/80），不画满 5 档。

---

## 五、价格刻度对齐方式

- **TradingView**：价格刻度默认在**右侧**（A股/美股惯例相反，但 TradingView 国际惯例右轴）
- **同花顺/东方财富**：价格刻度在**左侧**（A 股惯例）← 当前实现是左侧 ✓ 正确
- 标签 text-anchor=end（右对齐），离图表边缘 6px
- **刻度文字 Y 坐标必须与网格线 Y 坐标完全一致**（当前实现 ✓ 因为都用 `priceH*r`）

---

## 六、当前 KLineChart.tsx 的问题清单

对照源码，逐条诊断：

### 问题1（严重）：Y 轴固定 5 等分，而非 nice-number 动态刻度
- **现状**（line 597）：`[0, 0.25, 0.5, 0.75, 1].map(r => maxV - span*r)` — 均匀 5 等分
- **问题**：
  1. 5 档太少，专业是 8-10 档（TradingView density=2.5 → 232px≈8-9档）
  2. 刻度值不是圆整数（如 span=3.47 时出现 100.00 / 99.13 / 98.27...），不专业
  3. **放大后 span 变小，5 等分点间距更密，但档数不变 → 看不出细节**
- **应改为**：用 nice-number 算法，动态 8-10 档，值取 1/2/5×10^n

### 问题2（严重）：放大后 Y 轴刻度"消失"的根因
- **现状**：`maxV = max(highs)`, `minV = min(lows)` 其中 highs/lows 来自 `data`（已缩放的窗口）
- 这个逻辑**本身是对的**（跟随窗口），刻度消失的真正原因是：
  - `[0,0.25,...,1]` 5 档的 `maxV-span*r` 在窄区间下数值都挤在一起
  - 加上问题1的非圆整，看起来像"刻度没了/乱了"
- **真正修法**：改成 nice-number 刻度后，窄区间会自动显示合适密度，问题消失

### 问题3（严重）：X 轴固定 8 个标签，不随缩放/宽度调整
- **现状**（line 607）：`const n = Math.min(8, data.length)` — 写死 8
- **问题**：
  1. 放大后窗口 bar 少（如 winCount=30），8 个标签会重叠
  2. 缩到最小时（winCount=10），8 个标签 > bar 数，重复
  3. TradingView 是按"最小标签间距"（timeScale 的 tickMarkSpacing）动态算
- **应改为**：标签数 = `floor(vw / minLabelWidth)`，minLabelWidth≈70-80px（一个日期标签宽度）
  - vw=614 → 614/75 ≈ 8 个（巧合接近 8，但放大后 vw 不变但数据少时应减少）

### 问题4（中等）：网格线是独立虚线，未对齐刻度
- **现状**：
  - 水平网格（line 544）：`[0.25,0.5,0.75]` 3 条虚线 —— **与 5 档价格刻度不对齐**（刻度在 0/0.25/0.5/0.75/1，网格在 0.25/0.5/0.75，缺顶底）
  - 竖直网格（line 639）：`strokeDasharray="2 4"` 虚线 opacity 0.5 —— 太弱
- **专业做法**：网格线 = 刻度位置（每个 tick 一条线），实线，浅色低对比
- **应改为**：水平网格画在每个价格 tick 处（与标签同 Y），实线 `#1e293b` 不透明或 opacity 0.8

### 问题5（轻微）：网格线颜色不一致
- 主图水平网格 `#1e293b`（line 545），X 轴竖网格也是 `#1e293b`（line 639）但虚线
- 副图 MACD 网格 `#1e293b` 虚线 `1 3`（line 703）
- 风格不统一，应统一为：水平实线 + 竖直实线，同色同透明度

### 问题6（轻微）：放大后 Y 轴范围"跳动"
- 每次 zoom 变化，`maxV/minV` 突变 → 网格和刻度抖动
- TradingView 有 `scaleMargins`（上下留 10% 空白）避免 K 线贴边，且 autoscale 有平滑
- 建议：给 priceH 上下各留 8% margin（`y(v)` 映射时区间放大 1.16 倍）

### 问题7（已正确，保留）
- ✓ 价格刻度在左侧（A 股惯例）
- ✓ MACD 零轴、KDJ 20/50/80 参考线
- ✓ 副图 clipPath 裁剪
- ✓ 刻度文字与网格同 Y 坐标（只要都改成 tick 驱动即可）

---

## 七、具体修改建议（给实现者）

### 1. Y 轴刻度：实现 nice-number 算法
```typescript
// 替换 line 345-349 的 maxV/minV 和 line 596-604 的 5 档刻度
function niceNum(range: number, round: boolean): number {
  const exp = Math.floor(Math.log10(range))
  const f = range / Math.pow(10, exp)
  let nf
  if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10
  else       nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10
  return nf * Math.pow(10, exp)
}
// 目标档数：根据高度动态算
const yTickCount = Math.max(6, Math.round(priceH / 28))  // 28≈11*2.5
const rawStep = (maxV - minV) / yTickCount
const niceStep = niceNum(rawStep, true)
const niceMin = Math.floor(minV / niceStep) * niceStep
const niceMax = Math.ceil(maxV / niceStep) * niceStep
// 重新映射 y()
const y = (v: number) => PAD.t + ((niceMax - v) / (niceMax - niceMin)) * priceH
// 刻度：从 niceMin 到 niceMax 每 niceStep 一档
const yTicks = []
for (let v = niceMin; v <= niceMax + 1e-9; v += niceStep) yTicks.push(v)
```

### 2. X 轴刻度：按最小标签宽度动态算
```typescript
// 替换 line 606-643 的固定 8 个
const MIN_LABEL_W = 72  // 一个 "MM-DD HH:MM" 标签的最小宽度
const xTickCount = Math.min(data.length, Math.max(3, Math.floor(vw / MIN_LABEL_W)))
const xInterval = Math.ceil(data.length / xTickCount)
// 取 idx = 0, xInterval, 2*xInterval ... 对应的 bar
```

### 3. 网格线：跟随刻度，实线
```typescript
// 水平网格 = 每个 yTick 一条实线
{yTicks.map(v => (
  <line x1={PAD.l} x2={W-PAD.r} y1={y(v)} y2={y(v)}
    stroke="#1e293b" strokeWidth="1" />
))}
// 竖直网格 = 每个 xTick 一条实线（去掉 strokeDasharray）
{xTicks.map(({x}) => (
  <line x1={x} y1={PAD.t} x2={x} y2={H-PAD.b}
    stroke="#1e293b" strokeWidth="1" />
))}
```

### 4. 放大响应：Y/X 刻度已在缩放后的 `data` 上计算（现状已对），
   改成 nice-number + 动态密度后自然正确，无需额外处理。

### 5. 副图 Y 轴：MACD 用 ±niceMaxAbs 对称，KDJ 固定 20/50/80 + 动态 max
```typescript
// MACD: 把 macdMax 也过一遍 niceNum
const macdNiceMax = niceNum(macdMax, true)
// KDJ: 保持 20/50/80，再补 0/100 和当前 kdjMax 的 nice 值
```

---

## 八、验证清单（改完后检查）
- [ ] 日K默认视图：Y 轴 8-10 档圆整价格，X 轴 7-9 个日期
- [ ] 放大到 5x：窗口 ~40 根 bar，Y 轴仍 6-8 档，X 轴 4-6 个日期（不重叠）
- [ ] 放大到 50x：窗口 ~10 根 bar，Y 轴 5-6 档，X 轴 3-4 个日期
- [ ] 网格线与刻度标签完全对齐（同 X / 同 Y）
- [ ] 网格实线、统一颜色、清晰可见
- [ ] MACD 副图零轴 + 对称 nice 刻度
- [ ] KDJ 副图 20/50/80 参考线清晰
