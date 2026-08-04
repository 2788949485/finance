// 自绘 SVG 图表：日K蜡烛图 + 分时折线图（无第三方库）
import { useMemo, useState } from 'react'
import type { KlineBar, MinutePoint } from './types'

const UP = '#22c55e'
const DOWN = '#ef4444'

interface Props {
  bars: KlineBar[]
  minute?: MinutePoint[]
  lastClose?: number | null
  symbol: string
  mode: 'day' | 'minute'
  onMode?: (m: 'day' | 'minute') => void
}

export default function KLineChart({ bars, minute, lastClose, symbol, mode, onMode }: Props) {
  const [range, setRange] = useState(60)
  const [hover, setHover] = useState<KlineBar | null>(null)

  const W = 680, H = 240, PAD = { t: 14, r: 10, b: 22, l: 56 }

  // ---------- 分时模式 ----------
  if (mode === 'minute' && minute && minute.length > 1) {
    const prices = minute.map((p) => p.price)
    const avgs = minute.map((p) => p.avg).filter((v): v is number => v != null)
    const base = lastClose ?? prices[0]
    const all = [...prices, ...avgs, base]
    const maxV = Math.max(...all)
    const minV = Math.min(...all)
    const span = maxV - minV || 1
    const vw = W - PAD.l - PAD.r
    const vh = H - PAD.t - PAD.b
    const y = (v: number) => PAD.t + ((maxV - v) / span) * vh
    const step = vw / minute.length
    const pts = (arr: number[]) => arr.map((v, i) => `${PAD.l + i * step},${y(v)}`).join(' ')
    const lastP = minute[minute.length - 1]
    const trend = lastP.price >= (lastClose ?? lastP.price) ? UP : DOWN
    return (
      <div className="kline-wrap">
        <div className="kline-head">
          <span className="kline-symbol">{symbol} 分时</span>
          <span className="kline-price" style={{ color: trend }}>
            {lastP.price} <small>{trend === UP ? '▲' : '▼'}</small>
          </span>
          <span className="kline-range">
            {onMode && <button className="ghost" onClick={() => onMode('day')}>日K</button>}
            <button className="ghost active">分时</button>
          </span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="kline-svg">
          {/* 昨收基准线 */}
          <line x1={PAD.l} x2={W - PAD.r} y1={y(base)} y2={y(base)} stroke="#64748b" strokeWidth="1" strokeDasharray="4 4" />
          <text x={PAD.l + 4} y={y(base) - 4} fontSize="10" fill="#64748b">昨收 {base.toFixed(2)}</text>
          {/* 网格 */}
          {[0.25, 0.5, 0.75].map((r) => (
            <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + vh * r} y2={PAD.t + vh * r} stroke="#1e293b" strokeWidth="1" />
          ))}
          {/* 价格线 */}
          <polyline points={pts(prices)} fill="none" stroke={trend} strokeWidth="1.6" />
          {/* 均价线 */}
          {avgs.length > 1 && (
            <polyline points={pts(avgs)} fill="none" stroke="#f59e0b" strokeWidth="1.1" opacity="0.85" />
          )}
          {/* Y 轴刻度 */}
          {[0, 0.5, 1].map((r) => (
            <text key={r} x={PAD.l - 6} y={PAD.t + vh * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {(maxV - span * r).toFixed(2)}
            </text>
          ))}
        </svg>
      </div>
    )
  }

  // ---------- 日K模式 ----------
  const data = useMemo(() => bars.slice(-range), [bars, range])
  if (data.length < 2) {
    return <div className="kline-empty">K线数据不足</div>
  }

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
          {onMode && <button className="ghost" onClick={() => onMode('minute')}>分时</button>}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="kline-svg" onMouseLeave={() => setHover(null)}>
        {[0.25, 0.5, 0.75].map((r) => (
          <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + vh * r} y2={PAD.t + vh * r} stroke="#1e293b" strokeWidth="1" />
        ))}
        <polyline
          points={ma5.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#f59e0b" strokeWidth="1.2" opacity="0.9"
        />
        <polyline
          points={ma20.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#3b82f6" strokeWidth="1.2" opacity="0.9"
        />
        {data.map((d, i) => {
          const x = PAD.l + i * step + step / 2
          const up = d.close >= d.open
          const c = up ? UP : DOWN
          const top = Math.min(y(d.open), y(d.close))
          const h = Math.max(Math.abs(y(d.open) - y(d.close)), 1)
          return (
            <g key={d.date} onMouseEnter={() => setHover(d)}>
              <line x1={x} x2={x} y1={y(d.high)} y2={y(d.low)} stroke={c} strokeWidth="1" />
              <rect x={x - bw / 2} y={top} width={bw} height={h} fill={c} opacity={0.95} stroke={c} strokeWidth="0.5" />
            </g>
          )
        })}
        {[0, 0.5, 1].map((r) => {
          const v = maxV - span * r
          return (
            <text key={r} x={PAD.l - 6} y={PAD.t + vh * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {v.toFixed(2)}
            </text>
          )
        })}
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
          {hover.date} 开 {hover.open} 高 {hover.high} 低 {hover.low} 收 {hover.close}
        </div>
      )}
    </div>
  )
}
