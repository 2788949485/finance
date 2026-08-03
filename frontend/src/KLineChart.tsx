// 自绘 SVG 蜡烛图（无第三方库）
import { useMemo, useState } from 'react'
import type { KlineBar } from './types'

const UP = '#22c55e'
const DOWN = '#ef4444'

export default function KLineChart({ bars, symbol }: { bars: KlineBar[]; symbol: string }) {
  const [range, setRange] = useState(60)
  const data = useMemo(() => bars.slice(-range), [bars, range])
  const [hover, setHover] = useState<KlineBar | null>(null)

  if (data.length < 2) {
    return <div className="kline-empty">K线数据不足</div>
  }

  const W = 680, H = 240, PAD = { t: 14, r: 10, b: 22, l: 56 }
  const vw = W - PAD.l - PAD.r
  const vh = H - PAD.t - PAD.b

  const highs = data.map((d) => d.high)
  const lows = data.map((d) => d.low)
  const maxV = Math.max(...highs)
  const minV = Math.min(...lows)
  const span = maxV - minV || 1
  const y = (v: number) => PAD.t + ((maxV - v) / span) * vh
  const step = vw / data.length
  const bw = Math.max(2, step * 0.6)

  // 均线（简单计算 MA5 / MA20）
  const ma = (n: number) => data.map((_, i) => {
    if (i < n - 1) return null
    const s = data.slice(i - n + 1, i + 1)
    return s.reduce((a, b) => a + b.close, 0) / n
  })
  const ma5 = ma(5)
  const ma20 = ma(20)

  const last = data[data.length - 1]
  const trend = last.close >= last.open ? UP : DOWN

  return (
    <div className="kline-wrap">
      <div className="kline-head">
        <span className="kline-symbol">{symbol}</span>
        <span className="kline-price" style={{ color: trend }}>
          {last.close} <small>{trend === UP ? '▲' : '▼'}</small>
        </span>
        <span className="kline-range">
          {[30, 60, 120].map((n) => (
            <button key={n} className={`ghost ${range === n ? 'active' : ''}`} onClick={() => setRange(n)}>{n}日</button>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="kline-svg" onMouseLeave={() => setHover(null)}>
        {/* 网格 */}
        {[0.25, 0.5, 0.75].map((r) => (
          <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + vh * r} y2={PAD.t + vh * r} stroke="#1e293b" strokeWidth="1" />
        ))}
        {/* 均线 */}
        <polyline
          points={ma5.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#f59e0b" strokeWidth="1.2" opacity="0.9"
        />
        <polyline
          points={ma20.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#3b82f6" strokeWidth="1.2" opacity="0.9"
        />
        {/* 蜡烛 */}
        {data.map((d, i) => {
          const x = PAD.l + i * step + step / 2
          const up = d.close >= d.open
          const c = up ? UP : DOWN
          const top = Math.min(y(d.open), y(d.close))
          const h = Math.max(Math.abs(y(d.open) - y(d.close)), 1)
          return (
            <g key={d.date} onMouseEnter={() => setHover(d)}>
              <line x1={x} x2={x} y1={y(d.high)} y2={y(d.low)} stroke={c} strokeWidth="1" />
              <rect x={x - bw / 2} y={top} width={bw} height={h} fill={up ? c : c} opacity={up ? 0.95 : 0.95} stroke={c} strokeWidth="0.5" />
            </g>
          )
        })}
        {/* Y 轴刻度 */}
        {[0, 0.5, 1].map((r) => {
          const v = maxV - span * r
          return (
            <text key={r} x={PAD.l - 6} y={PAD.t + vh * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {v.toFixed(2)}
            </text>
          )
        })}
        {/* 十字线 tooltip */}
        {hover && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(hover.close)} y2={y(hover.close)} stroke="#64748b" strokeWidth="0.6" strokeDasharray="3 3" />
            <text x={W - PAD.r} y={y(hover.close) - 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {hover.close.toFixed(2)}
            </text>
          </g>
        )}
      </svg>
      {hover && (
        <div className="kline-tooltip">
          {hover.date} 开 {hover.open} 高 {hover.high} 低 {hover.low} 收 {hover.close} 量 {Math.round(hover.volume / 10000)}万手
        </div>
      )}
    </div>
  )
}
