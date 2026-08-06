// 回测可视化组件：权益曲线 + 回撤水下图 + 蒙特卡洛直方图 + 参数热力图 + 月度收益热力图
import { useMemo } from 'react'
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell,
} from 'recharts'

const DOWN = '#ef4444'
const ACCENT = '#10b981'
const GRID = '#1f2937'
const AXIS = '#6b7280'

// 通用图例条
function LegendBar({ items }: { items: { color: string; label: string }[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 11, color: AXIS, marginTop: 6, marginBottom: 4 }}>
      {items.map((it, i) => (
        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 12, height: 12, background: it.color, borderRadius: 0, border: '1px solid #374151' }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

// ============ 权益曲线 + 回撤水下图 ============
export function EquityChart({ curve, initialCapital }: { curve: { date: string; value: number }[]; initialCapital?: number }) {
  if (!curve || curve.length < 2) return <div style={{ padding: 20, color: AXIS }}>数据不足</div>

  const data = curve.map(p => ({
    date: p.date,
    equity: Math.round(p.value),
  }))

  const initCap = initialCapital ?? curve[0]?.value ?? 100000

  return (
    <div>
      <LegendBar items={[
        { color: ACCENT, label: '策略权益' },
        { color: '#4b5563', label: '初始资金基准线' },
      ]} />
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={ACCENT} stopOpacity={0.3} />
              <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} opacity={0.4} />
          <XAxis dataKey="date" stroke={AXIS} fontSize={10} interval={Math.max(1, Math.floor(data.length / 8))} />
          <YAxis stroke={AXIS} fontSize={11} tickFormatter={v => (v / 10000).toFixed(1) + '万'} domain={['auto', 'auto']} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 0, fontSize: 12 }}
            labelStyle={{ color: '#9ca3af' }}
            formatter={(v: any) => [Number(v).toLocaleString() + '元', '权益']}
          />
          <ReferenceLine y={initCap} stroke="#4b5563" strokeDasharray="4 2" label={{ value: '初始资金', fill: AXIS, fontSize: 10, position: 'insideTopLeft' }} />
          <Area type="monotone" dataKey="equity" stroke={ACCENT} strokeWidth={1.5} fill="url(#equityGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============ 回撤水下图 (Underwater Chart) ============
export function DrawdownChart({ curve }: { curve: { date: string; value: number }[] }) {
  if (!curve || curve.length < 2) return null

  // 计算每个点的回撤百分比
  let peak = curve[0].value
  const ddData = curve.map(p => {
    if (p.value > peak) peak = p.value
    const dd = ((p.value - peak) / peak) * 100
    return { date: p.date, drawdown: Math.round(dd * 100) / 100 }
  })

  return (
    <div>
      <LegendBar items={[
        { color: DOWN, label: '回撤深度（从峰值下跌%）' },
        { color: '#4b5563', label: '0%基准线（无回撤）' },
      ]} />
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={ddData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={DOWN} stopOpacity={0} />
              <stop offset="100%" stopColor={DOWN} stopOpacity={0.4} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} opacity={0.3} />
          <XAxis dataKey="date" stroke={AXIS} fontSize={10} interval={Math.max(1, Math.floor(ddData.length / 8))} />
          <YAxis stroke={AXIS} fontSize={11} tickFormatter={v => v + '%'} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 0, fontSize: 12 }}
            labelStyle={{ color: '#9ca3af' }}
            formatter={(v: any) => [v.toFixed(2) + '%', '回撤']}
          />
          <ReferenceLine y={0} stroke="#4b5563" />
          <Area type="monotone" dataKey="drawdown" stroke={DOWN} strokeWidth={1} fill="url(#ddGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============ 蒙特卡洛收益分布直方图 ============
export function MonteCarloHistogram({ histogram, originalReturn }: { histogram: { bin_start: number; bin_end: number; count: number; label: string }[]; originalReturn?: number }) {
  if (!histogram || histogram.length === 0) return null

  const maxCount = Math.max(...histogram.map(h => h.count))

  return (
    <div>
      <LegendBar items={[
        { color: ACCENT, label: '正收益区间' },
        { color: DOWN, label: '亏损区间' },
        ...(originalReturn !== undefined ? [{ color: '#6b7280', label: `原始回测收益线(${originalReturn}%)` }] : []),
      ]} />
      <ResponsiveContainer width="100%" height={200}>
      <BarChart data={histogram} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} opacity={0.3} />
        <XAxis dataKey="bin_start" stroke={AXIS} fontSize={10} tickFormatter={v => v + '%'} />
        <YAxis stroke={AXIS} fontSize={11} label={{ value: '次数', angle: -90, position: 'insideLeft', fill: AXIS, fontSize: 10 }} />
        <Tooltip
          contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 0, fontSize: 12 }}
          labelStyle={{ color: '#9ca3af' }}
          formatter={(v: any) => [v + '次', '频次']}
          labelFormatter={(l: any) => `收益区间: ${l}%`}
        />
        {originalReturn !== undefined && (
          <ReferenceLine x={originalReturn} stroke={ACCENT} strokeWidth={2} label={{ value: '原始', fill: ACCENT, fontSize: 10, position: 'top' }} />
        )}
        <Bar dataKey="count" radius={0}>
          {histogram.map((h, i) => (
            <Cell key={i} fill={h.bin_start >= 0 ? ACCENT : DOWN} fillOpacity={0.3 + 0.7 * (h.count / maxCount)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
    </div>
  )
}

// ============ 参数敏感度二维热力图 ============
export function SensitivityHeatmap({ results }: { results: { fast: number; slow: number; total_return: number }[] }) {
  if (!results || results.length === 0) return null

  // 构建 fast × slow 矩阵
  const fastVals = [...new Set(results.map(r => r.fast))].sort((a, b) => a - b)
  const slowVals = [...new Set(results.map(r => r.slow))].sort((a, b) => a - b)

  // 找最大最小收益确定颜色范围
  const returns = results.map(r => r.total_return)
  const maxR = Math.max(...returns.map(Math.abs))

  // 构建矩阵数据
  const cellW = 48, cellH = 32, labelW = 30, labelH = 20

  const getColor = (val: number) => {
    const ratio = val / (maxR || 1)
    if (val >= 0) {
      const a = Math.min(Math.abs(ratio), 1)
      return `rgba(16, 185, 129, ${0.15 + 0.65 * a})`
    } else {
      const a = Math.min(Math.abs(ratio), 1)
      return `rgba(239, 68, 68, ${0.15 + 0.65 * a})`
    }
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={labelW + fastVals.length * cellW + 20} height={labelH + slowVals.length * cellH + 20}>
        {/* 列标签（快线） */}
        {fastVals.map((f, fi) => (
          <text key={`f${fi}`} x={labelW + fi * cellW + cellW / 2} y={labelH - 4}
            textAnchor="middle" fill={AXIS} fontSize={10}>{f}</text>
        ))}
        {/* 行标签（慢线） */}
        {slowVals.map((s, si) => (
          <text key={`s${si}`} x={labelW - 4} y={labelH + si * cellH + cellH / 2 + 3}
            textAnchor="end" fill={AXIS} fontSize={10}>{s}</text>
        ))}
        {/* 热力图单元格 */}
        {results.map((r, i) => {
          const fi = fastVals.indexOf(r.fast)
          const si = slowVals.indexOf(r.slow)
          if (fi < 0 || si < 0) return null
          return (
            <g key={i}>
              <rect x={labelW + fi * cellW} y={labelH + si * cellH} width={cellW - 1} height={cellH - 1}
                fill={getColor(r.total_return)} stroke="#1f2937" strokeWidth={0.5} />
              <text x={labelW + fi * cellW + cellW / 2} y={labelH + si * cellH + cellH / 2 + 3}
                textAnchor="middle" fill="#e5e7eb" fontSize={9} fontWeight={600}>
                {r.total_return > 0 ? '+' : ''}{r.total_return.toFixed(1)}
              </text>
            </g>
          )
        })}
      </svg>
      <div style={{ fontSize: 11, color: AXIS, marginTop: 8, marginBottom: 4 }}>
        <LegendBar items={[
          { color: `rgba(16, 185, 129, 0.8)`, label: '高收益' },
          { color: `rgba(16, 185, 129, 0.2)`, label: '低收益' },
          { color: `rgba(239, 68, 68, 0.2)`, label: '小亏损' },
          { color: `rgba(239, 68, 68, 0.8)`, label: '大亏损' },
        ]} />
        横轴=快线周期, 纵轴=慢线周期
        <br />最大收益: <span style={{ color: ACCENT }}>+{maxR.toFixed(2)}%</span>
        {' | '}最大亏损: <span style={{ color: DOWN }}>-{maxR.toFixed(2)}%</span>
      </div>
    </div>
  )
}

// ============ 月度收益热力图 ============
export function MonthlyHeatmap({ curve }: { curve: { date: string; value: number }[] }) {
  const monthlyData = useMemo(() => {
    if (!curve || curve.length < 2) return []
    // 按月聚合：取每月最后一天的权益
    const monthMap = new Map<string, number>()
    for (const p of curve) {
      const ym = p.date.substring(0, 7) // "2026-01"
      monthMap.set(ym, p.value)
    }
    const months = [...monthMap.entries()].sort()

    // 计算每月收益率
    const result: { month: string; year: number; monthNum: number; ret: number }[] = []
    for (let i = 1; i < months.length; i++) {
      const prev = months[i - 1][1]
      const curr = months[i][1]
      const [year, mon] = months[i][0].split('-')
      result.push({
        month: months[i][0],
        year: parseInt(year),
        monthNum: parseInt(mon),
        ret: ((curr - prev) / prev) * 100,
      })
    }
    return result
  }, [curve])

  if (monthlyData.length === 0) return null

  // 构建年份×月份矩阵
  const years = [...new Set(monthlyData.map(d => d.year))].sort()
  const maxAbs = Math.max(...monthlyData.map(d => Math.abs(d.ret)))

  const getColor = (ret: number) => {
    const ratio = ret / (maxAbs || 1)
    if (ret >= 0) return `rgba(16, 185, 129, ${0.15 + 0.65 * Math.abs(ratio)})`
    return `rgba(239, 68, 68, ${0.15 + 0.65 * Math.abs(ratio)})`
  }

  const monthLabels = ['', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
  const cellW = 42, cellH = 28, labelW = 40

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={labelW + 12 * cellW + 20} height={24 + years.length * cellH + 20}>
        {/* 列标签（月） */}
        {monthLabels.slice(1).map((m, mi) => (
          <text key={mi} x={labelW + mi * cellW + cellW / 2} y={18}
            textAnchor="middle" fill={AXIS} fontSize={10}>{m}</text>
        ))}
        {/* 行标签（年）+ 单元格 */}
        {years.map((year, yi) => {
          const dataForRow = monthlyData.filter(d => d.year === year)
          return (
            <g key={year}>
              <text x={labelW - 4} y={24 + yi * cellH + cellH / 2 + 3} textAnchor="end" fill={AXIS} fontSize={10}>{year}</text>
              {Array.from({ length: 12 }, (_, mi) => {
                const cell = dataForRow.find(d => d.monthNum === mi + 1)
                if (!cell) return (
                  <rect key={mi} x={labelW + mi * cellW} y={24 + yi * cellH} width={cellW - 1} height={cellH - 1}
                    fill="#111827" stroke="#1f2937" strokeWidth={0.5} />
                )
                return (
                  <g key={mi}>
                    <rect x={labelW + mi * cellW} y={24 + yi * cellH} width={cellW - 1} height={cellH - 1}
                      fill={getColor(cell.ret)} stroke="#1f2937" strokeWidth={0.5} />
                    <text x={labelW + mi * cellW + cellW / 2} y={24 + yi * cellH + cellH / 2 + 3}
                      textAnchor="middle" fill="#e5e7eb" fontSize={9} fontWeight={600}>
                      {cell.ret > 0 ? '+' : ''}{cell.ret.toFixed(1)}
                    </text>
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
      <LegendBar items={[
        { color: `rgba(16, 185, 129, 0.8)`, label: '月度大涨(>5%)' },
        { color: `rgba(16, 185, 129, 0.3)`, label: '小幅上涨' },
        { color: `rgba(239, 68, 68, 0.3)`, label: '小幅下跌' },
        { color: `rgba(239, 68, 68, 0.8)`, label: '月度大跌(<-5%)' },
        { color: '#111827', label: '无数据' },
      ]} />
    </div>
  )
}
